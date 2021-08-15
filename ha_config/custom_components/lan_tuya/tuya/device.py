from asyncio.windows_events import SelectorEventLoop
import colorsys
import logging
from typing import (
    AbstractSet,
    Any,
    Mapping,
    NamedTuple,
    Optional,
    Tuple,
    TypedDict,
)

from .device_scanner import ScanResult
from .tuya_protocol import DeviceData, TUYA_CODES
from .tuya_platform import TuyaState, TuyaStatus, set_status
from .tuya_platform import status as get_status

_LOGGER = logging.getLogger(__name__)


TURN_ON_CODES = {TUYA_CODES.SWITCH_LED, TUYA_CODES.SWITCH_1}
BRIGHTNESS_CODES = {TUYA_CODES.BRIGHT_VALUE, TUYA_CODES.BRIGHT_VALUE_V2}
COLOR_CODES = {TUYA_CODES.COLOUR_DATA}
COLOR_TEMP_CODES = {TUYA_CODES.TEMP_VALUE, TUYA_CODES.TEMP_VALUE_V2}
WORK_MODES = {TUYA_CODES.WORK_MODE}


class ValueRange(NamedTuple):
    min: int
    max: int


TUYA_TEMP_MODE = "white"
TUYA_COLOR_MODE = "colour"
TUYA_TYPE_LIGHT = "dj"
TUYA_TYPE_SWITCH = "cz"

RANGE_255 = ValueRange(0, 255)
TUYA_HSV_RANGE = ((0, 360), (0, 255), (0, 255))


def scale_value(value1: int, range1: ValueRange, range2: ValueRange):
    if range1 == range2:
        return int(value1)
    if value1 == range1.min:
        return range2.min
    if value1 == range1.max:
        return range2.max
    factor = (range2.max - range2.min) / (1.0 * (range1.max - range1.min))
    return int(range2.max - (factor * (range1.max - value1)))


class _TuyaMap(TypedDict):
    device_types: Mapping[str, str]
    light_modes: Mapping[str, str]
    range_brightness: ValueRange
    range_hsv: Tuple[ValueRange, ValueRange, ValueRange]
    range_color_temperature: ValueRange


