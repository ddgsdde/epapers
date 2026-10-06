import asyncio
from functools import partial

import pytest
from epapers.app import create_app
from epapers.config import Settings
from epapers.models import Device, DisplayProgram, JobState, Layout, Widget, template
from epapers.runtime import Runtime
from fastapi.testclient import TestClient


def settings(tmp_path):
    return Settings(
        _env_file=None, data_dir=tmp_path, api_token="private-api", gateway_token="private-gateway"
    )


@pytest.mark.asyncio
async def test_component_carousel_requires_new_firmware_and_watches_every_entity(tmp_path):
    runtime = Runtime(settings(tmp_path))
    await runtime.start()
    try:
        await runtime.add_device(Device(id="tag", name="test", firmware="0.2.0-ram"))
        layout = Layout(
            profile="HINK-E0213A41",
            widgets=[
                Widget(
                    id="cycle",
                    kind="entity_carousel",
                    x=0,
                    y=32,
                    width=96,
                    height=32,
                    carousel_entries=[{"entity_id": "sensor.a"}, {"entity_id": "sensor.b"}],
                )
            ],
        )
        await runtime.save_layout("tag", layout)
        with pytest.raises(ValueError, match="0.3.0"):
            await runtime.render_job("tag")
        runtime.devices["tag"].firmware = "0.3.0-test"
        job = await runtime.render_job("tag")
        assert job is not None
        runtime.dirty.clear()
        await runtime.entity_changed("sensor.unrelated")
        assert "tag" not in runtime.dirty
        await runtime.entity_changed("sensor.b")
        assert "tag" in runtime.dirty
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_ack_must_follow_mcu_sequence_and_match_gateway(tmp_path):
    runtime = Runtime(settings(tmp_path))
    await runtime.start()
    try:
        await runtime.add_device(Device(id="tag", name="test", gateway_id="g"))
        await runtime.save_layout("tag", template())
        job = await runtime.render_job("tag")
        with pytest.raises(ValueError):
            await runtime.ack("g", {"job_id": job.id, "state": "applied"})
        with pytest.raises(ValueError):
            await runtime.ack("wrong", {"job_id": job.id, "state": "transferring"})
        assert runtime.devices["tag"].last_refresh is None
        for state in ["transferring", "received", "applied"]:
            await runtime.ack("g", {"job_id": job.id, "state": state})
        assert job.state == JobState.APPLIED
        assert runtime.devices["tag"].applied_revision == job.revision
        assert runtime.devices["tag"].last_refresh is not None
        await runtime.ack("g", {"job_id": job.id, "state": "applied"})
    finally:
        await runtime.close()


