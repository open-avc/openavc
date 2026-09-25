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

PORT_OPEN = "open"
PORT_REFUSED = "refused"    # the host answered with a reset: it is there, the port is closed
PORT_FILTERED = "filtered"  # no answer inside the timeout: dropped, or nothing there
PORT_ERROR = "error"        # the connect failed locally (unreachable network, bind failure)

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


def _local_addr(source_ip: str) -> tuple[str, int] | None:
    """Source address for an outbound connect: the control adapter, or the
    OS's choice when none is set. Binding it is what brings the replies back
    on a multi-homed host (a VPN adapter beside the AV network, say)."""
    return (source_ip, 0) if source_ip else None


async def scan_host_ports(
    ip: str,
    ports: list[int],
    timeout: float = 1.0,
    stagger_ms: float = 20.0,
    source_ip: str = "",
) -> list[int]:
    """Probe TCP ports on a single host. Returns list of open ports.

    ``stagger_ms`` adds a small delay between connection starts to avoid
    blasting embedded AV devices with too many SYN packets at once.
    All connections still overlap — this just spreads the initial burst.

    ``source_ip`` binds every connection to that local address (the
    control interface); empty lets the OS pick.
    """
    states = await scan_host_port_states(
        ip, ports, timeout=timeout, stagger_ms=stagger_ms, source_ip=source_ip,
    )
    return sorted(p for p, state in states.items() if state == PORT_OPEN)


async def scan_host_port_states(
    ip: str,
    ports: list[int],
    timeout: float = 1.0,
    stagger_ms: float = 20.0,
    source_ip: str = "",
) -> dict[int, str]:
    """``scan_host_ports``, keeping how each port answered.

    Returns {port: state}, the state one of ``PORT_OPEN``, ``PORT_REFUSED``
    (a reset: the host is there and the port is closed), ``PORT_FILTERED``
    (silence until the timeout) or ``PORT_ERROR`` (the connect failed on
    this side). The same connects as a scan, nothing more.
    """
    if not ports:
        return {}
    local_addr = _local_addr(source_ip)

    async def _check(port: int, delay: float) -> tuple[int, str]:
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port, local_addr=local_addr),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return port, PORT_OPEN
        except asyncio.TimeoutError:
            return port, PORT_FILTERED
        except ConnectionRefusedError:
            return port, PORT_REFUSED
        except OSError:
            return port, PORT_ERROR

    stagger = stagger_ms / 1000.0
    results = await asyncio.gather(
        *[_check(p, i * stagger) for i, p in enumerate(ports)]
    )
    return dict(sorted(results))


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


async def grab_banner(
    ip: str, port: int, timeout: float = 2.0, source_ip: str = "",
) -> str | None:
    """Connect to a port and read the first response (banner).

    Many embedded devices send a welcome string immediately on connect.
    Returns the banner text or None if no data received within timeout.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, local_addr=_local_addr(source_ip)),
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
    source_ip: str = "",
) -> dict[int, str]:
    """Grab banners from all open ports that typically send one.

    Returns {port: banner_text} for ports that responded.
    """
    banner_candidates = [p for p in open_ports if p in BANNER_PORTS]
    if not banner_candidates:
        return {}

    banners: dict[int, str] = {}

    async def _grab(port: int) -> None:
        banner = await grab_banner(ip, port, timeout, source_ip=source_ip)
        if banner:
            banners[port] = banner

    await asyncio.gather(*[_grab(p) for p in banner_candidates])
    return banners
