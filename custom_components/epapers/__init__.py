import voluptuous as vol
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, PLATFORMS
from .coordinator import EPapersCoordinator


async def async_setup(hass, config):
    async def set_widget(call: ServiceCall):
        entries = [
            e for e in hass.config_entries.async_entries(DOMAIN) if getattr(e, "runtime_data", None)
        ]
        coordinator = next(
            (e.runtime_data for e in entries if e.runtime_data.device(call.data["tag_id"])), None
        )
        if not coordinator:
            raise HomeAssistantError("找不到墨水屏")
        key = call.data["tag_id"]
        layout = await coordinator.request(f"devices/{key}/layout")
        widget = next((w for w in layout["widgets"] if w["id"] == call.data["widget_id"]), None)
        if not widget:
            raise HomeAssistantError("找不到组件")
        if call.service == "set_text":
            widget["text"] = call.data["text"]
        else:
            widget["entity_id"] = call.data["entity_id"]
            widget["attribute"] = call.data.get("attribute")
        await coordinator.request(f"devices/{key}/layout", "PUT", layout)
        await coordinator.async_request_refresh()

    common = {vol.Required("tag_id"): str, vol.Required("widget_id"): str}
    hass.services.async_register(
        DOMAIN, "set_text", set_widget, schema=vol.Schema({**common, vol.Required("text"): str})
    )
    hass.services.async_register(
        DOMAIN,
        "set_entity",
        set_widget,
        schema=vol.Schema(
            {**common, vol.Required("entity_id"): str, vol.Optional("attribute"): str}
        ),
    )
    return True


async def async_setup_entry(hass, entry):
    coordinator = EPapersCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
