from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


class EPapersEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, suffix, name):
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{key}_{suffix}"
        self._attr_name = name

    @property
    def device_info(self):
        device = self.coordinator.device(self.key) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id + ":" + self.key)},
            name=device.get("name", self.key),
            manufacturer="epapers",
            model=device.get("profile"),
            sw_version=device.get("firmware"),
            configuration_url=self.coordinator.url,
        )

    @property
    def available(self):
        return (
            self.coordinator.last_update_success and self.coordinator.device(self.key) is not None
        )


def add_dynamic_entities(entry, coordinator, async_add_entities, factory):
    known = set()

    def discover():
        new = {d["id"] for d in coordinator.data.get("devices", [])} - known
        if new:
            async_add_entities([entity for key in new for entity in factory(key)])
            known.update(new)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))
