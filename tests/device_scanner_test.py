import sys
import pytest
import asyncio
import logging

_LOGGER = logging.getLogger(__name__)
from .tuya.device_scanner import DeviceScanner, ScanResult
if 'win32' in sys.platform:
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def on_device_found( data: ScanResult):
    _LOGGER.debug(data)

@pytest.mark.asyncio
async def test_scan():
    loop = asyncio.get_running_loop()
    try:
        scanner = DeviceScanner()
        remove = await scanner.add_listener(on_device_found)
        await asyncio.sleep(100)
        remove()
    except Exception as exc:
        print(exc)
