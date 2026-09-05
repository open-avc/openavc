"""Classify why a device connection failed into an actionable offline reason.

One shared classifier for every transport (SSH / TCP / serial / UDP / OSC /
HTTP). When a device fails to connect — or drops mid-session — the device
manager feeds the transport's last error string plus the connect exception to
``classify_connection_fault``. It pattern-matches the combination against a
fixed taxonomy and returns a stable ``code`` (used by triggers and automation
as ``device.<id>.offline_reason``) and a human ``message`` (shown on the device
card as ``device.<id>.offline_detail``).

It also owns the CHILD-entity fault vocabulary at the bottom of the file --
the same question ("why is this thing not working") one level down, for a
sub-unit of a device rather than the device itself. Those are asserted by the
driver rather than derived here, because nothing the platform can see tells a
wedged endpoint from an absent one, but they share this home so there is one
vocabulary and one place the frontend maps a code to copy. The one exception is
`parent_offline`, which the platform asserts and a driver may not: when the
connection to the parent drops there is nothing the driver can still see, so
the only honest statement about every child under it is the platform's.

This is the single place that owns both taxonomies: pure / stdlib, no I/O, and
no driver- or transport-specific branching beyond the small amount the taxonomy
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
TLS_CERT_UNTRUSTED = "tls_cert_untrusted"  # HTTPS/TLS cert couldn't be verified
NO_RESPONSE = "no_response"
CLIENT_MISSING = "client_missing"
INVALID_CONFIG = "invalid_config"  # bad connection settings (baud/parity/port/...)
TRANSPORT_DISCONNECTED = "transport_disconnected"  # generic fallback
BRIDGE_OFFLINE = "bridge_offline"  # a bridge-routed device whose bridge is down
NO_SIMULATOR = "no_simulator"  # simulating, but this driver ships no simulator


@dataclass(frozen=True)
class ConnectionFault:
    """A classified connection failure.

    ``code`` is the stable machine token; ``message`` is the integrator-facing
    sentence. The two are always set together.
    """

    code: str
    message: str


# Codes a driver may declare on a ConnectionFaultError. bridge_offline is
# excluded on purpose — it's assigned by the DeviceManager when it mirrors a
# bridge's state onto dependents, never raised from inside a driver.
_DRIVER_FAULT_CODES = frozenset({
    AUTH_FAILED,
    CONNECTION_REFUSED,
    UNREACHABLE,
    HOST_KEY_REJECTED,
    NO_RESPONSE,
    CLIENT_MISSING,
    INVALID_CONFIG,
    TRANSPORT_DISCONNECTED,
})

# Canonical generic wording per code, used when a typed fault carries no
# message of its own. Branch-specific wording in classify_connection_fault()
# stays richer (endpoint interpolation, transport-specific hints).
_DEFAULT_MESSAGES = {
    AUTH_FAILED: (
        "Authentication failed. Check the username and password, or "
        "install the OpenAVC key on the device."
    ),
    CONNECTION_REFUSED: (
        "Connection refused on {where}. Is the service enabled and the "
        "port correct?"
    ),
    UNREACHABLE: "Can't reach {where}. Check the IP address and network.",
    HOST_KEY_REJECTED: (
        "The device's SSH host key changed or was rejected. Verify the "
        "device, then re-accept it."
    ),
    NO_RESPONSE: (
        "Connected, but the device didn't respond as expected. Wrong "
        "transport or protocol for this device?"
    ),
    CLIENT_MISSING: (
        "Required client not found. Install it and make sure it's on the "
        "system PATH."
    ),
    INVALID_CONFIG: (
        "The device's connection settings are invalid. Check the "
        "configuration (e.g. baud rate, parity, data bits, or port)."
    ),
    TRANSPORT_DISCONNECTED: (
        "The connection to the device dropped. OpenAVC is retrying "
        "automatically."
    ),
}


def default_fault_message(code: str, where: str = "the device") -> str:
    """The taxonomy's standard integrator-facing sentence for ``code``."""
    template = _DEFAULT_MESSAGES.get(code) or _DEFAULT_MESSAGES[TRANSPORT_DISCONNECTED]
    return template.format(where=where)


