import asyncio
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple, TypeVar, cast

mac_strip_regex = re.compile(r"[^a-f0-9]")
arp_output_regx = re.compile(
    r"^\s*(?P<ip>[0-9.]*)\s*(?P<mac>[-a-f0-9]*)\s*(?P<type>\S*)\s*$", flags=re.M
)


def normalize_mac(mac: str):
    return mac_strip_regex.sub("", mac.lower())


async def get_arp_table() -> Mapping[str, Tuple[str, str]]:
    proc = await asyncio.create_subprocess_exec(
        "arp", "-a", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    retcode = proc.returncode
    if retcode != 0:
        raise ValueError(stderr.decode("ascii"))
    output = stdout.decode("ascii")
    matches: List[Tuple[str, str, str]] = arp_output_regx.findall(output)
    arp_table = {}
    for ip, mac, type in matches:
        if ip and mac and type:
            arp_table[normalize_mac(mac)] = (ip, type)
    return arp_table

TK = TypeVar("TK")
TV = TypeVar("TV")



