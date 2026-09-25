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

Observations
------------
A scan keeps only the replies that matched, as ``Evidence``. The
``observe_*`` runners also return a ``ProbeObservation`` for every exchange,
matched or not: the bytes sent and received, the certificate subject, the
``then:`` step, the timing, and why nothing came back when nothing did. A
single-device check needs the misses as much as the matches (a probe that
nearly matched is how a driver's fingerprint gets corrected). The ``run_*``
runners the scan calls are thin wrappers that return only the evidence.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from openavc.discovery.certificates import (
    certificate_match_text,
    read_peer_certificate,
)
from openavc.discovery.hints import (
    CustomProbeSpec,
    ExtractRule,
    ProbeFollowUp,
    RESERVED_EXTRACT_KEYS,
    ResponseMatch,
    describe_response_match,
)
from openavc.discovery.result import Evidence
from openavc.discovery.tier_matcher import (
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


def _bytes_view(data: bytes) -> dict[str, str]:
    """Raw bytes as both hex and text, the way a report shows them."""
    return {"hex": data.hex(), "text": data.decode("latin-1", errors="replace")}


def _connect_error(exc: BaseException) -> str:
    """A short, stable reason a probe never got to exchange anything."""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, ssl.SSLError):
        return f"tls: {exc}"
    return str(exc) or type(exc).__name__


@dataclass
class ProbeObservation:
    """One probe exchange as it happened, whether or not it matched.

    ``target`` is the host a TCP probe connected to, or the host a UDP reply
    came from. ``sent_to`` lists every address a UDP probe went to (a
    directed broadcast, or unicast hosts); a UDP probe nothing answered is
    one observation with an empty ``target`` and ``error`` ``"no reply"``.
    ``error`` says why no exchange happened (``refused``, ``timeout``,
    ``tls: ...``) or why one broke off; the bytes read before a break are
    kept. Times are milliseconds from the connect or send.
    """

    probe_id: str
    kind: str                      # "tcp" or "udp"
    port: int
    target: str
    sent: bytes = b""
    sent_to: tuple[str, ...] = ()
    reply: bytes = b""
    error: str = ""
    tls: bool = False
    cert_subject: str = ""
    certificate: dict[str, Any] | None = None
    follow_up_sent: bytes = b""
    follow_up_reply: bytes = b""
    follow_up_ok: bool | None = None
    started_at: float = field(default_factory=time.time)
    connect_ms: float | None = None
    first_reply_ms: float | None = None
    elapsed_ms: float | None = None
    matched: bool = False
    evidence: Evidence | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "probe_id": self.probe_id,
            "kind": self.kind,
            "port": self.port,
            "target": self.target,
            "sent": _bytes_view(self.sent),
            "reply": _bytes_view(self.reply),
            "error": self.error,
            "matched": self.matched,
            "started_at": self.started_at,
            "connect_ms": self.connect_ms,
            "first_reply_ms": self.first_reply_ms,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.sent_to:
            out["sent_to"] = list(self.sent_to)
        if self.tls:
            out["tls"] = True
            out["cert_subject"] = self.cert_subject
            out["certificate"] = self.certificate
        if self.follow_up_sent or self.follow_up_ok is not None:
            out["follow_up"] = {
                "sent": _bytes_view(self.follow_up_sent),
                "reply": _bytes_view(self.follow_up_reply),
                "ok": self.follow_up_ok,
            }
        return out


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
    ``rate_limiter`` before each send. ``observe_udp_probe`` is the same
    run with every reply kept.
    """
    observations = await observe_udp_probe(
        spec, targets=targets, source_ip=source_ip, rate_limiter=rate_limiter,
    )
    return {
        obs.target: obs.evidence
        for obs in observations
        if obs.evidence is not None
    }


async def observe_udp_probe(
    spec: CustomProbeSpec,
    *,
    targets: Sequence[str],
    source_ip: str,
    rate_limiter: RateLimiter,
) -> list[ProbeObservation]:
    """``run_udp_broadcast_probe``, returning every reply as an observation.

    One observation per datagram received, matched or not, with its raw
    bytes and arrival time. The first matching datagram from each responder
    carries the ``Evidence`` a scan keeps (one record per device per probe,
    as a scan has always kept). A probe nothing answered comes back as one
    observation with ``error`` ``"no reply"`` (or why it could not be sent).
    """
    if spec.kind != "udp":
        raise ValueError(f"run_udp_broadcast_probe got non-udp spec: {spec.kind!r}")
    if not targets:
        return []

    sent_to = tuple(targets)
    observations: list[ProbeObservation] = []

    def _nothing(error: str) -> list[ProbeObservation]:
        return [ProbeObservation(
            probe_id=spec.probe_id, kind="udp", port=spec.port, target="",
            sent=spec.send, sent_to=sent_to, error=error,
        )]

    sock = _make_udp_socket(source_ip, broadcast=True)
    if sock is None:
        return _nothing(f"could not bind a socket to {source_ip or 'any address'}")

    matched_from: set[str] = set()
    cap_warned = False
    obs_cap_warned = False
    send_errors: list[str] = []
    loop = asyncio.get_event_loop()
    timeout_seconds = spec.timeout_ms / 1000.0
    started_at = time.time()
    sent_mono = loop.time()

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
                send_errors.append(f"{target}: {exc}")
                log.debug(
                    "probe_runner: %s send to %s failed: %s",
                    spec.probe_id, target, exc,
                )
        # Reply times count from the last send.
        sent_mono = loop.time()

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
            arrived_ms = round((loop.time() - sent_mono) * 1000.0, 1)
            obs: ProbeObservation | None = None
            if len(observations) < _MAX_PROBE_RESPONDERS:
                obs = ProbeObservation(
                    probe_id=spec.probe_id, kind="udp", port=spec.port,
                    target=sender_ip, sent=spec.send, sent_to=sent_to,
                    reply=bytes(data), started_at=started_at,
                    first_reply_ms=arrived_ms, elapsed_ms=arrived_ms,
                )
                observations.append(obs)
            elif not obs_cap_warned:
                obs_cap_warned = True
                log.warning(
                    "probe_runner: %s kept %d replies; not recording more "
                    "for the rest of this probe window",
                    spec.probe_id, _MAX_PROBE_RESPONDERS,
                )

            if not _matches(data, spec.response_match):
                continue
            if obs is not None:
                obs.matched = True
            if sender_ip in matched_from:
                continue  # one evidence record per device per probe
            if len(matched_from) >= _MAX_PROBE_RESPONDERS:
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
            evidence = evidence_broadcast(
                probe_id=spec.probe_id,
                response={"ip": sender_ip},
                txt=txt or None,
                port=spec.port,
                matched_pattern=describe_response_match(spec.response_match) or None,
            )
            matched_from.add(sender_ip)
            if obs is None:
                # Past the observation cap, a match still reaches the scan.
                obs = ProbeObservation(
                    probe_id=spec.probe_id, kind="udp", port=spec.port,
                    target=sender_ip, sent=spec.send, sent_to=sent_to,
                    reply=bytes(data), started_at=started_at,
                    first_reply_ms=arrived_ms, elapsed_ms=arrived_ms,
                    matched=True,
                )
                observations.append(obs)
            obs.evidence = evidence
            log.debug(
                "probe_runner: %s match from %s reserved=%s extracted=%s",
                spec.probe_id, sender_ip, reserved, extracted,
            )
    finally:
        try:
            sock.close()
        except OSError:
            pass

    if not observations:
        if send_errors and len(send_errors) == len(targets):
            return _nothing("could not send: " + "; ".join(send_errors))
        return _nothing("no reply")
    return observations


# ---------------------------------------------------------------------------
# TCP active probe runner
# ---------------------------------------------------------------------------


async def _run_follow_up(
    step: "ProbeFollowUp",
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    probe_id: str,
    target: str,
    port: int,
) -> tuple[bool, bytes]:
    """Run a probe's second exchange on the established connection.

    Returns whether the step's expectation held, and the bytes it read.

    For ``expect_silence`` the whole timeout is spent proving a negative, so it
    cannot short-circuit the way a matcher can: the device gets the full window
    to answer, and only an empty read counts as a pass. That is the price of
    identifying by absence, and it is why the step carries its own (usually
    shorter) ``timeout_ms``.
    """
    timeout = step.timeout_ms / 1000.0
    writer.write(step.send)
    try:
        await asyncio.wait_for(writer.drain(), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        pass

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    acc = bytearray()
    while loop.time() < deadline and len(acc) < _MAX_RESPONSE_BYTES:
        remaining = deadline - loop.time()
        # A silence step waits out the full window; a matching step may stop
        # early once its pattern lands.
        read_timeout = (
            remaining if step.expect_silence or not acc
            else min(_PROBE_READ_QUIET_SECONDS, remaining)
        )
        try:
            chunk = await asyncio.wait_for(
                reader.read(_MAX_RESPONSE_BYTES - len(acc)),
                timeout=read_timeout,
            )
        except (TimeoutError, asyncio.TimeoutError):
            break
        if not chunk:
            break  # peer closed
        acc += chunk
        if step.expect_silence:
            break  # it spoke; that is the answer, no need to read more
        if _matches(bytes(acc), step.response_match):
            break

    reply = bytes(acc)
    if step.expect_silence:
        ok = not acc
        log.debug(
            "probe_runner: %s follow-up silence check on %s:%d -> %s (%d bytes)",
            probe_id, target, port, "silent" if ok else "answered", len(acc),
        )
        return ok, reply
    return _matches(reply, step.response_match), reply


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

    ``observe_tcp_active_probe`` is the same run with the whole exchange
    kept, matched or not.
    """
    observation = await observe_tcp_active_probe(
        spec, target=target, source_ip=source_ip,
        stagger_ms=stagger_ms, rate_limiter=rate_limiter,
    )
    return observation.evidence


async def observe_tcp_active_probe(
    spec: CustomProbeSpec,
    *,
    target: str,
    source_ip: str,
    stagger_ms: float = 0.0,
    rate_limiter: RateLimiter | None = None,
) -> ProbeObservation:
    """``run_tcp_active_probe``, returning the exchange as an observation.

    The observation holds what was sent, every byte read (up to the cap),
    the certificate subject on a TLS probe, the ``then:`` step's bytes and
    verdict, the connect and first-reply times, and why the exchange failed
    when it did. ``evidence`` is set exactly when ``run_tcp_active_probe``
    would return it.
    """
    if spec.kind != "tcp":
        raise ValueError(f"run_tcp_active_probe got non-tcp spec: {spec.kind!r}")

    if stagger_ms > 0:
        await asyncio.sleep(stagger_ms / 1000.0)

    # Global send-rate cap: acquire a slot before the SYN so a scan with many
    # TCP-probe drivers and many matching hosts stays under the shared limit.
    if rate_limiter is not None:
        await rate_limiter.acquire()

    obs = ProbeObservation(
        probe_id=spec.probe_id, kind="tcp", port=spec.port, target=target,
        tls=bool(spec.tls),
    )
    loop = asyncio.get_event_loop()
    began = loop.time()

    def _ms_since_start() -> float:
        return round((loop.time() - began) * 1000.0, 1)

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
        obs.error = _connect_error(exc)
        obs.elapsed_ms = _ms_since_start()
        return obs
    obs.connect_ms = _ms_since_start()

    match = spec.response_match
    has_matcher = (
        match.starts_with is not None
        or match.contains is not None
        or match.regex is not None
    )
    # A cert-only probe (identify by the cert subject, no send + no payload
    # matcher) must not wait for a banner: HTTPS gear like NVX only speaks after
    # a request, so reading would just burn the whole timeout for nothing.
    cert_only = spec.cert_subject is not None and not spec.send and not has_matcher

    cert_subject_str = ""
    payload = b""
    # None = no follow-up declared; True/False = it ran and passed/failed. A
    # declared follow-up that never ran (connection died first) stays None and
    # is treated as a miss below -- an unanswered question is not a pass.
    follow_up_ok: bool | None = None
    acc = bytearray()
    try:
        # Self-signed identity certs carry the model (NVX: CN=DM-NVX-E20-<mac>).
        # The observation reads it on every TLS probe; the evidence uses it
        # only when the probe matches on the cert or extracts from it.
        if spec.tls:
            obs.certificate = read_peer_certificate(writer)
            obs.cert_subject = certificate_match_text(obs.certificate)
            if spec.cert_subject is not None or spec.extract:
                cert_subject_str = obs.cert_subject

        if not cert_only:
            if spec.send:
                writer.write(spec.send)
                obs.sent = spec.send
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
            deadline = loop.time() + timeout
            while loop.time() < deadline and len(acc) < _MAX_RESPONSE_BYTES:
                remaining = deadline - loop.time()
                # Wait the full remaining budget for the first byte (a device
                # can be slow to start), but once data is flowing only wait a
                # short quiet-gap for further segments — so a multi-segment
                # banner lands while a non-matching host that sent one chunk and
                # went silent is released promptly.
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
                if not acc:
                    obs.first_reply_ms = _ms_since_start()
                acc += chunk
                if not has_matcher:
                    break  # connect-only probe: any reply is enough
                if _matches(bytes(acc), match):
                    break  # fingerprint satisfied — return immediately
            payload = bytes(acc)

        # Follow-up step, on the SAME connection. Only worth running when the
        # first exchange already matched — otherwise this is not the device.
        if spec.follow_up is not None and _matches(payload, spec.response_match):
            obs.follow_up_sent = spec.follow_up.send
            follow_up_ok, obs.follow_up_reply = await _run_follow_up(
                spec.follow_up, reader, writer, spec.probe_id, target, spec.port,
            )
    except (ConnectionResetError, BrokenPipeError, OSError) as exc:
        log.debug(
            "probe_runner: %s read from %s:%d failed: %s",
            spec.probe_id, target, spec.port, exc,
        )
        obs.error = _connect_error(exc)
        payload = payload or bytes(acc)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass

    obs.reply = payload
    obs.follow_up_ok = follow_up_ok
    obs.elapsed_ms = _ms_since_start()

    # Cert gate: a declared cert_subject must match, and stands in for the
    # payload requirement (a matched cert is signal enough on its own).
    if spec.cert_subject is not None:
        if not cert_subject_str or not spec.cert_subject.search(cert_subject_str):
            return obs
    elif not payload:
        return obs
    # The payload matcher (if any) must still pass; an empty matcher passes.
    if not _matches(payload, spec.response_match):
        return obs
    if spec.follow_up is not None and follow_up_ok is not True:
        log.debug(
            "probe_runner: %s follow-up not satisfied for %s:%d",
            spec.probe_id, target, spec.port,
        )
        return obs

    reserved, extracted = _apply_extract(payload, spec.extract)
    if cert_subject_str:
        # Extract from the cert subject too (e.g. the model out of the CN); the
        # payload takes precedence, the cert fills in what it didn't provide.
        c_reserved, c_extracted = _apply_extract(cert_subject_str.encode("utf-8"), spec.extract)
        for k, v in c_reserved.items():
            reserved.setdefault(k, v)
        for k, v in c_extracted.items():
            extracted.setdefault(k, v)

    response: dict[str, object] = {
        "text": payload.decode("latin-1", errors="replace") or cert_subject_str,
    }
    # Lift manufacturer/make to top of response so extract_vendor_strings
    # finds them; everything else lands under "extracted".
    response.update(reserved)
    if extracted:
        response["extracted"] = extracted
    if cert_subject_str:
        response["cert_subject"] = cert_subject_str

    matched_pattern = describe_response_match(spec.response_match) or None
    if spec.cert_subject is not None:
        cert_desc = f"cert:{spec.cert_subject_source}"
        matched_pattern = f"{matched_pattern}, {cert_desc}" if matched_pattern else cert_desc

    log.debug(
        "probe_runner: %s match from %s reserved=%s extracted=%s cert=%r",
        spec.probe_id, target, reserved, extracted, cert_subject_str,
    )
    obs.matched = True
    obs.evidence = evidence_active_probe(
        spec.probe_id,
        response=response,
        port=spec.port,
        matched_pattern=matched_pattern,
    )
    return obs
