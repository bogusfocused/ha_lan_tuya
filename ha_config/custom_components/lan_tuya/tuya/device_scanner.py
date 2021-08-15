import asyncio
import json
import logging
import random
import socket
from asyncio import DatagramProtocol, DatagramTransport
from hashlib import md5
import string
from typing import AbstractSet, Any, Callable, Dict, Final, NamedTuple, NewType, Optional, Set, Tuple, TypedDict

from Crypto.Cipher import AES

_LOGGER = logging.getLogger("custom_components.lan_tuya")

Address = Tuple[str, int]
PORT_31 = 6666
PORT_33 = 6667
Id = NewType("Id", str)


class ScanResult(NamedTuple):
    ip: str
    gwId: Id
    active: int
    ability: int
    mode: int
    encrypt: bool
    productKey: str
    version: str


DeviceFoundCallback = Callable[[ScanResult], None]


class v31PresenseProto(DatagramProtocol):
    def __init__(
        self,
        callback:  Callable[[ScanResult], None],
    ):
        self._callback = callback
        self.transport: Optional[DatagramTransport] = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Address):
        ip = addr[0]
        result = data[20:-8]
        message = self.decode(result)
        json_data = json.loads(message)
        scan_result = ScanResult(**json_data)
        try:
            self._callback(scan_result)
        except:
            pass

    def decode(self, data: bytes):
        return data.decode()


class v33PresenseProto(v31PresenseProto):
    def __init__(
        self,
        callback:  Callable[[ScanResult], None],
    ):
        super().__init__(callback)

    def decode(self, data: bytes):
        return self.decrypt_udp(data)

    # UDP packet payload decryption - credit to tuya-convert
    udpkey = md5(b"yGAdlopoPVldABfn").digest()

    def unpad(self, s: bytes):
        return s[: -ord(s[len(s) - 1:])]

    def decrypt(self, msg: bytes, key: bytes):
        return self.unpad(AES.new(key, AES.MODE_ECB).decrypt(msg)).decode()

    def decrypt_udp(self, msg: bytes):
        return self.decrypt(msg, self.udpkey)


def generate_random_key(check_dict: Dict[str, Any], length: int = 5):
    while True:
        randomstr = "".join(
            random.choices(string.ascii_letters + string.digits, k=length)
        )
        if randomstr not in check_dict:
            return randomstr


class DeviceScanner:
    def __init__(self) -> None:
        self._seen: Final[Set[ScanResult]] = set()
        self.on_device_found: Final[Dict[str, DeviceFoundCallback]] = {}
        self.proto_31: Final = v31PresenseProto(self._on_device_found_wrapper)
        self.proto_33: Final = v33PresenseProto(self._on_device_found_wrapper)
        self.is_running: bool = False

    @property
    def seen(self) -> AbstractSet[ScanResult]:
        return self._seen.copy()

    def _on_device_found_wrapper(self, data: ScanResult):
        if data not in self._seen:
            self._seen.add(data)
            for callback in self.on_device_found.values():
                callback(data)

    async def add_listener(self, cb: DeviceFoundCallback) -> Callable[[], None]:
        key = generate_random_key(self.on_device_found)
        self.on_device_found[key] = cb
        await self._start()

        def remove():
            self.on_device_found.pop(key, None)
            if not self.on_device_found:
                self._stop()
        return remove

    def _stop(self):
        if not self.is_running:
            return
        try:
            t1 = self.proto_31.transport
            t2 = self.proto_33.transport
            [t.close() for t in [t1, t2] if t and not t.is_closing()]
        finally:
            self.is_running = False
        _LOGGER.debug("Stoping scan")

    async def _start(self):
        if self.is_running:
            _LOGGER.debug("Skipping scan because its already running")
            return
        _LOGGER.debug("Starting scan")

        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self.proto_31,
            local_addr=("0.0.0.0", PORT_31),
            allow_broadcast=True,
            proto=socket.IPPROTO_UDP,
            family=socket.AF_INET,
        )

        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: self.proto_33,
            local_addr=("0.0.0.0", PORT_33),
            allow_broadcast=True,
            proto=socket.IPPROTO_UDP,
            family=socket.AF_INET,
        )
        self.is_running = True
