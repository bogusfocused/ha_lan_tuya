
from typing import Any, Dict
import homeassistant.util.color as color_util
from homeassistant.components.light import (ATTR_BRIGHTNESS, ATTR_COLOR_TEMP,
                                            ATTR_HS_COLOR,
                                            COLOR_MODE_BRIGHTNESS,
                                            COLOR_MODE_COLOR_TEMP,
                                            COLOR_MODE_HS, COLOR_MODE_ONOFF,
                                            COLOR_MODES_COLOR, LightEntity, DOMAIN as LIGHT)
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import HomeAssistantType


from . import TuyaEntity, get_broker

from .tuya import TuyaDevice

COLOR_TEMP_RANGE = (color_util.color_temperature_kelvin_to_mired(2700),
                    color_util.color_temperature_kelvin_to_mired(6500))


async def async_setup_entry(hass: HomeAssistantType, entry: ConfigEntry,
                            async_add_entities):
    broker = get_broker(hass, entry)
    devices = broker.devices
    lights = [TuyaLightEntity(device)
              for device in devices if device.device_type == LIGHT]
    async_add_entities(lights)
    return True


class TuyaLightEntity(TuyaEntity, LightEntity):
    def __init__(self, device: TuyaDevice) -> None:
        super().__init__(device)
        self._max_mireds, self._min_mireds = COLOR_TEMP_RANGE
        self._supported_color_modes = self.determine_supported_color_modes()


    def determine_supported_color_modes(self):
        supported_color_modes = set()
        if self._device.color_code:
            supported_color_modes.add(COLOR_MODE_HS)
        if self._device.color_temp_code:
            supported_color_modes.add(COLOR_MODE_COLOR_TEMP)
        if self._device.brightness_code:
            supported_color_modes.add(COLOR_MODE_BRIGHTNESS)
        supported_color_modes.add(COLOR_MODE_ONOFF)
        return supported_color_modes

    @property
    def is_on(self):
        return self.device.power_state

    async def async_turn_on(self, **kwargs):
        dps: Dict[str, Any] = self.device.set_power_state(True) or {}
        if ATTR_HS_COLOR in kwargs or (
                self.color_mode in COLOR_MODES_COLOR and
                ATTR_BRIGHTNESS in kwargs):
            dp = self._device.set_color_mode(COLOR_MODE_HS)
            dps.update(dp) if dp else None
            h, s = kwargs[ATTR_HS_COLOR] if ATTR_HS_COLOR in kwargs else self.hs_color
            v = kwargs[ATTR_BRIGHTNESS] if ATTR_BRIGHTNESS in kwargs else self.brightness
            if v is None:
                raise ValueError("Brightness is not supported")
            dp = self.device.set_color_hsv((int(h), int(s), int(v)))
            dps.update(dp) if dp else None

        elif ATTR_COLOR_TEMP in kwargs:
            dp = self.device.set_color_mode(COLOR_MODE_COLOR_TEMP)
            dps.update(dp) if dp else None

            brightness = kwargs[ATTR_BRIGHTNESS] if ATTR_BRIGHTNESS in kwargs else self.brightness
            if brightness is None:
                raise ValueError("Brightness is not supported")
            dp = self.device.set_brightness(brightness)
            dps.update(dp) if dp else None

            mired = kwargs[ATTR_COLOR_TEMP]
            dp = self.device.set_color_temp(
                mired)
            dps.update(dp) if dp else None
        # restore brightness
        else:
            brightness = kwargs[ATTR_BRIGHTNESS] if ATTR_BRIGHTNESS in kwargs else self.brightness
            if brightness is None:
                raise ValueError("Brightness is not supported")
            dp = self.device.set_brightness(brightness)
            dps.update(dp) if dp else None
        await self.update_device(dps)

    async def async_turn_off(self, **kwargs):
        dps = self._device.set_power_state(False) or {}
        await self.update_device(dps)

    @property
    def min_mireds(self):
        return self._min_mireds

    @property
    def max_mireds(self):
        return self._max_mireds

    @property
    def brightness(self):
        return self.device.brightness

    @property
    def hs_color(self):
        return (self.device.color_hsv[0], self.device.color_hsv[1])

    @property
    def color_temp(self):
        return self.device.color_temp

    @property
    def color_mode(self):
        return self.device.color_mode

    @property
    def supported_color_modes(self):
        return self._supported_color_modes
