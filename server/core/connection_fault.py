"""Classify why a device connection failed into an actionable offline reason.

One shared classifier for every transport (SSH / TCP / serial / UDP / OSC /
HTTP). When a device fails to connect — or drops mid-session — the device
manager feeds the transport's last error string plus the connect exception to
``classify_connection_fault``. It pattern-matches the combination against a
fixed taxonomy and returns a stable ``code`` (used by triggers and automation
as ``device.<id>.offline_reason``) and a human ``message`` (shown on the device
card as ``device.<id>.offline_detail``).

This is the single place that owns the taxonomy: pure / stdlib, no I/O, and no
driver- or transport-specific branching beyond the small amount the taxonomy
itself needs (serial has no auth / route / host-key semantics). Adding a new
failure signature means adding it here, not in a driver, a transport, or the
frontend.
"""

from __future__ import annotations

import asyncio
import errno
from dataclasses import dataclass

# --- Stable offline_reason codes -------------------------------------------
# These strings are a contract: triggers, scripts, and panels match on them,
# so don't rename an existing one without a migration.
AUTH_FAILED = "auth_failed"
CONNECTION_REFUSED = "connection_refused"
UNREACHABLE = "unreachable"
HOST_KEY_REJECTED = "host_key_rejected"
NO_RESPONSE = "no_response"
CLIENT_MISSING = "client_missing"
TRANSPORT_DISCONNECTED = "transport_disconnected"  # generic fallback


@dataclass(frozen=True)
class ConnectionFault:
    """A classified connection failure.

    ``code`` is the stable machine token; ``message`` is the integrator-facing
    sentence. The two are always set together.
    """

    code: str
    message: str


# --- Signature tables ------------------------------------------------------
# All matched against a lowercased haystack of (last_error + str(exc)). Order
# of the checks in classify_connection_fault() matters more than these lists —
# see the comments there.

# Host-key rejection is checked first: it's a very specific SSH safety signal
# (a possible MITM) and must win over the generic "auth failed" it resembles.
_HOST_KEY_SIGS = (
    "host key verification failed",
    "remote host identification has changed",
    "host key for",  # "Host key for <host> has changed"
    "key verification failed",
)

# Authentication failures. Gated to non-serial transports by the caller order
# (a serial "Permission denied" is an OS port-permission problem, not a login).
_AUTH_SIGS = (
    "permission denied",
    "authentication failed",
    "password authentication failed",
    "auth fail",
    "access denied",
    "incorrect password",
    "login incorrect",
    "too many authentication failures",
    "unable to authenticate",
    "401 unauthorized",
    "403 forbidden",
)

# Port closed / service off.
_REFUSED_SIGS = (
    "connection refused",
    "econnrefused",
    "actively refused",  # Windows: "...actively refused it"
    "refused it",
)

# Wrong IP / not on the network — the *strong* signals that mean the socket
# could not be established at all (route, DNS, host down, or a connect-phase
# timeout). Checked before no_response so a real connect failure never gets
# mislabelled "device didn't respond".
_UNREACHABLE_STRONG_SIGS = (
    "no route to host",
    "network is unreachable",
    "host is down",
    "ehostunreach",
    "enetunreach",
    "name or service not known",  # DNS (Linux)
    "nodename nor servname",  # DNS (macOS)
    "getaddrinfo failed",  # DNS (Windows)
    "name resolution",
    "no address associated with hostname",
    "connection timed out",
    "operation timed out",
    "connect to host",  # OpenSSH connect-phase prefix: "connect to host X port N: ..."
)

# Socket opened but the device never spoke the expected protocol. These are
# post-connect wrappers raised by BaseDriver.connect()/verify() and CLI drivers
# after the transport is already up.
_NO_RESPONSE_SIGS = (
    "is not responding",
    "not responding",
    "no cli prompt",
    "no response to",
    "no banner",
    "no usable response",
    "didn't respond as expected",
    "did not respond as expected",
    "unexpected response",
)

