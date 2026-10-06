import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN


class EPapersConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            url = user_input["url"].rstrip("/")
            if not url.startswith(("http://", "https://")):
                errors["base"] = "invalid_url"
            else:
                try:
                    session = async_get_clientsession(self.hass)
                    async with session.get(
                        url + "/api/state",
                        headers={"Authorization": "Bearer " + user_input["token"]},
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status == 401:
                            errors["base"] = "invalid_auth"
                        else:
                            response.raise_for_status()
                            data = await response.json()
                            if "devices" not in data:
                                errors["base"] = "cannot_connect"
                except (aiohttp.ClientError, TimeoutError, ValueError):
                    errors["base"] = "cannot_connect"
            if not errors:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="epapers", data={**user_input, "url": url})
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("url", default="http://192.168.1.100:8099"): str,
                    vol.Required("token"): str,
                }
            ),
            errors=errors,
        )
