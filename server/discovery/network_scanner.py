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

from server.discovery import icmp
from server.utils.spawn import CREATE_NO_WINDOW

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


def get_interface_ips() -> list[str]:
    """Non-loopback, non-link-local IPv4 addresses of physical adapters.

    Used for per-interface multicast group joins and sends (see
    ``discovery.multicast``). Same adapter filtering as
    ``get_local_subnets``.
    """
    ips: list[str] = []
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
                if addr not in ips:
                    ips.append(addr)
    except ImportError:
        log.warning("ifaddr not installed -- cannot enumerate interface IPs")
    except OSError as exc:
        log.warning("Failed to enumerate interface IPs: %s", exc)
    return ips


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
    stats: icmp.PingSweepStats | None = None,
) -> list[str]:
    """Ping all addresses in the given subnets. Returns list of responding IPs.

    The ICMP method is probed once per sweep (unprivileged datagram socket,
    raw socket, or system ping — see ``discovery.icmp``), so no elevated
    privileges or ping binary are required where the kernel allows ICMP
    sockets. Runs up to ``concurrency`` pings simultaneously.

    Args:
        on_found: Called with IP when a host responds.
        on_progress: Called with (completed_count, total_count) after each host.
        min_prefix: Minimum CIDR prefix length. Subnets larger than this are skipped.
        source_ip: Bind pings to this source address on multi-homed hosts.
        stats: Optional accounting object — filled with the selected method
            and alive/timeout/error counts so the caller can surface
            environment failures (errors are NOT dead hosts).
    """
    if stats is None:
        stats = icmp.PingSweepStats()

    all_ips: list[str] = []
    for cidr in subnets:
        all_ips.extend(_parse_cidr(cidr, min_prefix=min_prefix))

    if not all_ips:
        return []

    total = len(all_ips)
    stats.total = total
    stats.method = await icmp.select_ping_method()

    if stats.method == icmp.METHOD_NONE:
        # No ICMP socket permission and no ping binary on PATH. The scan
        # carries on (passive listeners may still find devices) but every
        # host counts as an error so the status warnings call this out.
        stats.errors = total
        log.error(
            "Ping sweep skipped: no ICMP socket permission and no system "
            "ping binary found — active discovery cannot see hosts",
        )
        return []

    if source_ip:
        log.info(
            "Ping sweep: %d addresses across %d subnet(s) (method: %s, source: %s)",
            total, len(subnets), stats.method, source_ip,
        )
    else:
        log.info(
            "Ping sweep: %d addresses across %d subnet(s) (method: %s)",
            total, len(subnets), stats.method,
        )

    alive: list[str] = []
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0

    async def _ping_one(ip: str) -> None:
        nonlocal completed
        async with semaphore:
            result = await icmp.ping_host(
                ip, timeout, source_ip=source_ip, method=stats.method,
            )
            if result == icmp.RESULT_ALIVE:
                alive.append(ip)
                stats.alive += 1
                if on_found:
                    await on_found(ip)
            elif result == icmp.RESULT_ERROR:
                stats.errors += 1
            else:
                stats.timeouts += 1
            completed += 1
            if on_progress:
                await on_progress(completed, total)

    await asyncio.gather(*[_ping_one(ip) for ip in all_ips])
    log.info(
        "Ping sweep complete: %d/%d hosts alive (%d timeouts, %d errors, method=%s)",
        len(alive), total, stats.timeouts, stats.errors, stats.method,
    )
    return sorted(alive, key=lambda x: ipaddress.IPv4Address(x))


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
        creationflags=CREATE_NO_WINDOW,
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


_PROC_NET_ARP = "/proc/net/arp"

# Complete /proc/net/arp entry: "192.168.1.1  0x1  0x2  a4:91:b1:aa:bb:cc  *  wlan0"
_ARP_MAC_RE = re.compile(
    r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$"
)


def _parse_proc_net_arp(text: str) -> dict[str, str]:
    """Parse /proc/net/arp content. Returns {ip: mac} for complete entries.

    Skips the header line, incomplete entries (ATF_COM flag 0x2 not set),
    all-zero MACs, and broadcast MACs.
    """
    result: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 4:
            continue
        ip, _hw_type, flags, mac = fields[0], fields[1], fields[2], fields[3]
        try:
            if not int(flags, 16) & 0x2:  # ATF_COM unset — incomplete entry
                continue
        except ValueError:
            continue
        mac = mac.lower()
        if not _ARP_MAC_RE.match(mac):
            continue
        if mac in ("00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"):
            continue
        result[ip] = mac
    return result


async def _harvest_arp_linux() -> dict[str, str]:
    """Read the kernel ARP table from /proc/net/arp.

    procfs is always present on Linux — no iproute2 needed, which slim
    container/appliance images don't ship. ``ip neigh`` remains only as a
    fallback for the unlikely case /proc/net/arp is unreadable.
    """
    try:
        with open(_PROC_NET_ARP, encoding="ascii", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        log.debug("%s unreadable (%s); falling back to 'ip neigh'", _PROC_NET_ARP, exc)
        return await _harvest_arp_ip_neigh()
    return _parse_proc_net_arp(text)


async def _harvest_arp_ip_neigh() -> dict[str, str]:
    """Parse 'ip neigh' output (fallback when /proc/net/arp is unreadable)."""
    proc = await asyncio.create_subprocess_exec(
        "ip", "neigh",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
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

            if name_type == 0x00:
                flag_word = struct.unpack(">H", data[offset - 2:offset])[0]
                is_group = bool(flag_word & 0x8000)
                if is_group and workgroup is None:
                    workgroup = name
                elif not is_group and hostname is None:
                    hostname = name

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
