"""Classify why a device connection failed into an actionable offline reason.

One shared classifier for every transport (SSH / TCP / serial / UDP / OSC /
HTTP). When a device fails to connect — or drops mid-session — the device
manager feeds the transport's last error string plus the connect exception to
``classify_connection_fault``. It pattern-matches the combination against a
fixed taxonomy and returns a stable ``code`` (used by triggers and automation
as ``device.<id>.offline_reason``) and a human ``message`` (shown on the device
card as ``device.<id>.offline_detail``).

It also owns what the CHILD-entity fault codes mean, at the bottom of the file
-- the same question ("why is this thing not working") one level down, for a
sub-unit of a device: each code's sentence, which count as trouble, and the
seam every driver's child write passes (``normalize_child_fault_claim``). The
code names are the driver contract's (``drivers/spec.py``), where the checker
reads them. A driver asserts them, because nothing the platform can see tells
a wedged endpoint from an absent one; the one exception is `parent_offline`,
which only the platform writes: when the connection to the parent drops there
is nothing the driver can still see.

This is the single place that owns both taxonomies: pure / stdlib, no I/O, and
no driver- or transport-specific branching beyond the small amount the taxonomy
itself needs (serial has no auth / route / host-key semantics). Adding a new
failure signature means adding it here, not in a driver, a transport, or the
frontend.
"""

from __future__ import annotations

import asyncio
import errno
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.spec import (
    CHILD_NOT_FITTED, CHILD_NOT_RESPONDING, CHILD_PARENT_OFFLINE, CHILD_SERVICE_FAULT,
)

# --- Stable offline_reason codes -------------------------------------------
# These strings are a contract: triggers, scripts, and panels match on them,
# so don't rename an existing one without a migration.
AUTH_FAILED = "auth_failed"
CONNECTION_REFUSED = "connection_refused"
UNREACHABLE = "unreachable"
HOST_KEY_REJECTED = "host_key_rejected"
TLS_CERT_UNTRUSTED = "tls_cert_untrusted"  # HTTPS/TLS cert couldn't be verified
NO_RESPONSE = "no_response"
WRITE_STALLED = "write_stalled"  # link open, but the device stopped reading it
CLIENT_MISSING = "client_missing"
INVALID_CONFIG = "invalid_config"  # bad connection settings (baud/parity/port/...)
TRANSPORT_DISCONNECTED = "transport_disconnected"  # generic fallback
BRIDGE_OFFLINE = "bridge_offline"  # a device bound to a bridge port whose bridge is down
NO_SIMULATOR = "no_simulator"  # simulating, but nothing simulates this device (SimulatorGap says why)
LOGIN_MISSING = "login_missing"  # the project says a login is saved here, and none is
DRIVER_FAILED = "driver_failed"  # its driver raised setting it up (core/device_build_failures.py)


@dataclass(frozen=True)
class ConnectionFault:
    """A classified connection failure.

    ``code`` is the stable machine token; ``message`` is the integrator-facing
    sentence. The two are always set together.
    """

    code: str
    message: str


# Codes a driver may declare on a ConnectionFaultError. bridge_offline is
# excluded on purpose — it is the DeviceManager's to assign, because only it
# knows whether the bridge is up, and never raised from inside a driver. So is
# driver_failed: a driver that raised while being set up has no instance to
# raise anything from.
_DRIVER_FAULT_CODES = frozenset({
    AUTH_FAILED,
    CONNECTION_REFUSED,
    UNREACHABLE,
    HOST_KEY_REJECTED,
    NO_RESPONSE,
    WRITE_STALLED,
    CLIENT_MISSING,
    INVALID_CONFIG,
    TRANSPORT_DISCONNECTED,
    LOGIN_MISSING,
})

