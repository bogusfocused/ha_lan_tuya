import asyncio
import logging
from typing import (
    Any,
    Callable,
    Dict,
    Final,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    cast,
)
from aiohttp.client import ClientTimeout
import homeassistant.util.color as color_util
from homeassistant.components.light import COLOR_MODE_COLOR_TEMP, COLOR_MODE_HS
from homeassistant.components.light import DOMAIN as LIGHT
from homeassistant.components.switch import DOMAIN as SWITCH
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .tuya import DeviceData, DeviceScanner, download_devices_info, TuyaDevice, ScanResult, Id
from .const import CONF_APP_ID, DOMAIN, SIGNAL_UPDATED, STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)
SUPPORTED_PLATFORMS: Final[List[str]] = [LIGHT, SWITCH]

HA_HSV_RANGE = ((0, 360), (0, 100), (1, 255))
RANGE_255 = (0, 255)
COLOR_TEMP_RANGE = (
    color_util.color_temperature_kelvin_to_mired(2700),
    color_util.color_temperature_kelvin_to_mired(6500),
)


class DomainData(NamedTuple):
    settings: Mapping[str, Any]
    scanner: DeviceScanner
    brokers: Dict[str, "Broker"]


TUYA_MAPPINGS = {
    "device_type_light": LIGHT,
    "device_type_switch": SWITCH,
    "device_type_unknown": "",
    "light_mode_color": COLOR_MODE_HS,
    "light_mode_temperature": COLOR_MODE_COLOR_TEMP,
    "range_brightness": RANGE_255,
    "range_hsv": HA_HSV_RANGE,
    "range_color_temperature": COLOR_TEMP_RANGE,
}

TuyaDevice.build_map(**TUYA_MAPPINGS)


def get_broker(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainData = hass.data[DOMAIN]
    app_id: str = entry.data[CONF_APP_ID]
    return domain_data.brokers[app_id]


async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Initialize the Tiny tuya platform."""
    domain_config = config.get(DOMAIN, None)
    settings = dict(**domain_config) if domain_config else {}
    hass.data[DOMAIN] = DomainData(
        settings=settings, brokers={}, scanner=DeviceScanner()
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainData = hass.data[DOMAIN]
    app_id: str = entry.data[CONF_APP_ID]
    domain_data.brokers[app_id] = broker = Broker(
        app_id, domain_data.scanner, hass, entry)
    if hass.state == CoreState.running:
        await broker.start()
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED, broker.start)
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainData = hass.data[DOMAIN]
    app_id: str = entry.data[CONF_APP_ID]
    broker = domain_data.brokers.pop(app_id, None)
    if broker:
        await broker.stop()


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry):
    domain_data: DomainData = hass.data[DOMAIN]
    app_id: str = entry.data[CONF_APP_ID]
    broker = domain_data.brokers.pop(app_id, None)
    if broker:
        await broker.remove()


class Broker:
    def __init__(
        self,
        app_id: str,
        scanner: DeviceScanner,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        self.scanner = scanner
        self.hass = hass
        self.entry = entry
        self._remove_listener: Optional[Callable[[], None]] = None
        self._store_key = STORAGE_KEY.format_map(
            {"unique_id": app_id})
        self._devices: Optional[Mapping[Id, TuyaDevice]] = None

    @property
    def devices(self) -> Iterable[TuyaDevice]:
        devices = self._devices
        return devices.values() if devices is not None else []

    async def _load_devices(self, timeout: Optional[ClientTimeout] = None):
        store = Store(self.hass, STORAGE_VERSION,
                      self._store_key, private=True)
        if not (data := await store.async_load()):
            session = async_get_clientsession(self.hass)
            data, _ = await download_devices_info(
                session, **self.entry.data, timeout=timeout
            )
            await store.async_save(data)
            _LOGGER.info("Loaded data from cloud")
        else:
            _LOGGER.info("Loaded from the store")
        data = cast(Mapping[Id, DeviceData], data)
        self._devices = {id: TuyaDevice(data) for id, data in data.items()}

    def on_new_device_found(self, result: ScanResult):
        async def set_result(device: TuyaDevice):
            device.apply(result)
            await device.fetch_state()
            async_dispatcher_send(self.hass, SIGNAL_UPDATED,)

        if device := self._find_device(result):
            self.hass.async_create_task(set_result(device))

    def _find_device(self, result: ScanResult):
        if devices := self._devices:
            return devices.get(result.gwId)

    async def remove(self):
        store = Store(self.hass, STORAGE_VERSION,
                      self._store_key, private=True)
        await store.async_remove()
        _LOGGER.info("Removed from the store")

    async def update_all_states(self):
        _LOGGER.debug("Fetching updated status started")
        timeout = 20.0
        devices = self._devices
        if not devices:
            return
        tasks = asyncio.gather(
            *[device.fetch_state() for device in devices.values() if device.ip], return_exceptions=True
        )
        try:
            await asyncio.wait_for(tasks, timeout=timeout)
        except asyncio.TimeoutError:
            _LOGGER.warn(f"Update all devices timeout with timeout={timeout}")
        async_dispatcher_send(
            self.hass,
            SIGNAL_UPDATED,
        )
        _LOGGER.debug("Fetching updated status finished")

    async def start(self, _: Any = None):
        await self._load_devices()
        self._remove_listener = await self.scanner.add_listener(self.on_new_device_found)
        seen = self.scanner.seen

        async def set_result(device: TuyaDevice, result: ScanResult):
            device.apply(result)
            await device.fetch_state()

        async_dispatcher_send(self.hass, SIGNAL_UPDATED,)
        tasks = asyncio.gather(*[set_result(device, result) for result in seen if (
            device := self._find_device(result))], return_exceptions=True)
        await tasks
        for component in SUPPORTED_PLATFORMS:
            await self.hass.config_entries.async_forward_entry_setup(
                self.entry, component
            )
        _LOGGER.debug("Started")

    async def stop(self, _: Any = None):
        remove = self._remove_listener
        if remove:
            remove()


class TuyaEntity(Entity):
    def __init__(self, device: TuyaDevice) -> None:
        self._device = device
        self._dispatcher_remove = None

    async def async_added_to_hass(self):
        """Device added to hass."""

        async def async_update_state():
            """Update device state."""
            self.async_schedule_update_ha_state()

        self._dispatcher_remove = async_dispatcher_connect(
            self.hass, SIGNAL_UPDATED, async_update_state
        )

    async def async_will_remove_from_hass(self) -> None:
        """Disconnect the device when removed."""
        if self._dispatcher_remove:
            self._dispatcher_remove()

    async def update_device(self, dps: Dict[str, Any]):
        if dps:
            await self._device.set_state(dps)
            self.hass.async_create_task(self._device.fetch_state())
        self.async_schedule_update_ha_state()

    @property
    def assumed_state(self) -> bool:
        return self._device.assumed_state

    @property
    def unique_id(self):
        return self._device.id

    @property
    def name(self):
        return self._device.name

    @property
    def available(self):
        return self._device.is_available

    @property
    def device_info(self) -> DeviceInfo:
        device = self._device
        return {
            "default_manufacturer": "Tuya/Smartlife",
            "name": device.name,
            "identifiers": {(DOMAIN, device.mac)},
            "model": device.product_model,
        }

    @property
    def device(self):
        return self._device