# Faults that retrying can never heal: the fix is a human changing something
# (credentials, a host key, a certificate trust decision, a connection setting,
# or installing a missing client). Retrying one of these for an hour only burns
# CPU and fills the log — and for auth it actively harms, since devices with
# brute-force lockouts block the source IP after a handful of failures. The
# device manager stops reconnecting when it classifies one of these; a config
# edit or the Reconnect button starts a fresh attempt.
#
# Everything else (unreachable, connection_refused, no_response,
# bridge_offline, transport_disconnected) is a network condition that can and
# does heal on its own, so those keep retrying.
_PERMANENT_FAULT_CODES = frozenset({
    AUTH_FAILED,
    HOST_KEY_REJECTED,
    TLS_CERT_UNTRUSTED,
    INVALID_CONFIG,
    CLIENT_MISSING,
})


def is_permanent_fault(code: str) -> bool:
    """True when ``code`` names a fault that retrying can't fix on its own."""
    return code in _PERMANENT_FAULT_CODES


# --- Child entities ---------------------------------------------------------
# A device's fault codes above answer "why did the CONNECTION fail". A child
# entity's answer a different question, because by the time one is in trouble
# the connection to its parent is usually fine: the controller is talking to
# us happily and telling us that one of the things it manages is not right.
#
# They live in this module anyway, beside the device set, because there is one
# question here ("why is this thing not working") and it should have one home,
# one vocabulary and one place the frontend maps codes to copy. Splitting them
# would leave two modules both answering it.
#
# Almost all of these are never derived — nothing the platform can see
# distinguishes a wedged endpoint from an absent one. Only the driver knows, so
# the driver asserts one, exactly as it already asserts `online`.
#
# `parent_offline` is the ONE exception, and it is here because the platform
# genuinely does know it: when the connection to the parent device drops,
# nothing under that device can be seen at all, whatever the driver last said.
# A driver cannot assert it (its own transport is gone), so the platform does —
# see BaseDriver._children_follow_parent, which is the only writer.
#
# The set is deliberately small: every code here earns its place by having a
# DIFFERENT REMEDY from the others, which is the whole point of the item that
# added them (an endpoint somebody carried out of the rack and an endpoint
# sitting right there with a wedged service both read `online: false`, and one
# means go find it while the other means power-cycle it). A driver that knows
# something is wrong but not what leaves the reason empty and sets
# `online: False` — that is what every driver does today and it degrades
# correctly, which is better than a code meaning "dunno".

#: The device lists it, and it is not answering. Go find it — power, cabling,
#: network. This is absence, whether it was unplugged or has left the building.
CHILD_NOT_RESPONDING = "not_responding"
#: It IS answering — presence is fine, its control channel is live — but the
#: function it exists to perform is not running. Power-cycle or restart it.
#: This is the one the boolean could never say.
CHILD_SERVICE_FAULT = "service_fault"
#: The slot is empty: this id is a place something CAN be, and nothing is
#: there. Not a fault, and nothing to go and fix — a mixer with no AT-LINK
#: extensions chained to it is a correctly-configured mixer. Only a roster
#: whose driver reports presence (`instances.presence: reported`) uses it; on
#: an `assumed` roster every id is fitted by definition.
CHILD_NOT_FITTED = "not_fitted"
#: The parent device is unreachable, so nothing under it can be seen. Platform-
#: asserted, never a driver's to claim — the fix is on the device card above,
#: not on the child. Clears itself when the device reconnects.
CHILD_PARENT_OFFLINE = "parent_offline"
# A fifth code, `disabled` (off on purpose, not a fault), was drafted and cut
# before it shipped. The one case in the corpus that looked like it — an MXNet
# destination whose video path is disabled — is what pressing Off on a matrix
# destination produces, so it is ordinary operation and `source_video: ""`
# already says it. A code that fires on a normal state is noise, and noise in
# this particular field is the thing the whole feature exists to remove. Add it
# when a device genuinely reports "taken out of service".

