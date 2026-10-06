from homeassistant.components.select import SelectEntity

from .entity import EPapersEntity, add_dynamic_entities


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    add_dynamic_entities(
        entry, coordinator, async_add_entities, lambda key: [EPapersProfile(coordinator, key)]
    )


class EPapersProfile(EPapersEntity, SelectEntity):
    def __init__(self, coordinator, key):
        super().__init__(coordinator, key, "profile", "屏幕型号")
        self._attr_options = list(coordinator.data["profiles"])

    @property
    def current_option(self):
        return (self.coordinator.device(self.key) or {}).get("profile")

    async def async_select_option(self, option):
        layout = await self.coordinator.request(f"devices/{self.key}/layout")
        layout["profile"] = option
        await self.coordinator.request(f"devices/{self.key}/layout", "PUT", layout)
        await self.coordinator.async_request_refresh()
