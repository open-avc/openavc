"""Driver-declared probe runners.

The discovery schema lets a driver declare a UDP broadcast probe or a
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
so a single scan can't flood the network — the UDP broadcast runner
acquires before each ``sendto`` and the TCP active runner before each
connect, so both send paths honor the one global limit. The runner
does not retry — a missed reply is silently a missed reply, which is
the right behavior for a discovery probe.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import ssl
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

# Flood guard for the UDP broadcast probe. A hostile peer on the subnet can
# answer a broadcast probe from spoofed source addresses, and every distinct
# matching source costs a results entry -> a DiscoveredDevice -> a WebSocket
# fan-out in phase 8. No real site answers a single probe from anywhere near
# this many hosts; past the cap, new sources are dropped (already-seen ones
# are deduped away anyway). Mirrors amx_ddp_scanner.MAX_BEACON_SOURCES.
_MAX_PROBE_RESPONDERS = 512

# Quiet-gap timeout for the TCP active-probe accumulation loop. A device
# may send its identifying banner in a later TCP segment than the first
# (telnet controllers emit IAC negotiation in its own segment ahead of the
# welcome line; SSH/banner protocols are similar). We keep reading while
# data is actively arriving and stop the first time the peer goes silent
# for this long — so a multi-segment banner lands, while a non-matching
# host that sends one chunk and waits is released promptly instead of
# sitting through the whole probe budget. 1.5s matches the validated
# inter-segment margin the Python banner-grab companions already use.
_PROBE_READ_QUIET_SECONDS = 1.5


def _make_probe_tls_context() -> ssl.SSLContext:
    """A permissive TLS context for ``tls: true`` tcp probes.

    Discovery happens before a device is trusted or configured, and AV gear
    ships self-signed certs out of the box, so a probe can't verify the chain
    or hostname — it only needs the encrypted channel to read the device's
    own banner/landing page. Verification is the runtime driver's job once the
    user adds the device. Built once and reused; it holds no per-host state.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_PROBE_TLS_CONTEXT = _make_probe_tls_context()


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
    cap_warned = False
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
            if len(results) >= _MAX_PROBE_RESPONDERS:
                # Flood guard: a spoofed-source responder storm would otherwise
                # accumulate an unbounded results dict (each entry becomes a
                # device + WS broadcast downstream). Drop new sources past the
                # cap; keep listening so the already-matched set stays stable.
                if not cap_warned:
                    cap_warned = True
                    log.warning(
                        "probe_runner: %s hit the %d distinct-responder cap; "
                        "ignoring new sources for the rest of this probe window",
                        spec.probe_id, _MAX_PROBE_RESPONDERS,
                    )
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
    rate_limiter: RateLimiter | None = None,
) -> Evidence | None:
    """Connect to ``target:spec.port``, send, read, match, extract.

    Returns one ``Evidence`` record on a successful match, or
    ``None`` on connect failure / timeout / non-match. The caller is
    expected to invoke this against every host whose port-scan
    results include ``spec.port``.

    ``stagger_ms`` is a pre-connection delay (port_scanner pattern):
    the engine spreads a batch of probes by passing increasing
    stagger values so embedded AV devices aren't hit with a SYN
    burst. ``rate_limiter``, when supplied, is the same shared
    ``RateLimiter`` the UDP probes use: this runner acquires a slot
    from it immediately before connecting, so the documented global
    10/sec send cap actually bounds the TCP SYN rate too (a batch of
    matching hosts across many drivers can't burst past it).
    """
    if spec.kind != "tcp":
        raise ValueError(f"run_tcp_active_probe got non-tcp spec: {spec.kind!r}")

    if stagger_ms > 0:
        await asyncio.sleep(stagger_ms / 1000.0)

    # Global send-rate cap: acquire a slot before the SYN so a scan with many
    # TCP-probe drivers and many matching hosts stays under the shared limit.
    if rate_limiter is not None:
        await rate_limiter.acquire()

    timeout = spec.timeout_ms / 1000.0
    local_addr = (source_ip, 0) if source_ip else None
    # For a tls probe, hand asyncio a permissive context so the handshake runs
    # before send/read. A plain-TCP host on this port fails the handshake with
    # ssl.SSLError (an OSError subclass) and is dropped like any other miss.
    tls_ctx = _PROBE_TLS_CONTEXT if spec.tls else None

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                target, spec.port, local_addr=local_addr,
                ssl=tls_ctx, server_hostname=target if tls_ctx else None,
            ),
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
        # Accumulate short reads rather than a single read. A single read
        # can return just the first TCP segment; for telnet/SSH-style
        # devices that send IAC negotiation (or a partial greeting) in its
        # own segment ahead of the identifying banner, that first segment
        # never carries the fingerprint. Read until the matcher hits, the
        # peer closes, the byte cap is reached, or the peer goes quiet for
        # _PROBE_READ_QUIET_SECONDS (whichever comes first). A connect-only
        # probe (no matcher) returns as soon as any reply arrives.
        match = spec.response_match
        has_matcher = (
            match.starts_with is not None
            or match.contains is not None
            or match.regex is not None
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        acc = bytearray()
        while loop.time() < deadline and len(acc) < _MAX_RESPONSE_BYTES:
            remaining = deadline - loop.time()
            # Wait the full remaining budget for the first byte (a device can
            # be slow to start sending), but once data is flowing only wait a
            # short quiet-gap for further segments — so a multi-segment banner
            # lands while a non-matching host that sent one chunk and went
            # silent is released promptly.
            read_timeout = remaining if not acc else min(_PROBE_READ_QUIET_SECONDS, remaining)
            try:
                chunk = await asyncio.wait_for(
                    reader.read(_MAX_RESPONSE_BYTES - len(acc)),
                    timeout=read_timeout,
                )
            except (TimeoutError, asyncio.TimeoutError):
                break  # first-byte budget elapsed, or peer went quiet mid-banner
            if not chunk:
                break  # peer closed the connection
            acc += chunk
            if not has_matcher:
                break  # connect-only probe: any reply is enough
            if _matches(bytes(acc), match):
                break  # fingerprint satisfied — return immediately
        payload = bytes(acc)
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
    return evidence_active_probe(
        spec.probe_id,
        response=response,
        port=spec.port,
        matched_pattern=describe_response_match(spec.response_match) or None,
    )