# Canonical generic wording per code, used when a typed fault carries no
# message of its own. Branch-specific wording in classify_connection_fault()
# stays richer (endpoint interpolation, transport-specific hints).
_DEFAULT_MESSAGES = {
    # What the device refused, never where to fix it: the device page shows
    # the next step under an auth_failed fault (edit, then Reconnect). The key
    # fact stays, because the page cannot know a device signs in with one.
    AUTH_FAILED: (
        "The device refused the login. A device that signs in with a key "
        "needs the OpenAVC key installed on it."
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
        "The device didn't respond as expected. Check that the driver, "
        "transport and port are right for this device."
    ),
    TLS_CERT_UNTRUSTED: (
        "Couldn't verify the TLS certificate for {where}. If the device uses "
        "a self-signed certificate, turn off 'Verify SSL Certificate' for it."
    ),
    WRITE_STALLED: (
        "The device stopped accepting data on {where}. It's still on the "
        "network but not reading — power-cycle it if it stays this way."
    ),
    CLIENT_MISSING: (
        "Required client not found. Install it and make sure it's on the "
        "system PATH."
    ),
    INVALID_CONFIG: (
        "The device's connection settings are invalid. Check the "
        "configuration (e.g. baud rate, parity, data bits, or port)."
    ),
    # Raised by BaseDriver before dialing (core/device_logins.py): the project
    # holds the "saved on this system" marker where a login goes, and this
    # system has no saved login for that device and driver.
    LOGIN_MISSING: (
        "No login for this device is saved on this system. Enter it on "
        "the device's page in the Programmer."
    ),
    TRANSPORT_DISCONNECTED: (
        "The connection to the device dropped. OpenAVC is retrying "
        "automatically."
    ),
}


_NO_ADDRESS_MESSAGE = (
    "No address is set for this device. Enter its IP address or hostname "
    "in the device's Connection Settings."
)


def no_address_message() -> str:
    """What a network device with no address to dial says: on its card (an
    ``invalid_config`` fault BaseDriver raises before dialing) and from the
    connection test, one sentence for both."""
    return _NO_ADDRESS_MESSAGE


_NO_PORT_MESSAGE = (
    "No port is set for this device, and its driver does not supply one. "
    "Enter the port in the device's Connection Settings."
)
_NO_SERIAL_PORT_MESSAGE = (
    "No serial port is set for this device. Choose one in the device's "
    "Connection Settings."
)


def no_port_message() -> str:
    """What a network device with no port to dial says: on its card (an
    ``invalid_config`` fault the transport builder raises before dialing) and
    from the connection test, one sentence for both."""
    return _NO_PORT_MESSAGE


def no_serial_port_message() -> str:
    """What a serial device with no serial port chosen says: on its card and
    from the connection test, one sentence for both."""
    return _NO_SERIAL_PORT_MESSAGE


_BRIDGE_PORT_MISSING_MESSAGE = (
    "No bridge port is chosen for this device. In Edit Device, choose the "
    "port on the bridge that the device is wired to."
)


def bridge_port_missing(config: dict[str, Any]) -> bool:
    """True when ``config`` names a bridge but no port on it.

    Such a binding never resolves, so the device has nowhere to connect, and
    its Edit Device form is on Through a bridge with no IP address field.
    Asked by the bridge resolver, which records it (core/bridge_binding.py:
    the connect door and the connection test read that record), and by the
    required check that refuses saving one (core/required_config.py).
    """
    return bool(config.get("bridge")) and not str(config.get("bridge_port") or "").strip()


def bridge_port_missing_message() -> str:
    """What a device :func:`bridge_port_missing` holds for says: on its card
    (an ``invalid_config`` fault) and from the connection test."""
    return _BRIDGE_PORT_MISSING_MESSAGE


