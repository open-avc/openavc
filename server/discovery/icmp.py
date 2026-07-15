"""Pure-Python ICMP echo (ping) with tiered method selection.

The ping sweep historically shelled out to the system ``ping`` binary for
every host. Minimal environments (slim containers, embedded appliance
images) don't ship one, and a missing binary used to be indistinguishable
from a dead host — the whole sweep "completed" with zero results. This
module removes the hard binary dependency and makes environment failures
reportable:

- **Method selection** (:func:`select_ping_method`, probed once per scan):

  - Windows: exec the system ``ping`` (always present; raw sockets need
    Administrator).
  - POSIX tier 1: unprivileged ICMP datagram socket
    (``socket(AF_INET, SOCK_DGRAM, IPPROTO_ICMP)``) — allowed when the
    kernel's ``ping_group_range`` covers the process gid.
  - POSIX tier 2: raw ICMP socket (root / CAP_NET_RAW).
  - POSIX tier 3: exec the system ``ping``.
  - Each candidate is confirmed with a loopback self-test before it is
    chosen: a socket that opens (or a ``ping`` that execs) but can't
    actually round-trip an echo is skipped, so a method that would make the
    whole sweep read "0 alive" never wins.
  - Nothing working → :data:`METHOD_NONE`; the sweep proceeds to passive
    discovery but reports a loud environment warning.

- **Tri-state per-host results**: :data:`RESULT_ALIVE`,
  :data:`RESULT_TIMEOUT` (no answer — host treated as dead), and
  :data:`RESULT_ERROR` (the environment failed, NOT a dead host). The sweep
  accounts for errors separately so "every ping exec failed" no longer
  reads as "empty network".

Stdlib only: socket / struct / asyncio.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import platform
import shutil
import socket
import struct
from dataclasses import dataclass
from itertools import count

from server.utils.spawn import CREATE_NO_WINDOW

log = logging.getLogger("discovery.icmp")

_IS_WINDOWS = platform.system() == "Windows"

# Ping methods (see select_ping_method)
METHOD_EXEC = "exec"    # shell out to the system ping binary
METHOD_DGRAM = "dgram"  # unprivileged ICMP datagram socket
METHOD_RAW = "raw"      # raw ICMP socket (root / CAP_NET_RAW)
METHOD_NONE = "none"    # nothing available — environment failure

# Per-host outcomes
RESULT_ALIVE = "alive"
RESULT_TIMEOUT = "timeout"  # no reply / unreachable — a dead host, not an error
RESULT_ERROR = "error"      # environment failure (socket/exec failed)

# Payload marker echoed back by the target; parse_echo_reply requires it so
# stray ICMP traffic on a raw socket can't count as a reply.
ECHO_PAYLOAD = b"openavc-discovery"

# The network answering "no route / host down" for a specific target is a
# dead-host result, not an environment failure. Async ICMP errors surface as
# these errnos on a connected datagram socket.
_HOST_DEAD_ERRNOS = {
    e for e in (
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETUNREACH", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "ENETDOWN", None),
        getattr(errno, "ECONNREFUSED", None),
        getattr(errno, "ETIMEDOUT", None),
    ) if e is not None
}

# Per-process echo sequence numbers. Each concurrent ping gets a distinct
# seq, so a raw socket (which can see replies meant for other sockets on
# rare stacks) still matches only its own.
_seq_counter = count(1)


@dataclass
class PingSweepStats:
    """Accounting for one ping sweep, consumed by the scan-status warnings."""

    method: str = ""
    total: int = 0
    alive: int = 0
    timeouts: int = 0
    errors: int = 0


def checksum_rfc1071(data: bytes) -> int:
    """Internet checksum (RFC 1071): one's-complement sum of 16-bit words."""
    if len(data) % 2:
        data += b"\x00"
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return ~total & 0xFFFF


def build_echo_request(ident: int, seq: int, payload: bytes = ECHO_PAYLOAD) -> bytes:
    """Build an ICMP echo request (type 8) packet."""
    header = struct.pack("!BBHHH", 8, 0, 0, ident & 0xFFFF, seq & 0xFFFF)
    csum = checksum_rfc1071(header + payload)
    return struct.pack("!BBHHH", 8, 0, csum, ident & 0xFFFF, seq & 0xFFFF) + payload


def _is_echo_reply(pkt: bytes, expected_seq: int, payload: bytes) -> bool:
    if len(pkt) < 8:
        return False
    ptype, code, _csum, _ident, seq = struct.unpack("!BBHHH", pkt[:8])
    # The identifier is NOT matched: Linux rewrites it on datagram ICMP
    # sockets (the kernel demuxes replies per socket), so seq + payload are
    # the stable correlators across both socket tiers.
    if ptype != 0 or code != 0 or seq != expected_seq & 0xFFFF:
        return False
    if checksum_rfc1071(pkt) != 0:
        return False
    return pkt[8:8 + len(payload)] == payload


def parse_echo_reply(data: bytes, expected_seq: int, payload: bytes = ECHO_PAYLOAD) -> bool:
    """True if ``data`` is an echo reply matching ``expected_seq`` + payload.

    Accepts the packet with or without a leading IPv4 header — raw sockets
    (and BSD datagram ICMP sockets) deliver the IP header, Linux datagram
    sockets don't.
    """
    if _is_echo_reply(data, expected_seq, payload):
        return True
    if len(data) >= 28 and (data[0] >> 4) == 4:
        ihl = (data[0] & 0x0F) * 4
        return _is_echo_reply(data[ihl:], expected_seq, payload)
    return False


