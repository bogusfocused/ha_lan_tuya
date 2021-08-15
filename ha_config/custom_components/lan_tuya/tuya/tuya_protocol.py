import base64
import binascii
import json
import logging
import struct
import time
from asyncio import AbstractEventLoop, Protocol, Transport
from asyncio.transports import BaseTransport
from hashlib import md5
from typing import Any, List, Literal, Mapping, NamedTuple, Optional, TypedDict, cast

from Crypto.Cipher import AES

_LOGGER = logging.getLogger(__name__)

log = _LOGGER

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)  # Uncomment to Debug

DeviceType = Literal["LIGHT", "SWITCH", "UNKNOWN"]
TUYA_REGIONS = Literal["us", "eu", "in", "cn"]

class DeviceData(TypedDict):
    name: str
    id: str
    local_key: str
    uid: str
    device_type: DeviceType
    attributes: List[str]
    model: str
    product_name: str
    product_id: str
    ip: Optional[str]
    version: str
    product_key: str
    mac: str
    online: bool
    code_to_name: Optional[Mapping[str, str]]
    name_to_code: Optional[Mapping[str, str]]


class AESCipher(object):
    def __init__(self, key: bytes):
        self.bs = 16
        self.key = key

    def encrypt(self, raw: bytes, use_base64: bool = True):
        raw = self._pad(raw)
        cipher = AES.new(self.key, mode=AES.MODE_ECB)
        crypted_text = cipher.encrypt(raw)

        if use_base64:
            return base64.b64encode(crypted_text)
        else:
            return crypted_text

    def decrypt(self, enc, use_base64: bool = True):
        if use_base64:
            enc = base64.b64decode(enc)

        cipher = AES.new(self.key, AES.MODE_ECB)
        raw = cipher.decrypt(enc)
        return self._unpad(raw).decode("utf-8")

    def _pad(self, s):
        padnum = self.bs - len(s) % self.bs
        return s + padnum * chr(padnum).encode()

    @staticmethod
    def _unpad(s: bytes):
        return s[: -ord(s[len(s) - 1 :])]


# Tuya Command Types
UDP = 0  # HEAT_BEAT_CMD
AP_CONFIG = 1  # PRODUCT_INFO_CMD
ACTIVE = 2  # WORK_MODE_CMD
BIND = 3  # WIFI_STATE_CMD - wifi working status
RENAME_GW = 4  # WIFI_RESET_CMD - reset wifi
RENAME_DEVICE = 5  # WIFI_MODE_CMD - Choose smartconfig/AP mode
UNBIND = 6  # DATA_QUERT_CMD - issue command
CONTROL = 7  # STATE_UPLOAD_CMD
STATUS = 8  # STATE_QUERY_CMD
HEART_BEAT = 9
DP_QUERY = 10  # UPDATE_START_CMD - get data points
QUERY_WIFI = 11  # UPDATE_TRANS_CMD
TOKEN_BIND = 12  # GET_ONLINE_TIME_CMD - system time (GMT)
CONTROL_NEW = 13  # FACTORY_MODE_CMD
ENABLE_WIFI = 14  # WIFI_TEST_CMD
DP_QUERY_NEW = 16
SCENE_EXECUTE = 17
UPDATEDPS = 18  # Request refresh of DPS
UDP_NEW = 19
AP_CONFIG_NEW = 20
GET_LOCAL_TIME_CMD = 28
WEATHER_OPEN_CMD = 32
WEATHER_DATA_CMD = 33
STATE_UPLOAD_SYN_CMD = 34
STATE_UPLOAD_SYN_RECV_CMD = 35
HEAT_BEAT_STOP = 37
STREAM_TRANS_CMD = 38
GET_WIFI_STATUS_CMD = 43
WIFI_CONNECT_TEST_CMD = 44
GET_MAC_CMD = 45
GET_IR_STATUS_CMD = 46
IR_TX_RX_TEST_CMD = 47
LAN_GW_ACTIVE = 240
LAN_SUB_DEV_REQUEST = 241
LAN_DELETE_SUB_DEV = 242
LAN_REPORT_SUB_DEV = 243
LAN_SCENE = 244
LAN_PUBLISH_CLOUD_CONFIG = 245
LAN_PUBLISH_APP_CONFIG = 246
LAN_EXPORT_APP_CONFIG = 247
LAN_PUBLISH_SCENE_PANEL = 248
LAN_REMOVE_GW = 249
LAN_CHECK_GW_UPDATE = 250
LAN_GW_UPDATE = 251
LAN_SET_GW_CHANNEL = 252