# Weak timeout signals — checked *after* no_response so a protocol read-timeout
# stays no_response, while a bare connect timeout with no other signal still
# resolves to unreachable.
_UNREACHABLE_WEAK_SIGS = (
    "timed out",
    "timeout",
    "did not properly respond",  # Windows connect-timeout phrasing
    "unreachable",
)

# Serial open failures (missing port, busy, no permission).
_SERIAL_OPEN_SIGS = (
    "could not open",
    "no such file",
    "filenotfound",
    "permission denied",
    "access is denied",
    "device or resource busy",
    "busy",
    "errno 2",
    "errno 13",
    "errno 16",
)

# Required client binary missing (the SSH transport shells out to `ssh`).
_CLIENT_MISSING_SIGS = (
    "not found on path",
    "command not found",
    "is not recognized",  # Windows: "'ssh' is not recognized..."
    "no such file or directory: 'ssh'",
)

# Transport connect-phase wrappers — used to tell a connect timeout (→
# unreachable) from a post-connect protocol timeout (→ no_response).
_CONNECT_WRAPPERS = (
    "failed to connect to",
    "failed to open serial",
    "failed to launch ssh",
    "connect to host",
)


def _has_any(haystack: str, signatures: tuple[str, ...]) -> bool:
    return any(sig in haystack for sig in signatures)


def _exc_chain(exc: BaseException | None) -> list[BaseException]:
    """Flatten an exception and its ``__cause__`` / ``__context__`` chain.

    Transports wrap the original OSError in a ConnectionError, so the errno /
    timeout signal lives a level or two down.
    """
    out: list[BaseException] = []
    seen: set[int] = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def _errno_of(chain: list[BaseException]) -> int | None:
    for node in chain:
        no = getattr(node, "errno", None)
        if isinstance(no, int):
            return no
    return None


def _has_timeout(chain: list[BaseException]) -> bool:
    # asyncio.TimeoutError is an alias of TimeoutError on 3.11+, a distinct
    # class before — check both so connect timeouts classify on every runtime.
    return any(isinstance(n, (TimeoutError, asyncio.TimeoutError)) for n in chain)


def _has_refused(chain: list[BaseException]) -> bool:
    return any(isinstance(n, ConnectionRefusedError) for n in chain)


def _is_client_missing(hay: str, chain: list[BaseException]) -> bool:
    """True when the failure is a missing client binary (e.g. no ``ssh``)."""
    if any(isinstance(n, FileNotFoundError) for n in chain):
        # A FileNotFoundError launching the client binary. A serial
        # "/dev/tty... not found" is handled in the serial branch, so by here
        # this is the ssh/exec case.
        if _has_any(hay, ("ssh", "client", "not found", "no such file")):
            return True
    return _has_any(hay, _CLIENT_MISSING_SIGS)


def _endpoint(host: str, port: object) -> str:
    """Render ``host:port`` for a message, degrading gracefully."""
    host = (host or "").strip()
    has_port = port not in (None, "", 0)
    if host and has_port:
        return f"{host}:{port}"
    if host:
        return host
    if has_port:
        return str(port)
    return "the device"


