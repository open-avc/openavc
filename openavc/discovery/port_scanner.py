"""Async TCP port scanner with banner grabbing.

Core does not ship a curated list of AV ports. The engine builds the
scan list at runtime from each loaded driver's ``tcp_probe.port`` plus
``port_open:`` hint plus the community catalog, with a tiny universal
baseline below for the "what kind of device is this?" sweep where no
driver is involved yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

log = logging.getLogger("discovery.ports")

# Universal baseline ports always included in every scan. These cover
# generic web management UIs and Telnet (used by enough embedded
# devices that grabbing banners on it is worthwhile). No vendor-
# specific ports — drivers contribute those via their declared
# ``tcp_probe.port`` and ``port_open:`` hints.
BASELINE_PORTS: frozenset[int] = frozenset({22, 23, 80, 443, 8080})

# Ports where devices typically send a banner immediately on connect.
# Limited to the two universal banner-friendly ports — Telnet (23) and
# SSH (22) — since vendor-specific banner regexes were removed with
# the legacy probe table. Drivers can still match banner contents by
# declaring a ``tcp_probe:`` with no ``send_*`` and an ``expect:`` /
# ``expect_regex:`` matcher, which is a generic capability.
BANNER_PORTS: frozenset[int] = frozenset({22, 23})


async def scan_host_ports(
    ip: str,
    ports: list[int],
    timeout: float = 1.0,
    stagger_ms: float = 20.0,
) -> list[int]:
    """Probe TCP ports on a single host. Returns list of open ports.

    ``stagger_ms`` adds a small delay between connection starts to avoid
    blasting embedded AV devices with too many SYN packets at once.
    All connections still overlap — this just spreads the initial burst.
    """
    if not ports:
        return []

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
    ports: list[int],
    timeout: float = 1.0,
    concurrency: int = 20,
    on_result: Callable[[str, list[int]], Awaitable[None]] | None = None,
) -> dict[str, list[int]]:
    """Scan ports on multiple hosts. Returns {ip: [open_ports]}.

    Limits concurrent host scans to ``concurrency``.
    """
    if not ports:
        return {}

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

    Many embedded devices send a welcome string immediately on connect.
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
