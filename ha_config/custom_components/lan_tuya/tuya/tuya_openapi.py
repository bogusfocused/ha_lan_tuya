import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Literal, Mapping, Optional, Tuple, TypedDict

from aiohttp import ClientSession
from aiohttp.client import ClientTimeout
from yarl import URL

from .tuya_protocol import DeviceData, TUYA_REGIONS, TuyaError
from .utils import normalize_mac

_LOGGER = logging.getLogger(__name__)

"""
Connects to cloud to download device information
"""


async def download_devices_info(
    session: ClientSession,
    *,
    api_region: Literal["us", "eu", "in", "cn"],
    api_key: str,
    api_secret: str,
    timeout: Optional[ClientTimeout] = None,
    **_,
):
    rdevices, app_id = await _get_all_devices(
        session,
        api_key=api_key,
        api_region=api_region,
        api_secret=api_secret,
        timeout=timeout,
    )
    devices: Mapping[str, DeviceData] = {
        id: {
            "name": dev["name"],
            "id": dev["id"],
            "local_key": dev["local_key"],
            "device_type": dev["category"],
            "uid": dev["uid"],
            "attributes": [c["code"] for c in dev["status"]],
            "model": dev.get("model", ""),
            "ip": None,
            "product_name": dev.get("product_name", ""),
            "product_id": dev.get("product_id", ""),
            "version": dev.get("version", ""),
            "product_key": dev.get("product_key", ""),
            "mac": dev.get("mac", ""),
            "online": dev.get("online", False),
            "code_to_name": None,
            "name_to_code": None,
        }
        for id, dev in rdevices.items()
    }
    return devices, app_id


async def _get_all_devices(
    session: ClientSession,
    *,
    api_key: str,
    api_secret: str,
    api_region: Optional[TUYA_REGIONS] = "us",
    timeout: Optional[ClientTimeout] = None,
) -> Tuple[Mapping[str, Mapping[str, Any]], str]:

    # Get Oauth Token from tuyaPlatform
    uri = "v1.0/token?grant_type=1"
    response = await _openapi(
        session,
        apiKey=api_key,
        apiRegion=api_region,
        apiSecret=api_secret,
        uri=uri,
        timeout=timeout,
        token=None,
    )

    _raise_error(response)
    auth = response["result"]
    access_token = auth["access_token"]

    uri = "v1.0/iot-01/associated-users/devices?last_row_key="
    response = await _openapi(
        session,
        apiKey=api_key,
        apiRegion=api_region,
        apiSecret=api_secret,
        uri=uri,
        timeout=timeout,
        token=access_token,
    )
    _raise_error(response)
    result: Dict[str, Dict[str, Any]] = {}
    details: List = response["result"]["devices"]
    for d in details:
        result.setdefault(d["id"], {}).update(d)
    device_ids: List[str] = [dev["id"] for dev in details]
    uri = "v1.0/devices/factory-infos?device_ids=" + ",".join(device_ids)
    response = await _openapi(
        session,
        apiKey=api_key,
        apiRegion=api_region,
        apiSecret=api_secret,
        uri=uri,
        timeout=timeout,
        token=access_token,
    )
    _raise_error(response)
    factory: List = response["result"]
    for d in factory:
        mac = d.get("mac", None)
        d["mac"] = normalize_mac(mac)
        result.setdefault(d["id"], {}).update(**d)

    return result, auth["uid"]


async def _openapi(
    session: ClientSession,
    *,
    apiRegion,
    apiKey: str,
    apiSecret: str,
    uri: str,
    timeout: Optional[ClientTimeout] = None,
    token=None,
):
    """Tuya IoT Platform Data Access"""
    url = URL("https://openapi.tuya%s.com/%s" % (apiRegion, uri))
    now = int(time.time() * 1000)
    if token == None:
        payload = apiKey + str(now)
    else:
        payload = apiKey + token + str(now)

    # Sign Payload
    signature = (
        hmac.new(
            apiSecret.encode("utf-8"),
            msg=payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        )
        .hexdigest()
        .upper()
    )

    # Create Header Data
    headers = {}
    headers["client_id"] = apiKey
    headers["sign_method"] = "HMAC-SHA256"
    headers["t"] = str(now)
    headers["sign"] = signature
    if token != None:
        headers["access_token"] = token

    async with session.get(url, headers=headers, timeout=timeout) as response:
        return await response.json()


def _raise_error(response: Mapping[str, Any]):
    if not response["success"]:
        raise TuyaError(response["msg"])
    return None