def classify_connection_fault(
    *,
    last_error: str | None,
    exc: BaseException | None,
    host: str = "",
    port: object = None,
    transport: str = "",
) -> ConnectionFault:
    """Map a connection failure to a stable code + human message.

    Args:
        last_error: The transport's last error string (e.g. ``ssh`` stderr, a
            wrapped OSError). May be empty.
        exc: The exception raised by ``connect()`` / reconnect, if any. Its
            ``__cause__`` chain is inspected for errno / timeout signals.
        host: Target host (IP/hostname), for the message. Empty for serial.
        port: Target port (or serial path), for the message.
        transport: Transport type (``"tcp"``, ``"ssh"``, ``"serial"``, ...),
            used to disambiguate transport-specific wording.

    Returns:
        A :class:`ConnectionFault`. Never raises; an unrecognised failure
        resolves to ``transport_disconnected`` with generic wording.
    """
    le = (last_error or "").strip()
    ex = "" if exc is None else str(exc)
    hay = f"{le}\n{ex}".lower()
    transport = (transport or "").lower()
    where = _endpoint(host, port)

    chain = _exc_chain(exc)
    err_no = _errno_of(chain)

    # Serial has no auth / route / refused / host-key semantics: a serial
    # failure is almost always "can't open the port" (missing, busy, or no OS
    # permission). Handle it up front so a serial "Permission denied" never
    # masquerades as a login failure.
    if transport == "serial":
        if _has_any(hay, _SERIAL_OPEN_SIGS) or err_no in (
            errno.ENOENT, errno.EACCES, errno.EBUSY,
        ):
            return ConnectionFault(
                UNREACHABLE,
                f"Can't open serial port {where}. Check the cable, the port "
                f"path, and that no other program is using it.",
            )
        return ConnectionFault(
            TRANSPORT_DISCONNECTED,
            "The serial connection dropped. OpenAVC is retrying automatically.",
        )

    # 1. Required client binary missing (SSH shells out to `ssh`).
    if _is_client_missing(hay, chain):
        client = "the OpenSSH 'ssh' client" if transport == "ssh" or "ssh" in hay else "the required client"
        return ConnectionFault(
            CLIENT_MISSING,
            f"Required client not found ({client}). Install it and make sure "
            f"it's on the system PATH.",
        )

    # 2. Host key changed / rejected (possible MITM) — before auth_failed.
    if _has_any(hay, _HOST_KEY_SIGS):
        return ConnectionFault(
            HOST_KEY_REJECTED,
            "The device's SSH host key changed or was rejected. Verify the "
            "device, then re-accept it.",
        )

    # 3. Authentication failed.
    if _has_any(hay, _AUTH_SIGS):
        return ConnectionFault(
            AUTH_FAILED,
            "Authentication failed. Check the username and password, or "
            "install the OpenAVC key on the device.",
        )

    # 4. Port closed / service off.
    if _has_refused(chain) or err_no == errno.ECONNREFUSED or _has_any(hay, _REFUSED_SIGS):
        return ConnectionFault(
            CONNECTION_REFUSED,
            f"Connection refused on {where}. Is the service enabled and the "
            f"port correct?",
        )

    # 5. Unreachable — strong signals (route, DNS, host down, connect-phase
    #    timeout). A connect-level timeout shows up as a TimeoutError in the
    #    exception chain paired with a transport connect wrapper, distinct from
    #    a protocol read-timeout (handled as no_response below).
    if (
        err_no in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT, errno.EHOSTDOWN)
        or _has_any(hay, _UNREACHABLE_STRONG_SIGS)
        or (_has_timeout(chain) and _has_any(hay, _CONNECT_WRAPPERS))
    ):
        return ConnectionFault(
            UNREACHABLE,
            f"Can't reach {where}. Check the IP address and network.",
        )

    # 6. Authed/opened but no usable response (wrong transport or protocol).
    if _has_any(hay, _NO_RESPONSE_SIGS):
        return ConnectionFault(
            NO_RESPONSE,
            "Connected, but the device didn't respond as expected. Wrong "
            "transport or protocol for this device?",
        )

    # 7. Weak timeout / unreachable signals with nothing more specific.
    if _has_any(hay, _UNREACHABLE_WEAK_SIGS):
        return ConnectionFault(
            UNREACHABLE,
            f"Can't reach {where}. Check the IP address and network.",
        )

    # 8. Fallback — an unexplained drop. Keep the existing generic wording.
    return ConnectionFault(
        TRANSPORT_DISCONNECTED,
        "The connection to the device dropped. OpenAVC is retrying "
        "automatically.",
    )