def _try_icmp_socket(kind: int) -> socket.socket | None:
    try:
        return socket.socket(socket.AF_INET, kind, socket.IPPROTO_ICMP)
    except OSError:
        return None


# Loopback self-test: 127.0.0.1 always answers an echo immediately, so a
# method that can't return RESULT_ALIVE for it can't carry a real sweep.
_SELFTEST_HOST = "127.0.0.1"
_SELFTEST_TIMEOUT = 1.0


async def _method_round_trips(method: str) -> bool:
    """True if ``method`` actually pings loopback and gets its echo back.

    Guards against the silent failure where a socket the kernel hands out (or
    a ``ping`` binary on PATH) opens fine but can't receive replies — which
    would otherwise make every host in the sweep look dead.
    """
    try:
        result = await ping_host(
            _SELFTEST_HOST, timeout=_SELFTEST_TIMEOUT, method=method,
        )
    except OSError:
        return False
    return result == RESULT_ALIVE


async def select_ping_method() -> str:
    """Probe the best WORKING ping method. Cheap — called once per scan.

    Each candidate must both initialize and pass a loopback self-test, so a
    method that silently can't round-trip an echo is skipped rather than
    making a live network read as empty.
    """
    if _IS_WINDOWS:
        # ping.exe is always present and functional; raw sockets would need
        # Administrator. The silent empty-network failure mode doesn't occur
        # here, so no self-test.
        return METHOD_EXEC

    for method, kind in (
        (METHOD_DGRAM, socket.SOCK_DGRAM),
        (METHOD_RAW, socket.SOCK_RAW),
    ):
        sock = _try_icmp_socket(kind)
        if sock is None:
            continue
        sock.close()
        if await _method_round_trips(method):
            return method
        log.debug("ICMP %s socket opened but failed loopback self-test", method)

    if shutil.which("ping") and await _method_round_trips(METHOD_EXEC):
        return METHOD_EXEC

    return METHOD_NONE


async def ping_host(
    ip: str,
    timeout: float = 1.0,
    source_ip: str = "",
    method: str = METHOD_EXEC,
) -> str:
    """Ping one host. Returns RESULT_ALIVE, RESULT_TIMEOUT, or RESULT_ERROR.

    Args:
        source_ip: Bind to this source address on multi-homed hosts.
        method: One of the METHOD_* constants from select_ping_method().
    """
    if method in (METHOD_DGRAM, METHOD_RAW):
        return await _ping_socket(ip, timeout, source_ip, method)
    if method == METHOD_EXEC:
        return await _ping_exec(ip, timeout, source_ip)
    return RESULT_ERROR


async def _ping_socket(ip: str, timeout: float, source_ip: str, method: str) -> str:
    """Send one echo request over an ICMP socket and await the reply."""
    kind = socket.SOCK_DGRAM if method == METHOD_DGRAM else socket.SOCK_RAW
    seq = next(_seq_counter) & 0xFFFF
    packet = build_echo_request(os.getpid() & 0xFFFF, seq)
    loop = asyncio.get_running_loop()
    sock: socket.socket | None = None
    try:
        sock = socket.socket(socket.AF_INET, kind, socket.IPPROTO_ICMP)
        sock.setblocking(False)
        if source_ip:
            sock.bind((source_ip, 0))
        # connect() scopes the socket to the target: replies and async ICMP
        # errors are routed to it, and a connected raw socket only receives
        # packets from this source address. Called directly, NOT via
        # loop.sock_connect(): datagram/raw connect just records the peer
        # and returns immediately, while asyncio would getaddrinfo() the
        # literal IP with this socket's type/proto — glibc rejects that
        # combination for ICMP sockets (gaierror: ai_socktype not
        # supported), which broke every socket-tier ping.
        sock.connect((ip, 0))
        await loop.sock_sendall(sock, packet)

        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return RESULT_TIMEOUT
            try:
                data = await asyncio.wait_for(
                    loop.sock_recv(sock, 2048), timeout=remaining,
                )
            except asyncio.TimeoutError:
                return RESULT_TIMEOUT
            if parse_echo_reply(data, seq):
                return RESULT_ALIVE
            # Unrelated ICMP (raw sockets see the host's other traffic) —
            # keep waiting until the deadline.
    except OSError as exc:
        if exc.errno in _HOST_DEAD_ERRNOS:
            return RESULT_TIMEOUT
        return RESULT_ERROR
    finally:
        if sock is not None:
            sock.close()


async def _ping_exec(ip: str, timeout: float, source_ip: str) -> str:
    """Shell out to the system ping binary (one echo)."""
    if _IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000))]
        if source_ip:
            cmd.extend(["-S", source_ip])
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout)))]
        if source_ip:
            cmd.extend(["-I", source_ip])
    cmd.append(ip)

    try:
        # CREATE_NO_WINDOW: a sweep spawns one ping per address — without it,
        # a console-less server (in-app restart) pops hundreds of windows.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError:
        # Binary missing or exec failed — an environment failure, NOT a
        # dead host. This is the distinction the old code collapsed.
        return RESULT_ERROR

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout + 2)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RESULT_TIMEOUT
    return RESULT_ALIVE if proc.returncode == 0 else RESULT_TIMEOUT