def usb_adapter_missing_message(usb_serial: str) -> str:
    """What a device bound to a USB-to-serial adapter that is not plugged in
    says: on its card (an ``unreachable`` fault, raised before anything is
    dialled by ``transport.serial_transport.require_usb_adapter``) and from
    the connection test."""
    return (
        f"The USB serial adapter this device uses (serial number {usb_serial}) "
        "is not plugged in to this system. Plug it back in, or if it was "
        "replaced, choose the new adapter in Edit Device."
    )


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
# Everything else (unreachable, connection_refused, no_response, write_stalled,
# bridge_offline, transport_disconnected) is a network condition that can and
# does heal on its own, so those keep retrying — write_stalled included, since a
# wedged device that gets power-cycled comes back on its own.
_PERMANENT_FAULT_CODES = frozenset({
    AUTH_FAILED,
    HOST_KEY_REJECTED,
    TLS_CERT_UNTRUSTED,
    INVALID_CONFIG,
    CLIENT_MISSING,
    LOGIN_MISSING,
    DRIVER_FAILED,
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
# A driver cannot assert it (its own transport is gone), so the platform does:
# BaseDriver._children_follow_parent as the device drops and returns, and the
# declared roster for a child registered while it is away. A driver's own write
# of it is dropped (normalize_child_fault_claim).
#
# The set is deliberately small: every code here earns its place by having a
# DIFFERENT REMEDY from the others, which is the whole point of the item that
# added them (an endpoint somebody carried out of the rack and an endpoint
# sitting right there with a wedged service both read `online: false`, and one
# means go find it while the other means power-cycle it). A driver that knows
# something is wrong but not what leaves the reason empty and sets
# `online: False` — that is what every driver does today and it degrades
# correctly, which is better than a code meaning "dunno".

# The four codes themselves, and the three a driver may write, are the driver
# contract's (drivers/spec.py, imported above): the checker that refuses a
# YAML driver writing any other reads the contract alone. What each code is
# FOR is written beside it there; its sentence and whether it counts as
# trouble are below.
#
# A fifth code, `disabled` (off on purpose, not a fault), was drafted and cut
# before it shipped. The one case in the corpus that looked like it — an MXNet
# destination whose video path is disabled — is what pressing Off on a matrix
# destination produces, so it is ordinary operation and `source_video: ""`
# already says it. A code that fires on a normal state is noise, and noise in
# this particular field is the thing the whole feature exists to remove. Add it
# when a device genuinely reports "taken out of service".

#: Every code that can appear in `device.<id>.<type>.<lid>.offline_reason`, from
#: any writer. ``None`` is always allowed and means "nothing claimed" — see
#: NO_FAULT_CLAIMED below.
CHILD_FAULT_CODES: frozenset[str] = frozenset({
    CHILD_NOT_RESPONDING,
    CHILD_SERVICE_FAULT,
    CHILD_NOT_FITTED,
    CHILD_PARENT_OFFLINE,
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


def is_child_fault_code(code: str | None) -> bool:
    """True when ``code`` is one this taxonomy defines."""
    return code in CHILD_FAULT_CODES


def is_child_trouble_code(code: str | None) -> bool:
    """True when ``code`` means something is WRONG rather than absent by design.

    An unknown code counts as trouble: a driver writing a code this taxonomy
    does not define is a bug, and the safe reading of "I don't recognise this"
    is not "everything is fine".
    """
    return bool(code) and code != CHILD_NOT_FITTED


# --- "nothing claimed" ------------------------------------------------------

#: What both fault keys hold when no code is claimed — at device level and at
#: child level, from the platform and from every driver.
#:
#: It has to be one value. Both levels were falsy, so every reader inside the
#: platform agreed and the split was invisible here; the surfaces that compare
#: rather than test are the ones outside it — a `state.change` trigger, a
#: condition on `offline_reason`, a script — and to those, `None` at device
#: level and `""` one level down are two different answers to the same
#: question. `None` is the one that stays: the store already uses it for "no
#: value", the device pair has always used it, and the child pair is the newer
#: and far less-bound-against of the two.
#:
#: This does NOT mean a child claiming nothing is in service. A driver that can
#: see something is wrong but not what kind sets `online: False` and claims no
#: code — a legitimate and common state, and `online` is what tells it from a
#: healthy child. That was true when this was spelled `""` and is unchanged.
NO_FAULT_CLAIMED = None

#: The two reserved child props :func:`normalize_child_fault_claim` governs.
#: `online` is a boolean and `label` has its own meaning for `""` (nobody has
#: named this unit), so neither is normalised.
CHILD_FAULT_PROPS: frozenset[str] = frozenset({"offline_reason", "offline_detail"})


def normalize_child_fault_claim(updates: Mapping[str, Any]) -> dict[str, Any]:
    """A driver's write to one child, prop by prop, as the platform stores it.

    Every door a driver writes a child through passes it here, so what is
    stored depends on what the driver meant rather than how it was written:

    * An empty fault code or detail folds onto ``None``. ``""`` is how a YAML
      ``child_set:`` clears one and what shipped drivers write, and it never
      carried anything ``online`` was not already carrying (NO_FAULT_CLAIMED).
    * A claim of ``parent_offline`` is dropped, and the child keeps the code it
      holds. The code is the platform's, undone when the device returns, so
      claimed by a driver it would put the child back in service against what
      the device said. The platform's own writes of it do not pass here.

    Any other code is written as given: refusing a misspelt one would leave the
    child showing no fault at all. The checker refuses one a definition writes.
    """
    return {
        prop: NO_FAULT_CLAIMED if prop in CHILD_FAULT_PROPS and value == "" else value
        for prop, value in updates.items()
        if not (prop == "offline_reason" and value == CHILD_PARENT_OFFLINE)
    }


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
    # "Host key for <host> has changed and you have requested strict checking."
    # Not "host key for" alone: a client before OpenSSH 8.5 also logs "the
    # ECDSA host key for IP address ..." when a device reached by name moves
    # to a new address, and the session goes on.
    "has changed and you have requested strict checking",
    "key verification failed",
)

# ssh and the device have no algorithm of one kind in common, in ssh's log:
# "Unable to negotiate with 192.0.2.10 port 22: no matching cipher found.
# Their offer: aes128-cbc,3des-cbc". Measured on OpenSSH 10.2 for all four
# kinds below; the Windows client's MAC line is the same shape. Retrying
# never helps, and the fix is one ssh option, which each kind names (the
# "+" adds to the client's list, so a better mutual choice still wins).
_SSH_NEGOTIATION_SIG = "unable to negotiate"
_SSH_NEGOTIATION_RE = re.compile(
    r"no matching (key exchange method|host key type|cipher|mac) found\.\s*"
    r"their offer:\s*(\S+)",
    re.IGNORECASE,
)
_SSH_NEGOTIATION_KINDS = {
    "key exchange method": ("key exchange methods", "KexAlgorithms"),
    "host key type": ("host key types", "HostKeyAlgorithms"),
    "cipher": ("ciphers", "Ciphers"),
    "mac": ("MACs", "MACs"),
}

# ssh refusing an option it was given, before it dials: "command-line line 0:
# Bad SSH2 cipher spec '+x'.", or "command-line: line 0: Bad configuration
# option: x". A device's SSH options, or an algorithm this client does not
# have at all (DSA host keys are gone from OpenSSH 10).
_SSH_OPTION_REFUSED_RE = re.compile(r"command-line:? line \d+:\s*(.+)", re.IGNORECASE)


def _ssh_negotiation_fault(text: str) -> ConnectionFault:
    match = _SSH_NEGOTIATION_RE.search(text)
    if match is None:
        return ConnectionFault(
            INVALID_CONFIG,
            "This device's SSH encryption settings and OpenAVC's have nothing "
            "in common. Check the SSH settings on the device.",
        )
    noun, keyword = _SSH_NEGOTIATION_KINDS[match.group(1).lower()]
    offer = match.group(2).rstrip(".,")
    return ConnectionFault(
        INVALID_CONFIG,
        f"This device only offers older SSH encryption settings that OpenAVC "
        f"does not use unless told to ({noun}: {', '.join(offer.split(','))}). "
        f"To connect anyway, add {keyword}=+{offer} to SSH options in Edit "
        f"Device, under Advanced.",
    )


def _ssh_option_refused_fault(match: re.Match[str]) -> ConnectionFault:
    detail = match.group(1).strip().rstrip(".")
    return ConnectionFault(
        INVALID_CONFIG,
        f"The SSH client on the OpenAVC server can't use one of this device's "
        f"SSH options: {detail}. Fix or remove it in SSH options in Edit "
        f"Device, under Advanced.",
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

# ssh's keep-alive giving up on a session that was up ("Timeout, server <host>
# not responding."): the device stopped answering mid-session, so the session
# dropped. Checked before no_response, whose "not responding" it contains and
# whose advice (check the driver, transport and port) is wrong for a session
# that was working.
_SSH_KEEPALIVE_SIGS = (
    "timeout, server",
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


# ERROR_NETWORK_UNREACHABLE and ERROR_HOST_UNREACHABLE. Windows raises these
# from an overlapped connect (asyncio's proactor loop) with errno EINVAL, so
# the errno check below never sees them, and the message ("The network
# location cannot be reached") is translated on a non-English system. A
# socket bound to an adapter that has no route to the device (Settings >
# Network > Reach devices through set to another network) fails this way.
_UNREACHABLE_WINERRORS = (1231, 1232)


def _winerror_of(chain: list[BaseException]) -> int | None:
    for node in chain:
        code = getattr(node, "winerror", None)
        if isinstance(code, int):
            return code
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


# The scheme typed in front of an address (``http://``), when there is one.
_SCHEME = re.compile(r"\s*[a-z][a-z0-9+.\-]*://", re.IGNORECASE)


def _login_masked(host: str) -> str:
    """``host`` with the password of a login typed into it shown as ``***``.

    ``admin:secret@10.0.0.5`` and ``http://admin:secret@cam.local`` keep the
    username and the address; an HTTP device dials such a host with that
    login. Everything between the first colon after the scheme and the last
    ``@`` is the password, so one holding an ``@`` or a ``/`` is masked whole.
    A host with no ``@`` after its first colon (a port, an IPv6 address,
    ``user@host``) is returned as it is.
    """
    scheme = _SCHEME.match(host)
    colon = host.find(":", scheme.end() if scheme else 0)
    at = host.rfind("@")
    if colon < 0 or at <= colon + 1:
        return host
    return f"{host[:colon + 1]}***{host[at:]}"


def _shown_host(host: str) -> str:
    """The host as a message shows it: blank when it is only spaces, quoted
    when a space is around it, a typed login's password masked."""
    if not host.strip():
        return ""
    shown = _login_masked(host)
    return f'"{shown}"' if host != host.strip() else shown


def _endpoint(host: str, port: object) -> str:
    """Render ``host:port`` for a message, degrading gracefully.

    The host is shown as it was dialed, never tidied: one with a space around
    it is quoted so the space shows, since that space is then the fault. A
    space typed into a device's settings is trimmed before it is dialed
    (``core.typed_fields``); an address that still carries one came from
    somewhere that rule does not reach. The one change is a login typed into
    the host, whose password is masked (:func:`_login_masked`): the sentence
    is published to everything that reads the device's state.
    """
    host = _shown_host(host or "")
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
        last_error: The transport's last error string (e.g. ``ssh``'s log, a
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
    #    matching only exists for causes nobody typed. A setting the
    #    transport could not be built from (framing, serial line settings,
    #    the port) arrives typed invalid_config from the transport builder
    #    (drivers/transport_ladder.py), so no message text is matched for it.
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
        if transport == "ssh" or "ssh" in hay:
            # Worded for the install type: a panel owner cannot install
            # anything, a Linux admin needs the command, Docker needs a pull.
            from openavc.system.os_programs import missing_program_sentence

            return ConnectionFault(CLIENT_MISSING, missing_program_sentence())
        return ConnectionFault(
            CLIENT_MISSING,
            "Required client not found. Install it and make sure it's on the "
            "system PATH.",
        )

    # 2. Host key changed / rejected (possible MITM) — before auth_failed.
    if _has_any(hay, _HOST_KEY_SIGS):
        return ConnectionFault(
            HOST_KEY_REJECTED,
            "The device's SSH host key changed or was rejected. Verify the "
            "device, then re-accept it.",
        )

    # 2b. ssh refused an option it was given, before it dialed. Matched on
    #     the original text: the sentence quotes ssh, and an algorithm's
    #     case is part of its name.
    refused = _SSH_OPTION_REFUSED_RE.search(f"{le}\n{ex}")
    if refused is not None:
        return _ssh_option_refused_fault(refused)

    # 2c. ssh and the device share no algorithm of one kind. Before every
    #     network bucket: the line names the device's address and port, and
    #     an unclassified one reads "dropped, retrying" for good.
    if _SSH_NEGOTIATION_SIG in hay:
        return _ssh_negotiation_fault(f"{le}\n{ex}")

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

    # 4. Authentication failed. The HTTP statuses that refuse a login are the
    #    credential check's own table, imported here because that module
    #    imports this one through drivers.base.
    from openavc.drivers.http_credential import REFUSED_LOGIN_TEXTS

    if _has_any(hay, _AUTH_SIGS + REFUSED_LOGIN_TEXTS):
        return ConnectionFault(AUTH_FAILED, _DEFAULT_MESSAGES[AUTH_FAILED])

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
        or _winerror_of(chain) in _UNREACHABLE_WINERRORS
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

    # 6c. ssh's keep-alive gave up on a session that was working: a drop.
    if _has_any(hay, _SSH_KEEPALIVE_SIGS):
        return ConnectionFault(
            TRANSPORT_DISCONNECTED, _DEFAULT_MESSAGES[TRANSPORT_DISCONNECTED],
        )

    # 7. Authed/opened but no usable response (wrong transport or protocol).
    if _has_any(hay, _NO_RESPONSE_SIGS):
        return ConnectionFault(
            NO_RESPONSE,
            "The device didn't respond as expected. Check that the driver, "
            "transport and port are right for this device.",
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
    """Offline reason for a device bound to a bridge port whose bridge is down.

    Not a connection failure to classify from an error string either time it is
    used, which is why the device manager calls this directly rather than going
    through the classifier. It reaches two kinds of device:

    * An IR device on an emitter port has no transport of its own and is
      reachable only while its bridge is online, so the manager mirrors the
      bridge's state onto it and publishes this on the way down.
    * A serial device on a pass-through port DOES have a transport, dialled at
      the bridge's own address. Left to itself it would report that socket —
      a host and port the integrator never entered — so when its bridge is
      known to be down this answers instead of the classifier.

    ``bridge_label`` is the bridge's display name (falls back to generic
    wording when empty).
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


def link_local_fault(host: str, port: object = None) -> ConnectionFault | None:
    """Why ``host`` could not be reached, when it is a 169.254 address and
    the adapter chosen in Reach devices through holds no 169.254 address of
    its own; None otherwise.

    Asked only after a connection failed: by the device card
    (``DeviceManager._set_offline_reason``) for a failure classified
    ``unreachable``, and by the connection test for any failure with nothing
    more specific, so both say the same sentence. The code stays
    ``unreachable``: the device cannot
    be reached from the chosen adapter, and an address added to it later is
    picked up by the next retry.
    """
    from openavc import reach_devices

    choice = reach_devices.missing_link_local(host)
    if choice is None:
        return None
    where = _endpoint(host, port)
    name = (choice.adapter_name or "").strip()
    if name:
        message = (
            f"Can't reach {where}. {name}, chosen in Settings > Network > Reach "
            f"devices through, has no 169.254 address to reach it from. Give "
            f"{name} one, or choose another adapter (or Every connected "
            f"adapter) there."
        )
    else:
        message = (
            f"Can't reach {where}. The adapter chosen in Settings > Network > "
            "Reach devices through has no 169.254 address to reach it from. "
            "Give it one, or choose another adapter (or Every connected "
            "adapter) there."
        )
    return ConnectionFault(UNREACHABLE, message)


# --- Why a running simulation has no simulator for a device ----------------
# The cause beside a NO_SIMULATOR fault. The simulator decides the first three
# when it turns a device down (SimulatorManager.start_device in
# openavc/simulator/engine.py); the server records REFUSED for whatever else it
# heard (an error answer, no port, no answer) and has no cause for a device it
# heard nothing about (UNKNOWN). The offline_reason
# code stays NO_SIMULATOR for every cause: only the sentence differs.
class SimulatorCause:
    """The causes, a vocabulary of their own (not offline_reason codes)."""

    MISSING = "no_simulator"  # the driver ships no simulator
    BUILD_FAILED = "build_failed"  # its simulator could not be built for this device
    START_FAILED = "start_failed"  # built, but it could not start (a port, a socket)
    REFUSED = "refused"  # anything else the server heard, or no answer at all
    UNKNOWN = "unknown"  # nothing was heard about this device


# How much of an error a card shows: its first line, cut to this many characters.
_SIMULATOR_DETAIL_CHARS = 200


@dataclass(frozen=True)
class SimulatorGap:
    """Why a device a running simulation selected has no simulator: its
    driver, the cause, and the error that went with it (or empty). Recorded by
    ``SimulationManager`` in core/simulation.py, worded by
    :func:`no_simulator_fault`."""

    driver_id: str
    cause: str = SimulatorCause.UNKNOWN
    detail: str = ""


def simulator_error_text(exc: BaseException) -> str:
    """An error as the detail of a simulator refusal: its type and the first
    line of its text, or the type alone when the text is empty, so a bare
    ``KeyError()`` still names something."""
    text = _first_line(str(exc))
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return " ".join(line.split())
    return ""


def card_detail(detail: str) -> str:
    """A refusal's detail as the card shows it: one line, bounded, with no
    closing full stop (it is set in brackets inside a sentence)."""
    line = _first_line(detail).rstrip(".")
    if len(line) > _SIMULATOR_DETAIL_CHARS:
        line = line[:_SIMULATOR_DETAIL_CHARS].rstrip() + "..."
    return line


def _no_simulator_sentence(who: str, because: str, product: bool) -> str:
    if product or not who:
        return "This device has no simulator. Stop simulation to control it."
    return (
        f"The driver '{who}' has no simulator, so this device stays disconnected "
        f"while simulation runs. Add a simulator for it (a Python driver needs a "
        f"companion _sim.py file), or stop simulation to control the real device."
    )


def _build_failed_sentence(who: str, because: str, product: bool) -> str:
    if product or not who:
        return (
            f"This device's simulator could not be built{because}. Stop "
            f"simulation to control it."
        )
    return (
        f"The simulator for the driver '{who}' could not be built for this "
        f"device{because}, so the device stays disconnected while simulation "
        f"runs. Fix the driver, or stop simulation to control the real device."
    )


def _not_started_sentence(who: str, because: str, product: bool) -> str:
    return (
        f"The simulator could not start this device{because}, so it stays "
        f"disconnected while simulation runs. Stop simulation and start it "
        f"again to retry, or leave it stopped to control the real device."
    )


def _not_running_sentence(who: str, because: str, product: bool) -> str:
    return (
        "The simulator is not running this device, so it stays disconnected "
        "while simulation runs. Stop simulation and start it again to retry, "
        "or leave it stopped to control the real device."
    )


_SIMULATOR_SENTENCES = {
    SimulatorCause.MISSING: _no_simulator_sentence,
    SimulatorCause.BUILD_FAILED: _build_failed_sentence,
    SimulatorCause.START_FAILED: _not_started_sentence,
    SimulatorCause.REFUSED: _not_started_sentence,
}


def no_simulator_fault(
    gap: SimulatorGap | None = None, *, product: bool = False,
) -> ConnectionFault:
    """A selected device has no virtual target, so hardware control is paused.

    THE wording of why, read from the recorded :class:`SimulatorGap`: a
    driver that ships no simulator is told how to give it one (a YAML driver
    simulates on its own, a Python driver needs a companion
    ``<name>_sim.py``), a simulator that could not be built or started says so
    with its error, and a device nothing was heard about claims no cause.
    ``product`` is manufacturer-owned equipment, whose installed integration
    the programmer cannot edit, so it is never told to change the driver.
    """
    gap = gap or SimulatorGap("")
    detail = card_detail(gap.detail)
    because = f" ({detail})" if detail else ""
    sentence = _SIMULATOR_SENTENCES.get(gap.cause, _not_running_sentence)
    return ConnectionFault(
        NO_SIMULATOR, sentence((gap.driver_id or "").strip(), because, product),
    )


def held_connection_fault() -> ConnectionFault:
    """A device dropped because it stopped answering, whose new connection is
    open while the automatic reconnect waits for its first answer. Shown in
    place of a reason about a connection that has since closed."""
    return ConnectionFault(
        NO_RESPONSE,
        "The device stopped answering. The connection is open again, and the "
        "device shows as online once it answers.",
    )


# --- Restarting: absence we asked for, which is not a fault -----------------
#
# A command declaring `restarts_device_for` takes the control channel away on
# purpose, so for that window the device is absent without anything being
# wrong. It gets a sentence but deliberately NO code: `offline_reason` stays
# None, which is what keeps the cloud's error tally, every alert rule and every
# `offline_reason` condition quiet without one of them having to learn a new
# value. `paused` established that shape; this is the second user of it.
#
# The sentence lives here because this module owns what `offline_detail` may
# say, and a second home for that string is how the frontend ends up wording
# its own.


def restarting_message(seconds_left: int, waking: bool = False) -> str:
    """What ``offline_detail`` says while a commanded restart is in progress.

    ``seconds_left`` is what remains of the driver's declared window, not the
    window it declared: a card opened twenty seconds in should not still be
    promising the original figure. ``waking`` is the same window opened by a
    sleeping device's wake command (``sleeps_device``).
    """
    remaining = max(1, int(seconds_left))
    unit = "second" if remaining == 1 else "seconds"
    lead = "Waking up" if waking else "Restarting"
    return f"{lead}. It should be back in about {remaining} {unit}."
