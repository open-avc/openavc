"""Network scanner — subnet detection, ping sweep, ARP table harvest, NetBIOS."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import platform
import re
import socket
import struct
from typing import Callable, Awaitable

log = logging.getLogger("discovery.network")

# Platform detection
_IS_WINDOWS = platform.system() == "Windows"


# Adapter name patterns for virtual/non-physical interfaces.
# These never host real AV devices and just slow down scans.
_VIRTUAL_ADAPTER_PATTERNS = re.compile(
    r"hyper-v|virtualbox|vmware|vmnet|docker|veth|wsl|"
    r"vEthernet|ham|loopback|pseudo|teredo|isatap|"
    r"vpn|tap-|tun\d|wireguard|nordlynx|mullvad|"
    r"br-[0-9a-f]|cni\d|flannel|calico|virbr|podman",
    re.IGNORECASE,
)


def get_network_adapters() -> list[dict[str, str]]:
    """Return all physical network adapters with IP and subnet info.

    Returns list of dicts, e.g.:
        [{"name": "Ethernet 2", "ip": "192.168.1.50", "subnet": "192.168.1.0/24", "mac": "aa:bb:cc:dd:ee:ff"}, ...]

    Excludes loopback, link-local, virtual adapters, and IPv6.
    """
    adapters: list[dict[str, str]] = []
    try:
        import ifaddr

        for adapter in ifaddr.get_adapters():
            if _VIRTUAL_ADAPTER_PATTERNS.search(adapter.nice_name):
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
                    adapters.append({
                        "name": adapter.nice_name,
                        "ip": addr,
                        "subnet": str(network),
                    })
                except (ValueError, TypeError):
                    continue
    except ImportError:
        log.warning("ifaddr not installed -- cannot detect network adapters")
    except OSError as exc:
        log.warning("Failed to detect network adapters: %s", exc)

    # Enrich with MAC addresses via psutil (if available)
    try:
        import psutil
        mac_map: dict[str, str] = {}
        for name, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name == "AF_LINK":
                    mac_map[name] = addr.address
                    break
        for entry in adapters:
            entry["mac"] = mac_map.get(entry["name"], "")
    except ImportError:
        for entry in adapters:
            entry.setdefault("mac", "")
    except OSError:
        for entry in adapters:
            entry.setdefault("mac", "")

    return adapters


def get_local_subnets(interface_ip: str | None = None) -> list[str]:
    """Detect subnets from local network interfaces.

    Args:
        interface_ip: If set, only return the subnet for this specific IP.
            If empty or None, return all physical adapter subnets.

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
                # If filtering by interface IP, skip non-matching adapters
                if interface_ip and addr != interface_ip:
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


def _parse_cidr(cidr: str, min_prefix: int = 20) -> list[str]:
    """Expand a CIDR range to a list of host IPs (excluding network and broadcast).

    Args:
        min_prefix: Minimum prefix length allowed. Subnets larger than this
            (smaller prefix number) are skipped. Default /20 (~4K hosts).
    """
    try:
        network = ipaddress.IPv4Network(cidr, strict=False)
    except ValueError:
        log.warning("Invalid CIDR: %s", cidr)
        return []

    # Safety: skip subnets larger than the configured limit
    if network.prefixlen < min_prefix:
        log.warning(
            "Subnet %s too large (/%d), skipping (limit is /%d). "
            "Increase 'Max subnet size' in Discovery Settings, or set a specific "
            "control interface in System Settings.",
            cidr, network.prefixlen, min_prefix,
        )
        return []

    return [str(ip) for ip in network.hosts()]


async def ping_sweep(
    subnets: list[str],
    concurrency: int = 50,
    timeout: float = 1.0,
    on_found: Callable[[str], Awaitable[None]] | None = None,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
    min_prefix: int = 20,
    source_ip: str = "",
) -> list[str]:
    """Ping all addresses in the given subnets. Returns list of responding IPs.

    Uses system ping command (no elevated privileges required).
    Runs up to ``concurrency`` pings simultaneously.

    Args:
        on_found: Called with IP when a host responds.
        on_progress: Called with (completed_count, total_count) after each host.
        min_prefix: Minimum CIDR prefix length. Subnets larger than this are skipped.
        source_ip: Bind pings to this source address on multi-homed hosts.
    """
    all_ips: list[str] = []
    for cidr in subnets:
        all_ips.extend(_parse_cidr(cidr, min_prefix=min_prefix))

    if not all_ips:
        return []

    total = len(all_ips)
    if source_ip:
        log.info("Ping sweep: %d addresses across %d subnet(s) (source: %s)", total, len(subnets), source_ip)
    else:
        log.info("Ping sweep: %d addresses across %d subnet(s)", total, len(subnets))

    alive: list[str] = []
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0

    async def _ping_one(ip: str) -> None:
        nonlocal completed
        async with semaphore:
            if await _ping(ip, timeout, source_ip=source_ip):
                alive.append(ip)
                if on_found:
                    await on_found(ip)
            completed += 1
            if on_progress:
                await on_progress(completed, total)

    await asyncio.gather(*[_ping_one(ip) for ip in all_ips])
    log.info("Ping sweep complete: %d/%d hosts alive", len(alive), total)
    return sorted(alive, key=lambda x: ipaddress.IPv4Address(x))


