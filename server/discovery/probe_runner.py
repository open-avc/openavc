"""Phase 9 driver-declared probe runners.

The Phase 9 schema lets a driver declare a UDP broadcast probe or a
TCP active probe in YAML — port, send bytes, response_match, and
optional extract rules. This module is the runtime executor: given
a parsed ``CustomProbeSpec``, send the probe, listen for replies,
match them, and emit ``Evidence`` records the deterministic matcher
already understands (``KIND_BROADCAST`` / ``KIND_ACTIVE_PROBE``).

Network safety
--------------
Every socket binds to the configured ``source_ip`` (control adapter)
so on multi-homed hosts the probe leaves through the right NIC and
replies route back the same way. This is non-negotiable: the runner
refuses to send if it can't bind.

A shared ``RateLimiter`` caps custom probes at 10 sends/sec globally
so a single scan can't flood the network with broadcasts. The runner
does not retry — a missed reply is silently a missed reply, which is
the right behavior for a discovery probe.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from typing import Sequence

from server.discovery.hints import (
    CustomProbeSpec,
    ExtractRule,
    RESERVED_EXTRACT_KEYS,
    ResponseMatch,
    describe_response_match,
)
from server.discovery.result import Evidence
from server.discovery.tier_matcher import (
    evidence_active_probe,
    evidence_broadcast,
)

log = logging.getLogger("discovery.probe_runner")


# Hard cap on bytes read from a probe response. UDP datagrams that
# exceed this get truncated; TCP responses past this byte count are
# discarded. Real AV discovery responses fit easily in 4096 bytes.
_MAX_RESPONSE_BYTES = 4096


class RateLimiter:
    """Async token-bucket-style limiter, ``rate`` calls per second.

    A single ``RateLimiter`` instance shared across all custom probes
    in a scan keeps the global send rate bounded even when many
    drivers contribute custom probes.
    """

    def __init__(self, rate_per_sec: float) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            wait = self._next_slot - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_slot = max(self._next_slot, now) + self._interval


# ---------------------------------------------------------------------------
# Match + extract helpers
# ---------------------------------------------------------------------------


def _matches(payload: bytes, match: ResponseMatch) -> bool:
    """Return True iff every declared matcher in ``match`` succeeds."""
    if match.starts_with is not None:
        if not payload.startswith(match.starts_with):
            return False
    if match.contains is not None:
        needle = match.contains
        # Try bytes first (binary protocols), then latin-1 text.
        if needle.encode("utf-8") not in payload:
            text = payload.decode("latin-1", errors="replace")
            if needle not in text:
                return False
    if match.regex is not None:
        text = payload.decode("latin-1", errors="replace")
        if not match.regex.search(text):
            return False
    return True


def _apply_extract(
    payload: bytes,
    rules: tuple[ExtractRule, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """Run extract rules. Returns (reserved, extracted).

    ``reserved`` carries the manufacturer / make values which the
    runner lifts to the top of the evidence ``response`` / ``txt``
    dict so ``extract_vendor_strings`` finds them. Everything else
    lands in ``extracted``.
    """
    reserved: dict[str, str] = {}
    extracted: dict[str, str] = {}
    if not rules:
        return reserved, extracted

    text = payload.decode("latin-1", errors="replace")
    for rule in rules:
        value: str | None = None
        if rule.value is not None:
            value = rule.value
        elif rule.regex is not None:
            m = rule.regex.search(text)
            if m is None:
                continue
            try:
                if rule.group == 0:
                    value = m.group(0)
                elif rule.group <= (m.re.groups or 0):
                    value = m.group(rule.group)
                else:
                    continue
            except (IndexError, re.error):
                continue
        if value is None:
            continue
        if rule.field_name in RESERVED_EXTRACT_KEYS:
            reserved[rule.field_name] = value
        else:
            extracted[rule.field_name] = value
    return reserved, extracted


# ---------------------------------------------------------------------------
# UDP broadcast probe runner
# ---------------------------------------------------------------------------


def _make_udp_socket(source_ip: str, *, broadcast: bool) -> socket.socket | None:
    """Create a UDP socket bound to ``(source_ip, 0)``.

    Source-IP binding is non-negotiable: without it the kernel may
    pick the wrong adapter on multi-homed hosts. Returns ``None`` if
    the socket can't be bound; the caller logs and skips the probe.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if broadcast:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((source_ip or "", 0))
        sock.setblocking(False)
        return sock
    except OSError as exc:
        log.warning(
            "probe_runner: could not create UDP socket bound to %r: %s",
            source_ip, exc,
        )
        return None