#: Every code that can appear in `device.<id>.<type>.<lid>.offline_reason`, from
#: any writer. The empty string is always allowed and means "nothing claimed".
CHILD_FAULT_CODES: frozenset[str] = frozenset({
    CHILD_NOT_RESPONDING,
    CHILD_SERVICE_FAULT,
    CHILD_NOT_FITTED,
    CHILD_PARENT_OFFLINE,
})

#: The subset a DRIVER may assert through ``BaseDriver.child_fault``.
#: `parent_offline` is excluded for the same reason `bridge_offline` is excluded
#: from the device set above: it is the platform's own statement about a
#: connection the driver no longer has, and a driver writing it would be
#: claiming something it cannot see — the platform would clear it on the next
#: reconnect anyway.
CHILD_DRIVER_FAULT_CODES: frozenset[str] = frozenset({
    CHILD_NOT_RESPONDING,
    CHILD_SERVICE_FAULT,
    CHILD_NOT_FITTED,
})

#: Codes that mean something is WRONG, as opposed to something being absent by
#: design. The distinction is what the IDE counts and banners on: a chassis with
#: seven empty extension slots is not "7 down", and saying so would trade one
#: false alarm for another. Mirrored in the frontend by childPresence.ts.
CHILD_TROUBLE_CODES: frozenset[str] = CHILD_FAULT_CODES - {CHILD_NOT_FITTED}

#: The sentence each code gets when the driver does not word its own. A driver
#: SHOULD word its own where it knows more (which service, which port), and
#: these keep a bare code from reaching a person as a bare code.
_CHILD_DEFAULT_MESSAGES = {
    CHILD_NOT_RESPONDING: (
        "Not answering. Check that it has power and a network connection."
    ),
    CHILD_SERVICE_FAULT: (
        "Reachable, but not running. Power-cycle it, or restart it from the "
        "controller."
    ),
    CHILD_NOT_FITTED: "Nothing is connected here.",
    CHILD_PARENT_OFFLINE: (
        "The device is offline, so this can't be checked. See the device's own "
        "status above."
    ),
}


def default_child_fault_message(code: str) -> str:
    """The standard sentence for a child fault ``code``, or "" if unknown.

    Empty rather than a generic fallback on purpose: an unrecognised code is
    a driver bug, and inventing a confident sentence for it would hide that.
    """
    return _CHILD_DEFAULT_MESSAGES.get(code, "")


def is_child_fault_code(code: str) -> bool:
    """True when ``code`` is one this taxonomy defines."""
    return code in CHILD_FAULT_CODES


def is_child_trouble_code(code: str) -> bool:
    """True when ``code`` means something is WRONG rather than absent by design.

    An unknown code counts as trouble: a driver writing a code this taxonomy
    does not define is a bug, and the safe reading of "I don't recognise this"
    is not "everything is fine".
    """
    return bool(code) and code != CHILD_NOT_FITTED


class ConnectionFaultError(ConnectionError):
    """A connection failure carrying an explicit, pre-classified fault code.

    Drivers raise this instead of wording a plain ConnectionError so a
    substring in the classifier's signature tables happens to match. The
    classifier honors ``code`` before any string matching, so the driver
    states its meaning once, explicitly — the message is free to say
    whatever is most useful to the integrator (it becomes
    ``offline_detail``; when empty, the taxonomy's standard wording for the
    code is used). Re-exported from ``openavc.drivers.base`` for drivers.

    Unknown codes fail at construction: a typo'd code would silently
    misclassify forever, and every raise site should be covered by a test
    that trips it immediately.
    """

    def __init__(self, message: str = "", *, code: str):
        if code not in _DRIVER_FAULT_CODES:
            raise ValueError(
                f"Unknown connection-fault code {code!r}. Valid codes: "
                f"{', '.join(sorted(_DRIVER_FAULT_CODES))}"
            )
        super().__init__(message)
        self.fault_code = code


