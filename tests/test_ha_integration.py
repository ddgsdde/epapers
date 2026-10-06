"""Run with an HA 2026.9 environment; host Python skips these integration checks."""

from types import MappingProxyType
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

import aiohttp
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.epapers.coordinator import EPapersCoordinator
from custom_components.epapers.sensor import DESCRIPTIONS, EPapersSensor


@pytest.fixture
async def ha(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    entry = ConfigEntry(
        domain="epapers",
        title="epapers",
        version=1,
        minor_version=1,
        data={"url": "http://127.0.0.1:8099", "token": "test-only"},
        options={},
        source="user",
        unique_id="local",
        discovery_keys=MappingProxyType({}),
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await ar.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    session = aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    )
    monkeypatch.setattr(
        "custom_components.epapers.coordinator.async_get_clientsession", lambda hass: session
    )
    coordinator = EPapersCoordinator(hass, entry)
    coordinator.request = AsyncMock(return_value={"ok": True})
    yield hass, entry, coordinator
    await coordinator.async_shutdown()
    await session.close()
    await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_sensor_units_identity_room_sync_and_collision(ha):
    hass, entry, coordinator = ha
    devices, entities, areas = dr.async_get(hass), er.async_get(hass), ar.async_get(hass)
    screen = {
        "id": "tag",
        "name": "原屏幕",
        "ha_name": "客厅信息牌",
        "area_name": "客厅",
        "ha_entity_prefix": "living_epaper",
        "temperature_centi": 2362,
        "supply_mv": 3267,
        "battery_mv": None,
        "battery_percent": None,
        "battery_source": "external",
        "temperature_sampled_at": 1750000000,
    }
    coordinator.data = {"devices": [screen], "jobs": []}
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("epapers", entry.entry_id + ":tag")},
        name="原屏幕",
    )
    for field, name, unit, device_class in DESCRIPTIONS:
        sensor = EPapersSensor(coordinator, "tag", field, name, unit, device_class)
        entity = entities.async_get_or_create(
            "sensor",
            "epapers",
            sensor.unique_id,
            config_entry=entry,
            device_id=device.id,
            suggested_object_id="original_" + field,
        )
        assert entity.device_id == device.id
        if field == "temperature_centi":
            assert sensor.native_value == 23.62 and sensor.native_unit_of_measurement == "°C"
            assert sensor.extra_state_attributes["source"] == "panel_internal_sensor"
        if field == "battery_percent":
            assert sensor.native_value is None
        if field == "supply_mv":
            assert sensor.native_value == 3267 and sensor.native_unit_of_measurement == "mV"
    await coordinator.sync_registry(coordinator.data)
    updated = devices.async_get(device.id)
    assert updated.name_by_user == "客厅信息牌"
    assert updated.area_id == areas.async_get_area_by_name("客厅").id
    assert entities.async_get("sensor.living_epaper_battery_mv")
    assert entities.async_get("sensor.living_epaper_temperature_centi")
    assert coordinator.request.call_args.args == (
        "devices/tag/ha-sync",
        "POST",
        {"status": "synced"},
    )
    count = coordinator.request.call_count
    await coordinator.sync_registry(coordinator.data)
    assert coordinator.request.call_count == count
    # A foreign entity occupies the target ID: fail before mutating names or areas.
    entities.async_get_or_create(
        "sensor", "foreign", "foreign_sensor", suggested_object_id="bedroom_epaper_battery_mv"
    )
    screen.update(ha_name="卧室信息牌", area_name="卧室", ha_entity_prefix="bedroom_epaper")
    await coordinator.sync_registry(coordinator.data)
    assert devices.async_get(device.id).name_by_user == "客厅信息牌"
    assert areas.async_get_area_by_name("卧室") is None
    assert coordinator.request.call_args.args[-1] == {"status": "error"}