async def run_udp_broadcast_probe(
    spec: CustomProbeSpec,
    *,
    targets: Sequence[str],
    source_ip: str,
    rate_limiter: RateLimiter,
) -> dict[str, Evidence]:
    """Send ``spec.send`` to each target, collect matching replies.

    Returns a dict keyed by responder IP -> ``Evidence``. Targets
    typically include the directed broadcast addresses for each
    subnet, but unicast IPs are also valid (some firmware ignores
    broadcast and only answers per-host probes).

    The runner binds to ``source_ip`` and acquires from
    ``rate_limiter`` before each send.
    """
    if spec.kind != "udp":
        raise ValueError(f"run_udp_broadcast_probe got non-udp spec: {spec.kind!r}")
    if not targets:
        return {}

    sock = _make_udp_socket(source_ip, broadcast=True)
    if sock is None:
        return {}

    results: dict[str, Evidence] = {}
    loop = asyncio.get_event_loop()
    timeout_seconds = spec.timeout_ms / 1000.0

    try:
        for target in targets:
            await rate_limiter.acquire()
            try:
                await loop.run_in_executor(
                    None,
                    lambda t=target: sock.sendto(spec.send, (t, spec.port)),
                )
                log.debug(
                    "probe_runner: %s sent to %s:%d (%d bytes)",
                    spec.probe_id, target, spec.port, len(spec.send),
                )
            except OSError as exc:
                log.debug(
                    "probe_runner: %s send to %s failed: %s",
                    spec.probe_id, target, exc,
                )

        end = loop.time() + timeout_seconds
        while loop.time() < end:
            remaining = end - loop.time()
            if remaining <= 0:
                break
            try:
                sock.settimeout(min(remaining, 0.5))
                data, addr = await loop.run_in_executor(
                    None, lambda: sock.recvfrom(_MAX_RESPONSE_BYTES),
                )
            except (socket.timeout, TimeoutError):
                continue
            except OSError as exc:
                log.debug("probe_runner: %s recv error: %s", spec.probe_id, exc)
                break

            sender_ip = addr[0]
            if sender_ip in results:
                continue  # one evidence record per device per probe
            if not _matches(data, spec.response_match):
                continue

            reserved, extracted = _apply_extract(data, spec.extract)
            # UDP txt is flat — every extracted field is a top-level key,
            # which puts manufacturer/make exactly where
            # extract_vendor_strings looks for them.
            txt: dict[str, str] = {**reserved, **extracted}
            results[sender_ip] = evidence_broadcast(
                probe_id=spec.probe_id,
                response={"ip": sender_ip},
                txt=txt or None,
                port=spec.port,
                matched_pattern=describe_response_match(spec.response_match) or None,
            )
            log.debug(
                "probe_runner: %s match from %s reserved=%s extracted=%s",
                spec.probe_id, sender_ip, reserved, extracted,
            )
    finally:
        try:
            sock.close()
        except OSError:
            pass

    return results


# ---------------------------------------------------------------------------
# TCP active probe runner
# ---------------------------------------------------------------------------


async def run_tcp_active_probe(
    spec: CustomProbeSpec,
    *,
    target: str,
    source_ip: str,
    stagger_ms: float = 0.0,
) -> Evidence | None:
    """Connect to ``target:spec.port``, send, read, match, extract.

    Returns one ``Evidence`` record on a successful match, or
    ``None`` on connect failure / timeout / non-match. The caller is
    expected to invoke this against every host whose port-scan
    results include ``spec.port``.

    ``stagger_ms`` is a pre-connection delay (port_scanner pattern):
    the engine spreads a batch of probes by passing increasing
    stagger values so embedded AV devices aren't hit with a SYN
    burst. The same ``RateLimiter`` used for UDP probes applies via
    the engine layer for TCP — the schema's per-probe budget is
    governed there.
    """
    if spec.kind != "tcp":
        raise ValueError(f"run_tcp_active_probe got non-tcp spec: {spec.kind!r}")

    if stagger_ms > 0:
        await asyncio.sleep(stagger_ms / 1000.0)

    timeout = spec.timeout_ms / 1000.0
    local_addr = (source_ip, 0) if source_ip else None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(target, spec.port, local_addr=local_addr),
            timeout=timeout,
        )
    except (TimeoutError, asyncio.TimeoutError, ConnectionRefusedError, OSError) as exc:
        log.debug(
            "probe_runner: %s connect to %s:%d failed: %s",
            spec.probe_id, target, spec.port, exc,
        )
        return None

    payload = b""
    try:
        if spec.send:
            writer.write(spec.send)
            try:
                await asyncio.wait_for(writer.drain(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                pass
        try:
            payload = await asyncio.wait_for(
                reader.read(_MAX_RESPONSE_BYTES), timeout=timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            payload = b""
    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
        log.debug(
            "probe_runner: %s read from %s:%d failed: %s",
            spec.probe_id, target, spec.port, exc,
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass

    if not payload:
        return None
    if not _matches(payload, spec.response_match):
        return None

    reserved, extracted = _apply_extract(payload, spec.extract)
    response: dict[str, object] = {
        "text": payload.decode("latin-1", errors="replace"),
    }
    # Lift manufacturer/make to top of response so extract_vendor_strings
    # finds them; everything else lands under "extracted".
    response.update(reserved)
    if extracted:
        response["extracted"] = extracted

    log.debug(
        "probe_runner: %s match from %s reserved=%s extracted=%s",
        spec.probe_id, target, reserved, extracted,
    )
    return evidence_active_probe(spec.probe_id, response=response, port=spec.port)