# Protocol Versions and Headers
PROTOCOL_VERSION_BYTES_31 = b"3.1"
PROTOCOL_VERSION_BYTES_33 = b"3.3"
PROTOCOL_33_HEADER = PROTOCOL_VERSION_BYTES_33 + 12 * b"\x00"
MESSAGE_HEADER_FMT = ">4I"  # 4*uint32: prefix, seqno, cmd, length
MESSAGE_RECV_HEADER_FMT = ">5I"  # 4*uint32: prefix, seqno, cmd, length, retcode
MESSAGE_END_FMT = ">2I"  # 2*uint32: crc, suffix
PREFIX_VALUE = 0x000055AA
SUFFIX_VALUE = 0x0000AA55
SUFFIX_BIN = b"\x00\x00\xaaU"


PORT = 6668

CONNECTION_TIMEOUT = 5


# Tuya Device Dictionary - Commands and Payload Template
# See requests.json payload at http s://github.com/codetheweb/tuyapi
# 'default' devices require the 0a command for the DP_QUERY request
# 'device22' devices require the 0d command for the DP_QUERY request and a list of
#            dps used set to Null in the request payload
TUYA_PREFIX = {
    "default": {
        "prefix": "000055aa00000000000000",
        # Next byte is command "hexByte" + length of remaining payload + command + suffix
        # (unclear if multiple bytes used for length, zero padding implies could be more
        # than one byte)
        "suffix": "000000000000aa55",
    },
    "device22": {"prefix": "000055aa00000000000000", "suffix": "000000000000aa55"},
}


class CommanData(TypedDict):
    hexByte: str
    command: Mapping[str, Any]


TUYA_COMMANDS: Mapping[str, Mapping[int, CommanData]] = {
    # Default Device
    "default": {
        AP_CONFIG: {  # [BETA] Set Control Values on Device
            "hexByte": "01",
            "command": {"gwId": "", "devId": "", "uid": "", "t": ""},
        },
        CONTROL: {  # Set Control Values on Device
            "hexByte": "07",
            "command": {"devId": "", "uid": "", "t": ""},
        },
        STATUS: {  # Get Status from Device
            "hexByte": "08",
            "command": {"gwId": "", "devId": ""},
        },
        HEART_BEAT: {"hexByte": "09", "command": {"gwId": "", "devId": ""}},
        DP_QUERY: {  # Get Data Points from Device
            "hexByte": "0a",
            "command": {"gwId": "", "devId": "", "uid": "", "t": ""},
        },
        CONTROL_NEW: {"hexByte": "0d", "command": {"devId": "", "uid": "", "t": ""}},
        DP_QUERY_NEW: {"hexByte": "0f", "command": {"devId": "", "uid": "", "t": ""}},
        UPDATEDPS: {"hexByte": "12", "command": {"dpId": [18, 19, 20]}},
    },
    # Special Case Device with 22 character ID - Some of these devices
    # Require the 0d command as the DP_QUERY status request and the list of
    # dps requested payload
    "device22": {
        DP_QUERY: {  # Get Data Points from Device
            "hexByte": "0d",  # Uses CONTROL_NEW command for some reason
            "command": {"devId": "", "uid": "", "t": ""},
        },
        CONTROL: {  # Set Control Values on Device
            "hexByte": "07",
            "command": {"devId": "", "uid": "", "t": ""},
        },
        HEART_BEAT: {"hexByte": "09", "command": {"gwId": "", "devId": ""}},
        UPDATEDPS: {
            "hexByte": "12",
            "command": {"dpId": [18, 19, 20]},
        },
    },
}


class TuyaRequest(NamedTuple):
    seqno: int
    cmd: int
    payload: bytes


class TuyaResponse(NamedTuple):
    seqno: int
    cmd: int
    payload: bytes
    retcode: int
    crc: int


class TuyaError(RuntimeError):
    ...