async def _ping(ip: str, timeout: float = 1.0, source_ip: str = "") -> bool:
    """Ping a single IP address. Returns True if it responds.

    Args:
        source_ip: If set, bind the ping to this source address (``-S`` on
            Windows, ``-I`` on Linux).  Needed on multi-homed hosts where
            the kernel might pick a Docker/VPN source and get filtered.
    """
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000))]
        if source_ip:
            cmd.extend(["-S", source_ip])
        cmd.append(ip)
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout)))]
        if source_ip:
            cmd.extend(["-I", source_ip])
        cmd.append(ip)

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


# ---------------------------------------------------------------------------
# NetBIOS Name Query (UDP 137)
# ---------------------------------------------------------------------------


def _build_nbstat_request() -> bytes:
    """Build a NetBIOS Node Status Request packet.

    This is a single UDP packet that asks the target for all registered
    NetBIOS names. No credentials needed.
    """
    # Transaction ID
    xid = struct.pack(">H", 0x0001)
    # Flags: 0x0000 (query)
    flags = struct.pack(">H", 0x0000)
    # Questions: 1, Answers: 0, Authority: 0, Additional: 0
    counts = struct.pack(">HHHH", 1, 0, 0, 0)
    # Query name: "*" encoded as NetBIOS name (CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)
    # 0x20 length prefix, then 32 bytes of encoded "*\0" padded name, then 0x00 terminator
    name = b"\x20" + b"CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" + b"\x00"
    # Type: NBSTAT (0x0021), Class: IN (0x0001)
    qtype = struct.pack(">HH", 0x0021, 0x0001)
    return xid + flags + counts + name + qtype


def _parse_nbstat_response(data: bytes) -> dict[str, str] | None:
    """Parse a NetBIOS Node Status Response.

    Returns dict with 'hostname' and optionally 'workgroup', or None.
    """
    if len(data) < 57:  # Minimum valid response
        return None

    # Skip header (12 bytes) + query name echo (34 bytes) + type/class (4 bytes) + TTL (4 bytes) + rdlength (2 bytes)
    # = 56 bytes before the name count
    try:
        offset = 56
        name_count = data[offset]
        offset += 1

        hostname = None
        workgroup = None

        for _ in range(name_count):
            if offset + 18 > len(data):
                break
            name_bytes = data[offset:offset + 15]
            name_type = data[offset + 15]
            # name_flags = struct.unpack(">H", data[offset + 16:offset + 18])
            offset += 18

            name = name_bytes.decode("ascii", errors="replace").rstrip()
            if not name or name.startswith("\x00"):
                continue

            # Type 0x00 = Workstation, Type 0x20 = File Server
            if name_type == 0x00 and hostname is None:
                hostname = name
            # Type 0x00 with GROUP flag (bit 15 of flags) = workgroup
            elif name_type == 0x00 and workgroup is None:
                flag_word = struct.unpack(">H", data[offset - 2:offset])[0]
                if flag_word & 0x8000:
                    workgroup = name

        if not hostname:
            return None

        result = {"hostname": hostname}
        if workgroup:
            result["workgroup"] = workgroup
        return result
    except (IndexError, struct.error):
        return None


async def netbios_query(
    ip: str,
    timeout: float = 1.0,
) -> dict[str, str] | None:
    """Query a device for its NetBIOS name via UDP 137.

    Returns dict with 'hostname' and optionally 'workgroup', or None.
    Windows PCs, NAS devices, and some Linux hosts with Samba respond.
    """
    packet = _build_nbstat_request()
    loop = asyncio.get_event_loop()

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.settimeout(0)

        await loop.run_in_executor(None, sock.sendto, packet, (ip, 137))

        # Wait for response
        data = await asyncio.wait_for(
            loop.sock_recv(sock, 1024),
            timeout=timeout,
        )
        return _parse_nbstat_response(data)
    except (asyncio.TimeoutError, OSError, socket.error):
        return None
    finally:
        try:
            sock.close()
        except OSError:
            pass


async def netbios_sweep(
    ips: list[str],
    concurrency: int = 30,
    timeout: float = 1.0,
) -> dict[str, dict[str, str]]:
    """Query multiple IPs for NetBIOS names. Returns {ip: {hostname, workgroup}}."""
    results: dict[str, dict[str, str]] = {}
    sem = asyncio.Semaphore(concurrency)

    async def query_one(ip: str) -> None:
        async with sem:
            result = await netbios_query(ip, timeout)
            if result:
                results[ip] = result

    await asyncio.gather(*[query_one(ip) for ip in ips], return_exceptions=True)
    return results
