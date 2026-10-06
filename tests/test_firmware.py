import hashlib
import io
import json
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from epapers.app import create_app
from epapers.config import Settings
from epapers.firmware import inspect_firmware
from epapers.models import Device
from epapers.runtime import Runtime
from fastapi.testclient import TestClient


def gateway_image():
    data = bytearray(24 + 8 + 256)
    data[0], data[1] = 0xE9, 1
    struct.pack_into("<H", data, 12, 9)
    struct.pack_into("<II", data, 24, 0x3C000020, 256)
    struct.pack_into("<I", data, 32, 0xABCD5432)
    data[48:53] = b"0.2.0"
    data[80:95] = b"epapers_gateway"
    data[176:208] = b"i" * 32
    checksum = 0xEF
    for value in data[32:]:
        checksum ^= value
    data.extend(b"\0" * ((len(data) // 16 + 1) * 16 - len(data)))
    data[-1] = checksum
    return bytes(data) + hashlib.sha256(data).digest()


def tag_image():
    code = struct.pack("<II", 0x07FCB800, 0x07FC0021)
    header = bytearray(64)
    header[:2] = b"pQ"
    struct.pack_into("<II", header, 4, len(code), zlib.crc32(code))
    header[12:17] = b"0.2.0"
    return bytes(header) + code


@pytest.mark.parametrize("kind,factory", [("gateway", gateway_image), ("tag", tag_image)])
def test_firmware_rejects_truncation_and_corruption(kind, factory):
    image = factory()
    assert inspect_firmware(kind, image)["version"] == "0.2.0"
    for damaged in [image[:-1], image[:70], image[:-4] + b"\0" * 4]:
        with pytest.raises(ValueError):
            inspect_firmware(kind, damaged)


def test_installable_ha_download_excludes_caches_and_requires_remote_auth(tmp_path):
    config = Settings(
        _env_file=None,
        data_dir=tmp_path,
        resource_dir=Path(__file__).resolve().parents[1],
        api_token="private",
    )
    with TestClient(create_app(config), client=("10.0.0.10", 1)) as client:
        assert client.get("/api/ha/integration.zip").status_code == 401
        response = client.get(
            "/api/ha/integration.zip", headers={"Authorization": "Bearer private"}
        )
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert json.loads(archive.read("epapers/manifest.json"))["version"] == "0.2.0"
            assert "epapers/coordinator.py" in archive.namelist()
            assert not any(
                "__pycache__" in name or name.endswith(".pyc") for name in archive.namelist()
            )


@pytest.mark.asyncio
async def test_batch_preflight_does_not_create_partial_jobs(tmp_path):
    runtime = Runtime(Settings(_env_file=None, data_dir=tmp_path))
    await runtime.start()
    try:
        manager = runtime.firmware
        meta = await manager.upload("tag", tag_image(), "tag.img")
        runtime.gateways["g"] = {"ws": object(), "ota": True}
        for key, suota in [("ready", True), ("ram_only", False)]:
            await runtime.add_device(
                Device(
                    id=key,
                    name=key,
                    address="F5:5F:1D:E4:26:53",
                    gateway_id="g",
                    capabilities={"suota": suota},
                )
            )
        with pytest.raises(ValueError, match="基础固件"):
            await manager.deploy(meta["id"], ["ready", "ram_only"])
        assert manager.jobs == {} and not manager.tasks
    finally:
        await runtime.close()
