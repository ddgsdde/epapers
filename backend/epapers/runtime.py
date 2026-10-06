import asyncio
import base64
import hashlib
import time
import uuid
from datetime import datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from .firmware import FirmwareManager
from .ha import HomeAssistant
from .ha_config import PrivateHAConfig
from .models import PROFILES, Device, DisplayProgram, Job, JobState, Layout, template
from .render import Renderer, png
from .store import Store


class Runtime:
    def __init__(self, settings):
        self.settings = settings
        self.store = Store(settings.data_dir / "epapers.sqlite")
        self.assets = settings.data_dir / "assets"
        self.assets.mkdir(parents=True, exist_ok=True)
        self.renderer = Renderer(self.assets, settings.resource_dir / "assets/fonts")
        self.devices = {}
        self.removed_devices = {}
        self.removed_jobs = set()
        self.layouts = {}
        self.programs = {}
        self.user_templates = {}
        self.jobs = {}
        self.previews = {}
        self.blobs = {}
        self.gateways = {}
        self.gateway_options = {}
        self.listeners = set()
        self.tasks = []
        self.dirty = {}
        self.last_render = {}
        self.last_sync_attempt = {}
        self.ha = HomeAssistant(settings, self.entity_changed)
        self.ha_config = PrivateHAConfig(settings.data_dir)
        self.ha_task = None
        self.ha_config_lock = asyncio.Lock()
        self.lock = asyncio.Lock()
        self.firmware = FirmwareManager(self)

    async def start(self):
        private = self.ha_config.load()
        if private and not self.settings.supervisor:
            self.ha.url, self.ha.token = private
        await self.store.open()
        self.devices = {
            k: Device.model_validate(v) for k, v in (await self.store.all("device")).items()
        }
        self.removed_devices = await self.store.all("removed_device")
        self.devices = {k: d for k, d in self.devices.items() if k not in self.removed_devices}
        self.layouts = {
            k: Layout.model_validate(v) for k, v in (await self.store.all("layout")).items()
        }
        self.programs = {
            k: DisplayProgram.model_validate(v)
            for k, v in (await self.store.all("program")).items()
        }
        self.layouts = {k: p for k, p in self.layouts.items() if k in self.devices}
        self.programs = {k: p for k, p in self.programs.items() if k in self.devices}
        for key, layout in self.layouts.items():
            self.programs.setdefault(key, DisplayProgram(pages=[layout]))
        self.user_templates = await self.store.all("template")
        self.gateway_options = await self.store.all("gateway")
        for key, options in self.gateway_options.items():
            self.gateways[key] = {"id": key, **options, "ws": None, "last_seen": 0}
        await self.firmware.start()
        # A reconnect re-renders current desired state; stale in-flight commands are not replayed.
        if self.settings.demo:
            self.ha.states = {
                "sensor.demo_temperature": {
                    "entity_id": "sensor.demo_temperature",
                    "state": "23.6",
                    "attributes": {"friendly_name": "演示温度", "unit_of_measurement": "°C"},
                },
                "sensor.demo_humidity": {
                    "entity_id": "sensor.demo_humidity",
                    "state": "48",
                    "attributes": {"friendly_name": "演示湿度", "unit_of_measurement": "%"},
                },
            }
            for i, (name, mode) in enumerate(
                [("客厅温湿度", "climate"), ("书房时钟", "clock"), ("玄关信息", "entities")]
            ):
                key = f"demo-{i}"
                if key not in self.devices:
                    await self.add_device(Device(id=key, name=name, simulated=True))
                if key not in self.layouts:
                    await self.save_layout(key, template(mode=mode, title=name[:2]))
        self.ha_task = asyncio.create_task(self.ha.run())
        self.tasks = [self.ha_task, asyncio.create_task(self.scheduler())]

    async def configure_ha(self, config):
        if self.settings.supervisor:
            raise ValueError("Supervisor 已管理 HA 连接")
        async with self.ha_config_lock:
            token = config.api_key.get_secret_value() if config.api_key else self.ha.token
            if not token:
                raise ValueError("首次连接需要填写 HA Key")
            await asyncio.to_thread(self.ha_config.save, config.base_url, token)
            if self.ha_task:
                self.ha_task.cancel()
                await asyncio.gather(self.ha_task, return_exceptions=True)
                self.tasks.remove(self.ha_task)
            self.ha.url, self.ha.token = config.base_url, token
            self.ha.connected = False
            self.ha.error = None
            self.ha.confirmed_at = None
            for name in (
                "states",
                "histories",
                "history_details",
                "entity_registry",
                "device_registry",
                "area_registry",
            ):
                getattr(self.ha, name).clear()
            self.ha_task = asyncio.create_task(self.ha.run())
            self.tasks.append(self.ha_task)
            await self.event("Home Assistant 连接配置已保存")

    def public_ha_config(self):
        return {
            "base_url": self.ha.url,
            "key_configured": bool(self.ha.token),
            "managed": self.settings.supervisor,
        }

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.firmware.close()
        await self.store.close()

    async def event(self, message, device_id=None, level="info"):
        await self.store.event(time.time(), message, device_id, level)
        for listener in tuple(self.listeners):
            if listener.full():
                try:
                    listener.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            listener.put_nowait({"type": "changed", "device_id": device_id})

    async def add_device(self, device):
        if device.profile not in PROFILES:
            raise ValueError("不支持的型号")
        if device.id not in self.devices and len(self.devices) >= 20:
            raise ValueError("最多管理 20 块屏幕")
        self.devices[device.id] = device
        await self.store.put("device", device.id, device.model_dump())
        await self.event("添加屏幕", device.id)

    async def remove_device(self, key):
        async with self.lock:
            device = self.devices[key]
            if device.gateway_id in self.firmware.busy_gateways:
                raise ValueError("网关正在升级，请稍后删除屏幕")
            removed = {"device": device.model_dump()}
            if key in self.programs:
                removed["program"] = self.programs[key].model_dump()
            # Remember explicit removal so cached discovery/telemetry cannot recreate it.
            await self.store.put("removed_device", key, removed)
            self.removed_devices[key] = removed
            for mapping in (
                self.devices,
                self.layouts,
                self.programs,
                self.dirty,
                self.previews,
                self.blobs,
                self.last_render,
                self.last_sync_attempt,
            ):
                mapping.pop(key, None)
            self.removed_jobs.update(j.id for j in self.jobs.values() if j.device_id == key)
            self.jobs = {k: j for k, j in self.jobs.items() if j.device_id != key}
            for kind in ("device", "layout", "program"):
                await self.store.delete(kind, key)
            await self.event("屏幕已删除：" + device.name)

    async def restore_device(self, key):
        removed = self.removed_devices[key]
        device = Device.model_validate(removed["device"])
        device.last_seen = None
        device.applied_revision = None
        await self.add_device(device)
        if "program" in removed:
            await self.save_program(key, DisplayProgram.model_validate(removed["program"]))
        else:
            await self.save_layout(key, template(device.profile))
        self.removed_devices.pop(key)
        await self.store.delete("removed_device", key)
        await self.event("屏幕已恢复", key)
        return device

    async def save_layout(self, key, layout):
        if key not in self.devices:
            raise KeyError(key)
        from .templates import adapt_layout

        program = self.programs.get(key, DisplayProgram(pages=[layout])).model_copy(deep=True)
        program.pages = [adapt_layout(p, layout.profile) for p in program.pages]
        for page in program.pages:
            page.timezone = layout.timezone
        program.pages[0] = layout
        await self.save_program(key, program)
        await self.event("页面已保存", key)

    async def save_program(self, key, program):
        if key not in self.devices:
            raise KeyError(key)
        old = self.programs.get(key)
        if (
            not old
            or old.enabled != program.enabled
            or old.interval_minutes != program.interval_minutes
        ):
            program.started_at = int(time.time())
        elif old:
            program.started_at = old.started_at
        await self.compile_program(key, program)
        self.programs[key] = program
        self.layouts[key] = program.pages[0]
        self.devices[key].profile = program.pages[0].profile
        await self.store.put("program", key, program.model_dump())
        await self.store.put("layout", key, program.pages[0].model_dump())
        await self.store.put("device", key, self.devices[key].model_dump())
        self.dirty[key] = time.monotonic()
        await self.event("轮播画布已保存", key)

    async def compile_program(self, key, program, fresh_history=False):
        blobs = []
        first_preview = None
        for layout in program.pages if program.enabled else program.pages[:1]:
            await self.load_histories(layout, force=fresh_history)
            static, preview = await asyncio.to_thread(
                self.renderer.render,
                layout,
                self.ha.states,
                self.ha.histories,
                telemetry=self.devices[key].model_dump(),
            )
            if first_preview is None:
                first_preview = preview
            blobs.append(
                await asyncio.to_thread(self.renderer.blob, layout, static, self.ha.states)
            )
        return self.renderer.program_blob(program, blobs), first_preview

    async def entity_changed(self, entity_id):
        for key, program in self.programs.items():
            if entity_id is None or any(
                w.entity_id == entity_id
                or any(e.entity_id == entity_id for e in w.carousel_entries)
                for p in program.pages
                for w in p.widgets
            ):
                self.dirty.setdefault(key, time.monotonic())

    async def refresh_due(self, now=None):
        now = time.monotonic() if now is None else now
        # A history window moves even if its entity value stays constant. Do not depend
        # solely on state_changed, and do not use the editor's cached history for delivery.
        for key, program in list(self.programs.items()):
            device = self.devices.get(key)
            if not device:
                continue
            pages = program.pages if program.enabled else program.pages[:1]
            if (
                any(w.kind == "chart" and w.entity_id for p in pages for w in p.widgets)
                and (device.simulated or self.gateways.get(device.gateway_id, {}).get("ws"))
                and now - self.last_render.get(key, 0) >= device.refresh_interval
            ):
                self.dirty.setdefault(key, now - 2)
        for key, changed in list(self.dirty.items()):
            device = self.devices.get(key)
            if not device:
                self.dirty.pop(key, None)
                continue
            if (
                device.gateway_id not in self.firmware.busy_gateways
                and now - changed >= 2
                and now - self.last_render.get(key, 0) >= device.refresh_interval
            ):
                self.dirty.pop(key, None)
                try:
                    await self.render_job(key)
                except Exception as err:  # noqa: BLE001 - retry each screen independently
                    if key in self.devices:
                        self.last_render[key] = now
                        self.dirty.setdefault(key, now)
                        await self.event(
                            f"内容更新失败，将自动重试：{type(err).__name__}", key, "error"
                        )

    async def scheduler(self):
        while True:
            try:
                await self.refresh_due()
                for gateway in list(self.gateways.values()):
                    if gateway.get("ws") and time.time() - gateway.get("last_time", 0) > 3600:
                        await gateway["ws"].send_json(self.time_message())
                        gateway["last_time"] = time.time()
                for job in list(self.jobs.values()):
                    if (
                        job.state in {JobState.TRANSFERRING, JobState.RECEIVED}
                        and time.time() - job.updated > 180
                    ):
                        await self.ack(
                            self.devices[job.device_id].gateway_id,
                            {
                                "job_id": job.id,
                                "state": "failed",
                                "error": "网关操作超时，请检查设备后重试",
                            },
                        )
                for device in list(self.devices.values()):
                    gateway = self.gateways.get(device.gateway_id, {})
                    if (
                        device.gateway_id not in self.firmware.busy_gateways
                        and not device.simulated
                        and device.address
                        and gateway.get("ws")
                        and device.last_seen
                        and time.time() - device.last_seen < 90
                        and time.time() - (device.last_sync or 0) >= 86400
                        and time.time() - self.last_sync_attempt.get(device.id, 0) > 600
                    ):
                        self.last_sync_attempt[device.id] = time.time()
                        await self.command(device.id, "sync_time")
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - keep scheduler alive; record every failure without secrets
                await self.event(f"调度失败：{type(err).__name__}", level="error")
            await asyncio.sleep(1)

    async def load_histories(self, layout, force=False):
        charts = {
            (w.entity_id, w.history_duration, w.attribute)
            for w in layout.widgets
            if w.kind == "chart" and w.entity_id
        }
        await asyncio.gather(
            *(
                self.ha.history(key, hours, attribute, force=force)
                for key, hours, attribute in charts
            )
        )
        return [
            self.ha.history_details[self.ha.history_key(key, hours, attribute)]["error"]
            for key, hours, attribute in charts
            if self.ha.history_details[self.ha.history_key(key, hours, attribute)]["error"]
        ]

    def time_message(self, timezone=None):
        now = datetime.now(ZoneInfo(timezone or self.settings.timezone))
        return {
            "type": "time",
            "epoch": int(now.timestamp()),
            "offset": int(now.utcoffset().total_seconds()),
            "valid": True,
        }

    async def render_job(self, key, force=False):
        async with self.lock:
            if key not in self.devices:
                raise KeyError(key)
            if self.devices[key].gateway_id in self.firmware.busy_gateways:
                raise ValueError("网关正在升级，请稍后更新页面")
            layout = self.layouts.get(key)
            if not layout:
                raise ValueError("请先保存页面")
            program = self.programs.get(key, DisplayProgram(pages=[layout]))
            if not self.devices[key].simulated and any(
                w.kind == "entity_carousel"
                for p in (program.pages if program.enabled else program.pages[:1])
                for w in p.widgets
            ):
                version = (self.devices[key].firmware or "").split("-", 1)[0].split(".")
                if (
                    len(version) != 3
                    or not all(v.isdigit() for v in version)
                    or tuple(map(int, version)) < (0, 3, 0)
                ):
                    raise ValueError("组件轮播需要墨水屏固件 0.3.0 或更新版本，请先升级固件")
                gateway_version = (
                    self.gateways.get(self.devices[key].gateway_id, {})
                    .get("firmware", "")
                    .split("-", 1)[0]
                    .split(".")
                )
                if (
                    len(gateway_version) == 3
                    and all(v.isdigit() for v in gateway_version)
                    and tuple(map(int, gateway_version)) < (0, 3, 0)
                ):
                    raise ValueError("组件轮播需要网关固件 0.3.0 或更新版本，请先升级网关")
            blob, preview = await self.compile_program(key, program, fresh_history=True)
            revision = hashlib.sha256(blob).hexdigest()[:16]
            self.previews[key] = png(preview)
            self.last_render[key] = time.monotonic()
            pending = any(
                j.device_id == key
                and j.revision == revision
                and j.state in {JobState.QUEUED, JobState.TRANSFERRING, JobState.RECEIVED}
                for j in self.jobs.values()
            )
            if (
                not force
                and self.blobs.get(key, {}).get("revision") == revision
                and (pending or self.devices[key].applied_revision == revision)
            ):
                return None
            self.blobs[key] = {"revision": revision, "blob": blob}
            for old in self.jobs.values():
                if old.device_id == key and old.kind == "display" and old.state == JobState.QUEUED:
                    old.state = JobState.SUPERSEDED
                    old.updated = time.time()
            now = time.time()
            job = Job(
                id=uuid.uuid4().hex,
                device_id=key,
                revision=revision,
                created=now,
                updated=now,
                payload={"blob": base64.b64encode(blob).decode(), "length": len(blob)},
            )
            self.jobs[job.id] = job
            self.jobs = {k: v for k, v in list(self.jobs.items())[-200:]}
            await self.event("渲染完成，等待网关", key)
            gateway = self.gateways.get(self.devices[key].gateway_id)
            if gateway and gateway.get("ws") and not self.devices[key].simulated:
                await gateway["ws"].send_json(self.job_message(job))
            return job

    def job_message(self, job):
        device = self.devices[job.device_id]
        return {
            "type": "command",
            "job_id": job.id,
            "device_id": job.device_id,
            "address": device.address,
            "profile": device.profile,
            "power": {"refresh_interval": device.refresh_interval},
            "time": self.time_message(self.layouts[job.device_id].timezone),
            "kind": job.kind,
            "revision": job.revision,
            "payload": job.payload,
        }

    async def command(self, key, kind, payload=None):
        if key in self.devices and self.devices[key].gateway_id in self.firmware.busy_gateways:
            raise ValueError("网关正在升级，请稍后操作")
        if key not in self.devices:
            raise KeyError(key)
        if kind in {"refresh", "render"}:
            return await self.render_job(key, force=True)
        if kind == "sync_time":
            payload = self.time_message(self.layouts[key].timezone)
        now = time.time()
        job = Job(
            id=uuid.uuid4().hex,
            device_id=key,
            kind=kind,
            created=now,
            updated=now,
            payload=payload or {},
        )
        self.jobs[job.id] = job
        self.jobs = {k: v for k, v in list(self.jobs.items())[-200:]}
        gateway = self.gateways.get(self.devices[key].gateway_id)
        if gateway and gateway.get("ws"):
            await gateway["ws"].send_json(self.job_message(job))
        await self.event("操作已排队：" + kind, key)
        return job

    async def telemetry(self, gateway_id, data):
        key = str(data["device_id"])
        address = data.get("address")
        if key in self.removed_devices or any(
            address
            and r["device"].get("address")
            and address.upper() == r["device"]["address"].upper()
            for r in self.removed_devices.values()
        ):
            return
        match = next(
            (
                d
                for d in self.devices.values()
                if address and d.address and d.address.upper() == address.upper()
            ),
            None,
        )
        if match:
            key = match.id
        if key not in self.devices:
            await self.add_device(
                Device(
                    id=key,
                    name=data.get("name", key),
                    profile=data.get("profile", "HINK-E0213A41"),
                    gateway_id=gateway_id,
                    address=data.get("address"),
                )
            )
        if key not in self.layouts:
            await self.save_layout(
                key,
                template(
                    self.devices[key].profile,
                    title=self.devices[key].name,
                    temperature="sensor.temperature_keting",
                    humidity="sensor.humidity_keting",
                ),
            )
        d = self.devices[key]
        if d.simulated:
            raise ValueError("真实网关不能更新模拟设备")
        d.gateway_id = gateway_id
        d.last_seen = time.time()
        for field in [
            "battery_mv",
            "supply_mv",
            "battery_percent",
            "rssi",
            "firmware",
            "last_sync",
            "drift_ppm",
            "error",
            "address",
            "capabilities",
            "temperature_centi",
            "temperature_sampled_at",
            "battery_sampled_at",
        ]:
            if field in data:
                setattr(d, field, data[field])
        self.update_battery(d)
        await self.store.put("device", key, d.model_dump())
        await self.event("设备状态更新", key)

    @staticmethod
    def update_battery(device):
        # VBAT is an MCU supply measurement unless the user confirms direct battery wiring.
        if device.battery_source != "direct":
            device.battery_mv = device.battery_percent = None
            return
        device.battery_mv = device.supply_mv
        if device.supply_mv is None:
            device.battery_percent = None
            return
        curve = [
            (3000, 0),
            (3300, 5),
            (3500, 10),
            (3600, 20),
            (3700, 35),
            (3800, 55),
            (3900, 70),
            (4000, 85),
            (4100, 95),
            (4200, 100),
        ]
        mv = device.supply_mv
        device.battery_percent = 0 if mv <= curve[0][0] else 100
        for (lo, p0), (hi, p1) in pairwise(curve):
            if lo <= mv <= hi:
                device.battery_percent = round(p0 + (mv - lo) * (p1 - p0) / (hi - lo))
                break

    async def ack(self, gateway_id, message):
        if message.get("job_id") in self.removed_jobs:
            return
        job = self.jobs.get(message.get("job_id"))
        if not job or self.devices[job.device_id].gateway_id != gateway_id:
            raise ValueError("未知任务或网关不匹配")
        next_state = JobState(message["state"])
        valid = {
            JobState.QUEUED: {JobState.TRANSFERRING, JobState.FAILED},
            JobState.TRANSFERRING: {JobState.RECEIVED, JobState.FAILED},
            JobState.RECEIVED: {JobState.APPLIED, JobState.FAILED},
        }
        if next_state == job.state:
            return
        if next_state not in valid.get(job.state, set()):
            raise ValueError("非法任务状态转换")
        job.state = next_state
        job.updated = time.time()
        job.error = message.get("error")
        if next_state == JobState.FAILED and job.kind == "display":
            self.dirty.setdefault(job.device_id, time.monotonic())
        if next_state == JobState.APPLIED:
            device = self.devices[job.device_id]
            if job.kind == "display":
                device.applied_revision = job.revision
                device.last_refresh = time.time()
            if job.kind == "sync_time":
                device.last_sync = time.time()
            await self.store.put("device", device.id, device.model_dump())
        await self.event(
            "任务状态：" + next_state.value + (" · " + job.error if job.error else ""),
            job.device_id,
            "error" if next_state == JobState.FAILED else "info",
        )
