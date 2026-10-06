import json
import logging
from datetime import timedelta

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN


class EPapersCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry):
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name="epapers",
            config_entry=entry,
            update_interval=timedelta(seconds=15),
        )
        self.url = entry.data["url"].rstrip("/")
        self.token = entry.data["token"]
        self.session = async_get_clientsession(hass)
        self.identity_synced = {}

    async def request(self, path, method="GET", data=None):
        try:
            async with self.session.request(
                method,
                f"{self.url}/api/{path}",
                json=data,
                headers={"Authorization": "Bearer " + self.token},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status == 401:
                    raise ConfigEntryAuthFailed("API 密钥无效")
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"工作台请求失败：{type(err).__name__}") from err

    async def _async_update_data(self):
        data = await self.request("state")
        await self.sync_registry(data)
        return data

    async def sync_registry(self, data):
        """Sync only devices/entities owned by this integration, when identity changes."""
        devices, entities, areas = (
            dr.async_get(self.hass),
            er.async_get(self.hass),
            ar.async_get(self.hass),
        )
        entry_id = self.config_entry.entry_id
        for screen in data.get("devices", []):
            key = screen["id"]
            identifier = (DOMAIN, entry_id + ":" + key)
            if hasattr(devices, "async_get_device_by_identifier"):
                device = devices.async_get_device_by_identifier(identifier, entry_id)
            else:
                device = devices.async_get_device(identifiers={identifier})
            if not device:
                continue
            name = screen.get("ha_name") or screen["name"]
            room, prefix = screen.get("area_name", ""), screen.get("ha_entity_prefix", "")
            identity = json.dumps([name, room, prefix], ensure_ascii=False)
            if self.identity_synced.get(key) == identity:
                continue
            try:
                owned = er.async_entries_for_device(
                    entities, device.id, include_disabled_entities=True
                )
                renames = []
                uid = f"{entry_id}_{key}_"
                for entity in owned:
                    if (
                        prefix
                        and entity.config_entry_id == entry_id
                        and entity.unique_id.startswith(uid)
                    ):
                        domain = entity.entity_id.split(".")[0]
                        wanted = f"{domain}.{prefix}_{entity.unique_id[len(uid) :]}"
                        occupied = entities.async_get(wanted)
                        if occupied and occupied.entity_id != entity.entity_id:
                            raise ValueError("entity_id_already_exists")
                        if wanted != entity.entity_id:
                            renames.append((entity.entity_id, wanted))
                area = areas.async_get_area_by_name(room) if room else None
                if room and not area:
                    area = areas.async_create(room)
                devices.async_update_device(
                    device.id, name_by_user=name, area_id=area.id if area else None
                )
                for current, wanted in renames:
                    entities.async_update_entity(current, new_entity_id=wanted)
                await self.request(f"devices/{key}/ha-sync", "POST", {"status": "synced"})
                self.identity_synced[key] = identity
            except ValueError:
                await self.request(f"devices/{key}/ha-sync", "POST", {"status": "error"})

    async def command(self, key, kind):
        result = await self.request(f"devices/{key}/commands/{kind}", "POST")
        await self.async_request_refresh()
        return result

    def device(self, key):
        return next((d for d in self.data.get("devices", []) if d["id"] == key), None)
