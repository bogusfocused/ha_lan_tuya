# pylint: disable=missing-function-docstring missing-module-docstring
# pylint: disable=missing-class-docstring

import logging
from typing import Any, Mapping, Optional
from homeassistant.helpers.storage import Store

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_API_KEY,
    CONF_API_REGION,
    CONF_APP_ID,
    CONF_API_SECRET,
    DOMAIN,
    STORAGE_KEY,
    STORAGE_VERSION,
)

ENTRY_TITLE = "tuya LAN"
from .tuya import download_devices_info

_LOGGER = logging.getLogger(__name__)


@config_entries.HANDLERS.register(DOMAIN)
class ConfigFlow(config_entries.ConfigFlow):
    """Config flow for smart home"""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Initialize."""

    async def async_step_user(
        self, user_input: Optional[Mapping[str, Any]] = None
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors = {}
        description_placeholders = {"msg": ""}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                api_key = user_input[CONF_API_KEY]
                api_region = user_input[CONF_API_REGION]
                api_secret = user_input[CONF_API_SECRET]
                data, app_id = await download_devices_info(
                    session=session,
                    api_key=api_key,
                    api_region=api_region,
                    api_secret=api_secret,
                )
                config_data = {
                    CONF_API_KEY: api_key,
                    CONF_API_REGION: api_region,
                    CONF_API_SECRET: api_secret,
                    CONF_APP_ID: app_id,
                }
                unique_id = app_id
                store_key = STORAGE_KEY.format_map({"unique_id": unique_id})
                store = Store(self.hass, STORAGE_VERSION, store_key, private=True)
                await store.async_save(data)
                entry = await self.async_set_unique_id(unique_id)
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        title=ENTRY_TITLE,
                        data=config_data,
                    )
                    return self.async_abort(reason="Updated existing entry")
                else:
                    return self.async_create_entry(
                        title=ENTRY_TITLE,
                        data=config_data,
                        description="Control devices connected to Tuya",
                    )
            except Exception as ex:
                errors["base"] = "general_error"
                description_placeholders["msg"] = "Error: " + str(ex)
        input = user_input or {}
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_API_KEY, default=input.get(CONF_API_KEY, "")
                    ): str,
                    vol.Required(
                        CONF_API_SECRET, default=input.get(CONF_API_SECRET, "")
                    ): str,
                    vol.Required(
                        CONF_API_REGION, default=input.get(CONF_API_REGION, "us")
                    ): vol.All(str, vol.In(["us", "eu", "cn", "in"])),
                }
            ),
            description_placeholders=description_placeholders,
        )