def test_api_save_persist_preview_and_remote_auth(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        d = client.post("/api/devices", json={"name": "客厅", "profile": "OPM021B1"}).json()
        key = d["id"]
        layout = client.get(f"/api/devices/{key}/layout").json()
        layout["name"] = "卧室"
        assert client.put(f"/api/devices/{key}/layout", json=layout).status_code == 200
        result = client.post("/api/preview", json=layout).json()
        assert result["image"].startswith("data:image/png;base64,") and result["bytes"] < 16384
        layout["widgets"][0]["x"] = 250
        assert client.put(f"/api/devices/{key}/layout", json=layout).status_code == 422
    with TestClient(create_app(config)) as client:
        assert client.get(f"/api/devices/{key}/layout").json()["name"] == "卧室"
    with TestClient(create_app(config), client=("10.1.2.3", 1234)) as client:
        assert client.get("/api/state").status_code == 401
        assert (
            client.get("/api/state", headers={"Authorization": "Bearer private-api"}).status_code
            == 200
        )
        assert (
            client.post(
                "/api/devices",
                json={},
                headers={"Authorization": "Bearer private-api", "Origin": "http://evil.example"},
            ).status_code
            == 403
        )


def test_gateway_auth_and_telemetry(tmp_path):
    with (
        TestClient(create_app(settings(tmp_path))) as client,
        client.websocket_connect(
            "/ws/gateway", headers={"Authorization": "Bearer private-gateway"}
        ) as ws,
    ):
        ws.send_json({"type": "hello", "gateway_id": "g", "firmware": "test"})
        assert ws.receive_json()["type"] == "time"
        ws.send_json(
            {
                "type": "telemetry",
                "device_id": "tag",
                "battery_mv": 3900,
                "battery_percent": 70,
                "supply_mv": 3267,
            }
        )
        ws.send_json({"type": "time_request"})
        assert ws.receive_json()["type"] == "time"
        device = client.get("/api/state").json()["devices"][0]
        assert device["battery_mv"] is None and device["battery_percent"] is None
        assert device["supply_mv"] == 3267 and device["gateway_id"] == "g"
        assert device["last_refresh"] is None


@pytest.mark.parametrize("source", ["direct", "external"])
def test_status_response_replaces_voltage_and_sample_time(tmp_path, source):
    with (
        TestClient(create_app(settings(tmp_path))) as client,
        client.websocket_connect(
            "/ws/gateway", headers={"Authorization": "Bearer private-gateway"}
        ) as ws,
    ):
        ws.send_json({"type": "hello", "gateway_id": "g"})
        ws.receive_json()
        ws.send_json({
            "type": "telemetry", "device_id": "tag", "address": "CA:BB:CC:DD:EE:FF",
            "supply_mv": 3900, "battery_sampled_at": 1750000000,
        })
        ws.send_json({"type": "time_request"})
        ws.receive_json()
        client.put("/api/devices/tag/options", json={"name": "屏幕", "battery_source": source})
        job = client.post("/api/devices/tag/commands/status").json()
        command = ws.receive_json()
        assert command["kind"] == "status" and command["device_id"] == "tag"
        assert command["job_id"] == job["id"]
        for state in ["transferring", "received"]:
            ws.send_json({"type": "ack", "job_id": job["id"], "state": state})
        ws.send_json({
            "type": "telemetry", "device_id": "tag", "address": "CA:BB:CC:DD:EE:FF",
            "supply_mv": 3700, "battery_sampled_at": 1750000030,
        })
        ws.send_json({"type": "ack", "job_id": job["id"], "state": "applied"})
        ws.send_json({"type": "time_request"})
        ws.receive_json()
        state = client.get("/api/state").json()
        device = state["devices"][0]
        assert device["supply_mv"] == 3700 and device["battery_sampled_at"] == 1750000030
        assert device["battery_mv"] == (3700 if source == "direct" else None)
        assert device["battery_percent"] == (35 if source == "direct" else None)
        assert device["last_refresh"] is None
        assert next(j for j in state["jobs"] if j["id"] == job["id"])["state"] == "applied"


def test_gateway_name_room_survive_disconnect_reconnect_and_restart(tmp_path):
    config = settings(tmp_path)
    headers = {"Authorization": "Bearer private-gateway"}
    with TestClient(create_app(config)) as client:
        with client.websocket_connect("/ws/gateway", headers=headers) as ws:
            ws.send_json({"type": "hello", "gateway_id": "gateway", "firmware": "one"})
            ws.receive_json()
            assert (
                client.put(
                    "/api/gateways/gateway/options", json={"name": "客厅网关", "area_name": "客厅"}
                ).status_code
                == 200
            )
        with client.websocket_connect("/ws/gateway", headers=headers) as ws:
            ws.send_json({"type": "hello", "gateway_id": "gateway", "firmware": "two"})
            ws.receive_json()
            gateway = client.get("/api/state").json()["gateways"][0]
            assert gateway["name"] == "客厅网关" and gateway["area_name"] == "客厅"
            assert gateway["firmware"] == "two" and gateway["online"]
    with TestClient(create_app(config)) as client:
        gateway = client.get("/api/state").json()["gateways"][0]
        assert gateway["name"] == "客厅网关" and gateway["area_name"] == "客厅"
        assert not gateway["online"]


def test_pages_device_identity_template_and_telemetry_persist(tmp_path):
    config = settings(tmp_path)
    with TestClient(create_app(config)) as client:
        key = client.post(
            "/api/devices", json={"name": "屏幕一", "profile": "HINK-E0213A41"}
        ).json()["id"]
        options = {
            "name": "客厅屏幕",
            "ha_name": "客厅信息牌",
            "area_name": "客厅",
            "ha_entity_prefix": "living_epaper",
            "battery_source": "external",
            "refresh_interval": 600,
        }
        assert client.put(f"/api/devices/{key}/options", json=options).status_code == 200
        layout = client.get(f"/api/devices/{key}/layout").json()
        layout["name"] = "温湿度画布"
        assert client.put(f"/api/devices/{key}/layout", json=layout).status_code == 200
        second = dict(layout, name="第二页", widgets=[])
        result = client.put(
            f"/api/devices/{key}/pages",
            json={"pages": [layout, second], "enabled": True, "interval_minutes": 3},
        )
        assert result.status_code == 200
        program = result.json()
        assert len(program["pages"]) == 2 and program["started_at"] > 0
        assert client.get("/api/state").json()["devices"][0]["name"] == "客厅屏幕"
        custom = client.post("/api/templates", json={"name": "我的温湿度", "layout": layout}).json()
        adapted = client.get(f"/api/templates/OPM021B1/{custom['id']}").json()
        assert adapted["profile"] == "OPM021B1"
    with TestClient(create_app(config)) as client:
        assert client.get(f"/api/devices/{key}/pages").json() == program
        screen = client.get("/api/state").json()["devices"][0]
        assert screen["area_name"] == "客厅" and screen["ha_sync_status"] == "pending"
        assert any(t["id"] == custom["id"] for t in client.get("/api/templates").json())


def test_delete_ignores_late_discovery_survives_restart_and_restores_canvas(tmp_path):
    config = settings(tmp_path)
    headers = {"Authorization": "Bearer private-gateway"}
    with TestClient(create_app(config)) as client:
        key = client.post("/api/devices", json={"name": "旧屏幕"}).json()["id"]
        client.patch(f"/api/devices/{key}", json={"name": "旧屏幕", "address": "AA:BB:CC:DD:EE:FF"})
        layout = client.get(f"/api/devices/{key}/layout").json()
        layout["name"] = "保留的画布"
        client.put(f"/api/devices/{key}/layout", json=layout)
        client.post(f"/api/devices/{key}/commands/status")
        assert client.delete(f"/api/devices/{key}").status_code == 200
        state = client.get("/api/state").json()
        assert not state["devices"] and not state["jobs"]
        assert state["removed_devices"][0]["id"] == key
        with client.websocket_connect("/ws/gateway", headers=headers) as ws:
            ws.send_json({"type": "hello", "gateway_id": "g"})
            ws.receive_json()
            ws.send_json(
                {"type": "telemetry", "device_id": "rediscovered", "address": "aa:bb:cc:dd:ee:ff"}
            )
            ws.send_json({"type": "time_request"})
            assert ws.receive_json()["type"] == "time"
        assert not client.get("/api/state").json()["devices"]
    with TestClient(create_app(config)) as client:
        assert not client.get("/api/state").json()["devices"]
        assert client.get(f"/api/devices/{key}/layout").status_code == 404
        assert client.post(f"/api/devices/{key}/restore").status_code == 200
        assert client.get(f"/api/devices/{key}/layout").json()["name"] == "保留的画布"
        assert not client.get("/api/state").json()["removed_devices"]


def test_custom_content_interval_accepts_arbitrary_seconds_from_30(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        key = client.post("/api/devices", json={"name": "屏幕"}).json()["id"]
        other = client.post("/api/devices", json={"name": "另一块屏幕"}).json()["id"]
        client.put(
            f"/api/devices/{other}/options", json={"name": "另一块屏幕", "refresh_interval": 600}
        )
        for seconds in (30, 45, 90, 86400):
            response = client.put(
                f"/api/devices/{key}/options", json={"name": "屏幕", "refresh_interval": seconds}
            )
            assert response.status_code == 200
            assert response.json()["refresh_interval"] == seconds
            other_state = next(
                d for d in client.get("/api/state").json()["devices"] if d["id"] == other
            )
            assert other_state["refresh_interval"] == 600
        for seconds in (29, 86401, 30.5):
            assert (
                client.put(
                    f"/api/devices/{key}/options",
                    json={"name": "屏幕", "refresh_interval": seconds},
                ).status_code
                == 422
            )


@pytest.mark.asyncio
async def test_history_refresh_is_periodic_without_state_events_and_retries_failure(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    runtime = Runtime(settings(tmp_path))
    await runtime.start()
    runtime.tasks[1].cancel()
    await asyncio.gather(runtime.tasks[1], return_exceptions=True)
    try:
        await runtime.add_device(
            Device(id="chart", name="功率", simulated=True, refresh_interval=45)
        )
        await runtime.save_layout(
            "chart",
            Layout(
                profile="HINK-E0213A41",
                widgets=[
                    Widget(
                        id="trend",
                        kind="chart",
                        x=0,
                        y=0,
                        width=212,
                        height=104,
                        entity_id="sensor.power",
                    )
                ],
            ),
        )
        runtime.dirty.clear()
        runtime.last_render["chart"] = 100
        render = AsyncMock()
        monkeypatch.setattr(runtime, "render_job", render)
        await runtime.refresh_due(now=144.9)
        render.assert_not_awaited()
        await runtime.refresh_due(now=145)
        render.assert_awaited_once_with("chart")
        runtime.last_render["chart"] = 145
        render.reset_mock()
        runtime.firmware.busy_gateways.add(None)
        await runtime.refresh_due(now=190)
        render.assert_not_awaited()
        runtime.firmware.busy_gateways.clear()
        render.side_effect = OSError("temporary failure")
        await runtime.refresh_due(now=190)
        assert "chart" in runtime.dirty and runtime.last_render["chart"] == 190
        render.reset_mock()
        await runtime.refresh_due(now=200)
        render.assert_not_awaited()
        render.side_effect = None
        await runtime.refresh_due(now=235)
        render.assert_awaited_once_with("chart")
        # A chart on a disabled secondary page should cause no periodic traffic.
        runtime.programs["chart"] = DisplayProgram(
            pages=[Layout(profile="HINK-E0213A41"), runtime.layouts["chart"]]
        )
        runtime.dirty.clear()
        render.reset_mock()
        await runtime.refresh_due(now=300)
        render.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_delivery_fetches_fresh_history_and_identical_canvas_skips_radio(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from epapers.ha import HomeAssistant

    runtime = Runtime(settings(tmp_path))
    await runtime.start()
    runtime.tasks[1].cancel()
    await asyncio.gather(runtime.tasks[1], return_exceptions=True)
    try:
        await runtime.add_device(Device(id="chart", name="功率", simulated=True))
        layout = Layout(
            profile="HINK-E0213A41",
            widgets=[
                Widget(
                    id="trend",
                    kind="chart",
                    x=0,
                    y=0,
                    width=212,
                    height=104,
                    entity_id="sensor.power",
                )
            ],
        )

        async def history(entity, hours, attribute, force=False):
            key = HomeAssistant.history_key(entity, hours, attribute)
            runtime.ha.histories[key] = [[0, 12], [100, 12]]
            runtime.ha.history_details[key] = {"error": None}

        fetch = AsyncMock(side_effect=history)
        monkeypatch.setattr(runtime.ha, "history", fetch)
        monkeypatch.setattr(runtime.renderer, "render", partial(runtime.renderer.render, epoch=100))
        await runtime.save_layout("chart", layout)
        fetch.reset_mock()
        job = await runtime.render_job("chart")
        fetch.assert_awaited_once_with("sensor.power", 24, None, force=True)
        job.state = JobState.APPLIED
        runtime.devices["chart"].applied_revision = job.revision
        assert await runtime.render_job("chart") is None
        assert len(runtime.jobs) == 1
        await runtime.remove_device("chart")
        await runtime.ack(None, {"job_id": job.id, "state": "applied"})
        for cache in (runtime.previews, runtime.blobs, runtime.last_render, runtime.dirty):
            assert "chart" not in cache
    finally:
        await runtime.close()
