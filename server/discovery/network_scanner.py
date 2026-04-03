"""Network scanner — subnet detection, ping sweep, ARP table harvest."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import re
from typing import Callable, Awaitable

log = logging.getLogger("discovery.network")

# Platform detection
_IS_WINDOWS = platform.system() == "Windows"


# Adapter name patterns for virtual/non-physical interfaces.
# These never host real AV devices and just slow down scans.
_VIRTUAL_ADAPTER_PATTERNS = re.compile(
    r"hyper-v|virtualbox|vmware|vmnet|docker|veth|wsl|"
    r"vEthernet|ham|loopback|pseudo|teredo|isatap|"
    r"vpn|tap-|tun\d|wireguard|nordlynx|mullvad",
    re.IGNORECASE,
)


def get_local_subnets() -> list[str]:
    """Detect subnets from local network interfaces.

    Returns list of CIDR strings, e.g., ["192.168.1.0/24"].
    Excludes loopback, link-local, and virtual adapter addresses.
    """
    subnets: list[str] = []
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            if _VIRTUAL_ADAPTER_PATTERNS.search(adapter.nice_name):
                log.debug("Skipping virtual adapter: %s", adapter.nice_name)
                continue
            for ip_info in adapter.ips:
                if not isinstance(ip_info.ip, str):
                    continue  # Skip IPv6 tuples
                addr = ip_info.ip
                if addr.startswith("127.") or addr.startswith("169.254."):
                    continue
                try:
                    prefix = ip_info.network_prefix
                    network = ipaddress.IPv4Network(f"{addr}/{prefix}", strict=False)
                    cidr = str(network)
                    if cidr not in subnets:
                        subnets.append(cidr)
                except (ValueError, TypeError):
                    continue
    except ImportError:
        log.warning("ifaddr not installed -- cannot auto-detect subnets")
    except OSError as exc:  # ifaddr adapter enumeration can raise on network errors
        log.warning("Failed to detect subnets: %s", exc)

    return subnets


def _parse_cidr(cidr: str) -> list[str]:
    """Expand a CIDR range to a list of host IPs (excluding network and broadcast)."""
    try:
        network = ipaddress.IPv4Network(cidr, strict=False)
    except ValueError:
        log.warning("Invalid CIDR: %s", cidr)
        return []

    # For very large subnets, cap to avoid accidental scans of huge ranges
    if network.prefixlen < 20:
        log.warning("Subnet %s too large (/%d), skipping. Max is /20.", cidr, network.prefixlen)
        return []

    return [str(ip) for ip in network.hosts()]


async def ping_sweep(
    subnets: list[str],
    concurrency: int = 50,
    timeout: float = 1.0,
    on_found: Callable[[str], Awaitable[None]] | None = None,
) -> list[str]:
    """Ping all addresses in the given subnets. Returns list of responding IPs.

    Uses system ping command (no elevated privileges required).
    Runs up to ``concurrency`` pings simultaneously.
    """
    all_ips: list[str] = []
    for cidr in subnets:
        all_ips.extend(_parse_cidr(cidr))

    if not all_ips:
        return []

    log.info("Ping sweep: %d addresses across %d subnet(s)", len(all_ips), len(subnets))

    alive: list[str] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def _ping_one(ip: str) -> None:
        async with semaphore:
            if await _ping(ip, timeout):
                alive.append(ip)
                if on_found:
                    await on_found(ip)

    await asyncio.gather(*[_ping_one(ip) for ip in all_ips])
    log.info("Ping sweep complete: %d/%d hosts alive", len(alive), len(all_ips))
    return sorted(alive, key=lambda x: ipaddress.IPv4Address(x))


async def _ping(ip: str, timeout: float = 1.0) -> bool:
    """Ping a single IP address. Returns True if it responds."""
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout + 2)
        return proc.returncode == 0
    except (asyncio.TimeoutError, OSError):
        return False


async def harvest_arp_table() -> dict[str, str]:
    """Read the system ARP table. Returns {ip: mac_address}.

    MAC addresses are normalized to lowercase colon-separated format.
    """
    try:
        if _IS_WINDOWS:
            return await _harvest_arp_windows()
        else:
            return await _harvest_arp_linux()
    except Exception as exc:
        log.warning("Failed to read ARP table: %s", exc)
        return {}


async def _harvest_arp_windows() -> dict[str, str]:
    """Parse Windows 'arp -a' output."""
    proc = await asyncio.create_subprocess_exec(
        "arp", "-a",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode("utf-8", errors="replace")

    result: dict[str, str] = {}
    # Windows format: "  192.168.1.1          00-aa-bb-cc-dd-ee     dynamic"
    pattern = re.compile(
        r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+"
        r"([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-]"
        r"[0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})\s+"
        r"(dynamic|static)",
        re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        ip = match.group(1)
        mac = match.group(2).lower().replace("-", ":")
        if mac != "ff:ff:ff:ff:ff:ff":
            result[ip] = mac
    return result


async def _harvest_arp_linux() -> dict[str, str]:
    """Parse Linux 'ip neigh' output."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "neigh",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode("utf-8", errors="replace")

    result: dict[str, str] = {}
    # Linux format: "192.168.1.1 dev eth0 lladdr 00:aa:bb:cc:dd:ee REACHABLE"
    pattern = re.compile(
        r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+"
        r"dev\s+\S+\s+lladdr\s+"
        r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:"
        r"[0-9a-fA-F]{2}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2})"
    )
    for match in pattern.finditer(text):
        ip = match.group(1)
        mac = match.group(2).lower()
        if mac != "ff:ff:ff:ff:ff:ff":
            result[ip] = mac
    return result
