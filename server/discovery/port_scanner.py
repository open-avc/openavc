"""Async TCP port scanner with banner grabbing."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

log = logging.getLogger("discovery.ports")

# Well-known AV device ports (always scanned regardless of driver hints)
AV_PORTS: dict[int, str] = {
    22: "SSH (embedded devices, AV processors)",
    23: "Telnet (Extron, Biamp, QSC, Kramer, Shure, LG)",
    445: "SMB (Windows devices, NAS)",
    80: "HTTP (web management, Panasonic PTZ, REST APIs)",
    443: "HTTPS (secure web management)",
    1515: "Samsung MDC",
    1688: "Crestron CIP",
    3088: "Crestron XIO",
    4352: "PJLink",
    5000: "Kramer P3000 alt / Q-SYS QRC alt",
    5900: "VNC",
    7142: "AMX ICSP",
    8080: "HTTP alt (device web UIs)",
    9090: "HTTP alt",
    10500: "VISCA over IP (Sony cameras)",
    41794: "Crestron CTP",
    49152: "Biamp Tesira alt",
    52000: "QSC Q-SYS",
    61000: "Shure DCS alt",
    1400: "Sonos UPnP",
}

# Ports where devices typically send a banner immediately on connect
BANNER_PORTS = {22, 23, 4352, 1688, 41794}


async def scan_host_ports(
    ip: str,
    ports: list[int] | None = None,
    timeout: float = 1.0,
    stagger_ms: float = 20.0,
) -> list[int]:
    """Probe TCP ports on a single host. Returns list of open ports.

    If ``ports`` is None, uses the default AV_PORTS table.

    ``stagger_ms`` adds a small delay between connection starts to avoid
    blasting embedded AV devices with too many SYN packets at once.
    All connections still overlap — this just spreads the initial burst.
    """
    if ports is None:
        ports = list(AV_PORTS.keys())

    async def _check(port: int, delay: float) -> int | None:
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return port
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return None

    stagger = stagger_ms / 1000.0
    results = await asyncio.gather(
        *[_check(p, i * stagger) for i, p in enumerate(ports)]
    )
    return sorted(p for p in results if p is not None)


async def scan_multiple_hosts(
    hosts: list[str],
    ports: list[int] | None = None,
    timeout: float = 1.0,
    concurrency: int = 20,
    on_result: Callable[[str, list[int]], Awaitable[None]] | None = None,
) -> dict[str, list[int]]:
    """Scan ports on multiple hosts. Returns {ip: [open_ports]}.

    Limits concurrent host scans to ``concurrency``.
    """
    if ports is None:
        ports = list(AV_PORTS.keys())

    log.info("Port scan: %d hosts x %d ports", len(hosts), len(ports))
    results: dict[str, list[int]] = {}
    semaphore = asyncio.Semaphore(concurrency)

    async def _scan_one(ip: str) -> None:
        async with semaphore:
            open_ports = await scan_host_ports(ip, ports, timeout)
            if open_ports:
                results[ip] = open_ports
                if on_result:
                    await on_result(ip, open_ports)

    await asyncio.gather(*[_scan_one(ip) for ip in hosts])
    log.info("Port scan complete: %d hosts with open AV ports", len(results))
    return results


async def grab_banner(ip: str, port: int, timeout: float = 2.0) -> str | None:
    """Connect to a port and read the first response (banner).

    Many AV devices send a welcome string immediately on connect:
      - Extron: "(c) 2020 Extron Electronics..."
      - Biamp: "Welcome to the Tesira Text Protocol..."
      - Kramer: "Welcome to Kramer..."
      - PJLink: "PJLINK 0" or "PJLINK 1"

    Returns the banner text or None if no data received within timeout.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port),
            timeout=timeout,
        )
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            if data:
                return data.decode("utf-8", errors="replace").strip()
        finally:
            writer.close()
            await writer.wait_closed()
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        pass
    return None


async def grab_banners(
    ip: str,
    open_ports: list[int],
    timeout: float = 2.0,
) -> dict[int, str]:
    """Grab banners from all open ports that typically send one.

    Returns {port: banner_text} for ports that responded.
    """
    banner_candidates = [p for p in open_ports if p in BANNER_PORTS]
    if not banner_candidates:
        return {}

    banners: dict[int, str] = {}

    async def _grab(port: int) -> None:
        banner = await grab_banner(ip, port, timeout)
        if banner:
            banners[port] = banner

    await asyncio.gather(*[_grab(p) for p in banner_candidates])
    return banners
