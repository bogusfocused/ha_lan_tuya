import asyncio
import logging
import socket
from typing import (
    Any,
    Dict,
    Mapping,
    NewType,
    Optional,
)

from .tuya_protocol import (
    CONTROL,
    DP_QUERY,
    PORT,
    TuyaProtocol,
)

_LOGGER = logging.getLogger(__name__)

log = _LOGGER
TuyaStatus = NewType("TuyaStatus", Mapping[str, Any])
TuyaState = NewType("TuyaState", Dict[str, Any])

async def _lan_control(
    *,
    id: str,
    command: int,
    ip: str,
    local_key: str,
    version: str,
    data: Optional[Mapping[str, str]] = None,
):

    loop = asyncio.get_running_loop()
    proto = TuyaProtocol(
        loop=loop,
        command=command,
        data=data,
        id=id,
        local_key=local_key,
        version=version,
    )
    connection = loop.create_connection(
        lambda: proto,
        host=ip,
        port=PORT,
        family=socket.AF_INET,
        proto=socket.IPPROTO_TCP,
    )  # type: ignore

    t, _ = await connection
    try:
        return await proto.outcome
    finally:
        t.close()


async def status(
    *,
    id: str,
    local_key: str,
    version: str,
    ip: str,
) -> TuyaStatus:
    """Return device status."""
    data = await _lan_control(
        command=DP_QUERY,
        id=id,
        ip=ip,
        local_key=local_key,
        version=version,
    )
    if data and "dps" in data:
        # log.debug('status() received data=%r', data)
        return TuyaStatus(data["dps"])
    raise ValueError("Unexpected")


async def set_status(
    *, ip: str, id: str, local_key: str, version: str, value: TuyaStatus
):
    """
    Set status of the device .
    """
    log.debug("set_status sending data=%r", value)
    data = await _lan_control(
        command=CONTROL,
        id=id,
        ip=ip,
        local_key=local_key,
        version=version,
        data=value,
    )

    return data