class TuyaProtocol(Protocol):
    sequence_number: int = 0

    def __init__(
        self,
        *,
        loop: AbstractEventLoop,
        id: str,
        local_key: str,
        version: str,
        command: int,
        data: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__()
        TuyaProtocol.sequence_number += 1
        sqno = TuyaProtocol.sequence_number
        self._local_key = local_key
        self._version = version
        self._outcome = loop.create_future()
        timestamp = str(int(time.time()))
        self._payload = self._generate_payload(
            command=command,
            id=id,
            data=data,
            local_key=local_key,
            version=version,
            timestamp=timestamp,
            sequence_number=sqno,
        )
        self._transport: Optional[Transport] = None

    @property
    def outcome(self):
        return self._outcome

    def connection_made(self, transport: BaseTransport) -> None:
        payload = self._payload
        transport = cast(Transport, transport)
        self._transport = transport
        if payload:
            transport.write(payload)

    def data_received(self, data: bytes) -> None:
        if self._outcome.done():
            return
        # Unpack Message into TuyaMessage format
        # and payload decrypted
        try:
            msg = self.unpack_message(data)
            if msg:
                payload = self._decode_payload(
                    msg.payload, local_key=self._local_key, version=self._version
                )
                result = json.loads(payload) if payload else None
                self._outcome.set_result(result)
            else:
                self._outcome.set_result(None)
        except Exception as e:
            self._outcome.set_exception(e)

    def connection_lost(self, exc):
        if self._outcome.done():
            return
        if isinstance(exc, Exception):
            self._outcome.set_exception(exc)
        else:
            self._outcome.cancel()

    def eof_received(self):
        if self._outcome.done():
            return
        self._outcome.set_exception(TuyaError())

    @staticmethod
    def _pack_message(msg: TuyaRequest) -> bytes:
        """Pack a TuyaMessage into bytes."""
        # Create full message excluding CRC and suffix
        buffer = (
            struct.pack(
                MESSAGE_HEADER_FMT,
                PREFIX_VALUE,
                msg.seqno,
                msg.cmd,
                len(msg.payload) + struct.calcsize(MESSAGE_END_FMT),
            )
            + msg.payload
        )
        # Calculate CRC, add it together with suffix
        buffer += struct.pack(
            MESSAGE_END_FMT, binascii.crc32(buffer) & 0xFFFFFFFF, SUFFIX_VALUE
        )
        return buffer

    @staticmethod
    def unpack_message(data: bytes):
        """Unpack bytes into a TuyaMessage."""
        header_len = struct.calcsize(MESSAGE_RECV_HEADER_FMT)
        end_len = struct.calcsize(MESSAGE_END_FMT)

        _, seqno, cmd, _, retcode = struct.unpack(
            MESSAGE_RECV_HEADER_FMT, data[:header_len]
        )
        payload = data[header_len:-end_len]
        crc, _ = struct.unpack(MESSAGE_END_FMT, data[-end_len:])
        return TuyaResponse(seqno, cmd, payload, retcode, crc)

    @staticmethod
    def _generate_payload(
        *,
        command: int,
        id: str,
        local_key: str,
        version: str,
        sequence_number: int,
        timestamp: str,
        data: Optional[Mapping[str, Any]] = None,
    ):
        """
        Generate the payload to send.
        """
        json_data = TUYA_COMMANDS["default"][command]["command"]  # type: ignore
        json_data = {**json_data}  # create a copy to edit.
        command_hb: str = TUYA_COMMANDS["default"][command]["hexByte"]  # type: ignore

        if "gwId" in json_data:
            json_data["gwId"] = id
        if "devId" in json_data:
            json_data["devId"] = id
        if "uid" in json_data:
            json_data["uid"] = id
        if "t" in json_data:
            json_data["t"] = timestamp

        if data is not None:
            if "dpId" in json_data:
                json_data["dpId"] = data
            else:
                json_data["dps"] = data
        if command_hb == "0d":  # CONTROL_NEW
            raise NotImplementedError()
            # json_data['dps'] = self.dps_to_request

        # Create byte buffer from hex data
        message = json.dumps(json_data)
        # if spaces are not removed device does not respond!
        message = message.replace(" ", "")
        payload = message.encode("utf-8")
        # log.debug('building payload=%r', payload)

        blocal_key = local_key.encode("utf-8")
        if version == "3.3":
            # expect to connect and then disconnect to set new
            cipher = AESCipher(blocal_key)
            payload = cipher.encrypt(payload, False)
        if command_hb != "0a" and command_hb != "12":
            # add the 3.3 header
            payload = PROTOCOL_33_HEADER + payload
        elif command == CONTROL:
            # need to encrypt
            cipher = AESCipher(blocal_key)
            payload = cipher.encrypt(payload)
            preMd5String = (
                b"data="
                + payload
                + b"||lpv="
                + PROTOCOL_VERSION_BYTES_31
                + b"||"
                + blocal_key
            )
            m = md5()
            m.update(preMd5String)
            hexdigest = m.hexdigest()
            # some tuya libraries strip 8: to :24
            payload = (
                PROTOCOL_VERSION_BYTES_31
                + hexdigest[8:][:16].encode("latin1")
                + payload
            )

        # create Tuya message packet
        msg = TuyaRequest(sequence_number, int(command_hb, 16), payload)
        buffer = TuyaProtocol._pack_message(msg)
        # log.debug('payload generated=%r', buffer)
        return buffer

    @staticmethod
    def _decode_payload(payload: bytes, *, local_key: str, version: str):
        if not payload:
            return ""
        blocal_key = local_key.encode("utf-8")
        version = version
        cipher = AESCipher(blocal_key)
        if payload.startswith(PROTOCOL_VERSION_BYTES_31):
            # Received an encrypted payload
            # Remove version header
            payload = payload[len(PROTOCOL_VERSION_BYTES_31) :]
            # Decrypt payload
            # Remove 16-bytes of MD5 hexdigest of payload
            return cipher.decrypt(payload[16:])
        elif version == "3.3":
            if payload.startswith(PROTOCOL_VERSION_BYTES_33):
                payload = payload[len(PROTOCOL_33_HEADER) :]
                # log.debug('removing 3.3=%r', payload)
            try:
                # log.debug("decrypting=%r", payload)
                return cipher.decrypt(payload, False) if payload else ""
            except:
                log.debug("incomplete payload=%r", payload)
                raise
        elif payload.startswith(b"{"):
            return payload.decode()
        else:
            log.debug("Unexpected payload=%r", payload)
            raise ValueError("Unexpected payload")




class TUYA_CODES:

    SWITCH_1 = "switch_1"
    # "code": "switch_led",
    # "desc": "switch led",
    # "name": "switch led",
    # "type": "Boolean",
    # "values": "{}"
    SWITCH_LED = "switch_led"

    # "code": "work_mode",
    # "desc": "work mode",
    # "name": "work mode",
    # "type": "Enum",
    # "values": "{\"range\":[\"white\",\"colour\",\"scene\",\"music\",\"scene_1\",\"scene_2\",\"scene_3\",\"scene_4\"]}"
    WORK_MODE = "work_mode"
    # "code": "bright_value_v2",
    # "desc": "bright value v2",
    # "name": "bright value v2",
    # "type": "Integer",
    # "values": "{\"min\":10,\"scale\":0,\"unit\":\"\",\"max\":1000,\"step\":1}"
    BRIGHT_VALUE_V2 = "bright_value_v2"

    # "code": "temp_value_v2",
    # "desc": "temp value v2",
    # "name": "temp value v2",
    # "type": "Integer",
    # "values": "{\"min\":0,\"scale\":0,\"unit\":\"\",\"max\":1000,\"step\":1}"
    TEMP_VALUE_V2 = "temp_value_v2"
    # {
    # "code": "colour_data_v2",
    # "desc": "colour data v2",
    # "name": "colour data v2",
    # "type": "Json",
    # "values": "{}"
    # },
    # {
    # "code": "scene_data_v2",
    # "desc": "scene data v2",
    # "name": "scene data v2",
    # "type": "Json",
    # "values": "{}"
    # },
    # {
    # "code": "music_data",
    # "desc": "music data",
    # "name": "music data",
    # "type": "Json",
    # "values": "{}"
    # },
    # {
    # "code": "control_data",
    # "desc": "control data",
    # "name": "control data",
    # "type": "Json",
    # "values": "{}"
    # },
    # {
    # "code": "countdown_1",
    # "desc": "countdown 1",
    # "name": "countdown 1",
    # "type": "Integer",
    # "values": "{\"unit\":\"\",\"min\":0,\"max\":86400,\"scale\":0,\"step\":1}"
    # },
    COUNTDOWN_1 = "countdown_1"
    # {
    # "code": "bright_value",
    # "desc": "bright value",
    # "name": "bright value",
    # "type": "Integer",
    # "values": "{\"min\":25,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}"
    # },
    BRIGHT_VALUE = "bright_value"
    # {
    # "code": "temp_value",
    # "desc": "temp value",
    # "name": "temp value",
    # "type": "Integer",
    # "values": "{\"min\":0,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}"
    # },
    TEMP_VALUE = "temp_value"
    # {
    # "code": "flash_scene_1",
    # "desc": "flash scene 1",
    # "name": "flash scene 1",
    # "type": "Json",
    # "values": "{\"h\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":360,\"step\":1},\"s\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1},\"v\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}}"
    # },
    # {
    # "code": "flash_scene_2",
    # "desc": "flash scene 2",
    # "name": "flash scene 2",
    # "type": "Json",
    # "values": "{\"h\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":360,\"step\":1},\"s\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1},\"v\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}}"
    # },
    # {
    # "code": "flash_scene_3",
    # "desc": "flash scene 3",
    # "name": "flash scene 3",
    # "type": "Json",
    # "values": "{\"h\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":360,\"step\":1},\"s\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1},\"v\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}}"
    # },
    # {
    # "code": "flash_scene_4",
    # "desc": "flash scene 4",
    # "name": "flash scene 4",
    # "type": "Json",
    # "values": "{\"h\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":360,\"step\":1},\"s\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1},\"v\":{\"min\":1,\"scale\":0,\"unit\":\"\",\"max\":255,\"step\":1}}"
    # },
    # {
    # "code": "scene_select",
    # "desc": "scene select",
    # "name": "scene select",
    # "type": "Enum",
    # "values": "{\"range\":[\"1\",\"2\",\"3\",\"4\",\"5\"]}"
    # },
    # {
    # "code": "read_time",
    # "desc": "read time",
    # "name": "read time",
    # "type": "Integer",
    # "values": "{\"unit\":\"minute\",\"min\":1,\"max\":60,\"scale\":0,\"step\":1}"
    # },
    # {
    # "code": "rest_time",
    # "desc": "rest time",
    # "name": "rest time",
    # "type": "Integer",
    # "values": "{\"unit\":\"minute\",\"min\":1,\"max\":60,\"scale\":0,\"step\":1}"
    # },
    # {
    # "code": "switch_health_read",
    # "desc": "switch health read",
    # "name": "switch health read",
    # "type": "Boolean",
    # "values": "{}"
    # },
    # {
    # "code": "colour_data",
    # "desc": "colour data",
    # "name": "colour data",
    # "type": "Json",
    # "values": {"h":{"min":1,"scale":0,"unit":"","max":360,"step":1},
    #           "s":{"min":1,"scale":0,"unit":"","max":255,"step":1},
    #           "v":{"min":1,"scale":0,"unit":"","max":255,"step":1}}
    # },
    COLOUR_DATA = "colour_data"

    # {
    # "code": "scene_data",
    # "desc": "scene data",
    # "name": "scene data",
    # "type": "Json",
    # "values": "{}"
    # },
    # {
    # "code": "rhythm_mode",
    # "desc": "rhythm mode",
    # "name": "rhythm mode",
    # "type": "Raw",
    # "values": "{\"maxlen\":255}"
    # },
    # {
    # "code": "wakeup_mode",
    # "desc": "wakeup mode",
    # "name": "wakeup mode",
    # "type": "Raw",
    # "values": "{\"maxlen\":255}"
    # },
    # {
    # "code": "power_memory",
    # "desc": "power memory",
    # "name": "power memory",
    # "type": "Raw",
    # "values": "{\"maxlen\":255}"
    # },
    # {
    # "code": "debug_data",
    # "desc": "debug data",
    # "name": "debug data",
    # "type": "String",
    # "values": "{\"maxlen\":255}"
    # },
    # {
    # "code": "sleep_mode",
    # "desc": "sleep mode",
    # "name": "sleep mode",
    # "type": "Raw",
    # "values": "{\"maxlen\":255}"
    # }
