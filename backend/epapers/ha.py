import asyncio
import json
import logging
import math
import os
import time
from datetime import UTC, datetime

import httpx
import websockets

LOGGER = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self, settings, on_change):
        self.settings = settings
        self.on_change = on_change
        self.states = {}
        self.histories = {}
        self.history_details = {}
        self.history_locks = {}
        self.entity_registry = {}
        self.device_registry = {}
        self.area_registry = {}
        self.connected = False
        self.error = None
        self.confirmed_at = None
        self.url = "http://supervisor/core" if settings.supervisor else settings.ha_url.rstrip("/")
        self.token = os.getenv("SUPERVISOR_TOKEN", settings.ha_token)

    async def run(self):
        if not self.url or not self.token:
            self.error = "尚未配置 Home Assistant"
            return
        url = self.url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
        if self.settings.supervisor:
            url = "ws://supervisor/core/websocket"
        while True:
            try:
                async with websockets.connect(
                    url, ping_interval=20, max_size=8 * 1024 * 1024
                ) as ws:
                    hello = json.loads(await ws.recv())
                    if hello.get("type") != "auth_required":
                        raise RuntimeError("unexpected HA handshake")
                    await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                    if json.loads(await ws.recv()).get("type") != "auth_ok":
                        raise RuntimeError("HA authentication failed")
                    await ws.send(json.dumps({"id": 1, "type": "get_states"}))
                    result = json.loads(await ws.recv())
                    if not result.get("success"):
                        raise RuntimeError("get_states failed")
                    self.states = {i["entity_id"]: i for i in result["result"]}
                    # Registry metadata powers area/device filters without loading HA's UI.
                    for request_id, kind, key, attr in [
                        (3, "config/entity_registry/list", "entity_id", "entity_registry"),
                        (4, "config/device_registry/list", "id", "device_registry"),
                        (5, "config/area_registry/list", "area_id", "area_registry"),
                    ]:
                        await ws.send(json.dumps({"id": request_id, "type": kind}))
                        reply = json.loads(await ws.recv())
                        if reply.get("success"):
                            setattr(self, attr, {i[key]: i for i in reply["result"]})
                    self.history_details.clear()
                    self.connected = True
                    self.error = None
                    self.confirmed_at = datetime.now(UTC).timestamp()
                    await self.on_change(None)
                    await ws.send(
                        json.dumps(
                            {"id": 6, "type": "subscribe_events", "event_type": "state_changed"}
                        )
                    )
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get("type") == "event":
                            data = event["event"]["data"]
                            if data.get("new_state"):
                                self.states[data["entity_id"]] = data["new_state"]
                            else:
                                self.states.pop(data["entity_id"], None)
                            self.confirmed_at = datetime.now(UTC).timestamp()
                            await self.on_change(data["entity_id"])
            except asyncio.CancelledError:
                raise
            except (
                websockets.WebSocketException,
                OSError,
                ValueError,
                KeyError,
                RuntimeError,
            ) as err:
                self.connected = False
                self.error = type(err).__name__
                LOGGER.warning("HA connection interrupted: %s", type(err).__name__)
                await asyncio.sleep(5)

    def entities(self):
        for key, state in self.states.items():
            registry = self.entity_registry.get(key, {})
            device = self.device_registry.get(registry.get("device_id"), {})
            area = self.area_registry.get(registry.get("area_id") or device.get("area_id"), {})
            yield {
                "entity_id": key,
                "state": state.get("state"),
                "attributes": state.get("attributes", {}),
                "domain": key.split(".")[0],
                "area": area.get("name"),
                "device": device.get("name_by_user") or device.get("name"),
                "category": registry.get("entity_category"),
            }

    @staticmethod
    def history_key(entity_id, hours=24, attribute=None):
        return f"{entity_id}|{float(hours):g}|{attribute or ''}"

    @staticmethod
    def numeric(value):
        if value in {"on", "open", "home", "locked", "playing"}:
            return 1.0
        if value in {"off", "closed", "not_home", "unlocked", "idle"}:
            return 0.0
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("not finite")
        return number

    async def history(self, entity_id, hours=24, attribute=None, force=False):
        key = self.history_key(entity_id, hours, attribute)
        async with self.history_locks.setdefault(key, asyncio.Lock()):
            cached = self.history_details.get(key)
            if cached and not force and time.time() - cached["fetched"] < 60:
                return cached["points"]
            end = time.time()
            start = end - hours * 3600
            detail = {"points": [], "fetched": end, "start": start, "end": end, "error": None}
            if not self.connected:
                detail["error"] = "Home Assistant 未连接"
            else:
                try:
                    params = {
                        "filter_entity_id": entity_id,
                        "end_time": datetime.fromtimestamp(end, UTC).isoformat(),
                    }
                    if not attribute:
                        params.update({"minimal_response": "true", "no_attributes": "true"})
                    async with httpx.AsyncClient(timeout=15) as client:
                        r = await client.get(
                            f"{self.url}/api/history/period/{datetime.fromtimestamp(start, UTC).isoformat()}",
                            params=params,
                            headers={"Authorization": f"Bearer {self.token}"},
                        )
                        r.raise_for_status()
                        rows = r.json()
                    points = []
                    for row in rows[0] if rows else []:
                        try:
                            value = (
                                row.get("attributes", {}).get(attribute)
                                if attribute
                                else row["state"]
                            )
                            ts = datetime.fromisoformat(
                                row.get("last_updated") or row["last_changed"]
                            ).timestamp()
                            points.append([max(start, ts), self.numeric(value)])
                        except (ValueError, KeyError, TypeError):
                            # Preserve an unavailable interval rather than drawing through it.
                            try:
                                points.append(
                                    [
                                        datetime.fromisoformat(
                                            row.get("last_updated") or row["last_changed"]
                                        ).timestamp(),
                                        None,
                                    ]
                                )
                            except (ValueError, KeyError, TypeError):
                                continue
                    points.sort(key=lambda p: p[0])
                    if points and points[-1][1] is not None:
                        points.append([end, points[-1][1]])
                    detail["points"] = points
                    if not any(p[1] is not None for p in points):
                        detail["error"] = (
                            "HA 未提供数值历史，请检查 Recorder 是否记录此实体"
                            if not attribute
                            else f"HA 历史中没有数值属性 {attribute}"
                        )
                except (httpx.HTTPError, ValueError, TypeError) as err:
                    detail["error"] = "HA 历史读取失败：" + type(err).__name__
            self.history_details[key] = detail
            self.histories[key] = detail["points"]
            # Compatibility for original 24h state charts.
            if hours == 24 and not attribute:
                self.histories[entity_id] = detail["points"]
            return detail["points"]
