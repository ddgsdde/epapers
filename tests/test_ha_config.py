import asyncio
import json
import stat

from epapers.app import create_app
from epapers.config import Settings
from epapers.ha import HomeAssistant
from fastapi.testclient import TestClient


async def idle(self):
    await asyncio.Event().wait()


def test_write_only_credentials_keep_replace_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(HomeAssistant, "run", idle)
    settings = Settings(
        _env_file=None, data_dir=tmp_path, ha_url="http://old-ha", ha_token="old-private-key"
    )
    new_key = "secret-never-return-this-key"
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/ha/config").json() == {
            "base_url": "http://old-ha",
            "key_configured": True,
            "managed": False,
        }
        for body in [
            {"base_url": "http://new-ha:8123/", "api_key": new_key},
            {"base_url": "http://new-ha:8123", "api_key": ""},
        ]:
            result = client.put("/api/ha/config", json=body)
            assert result.status_code == 200
            assert new_key not in result.text and "api_key" not in result.json()
            assert result.headers["cache-control"] == "no-store"
        assert new_key not in client.get("/api/state").text
        assert new_key not in client.get("/api/ha/config").text
    file = tmp_path / "ha-connection.json"
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert json.loads(file.read_text())["api_key"] == new_key
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        assert runtime.ha.token == new_key
        assert runtime.ha.url == "http://new-ha:8123"
        assert client.get("/api/state").json()["ha_url"] == runtime.ha.url


def test_validation_never_echoes_key_and_rejects_url_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(HomeAssistant, "run", idle)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.put("/api/ha/config", json={"base_url": "http://ha"}).status_code == 422
        secret = "never-echo-me"
        for body in [
            {"base_url": f"http://user:{secret}@ha", "api_key": secret},
            {"base_url": f"http://ha?token={secret}", "api_key": secret},
            {"base_url": "ftp://ha", "api_key": secret},
            {"base_url": "http://ha", "api_key": secret + "\ninvalid"},
            {"base_url": "http://ha", "api_key": {"value": secret}},
        ]:
            result = client.put("/api/ha/config", json=body)
            assert result.status_code == 422 and secret not in result.text
        assert not (tmp_path / "ha-connection.json").exists()
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path)), client=("10.0.0.7", 1000)
    ) as client:
        assert client.get("/api/ha/config").status_code == 401
        assert (
            client.put(
                "/api/ha/config", json={"base_url": "http://ha", "api_key": "private"}
            ).status_code
            == 401
        )


def test_supervisor_connection_cannot_be_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(HomeAssistant, "run", idle)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervisor-private")
    with TestClient(
        create_app(Settings(_env_file=None, data_dir=tmp_path)), client=("172.30.32.2", 1234)
    ) as client:
        assert client.get("/api/ha/config").json()["managed"]
        assert (
            client.put(
                "/api/ha/config", json={"base_url": "http://ha", "api_key": "private"}
            ).status_code
            == 403
        )
        assert not (tmp_path / "ha-connection.json").exists()
