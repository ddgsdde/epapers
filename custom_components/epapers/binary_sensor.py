from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import EPapersEntity, add_dynamic_entities


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    add_dynamic_entities(
        entry, coordinator, async_add_entities, lambda key: [EPapersOnline(coordinator, key)]
    )


class EPapersOnline(EPapersEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key, "online", "连接")

    @property
    def is_on(self):
        return bool((self.coordinator.device(self.key) or {}).get("online"))