class TuyaDevice:
    _map_from_tuya: _TuyaMap
    _map_to_tuya: _TuyaMap

    @staticmethod
    def build_map(
        *,
        device_type_unknown: str = "",
        device_type_light: str = TUYA_TYPE_LIGHT,
        device_type_switch: str = TUYA_TYPE_SWITCH,
        light_mode_temperature: str = TUYA_TEMP_MODE,
        light_mode_color: str = TUYA_COLOR_MODE,
        range_brightness: Tuple[int, int] = RANGE_255,
        range_hsv: Tuple[
            Tuple[int, int], Tuple[int, int], Tuple[int, int]
        ] = TUYA_HSV_RANGE,
        range_color_temperature: Tuple[int, int] = RANGE_255,
        **kwargs,
    ) -> None:
        map_from_tuya: _TuyaMap = {
            "device_types": {
                TUYA_TYPE_LIGHT: device_type_light,
                TUYA_TYPE_SWITCH: device_type_switch,
                "": device_type_unknown,
            },
            "light_modes": {
                TUYA_TEMP_MODE: light_mode_temperature,
                TUYA_COLOR_MODE: light_mode_color,
            },
            "range_brightness": ValueRange(*range_brightness),
            "range_hsv": (
                ValueRange(*range_hsv[0]),
                ValueRange(*range_hsv[1]),
                ValueRange(*range_hsv[2]),
            ),
            "range_color_temperature": ValueRange(*range_color_temperature),
        }
        device_types = map_from_tuya["device_types"]
        light_modes = map_from_tuya["light_modes"]
        map_to_tuya: _TuyaMap = {
            "device_types": {device_types[k]: k for k in device_types},
            "light_modes": {light_modes[k]: k for k in light_modes},
            "range_brightness": ValueRange(*RANGE_255),
            "range_hsv": (
                ValueRange(*TUYA_HSV_RANGE[0]),
                ValueRange(*TUYA_HSV_RANGE[1]),
                ValueRange(*TUYA_HSV_RANGE[2]),
            ),
            "range_color_temperature": ValueRange(*RANGE_255),
        }
        TuyaDevice._map_from_tuya = map_from_tuya
        TuyaDevice._map_to_tuya = map_to_tuya

    def __init__(
        self,
        device_data: DeviceData,
    ) -> None:

        if not device_data:
            raise ValueError("value expected but is None")
        self._data = device_data
        self._state: Optional[TuyaState] = None
        self._failed_connect: int = 0
        self._attributes: AbstractSet[str] = set(device_data["attributes"])

    def apply(self, result: ScanResult):
        data = self._data
        data["ip"] = result.ip
        data["version"] = result.version

    @property
    def id(self):
        return self._data["id"]

    @property
    def ip(self):
        return self._data["ip"]

    @property
    def mac(self):
        return self._data["mac"]

    @property
    def name(self):
        return self._data["name"]

    @property
    def product_model(self):
        return self._data["model"]

    @property
    def _version(self):
        return self._data["version"]

    @property
    def _local_key(self):
        return self._data["local_key"]

    @property
    def is_available(self):
        return self.ip is not None and self._state is not None

    @property
    def assumed_state(self) -> bool:
        return self._failed_connect > 3

    @property
    def device_type(self):
        value = self._data["device_type"]
        mapping = self._map_from_tuya["device_types"]
        return mapping.get(value, mapping[""])

    @property
    def color_code(self) -> Optional[str]:
        attributes = self._attributes
        code = COLOR_CODES & attributes if attributes else None
        return code.pop() if code else None

    @property
    def color_temp_code(self) -> Optional[str]:
        attributes = self._attributes
        code = COLOR_TEMP_CODES & attributes if attributes else None
        return code.pop() if code else None

    @property
    def brightness_code(self) -> Optional[str]:
        attributes = self._attributes
        code = BRIGHTNESS_CODES & attributes if attributes else None
        return code.pop() if code else None

    @property
    def power_code(self) -> Optional[str]:
        attributes = self._attributes
        code = TURN_ON_CODES & attributes if attributes else None
        return code.pop() if code else None

    @property
    def work_mode_code(self) -> Optional[str]:
        attributes = self._attributes
        code = WORK_MODES & attributes if attributes else None
        return code.pop() if code else None

    @property
    def power_state(self) -> bool:
        state = self._state
        code = self.power_code
        if code and state and code in state:
            return state[code]
        return False

    def set_power_state(self, value: bool):
        code = self.power_code
        if code:
            if code == TUYA_CODES.SWITCH_LED:
                return {code: value}
            if code == TUYA_CODES.SWITCH_1:
                return {code: value}
        raise ValueError("Power state is unsupported")

    @property
    def color_mode(self):
        state = self._state
        code = self.work_mode_code
        if code and state and code in state:
            tuya_mode = state[code]
            mapping = self._map_from_tuya["light_modes"]
            return mapping.get(tuya_mode, None)
        return None

    def set_color_mode(self, value: str):
        mapping = self._map_to_tuya["light_modes"]
        code = self.work_mode_code
        if code and code == TUYA_CODES.WORK_MODE:
            return {code: mapping[value]}
        return None

    @property
    def brightness(self) -> Optional[int]:
        range = self._map_from_tuya["range_brightness"]
        t_range = self._map_to_tuya["range_brightness"]
        state = self._state
        code = self.brightness_code
        if code and state and code in state:
            if code == TUYA_CODES.BRIGHT_VALUE:
                return scale_value(state[code], t_range, range)
        return None

    def set_brightness(self, value: int):
        range = self._map_from_tuya["range_brightness"]
        t_range = self._map_to_tuya["range_brightness"]
        code = self.brightness_code
        if code and code == TUYA_CODES.BRIGHT_VALUE:
            v = scale_value(value, range, t_range)
            return {code: max(25, v)}
        return None

    @property
    def color_rgb(self):
        state = self._state
        code = self.color_code
        if code and state and code in state:
            if code == TUYA_CODES.COLOUR_DATA:
                hexvalue = state[code]
                if len(hexvalue) == 14:
                    r = int(hexvalue[0:2], 16)
                    g = int(hexvalue[2:4], 16)
                    b = int(hexvalue[4:6], 16)
                    return (r, g, b)
        return None

    @property
    def color_hsv(self):
        ranges = self._map_from_tuya["range_hsv"]
        t_ranges = self._map_to_tuya["range_hsv"]
        state = self._state
        code = self.color_code
        if code and state and code in state:
            if code == TUYA_CODES.COLOUR_DATA:
                hexvalue = state[code]
                if len(hexvalue) == 14:
                    h = int(hexvalue[7:10], 16)
                    s = int(hexvalue[10:12], 16)
                    v = int(hexvalue[12:14], 16)
                    ho = scale_value(
                        h,
                        t_ranges[0],
                        ranges[0],
                    )
                    so = scale_value(
                        s,
                        t_ranges[1],
                        ranges[1],
                    )
                    vo = scale_value(
                        v,
                        t_ranges[2],
                        ranges[2],
                    )
                    return (ho, so, vo)
        raise ValueError("HSV color is unsupported")

    def set_color_hsv(self, color: Tuple[int, int, int]):
        ranges = self._map_from_tuya["range_hsv"]
        t_ranges = self._map_to_tuya["range_hsv"]
        code = self.color_code
        if code and code == TUYA_CODES.COLOUR_DATA:
            hn = color[0] / float(ranges[0].max)
            sn = color[1] / float(ranges[1].max)
            vn = color[2] / float(ranges[2].max)
            rn, gn, bn = colorsys.hsv_to_rgb(hn, sn, vn)
            r = int(rn * 255)
            g = int(gn * 255)
            b = int(bn * 255)
            h = scale_value(color[0], ranges[0], t_ranges[0])
            s = scale_value(color[1], ranges[1], t_ranges[1])
            v = scale_value(color[2], ranges[2], t_ranges[2])
            hx = f"{r:02x}{g:02x}{b:02x}0{h:03x}{s:02x}{v:02x}"
            return {code: hx}
        return None

    @property
    def color_temp(self) -> Optional[int]:
        range = self._map_from_tuya["range_color_temperature"]
        t_range = self._map_to_tuya["range_color_temperature"]
        state = self._state
        code = self.color_temp_code
        if code and state and code in state:
            if code == TUYA_CODES.TEMP_VALUE:
                return scale_value(state[code], t_range, range)
        return None

    def set_color_temp(self, value: int):
        range = self._map_from_tuya["range_color_temperature"]
        t_range = self._map_to_tuya["range_color_temperature"]
        code = self.color_temp_code
        if code and code == TUYA_CODES.TEMP_VALUE:
            return {code: scale_value(value, range, t_range)}
        return None

    async def set_state(self, value: Mapping[str, Any]) -> bool:
        ip = self.ip
        if not ip:
            raise ValueError("No IP address")
        map = self._data["name_to_code"]
        if not map:
            raise ValueError(
                "No DPS codes found yet.Fetch state before setting it.")

        status = TuyaStatus({map[name]: value[name] for name in value})
        attempt = 0
        max_attempt = 2
        while attempt < max_attempt:
            try:
                await set_status(
                    id=self.id,
                    ip=ip,
                    local_key=self._local_key,
                    version=self._version,
                    value=status,
                )
                state = self._state
                if state:
                    state.update(value)
                return True
            except ConnectionError:
                attempt += 1
            except Exception as exc:
                _LOGGER.debug("set_state() exception=%r", exc)
                break
        return False

    async def fetch_state(self):
        ip = self.ip
        if not ip:
            raise ValueError("No IP address")
        try:
            status = await get_status(
                id=self.id,
                ip=ip,
                local_key=self._local_key,
                version=self._version,
            )
        except ConnectionError:
            _LOGGER.debug(f"Connection failed: {self.name}")
            self._failed_connect += 1
        except Exception as exc:
            _LOGGER.warn(f"Get status: {exc}")
        else:
            map = self._data["code_to_name"]
            map = map or self._create_code_name_maps(status)
            self._state = TuyaState(
                {map[code]: status[code] for code in status})
            self._failed_connect = 0
            _LOGGER.debug(
                f"Success on status {self.name} got {self._state}")

    def _create_code_name_maps(self, status: TuyaStatus):
        codes = list(status.keys())
        # get attributes from data as order is important
        attributes = self._data["attributes"]
        code_to_name = {code: name for code,
                        name in zip(codes, attributes)}
        name_to_code = {name: code for code,
                        name in code_to_name.items()}
        self._data["code_to_name"] = code_to_name
        self._data["name_to_code"] = name_to_code
        return code_to_name
