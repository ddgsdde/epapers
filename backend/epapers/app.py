import asyncio
import base64
import io
import os
import secrets
import time
import uuid
import zipfile
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect

from .config import Settings
from .fonts import FONT_CATALOG
from .ha_config import HAConnection
from .models import PROFILES, Device, DisplayProgram, Layout, template
from .render import png
from .runtime import Runtime
from .templates import TEMPLATES, build_template


class DeployFirmware(BaseModel):
    image_id: str
    targets: list[str] = Field(min_length=1, max_length=20)


class NewDevice(BaseModel):
    name: str = Field(default="新屏幕", min_length=1, max_length=100)
    profile: str = "OPM021B1"
    address: str | None = Field(default=None, pattern=r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
    gateway_id: str | None = None


class SaveTemplate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    layout: Layout


class DeviceOptions(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    ha_name: str = Field(default="", max_length=100)
    ha_entity_prefix: str = Field(default="", pattern=r"^(?:[a-z][a-z0-9_]{1,59})?$")
    area_name: str = Field(default="", max_length=100)
    battery_source: str = "unknown"
    refresh_interval: int = Field(default=300, ge=30, le=86400)


class GatewayOptions(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    area_name: str = Field(default="", max_length=100)


def create_app(settings=None):
    settings = settings or Settings()
    runtime = Runtime(settings)

    @asynccontextmanager
    async def lifespan(app):
        await runtime.start()
        yield
        await runtime.close()

    app = FastAPI(title="epapers", lifespan=lifespan)
    app.state.runtime = runtime

    def trusted(client):
        if not client:
            return False
        if settings.supervisor:
            return client.host == os.getenv("EPAPERS_INGRESS_PEER", "172.30.32.2")
        return client.host in {"127.0.0.1", "::1", "testclient"}

    def token_ok(value, expected):
        return bool(expected and value and secrets.compare_digest(value, expected))

    async def auth(request: Request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not trusted(request.client) and not token_ok(token, settings.api_token):
            raise HTTPException(401, "需要 API 密钥")
        # Browser mutations use JSON and same-origin checks; bearer clients need no Origin.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD"} and origin and not settings.supervisor:
            from urllib.parse import urlsplit

            if urlsplit(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "来源不匹配")

    @app.exception_handler(ValueError)
    async def value_error(request, err):
        return Response(
            content=__import__("json").dumps({"detail": str(err)}, ensure_ascii=False),
            status_code=422,
            media_type="application/json",
        )

    @app.exception_handler(KeyError)
    async def key_error(request, err):
        return Response(
            content='{"detail":"找不到屏幕或页面"}', status_code=404, media_type="application/json"
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, err):
        # FastAPI normally returns invalid input, which would expose a submitted key.
        if request.url.path == "/api/ha/config":
            return Response(
                '{"detail":"HA 地址或 Key 格式无效"}',
                status_code=422,
                media_type="application/json",
            )
        from fastapi.exception_handlers import request_validation_exception_handler

        return await request_validation_exception_handler(request, err)

    @app.get("/api/ha/config", dependencies=[Depends(auth)])
    async def ha_config(response: Response):
        response.headers["Cache-Control"] = "no-store"
        return runtime.public_ha_config()

    @app.put("/api/ha/config", dependencies=[Depends(auth)])
    async def save_ha_config(data: HAConnection, response: Response):
        if settings.supervisor:
            raise HTTPException(403, "Supervisor 已管理 HA 连接")
        await runtime.configure_ha(data)
        response.headers["Cache-Control"] = "no-store"
        return runtime.public_ha_config()

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/ha/integration.zip", dependencies=[Depends(auth)])
    async def ha_integration():
        root = settings.resource_dir / "custom_components" / "epapers"
        if not (root / "manifest.json").is_file():
            raise HTTPException(404, "HA 集成文件不可用")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                    archive.write(path, "epapers/" + path.relative_to(root).as_posix())
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="epapers-ha-0.2.0.zip"'},
        )

    @app.get("/api/state", dependencies=[Depends(auth)])
    async def state():
        now = time.time()
        return {
            "devices": [
                d.model_dump()
                | {
                    "online": bool(d.last_seen and now - d.last_seen < 90),
                    "last_content_check": now - time.monotonic() + runtime.last_render[d.id]
                    if d.id in runtime.last_render
                    else None,
                }
                for d in runtime.devices.values()
            ],
            "removed_devices": [
                {k: r["device"].get(k) for k in ("id", "name", "address", "profile")}
                for r in runtime.removed_devices.values()
            ],
            "gateways": [
                {k: v for k, v in g.items() if k != "ws"} | {"online": bool(g.get("ws"))}
                for g in runtime.gateways.values()
            ],
            "jobs": [j.model_dump(exclude={"payload"}) for j in runtime.jobs.values()],
            "ha": {
                "connected": runtime.ha.connected,
                "error": runtime.ha.error,
                "confirmed_at": runtime.ha.confirmed_at,
                "entities": len(runtime.ha.states),
            },
            "profiles": PROFILES,
            "demo": settings.demo,
            "timezone": settings.timezone,
            "ha_url": runtime.ha.url,
            "events": await runtime.store.events(),
        }

    @app.put("/api/gateways/{key}/options", dependencies=[Depends(auth)])
    async def gateway_options(key: str, data: GatewayOptions):
        if key not in runtime.gateways:
            raise KeyError(key)
        value = data.model_dump()
        runtime.gateway_options[key] = value
        runtime.gateways[key].update(value)
        await runtime.store.put("gateway", key, value)
        await runtime.event("网关名称和房间已保存")
        return {"ok": True}

    @app.get("/api/entities", dependencies=[Depends(auth)])
    async def entities(q: str = ""):
        query = q.casefold()
        return [
            v
            for v in runtime.ha.entities()
            if query in v["entity_id"].casefold()
            or query in str(v.get("attributes", {}).get("friendly_name", "")).casefold()
        ]

    @app.get("/api/fonts", dependencies=[Depends(auth)])
    async def fonts():
        from .models import Widget

        result = []
        for font in FONT_CATALOG:
            item = Widget(
                id="font",
                kind="text",
                x=0,
                y=0,
                width=200,
                height=30,
                text=font["sample"],
                font=font["id"],
                font_size=22,
                font_weight=400,
            )
            tile = await asyncio.to_thread(runtime.renderer.text_layer, item, item.text)
            result.append(
                {k: v for k, v in font.items() if k != "file"}
                | {"preview": "data:image/png;base64," + base64.b64encode(png(tile)).decode()}
            )
        return result

    @app.get("/api/templates", dependencies=[Depends(auth)])
    async def template_list():
        return [
            {"id": i, "name": n, "description": d, "custom": False} for i, n, d in TEMPLATES
        ] + [
            {"id": key, "name": value["name"], "description": "我的模板", "custom": True}
            for key, value in runtime.user_templates.items()
        ]

    @app.post("/api/templates", dependencies=[Depends(auth)])
    async def save_template(data: SaveTemplate):
        static, _ = await asyncio.to_thread(runtime.renderer.render, data.layout, runtime.ha.states)
        runtime.renderer.blob(data.layout, static, runtime.ha.states)
        key = "user-" + uuid.uuid4().hex[:12]
        value = {"name": data.name, "layout": data.layout.model_dump()}
        await runtime.store.put("template", key, value)
        runtime.user_templates[key] = value
        return {"id": key}

    @app.post("/api/discovery", dependencies=[Depends(auth)])
    async def discover():
        count = 0
        for gateway in runtime.gateways.values():
            if gateway.get("ws"):
                await gateway["ws"].send_json({"type": "discover"})
                count += 1
        return {"gateways": count}

    @app.get("/api/templates/{profile}/{mode}", dependencies=[Depends(auth)])
    async def templates(profile: str, mode: str):
        if profile not in PROFILES:
            raise HTTPException(404, "模板不存在")
        if mode in runtime.user_templates:
            from .templates import adapt_layout

            return adapt_layout(
                Layout.model_validate(runtime.user_templates[mode]["layout"]), profile
            )
        if mode not in {t[0] for t in TEMPLATES}:
            raise HTTPException(404, "模板不存在")
        if mode == "blank":
            return Layout(profile=profile, timezone=settings.timezone)
        return build_template(
            profile,
            mode,
            title="客厅",
            temperature="sensor.temperature_keting",
            humidity="sensor.humidity_keting",
            entities=runtime.ha.states,
        )

    @app.post("/api/devices", dependencies=[Depends(auth)])
    async def add_device(data: NewDevice):
        if data.address and any(d.address == data.address for d in runtime.devices.values()):
            raise ValueError("该蓝牙地址已经添加")
        device = Device(id=uuid.uuid4().hex[:12], **data.model_dump())
        await runtime.add_device(device)
        await runtime.save_layout(
            device.id,
            template(
                device.profile,
                title=device.name,
                temperature="sensor.temperature_keting",
                humidity="sensor.humidity_keting",
            ),
        )
        return device

    @app.delete("/api/devices/{key}", dependencies=[Depends(auth)])
    async def delete_device(key: str):
        await runtime.remove_device(key)
        return {"ok": True}

    @app.post("/api/devices/{key}/restore", dependencies=[Depends(auth)])
    async def restore_device(key: str):
        return await runtime.restore_device(key)

    @app.patch("/api/devices/{key}", dependencies=[Depends(auth)])
    async def configure_device(key: str, data: NewDevice):
        device = runtime.devices[key]
        if data.address and any(
            d.id != key and d.address and d.address.upper() == data.address.upper()
            for d in runtime.devices.values()
        ):
            raise ValueError("该蓝牙地址已经添加")
        # Profile and name are saved through the page editor; this endpoint binds hardware.
        device.address = data.address.upper() if data.address else None
        device.gateway_id = data.gateway_id
        await runtime.store.put("device", key, device.model_dump())
        await runtime.event("网关和蓝牙地址已保存", key)
        return device

    @app.get("/api/devices/{key}/layout", dependencies=[Depends(auth)])
    async def get_layout(key: str):
        return runtime.layouts[key]

    @app.get("/api/devices/{key}/pages", dependencies=[Depends(auth)])
    async def get_pages(key: str):
        return runtime.programs.get(key) or DisplayProgram(pages=[runtime.layouts[key]])

    @app.put("/api/devices/{key}/pages", dependencies=[Depends(auth)])
    async def save_pages(key: str, program: DisplayProgram):
        await runtime.save_program(key, program)
        return runtime.programs[key]

    @app.get("/api/ha/areas", dependencies=[Depends(auth)])
    async def areas():
        return [{"id": a["area_id"], "name": a["name"]} for a in runtime.ha.area_registry.values()]

    @app.put("/api/devices/{key}/options", dependencies=[Depends(auth)])
    async def device_options(key: str, data: DeviceOptions):
        values = runtime.devices[key].model_dump() | data.model_dump()
        device = Device.model_validate(values)
        identity_changed = any(
            getattr(device, k) != getattr(runtime.devices[key], k)
            for k in ["name", "ha_name", "ha_entity_prefix", "area_name"]
        )
        if identity_changed:
            device.ha_sync_status = "pending"
        runtime.update_battery(device)
        runtime.devices[key] = device
        await runtime.store.put("device", key, device.model_dump())
        await runtime.event("屏幕设置已保存", key)
        return device

    @app.post("/api/devices/{key}/ha-sync", dependencies=[Depends(auth)])
    async def ha_sync_status(key: str, data: dict):
        status = data.get("status")
        if status not in {"synced", "pending", "error"}:
            raise ValueError("无效同步状态")
        runtime.devices[key].ha_sync_status = status
        await runtime.store.put("device", key, runtime.devices[key].model_dump())
        return {"ok": True}

    @app.put("/api/devices/{key}/layout", dependencies=[Depends(auth)])
    async def save_layout(key: str, layout: Layout):
        await runtime.save_layout(key, layout)
        return {"ok": True}

    @app.post("/api/preview", dependencies=[Depends(auth)])
    async def preview(
        layout: Layout,
        device_id: str | None = None,
        carousel_widget: str | None = None,
        carousel_index: int = 0,
    ):
        warnings = await runtime.load_histories(layout)
        static, image = await asyncio.to_thread(
            runtime.renderer.render,
            layout,
            runtime.ha.states,
            runtime.ha.histories,
            telemetry=runtime.devices[device_id].model_dump()
            if device_id in runtime.devices
            else None,
            carousel_preview={carousel_widget: carousel_index} if carousel_widget else None,
        )
        blob = await asyncio.to_thread(runtime.renderer.blob, layout, static, runtime.ha.states)
        for widget in layout.widgets:
            if widget.kind in {"text", "entity", "clock", "date", "countdown", "tag_temperature"}:
                text = (
                    widget.text
                    if widget.kind == "text"
                    else runtime.renderer.entity_text(widget, runtime.ha.states)
                    if widget.kind == "entity"
                    else "88月88日"
                    if widget.kind == "date"
                    else "-25.0°C"
                    if widget.kind == "tag_temperature"
                    else "88:88"
                )
                font = runtime.renderer.font(widget.font, widget.font_size, widget.font_weight)
                if font.getlength(text) > widget.width or widget.font_size > widget.height:
                    warnings.append(f"{widget.id} 的内容可能被裁切，请调整字号或区域")
        return {
            "image": "data:image/png;base64," + base64.b64encode(png(image)).decode(),
            "bytes": len(blob),
            "warnings": warnings,
            "size": layout.size,
        }

    @app.post("/api/devices/{key}/commands/{kind}", dependencies=[Depends(auth)])
    async def command(key: str, kind: str):
        if kind not in {"refresh", "sync_time", "status", "full_refresh"}:
            raise HTTPException(404, "不支持的操作")
        job = await runtime.command(key, kind)
        return job.model_dump(exclude={"payload"}) if job else {"state": "unchanged"}

    @app.get("/api/firmware", dependencies=[Depends(auth)])
    async def firmware_library():
        return {
            "images": list(runtime.firmware.images.values()),
            "jobs": list(runtime.firmware.jobs.values())[-200:],
        }

    @app.post("/api/firmware/{kind}", dependencies=[Depends(auth)])
    async def firmware_upload(kind: str, file: UploadFile):
        data = await file.read(4 * 1024 * 1024 + 1)
        return await runtime.firmware.upload(kind, data, file.filename)

    @app.post("/api/firmware/deploy/start", dependencies=[Depends(auth)])
    async def firmware_deploy(data: DeployFirmware):
        return await runtime.firmware.deploy(data.image_id, data.targets)

    @app.post("/api/assets", dependencies=[Depends(auth)])
    async def upload_asset(file: UploadFile):
        data = await file.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("图片最大 2MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 4_000_000:
                    raise ValueError("图片最大 400 万像素")
                image.load()
                image.thumbnail((512, 512))
                key = uuid.uuid4().hex + ".png"
                image.convert("RGB").save(runtime.assets / key)
        except (OSError, Image.DecompressionBombError) as err:
            raise ValueError("图片格式无效") from err
        return {"asset": key}

    @app.websocket("/ws/events")
    async def events(ws: WebSocket):
        if not trusted(ws.client) and not token_ok(
            ws.query_params.get("token"), settings.api_token
        ):
            await ws.close(code=1008)
            return
        await ws.accept()
        queue = asyncio.Queue(maxsize=10)
        runtime.listeners.add(queue)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), 25)
                except TimeoutError:
                    item = {"type": "ping"}
                await ws.send_json(item)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return
        finally:
            runtime.listeners.discard(queue)

    @app.websocket("/ws/gateway")
    async def gateway(ws: WebSocket):
        token = ws.headers.get("authorization", "").removeprefix("Bearer ")
        if not token_ok(token, settings.gateway_token):
            await ws.close(code=1008)
            return
        await ws.accept()
        key = None
        try:
            hello = await asyncio.wait_for(ws.receive_json(), 10)
            key = str(hello.get("gateway_id", ""))
            if hello.get("type") != "hello" or not key or len(key) > 80:
                raise ValueError("无效网关握手")
            previous = runtime.gateways.get(key, {}).get("ws")
            if previous:
                await previous.close(code=1012)
            gateway = {
                "id": key,
                "firmware": str(hello.get("firmware", "unknown")),
                "last_seen": time.time(),
                "ws": ws,
                "last_time": time.time(),
                "ota": bool(hello.get("ota")),
                "build_id": hello.get("build_id"),
                **runtime.gateway_options.get(key, {}),
            }
            runtime.gateways[key] = gateway
            await ws.send_json(runtime.time_message())
            await runtime.firmware.hello(key, hello)
            await runtime.event("网关已连接：" + key)
            for device in runtime.devices.values():
                if (
                    device.gateway_id == key
                    and device.id in runtime.layouts
                    and key not in runtime.firmware.busy_gateways
                ):
                    await runtime.render_job(device.id, force=True)
            while True:
                message = await asyncio.wait_for(ws.receive_json(), 70)
                gateway["last_seen"] = time.time()
                if message.get("type") == "telemetry":
                    await runtime.telemetry(key, message)
                elif message.get("type") == "ota_status":
                    await runtime.firmware.status(key, message)
                elif message.get("type") == "ack":
                    await runtime.ack(key, message)
                elif message.get("type") == "heartbeat":
                    for field in ["free_heap", "queue_depth", "uptime", "wifi_rssi", "error"]:
                        if field in message:
                            gateway[field] = message[field]
                elif message.get("type") == "time_request":
                    await ws.send_json(runtime.time_message())
                else:
                    raise ValueError("未知消息")
        except (WebSocketDisconnect, RuntimeError, TimeoutError, ValueError, OSError) as err:
            if key:
                await runtime.event("网关连接结束：" + type(err).__name__, level="warning")
        finally:
            if key and runtime.gateways.get(key, {}).get("ws") is ws:
                runtime.gateways[key]["ws"] = None
            try:
                await ws.close()
            except (WebSocketDisconnect, RuntimeError, OSError):
                return

    web = settings.resource_dir / "frontend/dist"
    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        if (web / "index.html").is_file():
            return FileResponse(web / "index.html")
        return Response("请先构建 frontend", status_code=503)

    return app
