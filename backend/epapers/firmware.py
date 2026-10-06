"""Validated firmware library and acknowledged, sequential updates per gateway."""

import asyncio
import base64
import hashlib
import struct
import time
import uuid
import zlib
from pathlib import Path


def inspect_firmware(kind, data):
    if kind == "gateway":
        if (
            len(data) < 256
            or len(data) > 4 * 1024 * 1024
            or data[0] != 0xE9
            or struct.unpack_from("<H", data, 12)[0] != 9
        ):
            raise ValueError("请选择 ESP32-S3 的应用固件 .bin")
        if struct.unpack_from("<I", data, 32)[0] != 0xABCD5432:
            raise ValueError("文件不是 ESP32 应用镜像")
        version = data[48:80].split(b"\0", 1)[0].decode("ascii")
        project = data[80:112].split(b"\0", 1)[0].decode("ascii")
        if project != "epapers_gateway":
            raise ValueError("固件项目不匹配")
        # Parse every segment before trusting the footer hash.
        pos, checksum = 24, 0xEF
        for _ in range(data[1]):
            if pos + 8 > len(data):
                raise ValueError("固件段被截断")
            length = struct.unpack_from("<I", data, pos + 4)[0]
            pos += 8
            if pos + length > len(data):
                raise ValueError("固件段被截断")
            for value in data[pos : pos + length]:
                checksum ^= value
            pos += length
        tail = (pos // 16 + 1) * 16
        if (
            tail + 32 != len(data)
            or data[tail - 1] != checksum
            or hashlib.sha256(data[:tail]).digest() != data[tail:]
        ):
            raise ValueError("固件镜像校验失败")
        identity = data[176:208].hex()
    elif kind == "tag":
        if len(data) < 72 or len(data) > 65536 or data[:2] != b"pQ" or data[32] != 0:
            raise ValueError("请选择 DA14585 的未加密 SUOTA .img 固件")
        length, crc = struct.unpack_from("<II", data, 4)
        if length + 64 != len(data) or zlib.crc32(data[64:]) != crc:
            raise ValueError("墨水屏固件长度或 CRC 校验失败")
        stack, reset = struct.unpack_from("<II", data, 64)
        if not (
            0x07FC0000 <= stack <= 0x07FE0000
            and 0x07FC0000 <= (reset & ~1) < 0x07FE0000
            and reset & 1
        ):
            raise ValueError("固件不是 DA14585 RAM 应用")
        version = data[12:28].split(b"\0", 1)[0].decode("ascii")
        identity = f"{crc:08x}"
    else:
        raise ValueError("未知固件类型")
    if not version or len(version) > 31:
        raise ValueError("固件版本无效")
    return {
        "kind": kind,
        "version": version,
        "identity": identity,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


class FirmwareManager:
    def __init__(self, runtime):
        self.runtime = runtime
        self.root = runtime.settings.data_dir / "firmware"
        self.root.mkdir(parents=True, exist_ok=True)
        self.images, self.jobs, self.tasks, self.waiters, self.locks = {}, {}, set(), {}, {}
        self.busy_gateways = set()

    async def start(self):
        self.images = await self.runtime.store.all("firmware")
        self.jobs = await self.runtime.store.all("firmware_job")
        for job in self.jobs.values():
            if job["state"] not in {"verified", "failed"}:
                job.update(
                    state="failed", error="服务重启，升级已中断，请重试", updated=time.time()
                )
                await self.save(job)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def upload(self, kind, data, name):
        meta = inspect_firmware(kind, data)
        key = meta["sha256"]
        meta.update(id=key, name=Path(name or "firmware").name[:100], created=time.time())
        (self.root / (key + ".bin")).write_bytes(data)
        self.images[key] = meta
        await self.runtime.store.put("firmware", key, meta)
        return meta

    async def save(self, job):
        job["updated"] = time.time()
        await self.runtime.store.put("firmware_job", job["id"], job)

    async def deploy(self, image_id, targets):
        meta = self.images[image_id]
        if not targets or len(targets) > 20 or len(set(targets)) != len(targets):
            raise ValueError("请选择 1 到 20 个不同设备")
        plan = []
        for target in targets:
            device = self.runtime.devices.get(target) if meta["kind"] == "tag" else None
            gateway_id = device.gateway_id if device else target
            gateway = self.runtime.gateways.get(gateway_id, {})
            if meta["kind"] == "tag" and (
                not device or not device.address or not device.capabilities.get("suota")
            ):
                raise ValueError("所选墨水屏尚未安装支持无线升级的基础固件")
            if not gateway.get("ws") or not gateway.get("ota"):
                raise ValueError("所选网关未连接或尚未支持网页升级")
            if any(
                j["target"] == target and j["state"] not in {"verified", "failed"}
                for j in self.jobs.values()
            ):
                raise ValueError("设备已有升级任务")
            plan.append((target, gateway_id, device.address if device else None))
        result = []
        for target, gateway_id, address in plan:
            job = {
                "id": uuid.uuid4().hex,
                "image_id": image_id,
                "kind": meta["kind"],
                "version": meta["version"],
                "target": target,
                "gateway_id": gateway_id,
                "address": address,
                "state": "queued",
                "progress": 0,
                "offset": 0,
                "size": meta["size"],
                "created": time.time(),
                "error": None,
            }
            self.jobs[job["id"]] = job
            await self.save(job)
            task = asyncio.create_task(self.run(job, meta))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            result.append(job)
        return result

    async def request(self, job, message, timeout=20):
        gateway = self.runtime.gateways.get(job["gateway_id"], {})
        if not gateway.get("ws"):
            raise ValueError("网关连接已断开")
        future = asyncio.get_running_loop().create_future()
        self.waiters[job["id"]] = future
        await gateway["ws"].send_json(message | {"id": job["id"]})
        try:
            reply = await asyncio.wait_for(future, timeout)
            if reply.get("state") == "failed":
                raise ValueError(reply.get("error") or "网关升级失败")
            return reply
        finally:
            if self.waiters.get(job["id"]) is future:
                self.waiters.pop(job["id"], None)

    async def run(self, job, meta):
        gateway_id = job["gateway_id"]
        async with self.locks.setdefault(gateway_id, asyncio.Lock()):
            self.busy_gateways.add(gateway_id)
            try:
                data = (self.root / (meta["id"] + ".bin")).read_bytes()
                if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                    raise ValueError("本机固件文件校验失败")
                begin = {
                    "type": "ota_begin",
                    "kind": meta["kind"],
                    "size": len(data),
                    "sha256": meta["sha256"],
                    "version": meta["version"],
                    "identity": meta["identity"],
                    "address": job["address"],
                    "device_id": job["target"],
                }
                for _ in range(20):
                    reply = await self.request(job, begin)
                    if reply.get("state") != "busy":
                        break
                    await asyncio.sleep(1)
                if reply.get("state") != "ready":
                    raise ValueError("网关忙，未开始升级")
                job["state"] = "uploading"
                await self.save(job)
                for offset in range(0, len(data), 1536):
                    chunk = data[offset : offset + 1536]
                    reply = await self.request(
                        job,
                        {
                            "type": "ota_chunk",
                            "offset": offset,
                            "data": base64.b64encode(chunk).decode(),
                        },
                    )
                    if reply.get("offset") != offset + len(chunk):
                        raise ValueError("网关未确认正确的固件偏移")
                    job.update(
                        offset=offset + len(chunk),
                        progress=round((offset + len(chunk)) / len(data) * 70, 1),
                    )
                    await self.save(job)
                job["state"] = "flashing"
                await self.save(job)
                # Gateway image ends with a reboot; tag images end with BLE flashing and readback.
                reply = await self.request(job, {"type": "ota_end"}, timeout=300)
                if reply.get("state") != "verified" or reply.get("identity") != meta["identity"]:
                    raise ValueError("重启后固件身份不匹配")
                job.update(state="verified", progress=100)
                await self.runtime.event(
                    "固件升级完成：" + meta["version"],
                    job["target"] if meta["kind"] == "tag" else None,
                )
            except asyncio.CancelledError:
                job.update(state="failed", error="升级任务已中断")
                raise
            except (OSError, TimeoutError, ValueError, RuntimeError) as err:
                job.update(
                    state="failed",
                    error=str(err)[:200] if not isinstance(err, TimeoutError) else "升级确认超时",
                )
                await self.runtime.event(
                    "固件升级失败：" + job["error"],
                    job["target"] if meta["kind"] == "tag" else None,
                    "error",
                )
            finally:
                await self.save(job)
                self.busy_gateways.discard(gateway_id)
                self.waiters.pop(job["id"], None)
                for device in self.runtime.devices.values():
                    if device.gateway_id == gateway_id:
                        self.runtime.dirty[device.id] = time.monotonic()

    async def status(self, gateway_id, message):
        job = self.jobs.get(message.get("id"))
        if not job or job["gateway_id"] != gateway_id:
            raise ValueError("升级任务不属于此网关")
        state = message.get("state")
        if state in {"flashing", "rebooting"}:
            job.update(
                state=state,
                progress=max(job["progress"], min(99, float(message.get("progress", 75)))),
            )
            await self.save(job)
            return
        future = self.waiters.get(job["id"])
        if future and not future.done():
            future.set_result(message)

    async def hello(self, gateway_id, hello):
        for job in self.jobs.values():
            if (
                job["kind"] == "gateway"
                and job["gateway_id"] == gateway_id
                and job["state"] == "rebooting"
            ):
                await self.status(
                    gateway_id,
                    {"id": job["id"], "state": "verified", "identity": hello.get("build_id")},
                )
