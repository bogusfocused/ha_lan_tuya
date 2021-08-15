
from typing import Any, List, Optional
from homeassistant.helpers.entity_platform import async_get_current_platform

from homeassistant.components.switch import SwitchEntity, DOMAIN as SWITCH
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import HomeAssistantType

from . import TuyaEntity, get_broker
from .const import DOMAIN
from .tuya import TuyaDevice
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry,
                            async_add_entities):
    broker = get_broker(hass, entry)
    devices = broker.devices
    lights = [TuyaSwitchEntity(device)
              for device in devices if device.device_type == SWITCH]
    async_add_entities(lights)

    return True


class TuyaSwitchEntity(TuyaEntity, SwitchEntity):
    def __init__(self, device: TuyaDevice) -> None:
        super().__init__(device)

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self.device.power_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        dps = self.device.set_power_state(True)
        await self.update_device(dps)

    async def async_turn_off(self, **kwargs: Any):
        dps = self.device.set_power_state(False)
        await self.update_device(dps)