def typed_fault_from_exc(
    exc: BaseException | None,
    *,
    host: str = "",
    port: object = None,
) -> ConnectionFault | None:
    """The ConnectionFault declared by a :class:`ConnectionFaultError` in
    ``exc``'s cause chain, or None when nothing typed is present."""
    for node in _exc_chain(exc):
        if isinstance(node, ConnectionFaultError):
            message = str(node).strip() or default_fault_message(
                node.fault_code, _endpoint(host, port)
            )
            return ConnectionFault(node.fault_code, message)
    return None


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

# TLS certificate verification failed — the HTTPS analog of a rejected SSH host
# key: the peer's identity couldn't be verified (self-signed, expired, wrong
# hostname, or an unknown issuer). Every OpenSSL verification failure carries
# "certificate verify failed"; the others are extra specificity. Checked right
# after host-key so it wins over the generic buckets and can point the user at
# the "Verify SSL Certificate" toggle (self-signed certs are common on AV gear).
_TLS_CERT_SIGS = (
    "certificate verify failed",
    "certificate_verify_failed",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
    "certificate has expired",
    "sslcertverificationerror",
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
    # MQTT CONNACK rejections (rc 4 / rc 5). Checked before the generic
    # "connection refused" bucket so wrong broker credentials read as auth.
    "not authorized",
    "not authorised",
    "bad username or password",
    "bad user name or password",
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


# Transport/port pairings that are almost certainly a mis-set field rather
# than a network problem. Only 22 and 23 are here, because they are the two
# ports whose protocol is genuinely universal -- a "tcp" driver may legitimately
# use any other port, so guessing beyond these would put words in the author's
# mouth. Checked only AFTER a connection has already failed with nothing more
# specific to say, so a device that really does run telnet on 22 and works is
# never touched by this.
_WELL_KNOWN_TRANSPORT_PORTS = {22: "ssh", 23: "tcp"}


def _transport_port_mismatch(transport: str, port: object) -> str | None:
    """A sentence naming the two fields that disagree, or None.

    The failure this catches reads exactly like dead hardware: changing
    Connection does not change Port, so a driver offering both ends up
    speaking telnet at an SSH port, timing out, and being told to go check
    its cabling.
    """
    try:
        port_num = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    expected = _WELL_KNOWN_TRANSPORT_PORTS.get(port_num)
    if expected is None:
        return None
    actual = (transport or "").lower()
    if actual in ("", expected):
        return None
    # telnet is spelled "tcp" in driver config; treat the alias as the same.
    if expected == "tcp" and actual in ("telnet", "tcp"):
        return None
    if expected == "ssh" and actual == "ssh":
        return None
    names = {"ssh": "ssh", "tcp": "telnet"}
    wants = names.get(expected, expected)
    has = names.get(actual, actual)
    other_port = 22 if expected == "tcp" else 23
    return (
        f"Port {port_num} is the {wants} port, but this device's Connection is "
        f"set to '{actual}' ({has}). Set Port to {other_port}, or change "
        f"Connection to '{expected}'."
    )


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

    # 0. A driver-declared typed fault wins over everything below — the
    #    driver already classified itself (ConnectionFaultError). String
    #    matching only exists for causes nobody typed.
    typed = typed_fault_from_exc(exc, host=host, port=port)
    if typed is not None:
        return typed

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

    # 3. TLS certificate couldn't be verified (self-signed / expired / wrong
    #    host / unknown issuer). Point the integrator straight at the toggle —
    #    self-signed certs are the norm on AV gear.
    if _has_any(hay, _TLS_CERT_SIGS):
        return ConnectionFault(
            TLS_CERT_UNTRUSTED,
            f"Couldn't verify the TLS certificate for {where}. If the device "
            f"uses a self-signed certificate, turn off 'Verify SSL Certificate' "
            f"for it.",
        )

    # 4. Authentication failed.
    if _has_any(hay, _AUTH_SIGS):
        return ConnectionFault(
            AUTH_FAILED,
            "Authentication failed. Check the username and password, or "
            "install the OpenAVC key on the device.",
        )

    # 5. Port closed / service off.
    if _has_refused(chain) or err_no == errno.ECONNREFUSED or _has_any(hay, _REFUSED_SIGS):
        return ConnectionFault(
            CONNECTION_REFUSED,
            f"Connection refused on {where}. Is the service enabled and the "
            f"port correct?",
        )

    # 6. Unreachable — strong signals (route, DNS, host down, connect-phase
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

    # 6b. Two config fields disagreeing, which the generic wording below reads
    #     as a network fault and sends the reader off checking cables. Sits
    #     after every specific signal (a refused port or a rejected login is
    #     its own story) and before the fallbacks, which are the ones that
    #     mislead. INVALID_CONFIG because that is exactly what this is -- and
    #     being a permanent code, it also stops a retry loop that cannot win.
    mismatch = _transport_port_mismatch(transport, port)
    if mismatch is not None:
        return ConnectionFault(INVALID_CONFIG, mismatch)

    # 7. Authed/opened but no usable response (wrong transport or protocol).
    if _has_any(hay, _NO_RESPONSE_SIGS):
        return ConnectionFault(
            NO_RESPONSE,
            "Connected, but the device didn't respond as expected. Wrong "
            "transport or protocol for this device?",
        )

    # 8. Weak timeout / unreachable signals with nothing more specific.
    if _has_any(hay, _UNREACHABLE_WEAK_SIGS):
        return ConnectionFault(
            UNREACHABLE,
            f"Can't reach {where}. Check the IP address and network.",
        )

    # 9. Fallback — an unexplained drop. Keep the existing generic wording.
    return ConnectionFault(
        TRANSPORT_DISCONNECTED,
        "The connection to the device dropped. OpenAVC is retrying "
        "automatically.",
    )


def bridge_offline_fault(bridge_label: str = "") -> ConnectionFault:
    """Offline reason for a bridge-routed device whose bridge is unavailable.

    A device that emits through a bridge (an IR device on an emitter port) has
    no transport of its own — it's reachable only while its bridge is online.
    This isn't a connection failure to classify from an error string; the
    device manager calls this directly when it mirrors a bridge's offline state
    onto its dependents. ``bridge_label`` is the bridge's display name (falls
    back to generic wording when empty).
    """
    who = (bridge_label or "").strip()
    if who:
        message = (
            f"The bridge '{who}' this device sends through is offline. "
            f"It will come back when the bridge reconnects."
        )
    else:
        message = (
            "The bridge this device sends through is offline. It will come "
            "back when the bridge reconnects."
        )
    return ConnectionFault(BRIDGE_OFFLINE, message)


def no_simulator_fault(driver_id: str = "") -> ConnectionFault:
    """Offline reason for a device left behind by a running simulation.

    Simulation redirects each device to a simulator the platform starts for
    it. A driver with no simulator gets none, so that device alone keeps
    pointing at its real address and fails there — and the classifier, reading
    a genuine refused socket, correctly reported ``connection_refused`` and
    asked whether the port was right. The port was right; nothing was
    listening, and the answer was in the server log rather than on the card.

    Auto-generation covers a YAML driver with no ``simulator:`` section but
    cannot invent one for a Python driver, whose protocol lives in code — that
    driver needs a companion ``<name>_sim.py``. Said here so the device card
    says it too.
    """
    who = (driver_id or "").strip()
    named = f"'{who}' " if who else ""
    return ConnectionFault(
        NO_SIMULATOR,
        f"Simulation is running, but the driver {named}has no simulator, so "
        f"nothing is listening for this device. Add a simulator for it "
        f"(a Python driver needs a companion _sim.py file), or stop "
        f"simulation to reach the real device.",
    )
