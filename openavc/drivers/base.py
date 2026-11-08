"""
OpenAVC BaseDriver — abstract base class for all device drivers.

Every device driver inherits from this class. A driver encapsulates all
knowledge of how to communicate with a specific piece of AV equipment.

Subclasses must:
    1. Set DRIVER_INFO with metadata, commands, state variables, config schema
    2. Implement send_command()
    3. Optionally override connect(), disconnect(), on_data_received(), poll()

Auto-transport: The default connect() reads DRIVER_INFO["transport"] and
self.config to create a TCP or serial transport automatically. Drivers with
custom connection logic (e.g., PJLink greeting handshake) can override
connect() as before.
"""

from __future__ import annotations

import asyncio
import functools
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from openavc.core.connection_fault import (
    INVALID_CONFIG,
    LOGIN_MISSING,
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    ConnectionFault,
    ConnectionFaultError,
    bridge_port_missing,
    bridge_port_missing_message,
    classify_connection_fault,
    default_fault_message,
    no_address_message,
    typed_fault_from_exc,
)
from openavc.core.device_logins import SAVED_LOGIN, marked_fields
from openavc.core.device_traffic import record_traffic
from openavc.core.event_bus import EventBus
from openavc.drivers.child_entities import ChildEntities
# What a contract observer can be told lives in its own module; these names
# stay importable from here, the path drivers use.
from openavc.drivers.contract_events import (  # noqa: F401
    CHILD_UNREGISTERED,
    COERCION_FAILURE,
    CONTRACT_EVENTS,
    TYPE_MISMATCH,
    UNDECLARED_STATE,
    UNKNOWN_COMMAND,
    UNMATCHED_RESPONSE,
)
from openavc.drivers.liveness import LivenessWatch
from openavc.drivers.polling import PollLoop
# The push channel lives in its own module; these two helpers stay importable
# from here, the path core/push_ports.py and drivers use.
from openavc.drivers.push import PushChannel, push_port, resolve_push_value  # noqa: F401
# Building a device's transport from its config lives in its own module;
# _ssh_option_list stays importable from here.
from openavc.drivers.transport_ladder import TransportLadder, _ssh_option_list  # noqa: F401
# The runtime gate for command params and setting values lives in its own
# module; these names stay importable from here, the path drivers use.
from openavc.drivers.param_validation import (  # noqa: F401
    CommandParamError,
    DeviceSettingValueError,
    UnknownDeviceSettingError,
    missing_required_params,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from openavc.core.state_store import StateStore, is_flat_primitive
from openavc.transport.http_client import capture_driver_clients, capture_driver_websockets
from openavc.transport.udp import UDPTransport
from openavc.utils.log_redaction import (
    collect_identity_values,
    collect_secret_values,
    get_secret_registry,
)
from openavc.utils.logger import get_logger

# Re-exported for drivers: raise ConnectionFaultError(msg, code=...) instead of
# wording a plain ConnectionError to hit the classifier's signature tables.
__all__ = [
    "BaseDriver",
    "CommandParamError",
    "ConnectionFaultError",
    "DeviceSettingValueError",
    "UndeclaredStateError",
    "UnknownCommandError",
    "normalize_and_validate_command_params",
    "strict_driver_state",
    "validate_device_setting_value",
]

log = get_logger(__name__)

# The transports that dial a network address of their own (``config["host"]``).
_ADDRESSED_TRANSPORTS = frozenset({"tcp", "udp", "osc", "http", "ssh", "mqtt", "snmp"})

# An httpx client a driver creates for itself is recorded for its device like
# the platform's own transports, with nothing in the driver; it and a
# WebSocket the driver opens itself reach local equipment without a proxy.
capture_driver_clients()
capture_driver_websockets()

# Env var that turns driver-contract violations from a warning into a raise.
# Set in openavc's own tests/conftest.py and in a driver author's test harness,
# so a driver whose code writes something its DRIVER_INFO never declared fails
# the suite the author is already running instead of shipping silently.
STRICT_DRIVER_STATE_ENV = "OPENAVC_STRICT_DRIVER_STATE"

def strict_driver_state() -> bool:
    """True when the platform should hard-fail on a driver contract violation
    rather than warn.

    Read from the environment on every call rather than cached at import: the
    flag exists to be turned on around a single driver (a test, the Builder's
    Test tab), and an import-time constant cannot be. It costs nothing on a
    correct driver, because the only caller is the violation path itself.
    """
    raw = os.environ.get(STRICT_DRIVER_STATE_ENV, "")
    return raw.strip().lower() in ("1", "true", "yes", "on")


class ConnectEndedError(ConnectionError):
    """A connect was ended before it finished, by a disconnect or a newer
    connect of the same driver (the Reconnect button, a pause, a resume).

    The door that ended it owns the device now, so its caller reports nothing
    for it and starts no retry (core/device_manager.py).
    """


# What a disconnect resolves a connect's drop future with (_run_handshake).
_CONNECT_ENDED = object()


class CommandPartialError(RuntimeError):
    """The command changed the device and then could not finish or confirm it.

    Distinct from an outright failure because the two ask different things of
    whoever reads them: a failed command left nothing behind, while this one
    did something and stopped partway, so a person has to go and look.

    The case that named it: a PoE power-cycle cuts a port, waits, and restores
    it. When the port being cycled is the one carrying the control session,
    the restore has no path left to travel down -- the endpoint stays dark
    while the operator is told the command failed, which reads as "nothing
    happened" and is the opposite of what is true.

    Subclasses RuntimeError rather than ValueError so it cannot land in the
    device-not-found branch. The message is user-facing and actionable --
    surface it verbatim, including what was left in what state.
    """


class CommandRefusedError(RuntimeError):
    """The device refused the command, and the message says why.

    Raise it with the device's own reason ("Encoder 001 is offline") or a
    sentence of the driver's about the refusal. Send Command and a device
    action answer it with that sentence; any other exception a driver raises
    while running a command is kept out of their answer (its text is not
    written for the reader, and may hold what should stay private) and goes
    to the device's log instead. A panel and a macro show it as they show any
    RuntimeError. The message is user-facing -- surface it verbatim.
    """


class UnknownCommandError(ValueError):
    """A caller asked a device for a command name the driver does not declare.

    Distinct from a generic ValueError for the same reason as
    :class:`CommandParamError`: ``DeviceManager.send_command`` raises a plain
    ValueError for "no such device", so every ``except ValueError`` above it
    reported a *typo'd command* as ``Device 'x' not found`` — while the device
    was connected and its own page was rendering. The name of the thing that is
    missing is the one fact the message has to carry.

    Subclasses ValueError so existing handlers still catch it. The message is
    user-facing and actionable — surface it verbatim.
    """


class WakeAddressError(ValueError):
    """A Wake-on-LAN wake refused: the MAC address it needs is empty, or is
    not a MAC address.

    Typed so a door can tell it from a driver's other faults without reading
    its text. Somebody at a panel can open no setting, so they are told the
    device's setup is not finished; whoever can open Edit Device is told which
    setting to fill in (``api/error_messages.friendly_error`` does both).

    A driver with its own sentence passes it as the message. Without one, the
    sentence is built from what the refusal knows, so that a door can put the
    device's name in it (:meth:`sentence`): ``field_label`` is the setting the
    MAC goes in, as Edit Device shows it; ``value`` is what that setting holds
    when it is not a MAC ("" when it is empty); ``reported`` says the driver
    reads the MAC from the device when it connects, so the setting fills
    itself in.

    Subclasses ValueError so existing ``except ValueError`` handlers still
    catch it.
    """

    def __init__(
        self,
        message: str = "",
        *,
        field_label: str = "",
        value: str = "",
        reported: bool = False,
    ) -> None:
        self.field_label = field_label or "MAC Address"
        self.value = str(value or "").strip()
        self.reported = reported
        self._message = message
        super().__init__(message or self.sentence())

    def sentence(self, device: str = "") -> str:
        """The refusal for somebody who can open Edit Device, naming the
        device when its name is given."""
        if self._message:
            return self._message
        subject = device or "The device"
        if self.value:
            return (
                f"{subject} can't be woken: {self.field_label} is "
                f"'{self.value}', which is not a MAC address (six pairs of hex "
                f"digits, like aa:bb:cc:dd:ee:ff). Correct it under Edit Device."
            )
        if self.reported:
            return (
                f"{subject} can't be woken yet: {self.field_label} is empty. It "
                f"is filled in when {device or 'the device'} connects; until "
                f"then, enter it under Edit Device."
            )
        return (
            f"{subject} can't be woken: {self.field_label} is empty. Enter it "
            f"under Edit Device."
        )


class UndeclaredStateError(ValueError):
    """A driver wrote a state variable it never declared in
    ``DRIVER_INFO["state_variables"]``, under strict mode.

    Only raised when :func:`strict_driver_state` is on — see
    ``BaseDriver._check_undeclared_state`` for why this is a warning at
    runtime and a raise where the author is iterating.
    """


# The Python types a numeric state variable's value may be.
_NUMERIC_TYPES: dict[str, type | tuple[type, ...]] = {
    "integer": int,
    "number": (int, float),
    "float": (int, float),
}


def _type_mismatch(
    declared: str, value: Any, var_def: dict[str, Any], *, bool_is_number: bool = False,
) -> bool:
    """True when ``value`` is not of its ``declared`` type. A type this does
    not check (string, a type it does not know) never mismatches, and nor
    does an enum that declares no values.

    Python's ``bool`` is an ``int``. The contract observer reports ``True``
    written to a numeric variable; the debug log has always accepted it
    (``bool_is_number``).

    A declaration that is not a type name at all (a list, a dict) checks
    nothing either: the validator reports it, and the write still lands.
    """
    if not isinstance(declared, str):
        return False
    if declared == "enum":
        return "values" in var_def and str(value) not in [str(v) for v in var_def["values"]]
    if declared == "boolean":
        return not isinstance(value, bool)
    numeric = _NUMERIC_TYPES.get(declared)
    if numeric is None:
        return False
    if isinstance(value, bool):
        return not bool_is_number
    return not isinstance(value, numeric)


def _type_problem(declared: str, value: Any, var_def: dict[str, Any]) -> str:
    """Why ``value`` is not of its ``declared`` type, for the contract
    observer, or "" when it is."""
    if not _type_mismatch(declared, value, var_def):
        return ""
    if declared == "enum":
        return "not one of the declared values"
    return f"declared {declared}, got {type(value).__name__}"


#: The port a Wake-on-LAN magic packet is sent to unless a driver says otherwise
#: (the "discard" port; 7 is the other convention, and a listener honours both).
WAKE_ON_LAN_PORT = 9

#: Where ``send_udp(broadcast=True)`` sends: every host on the sending
#: adapter's own network, which no router passes on.
LIMITED_BROADCAST = "255.255.255.255"


def normalize_mac(value: Any) -> str | None:
    """``aa:bb:cc:dd:ee:ff`` for any usual spelling of a MAC, else None.

    Accepts colons, hyphens, dots or nothing between the bytes and any case.
    Twelve hex digits are the whole test; the all-zero address a device
    reports before it knows its own is not a MAC anyone can wake.
    """
    digits = re.sub(r"[^0-9A-Fa-f]", "", str(value or "")).lower()
    if len(digits) != 12 or digits == "000000000000":
        return None
    return ":".join(digits[i:i + 2] for i in range(0, 12, 2))


def magic_packet(mac: str) -> bytes:
    """The Wake-on-LAN magic packet for ``mac``: six 0xFF, then the MAC sixteen times.

    Raises :class:`WakeAddressError` when ``mac`` is not a MAC address, with
    the message a person fixing the config field needs to read.
    """
    normalized = normalize_mac(mac)
    if normalized is None:
        raise WakeAddressError(
            f"'{mac}' is not a MAC address (expected six hex pairs, "
            f"like aa:bb:cc:dd:ee:ff)",
            value=str(mac),
        )
    return b"\xff" * 6 + bytes.fromhex(normalized.replace(":", "")) * 16


def _verify_failure_error(
    said: str, cause: BaseException | None, host: Any, port: Any, transport: str,
) -> ConnectionError:
    """What a connect raises when the transport's verify step failed.

    "Device at <host>:<port> is not responding", unless the transport said
    why in a line of its own (ssh's "Connection refused" for a closed port):
    then that line, so the log's "Failed to connect" names the reason the
    device card shows. The line has to say that reason by itself (an HTTP
    refusal lives in the exception's chain, not in httpx's words). When none
    does, or the card's reason is ``no_response`` or a plain drop, "is not
    responding" stays: that sentence is what makes the card say
    ``no_response``. ``said`` is the transport's ``last_error``.
    """
    not_responding = ConnectionError(f"Device at {host}:{port} is not responding")
    not_responding.__cause__ = cause
    lines = [line.strip() for line in (said or "").splitlines() if line.strip()]
    if not lines:
        return not_responding
    fault = classify_connection_fault(
        last_error=said, exc=not_responding, host=str(host), port=port, transport=transport,
    )
    if fault.code in (NO_RESPONSE, TRANSPORT_DISCONNECTED):
        return not_responding
    for line in reversed(lines):
        alone = classify_connection_fault(
            last_error=line, exc=None, host=str(host), port=port, transport=transport,
        )
        if alone.code == fault.code:
            return ConnectionError(line)
    return not_responding


class BaseDriver(ChildEntities, PushChannel, LivenessWatch, PollLoop, TransportLadder, ABC):
    """Abstract base class for all device drivers."""

    # Subclasses MUST override this with their metadata dict
    DRIVER_INFO: dict[str, Any] = {}

    #: Told about contract faults as they happen: ``observer(kind, detail)``,
    #: ``kind`` one of ``CONTRACT_EVENTS``. None in production, where each
    #: check costs one comparison; a device audit and the Driver Builder's
    #: test set it on the one driver they run. An observer must not block,
    #: and its failures are its own.
    contract_observer: Callable[[str, dict[str, Any]], None] | None = None

    #: The state variable a driver writes when the device reports a problem
    #: it can still describe — a rejected command, a response it could not
    #: parse. A convention rather than a platform field: drivers declare it in
    #: ``DRIVER_INFO["state_variables"]`` and write it themselves. What the
    #: platform owns is the *clearing*, because nothing was clearing it: one
    #: transient failure stayed on screen for as long as the device stayed
    #: connected, so "once, three weeks ago" and "happening now" looked
    #: identical. A field that cannot tell those apart teaches people to
    #: ignore it, and then the real one is ignored too.
    LAST_ERROR_PROPERTY = "last_error"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Note when each poll() a subclass defines starts and finishes.

        The poll loop asks this to tell whether the connect has just polled:
        a driver's _initial_sync() reading state with ``await self.poll()``
        is that connect's first poll cycle, and the loop must not send the
        same reads again a moment later (see _poll_loop). A driver calls its
        own poll() directly, so the record has to live on the method itself.
        Only a poll() that returns is noted; one that raised read nothing.
        """
        super().__init_subclass__(**kwargs)
        poll = cls.__dict__.get("poll")
        if poll is None or getattr(poll, "_notes_poll_start", False):
            return

        @functools.wraps(poll)
        async def poll_noted(self: BaseDriver, *args: Any, **kw: Any) -> Any:
            started = time.monotonic()
            result = await poll(self, *args, **kw)
            self._last_poll_started_at = started
            return result

        poll_noted._notes_poll_start = True  # type: ignore[attr-defined]
        cls.poll = poll_noted  # type: ignore[method-assign]

    def __init__(
        self,
        device_id: str,
        config: dict[str, Any],
        state: StateStore,
        events: EventBus,
    ):
        self.device_id = device_id
        self.config = config
        self.state = state
        self.events = events
        self.transport: Any = None
        # The poll loop and its checks (drivers/polling.py).
        self._init_polling()
        self._connected = False
        # When the current connect() began, and when the latest poll() that
        # returned began (stamped by __init_subclass__'s wrapper). Together
        # they tell the poll loop whether this connect already polled.
        self._connect_began_at: float | None = None
        self._last_poll_started_at: float | None = None
        # Bumped every time this driver writes LAST_ERROR_PROPERTY. The poll
        # loop compares it across a poll to tell "the poll reported nothing"
        # from "the poll reported the same thing again" — a value comparison
        # cannot, because a fault that persists writes the identical string
        # every cycle and would look untouched.
        self._last_error_writes: int = 0
        # For a device that routes its commands through a bridge (e.g. an IR
        # device bound to a bridge's emitter port): a callable the DeviceManager
        # injects to reach the live bridge instance. None for a direct device.
        self._bridge_router: Any = None
        # True once connect() runs for a bridge-routed device (transport
        # "bridge"): it owns no transport, so its `connected` liveness is a
        # mirror of its bridge (the DeviceManager propagates the bridge's state).
        self._bridge_routed: bool = False
        # Last transport error captured before the live transport is torn down
        # on a failure path, so the DeviceManager's connection-fault classifier
        # can still read it after self.transport has been nulled. Cleared at
        # the start of each connect() attempt so a stale cause can't leak into
        # a later, unrelated failure.
        self._last_transport_error: str = ""
        # A typed offline reason stashed for failures with no exception to
        # carry the cause (liveness watchdogs, health loops forcing a
        # reconnect). Beats string classification; same lifecycle as
        # _last_transport_error.
        self._last_fault: ConnectionFault | None = None
        # Strong refs to fire-and-forget tasks (disconnect cleanup) so the GC
        # can't collect them mid-run — a bare create_task is only weakly held.
        self._bg_tasks: set[asyncio.Task] = set()
        # The push channel, closed (drivers/push.py).
        self._init_push()
        # The liveness probe and the hold after a device stopped answering
        # (drivers/liveness.py).
        self._init_liveness()
        # Set while connect() builds its transport, verifies it and runs the
        # handshake. A drop in that window resolves it instead of starting the
        # usual teardown: the connect is still running, so its failure is the
        # one report of this connection (_handle_transport_disconnect).
        self._connect_drop: asyncio.Future[object] | None = None
        # The connect that owns this driver (a token), and the lock that runs
        # connects one at a time. A disconnect, or a newer connect, ends the
        # one in flight (_end_connect_in_flight); it then unwinds before the
        # next one builds anything, so it can only close what it built.
        self._connect_attempt: object | None = None
        self._connect_lock = asyncio.Lock()
        # The registered child entities (drivers/child_entities.py).
        self._init_child_entities()
        # Set by the platform only for the duration of a run_setup_action call;
        # backs request_config_update / request_reconnect. None at all other times.
        self._setup_context: Any = None
        # State keys this driver wrote without declaring them. Used to warn
        # once per key instead of once per poll — a driver that writes an
        # undeclared key writes it every cycle, and a per-poll warning is a
        # log flood that trains everyone to ignore the channel.
        self._undeclared_state_seen: set[str] = set()
        # Per-instance strict mode. None means "follow the process-wide env
        # var"; True/False override it for this driver alone. See
        # `strict_state` below for why one driver has to be able to differ.
        self._strict_state_override: bool | None = None

        # Initialize state variables from DRIVER_INFO
        self._init_state_variables()

        # Register this device's credentials so the transport's TX/RX formatter
        # masks them. Done here rather than in the DeviceManager because every
        # path that builds a driver — the manager, a test, the Builder's live
        # test — runs this constructor, and both halves it needs (the resolved
        # config and the driver's own config_schema) are already in hand.
        self._register_config_secrets()

    def _observe(self, kind: str, **detail: Any) -> None:
        """Tell the contract observer, if there is one, about ``kind``."""
        observer = self.contract_observer
        if observer is None:
            return
        try:
            observer(kind, detail)
        except Exception:
            log.debug(f"[{self.device_id}] Contract observer failed", exc_info=True)

    def _register_config_secrets(self) -> None:
        """Publish this device's config credentials to the redaction registry."""
        try:
            schema = self.DRIVER_INFO.get("config_schema")
            # The marker is not a login, and is masked nowhere.
            config = {k: v for k, v in (self.config or {}).items() if v != SAVED_LOGIN}
            get_secret_registry().set_config_secrets(
                self.device_id,
                collect_secret_values(config, schema),
                identities=collect_identity_values(config, schema),
            )
        except Exception:  # pragma: no cover - never block a driver from loading
            log.debug(
                f"[{self.device_id}] Could not register config secrets for "
                f"log redaction",
                exc_info=True,
            )

    def redact_in_log(self, value: str) -> None:
        """Mask ``value`` wherever it appears in this device's log lines.

        For a secret the device issues at runtime — a session token from a
        login, a nonce — which the project config cannot know and so the
        automatic redaction of ``config_schema`` credentials cannot cover.
        Call it once, as soon as the value is known::

            token = reply["result"]["session"]
            self.redact_in_log(token)

        From then on the token is masked in TX/RX traffic and in any log line
        this driver writes itself. Values shorter than four characters are
        ignored, so a trivial value cannot blank unrelated log text.
        """
        get_secret_registry().add_runtime_secret(self.device_id, value)

    def record_traffic(
        self,
        direction: str,
        data: bytes | str,
        *,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Report bytes this driver moved over a connection it owns.

        Every platform transport records its own traffic, so a driver that
        uses ``self.transport`` never needs this. A driver that owns its
        session instead (a websocket library, its own socket) is invisible
        to the device's communication monitor and to a device audit unless
        it reports what it sends and receives here::

            await self._ws.send(frame)
            self.record_traffic("tx", frame)

        ``direction`` is ``"tx"`` or ``"rx"``; text is recorded as UTF-8.
        ``meta`` says what the bytes do not (a topic, a message type).
        Credentials are masked when the traffic is shown, as for any other
        channel.
        """
        payload = data.encode("utf-8") if isinstance(data, str) else bytes(data)
        record_traffic(
            self.device_id, "rx" if direction == "rx" else "tx", payload,
            channel="driver", meta=meta,
        )

    @property
    def connected(self) -> bool:
        """True only if both driver and transport report connected.

        A bridge-routed device has no transport of its own (it emits through a
        live bridge instance), so its liveness is carried by ``_connected``
        alone — the DeviceManager sets it to mirror the bound bridge's state.
        """
        if not self._connected:
            return False
        if self._bridge_routed:
            return True
        return self._link_alive()

    @property
    def last_transport_error(self) -> str:
        """The transport's last error string, retained across teardown.

        Read by the DeviceManager's connection-fault classifier — the live
        transport is nulled on every failure path, so the raw cause (an SSH
        ``Permission denied``, a refused socket) would otherwise be lost before
        the offline reason is computed.
        """
        return self._last_transport_error

    def _stash_transport_error(self) -> None:
        """Capture the live transport's cause before it's torn down.

        Takes ``last_error`` and, when the transport had already classified
        itself, its typed ``last_fault`` — a stalled write knows its own code,
        and the string it leaves behind matches no signature, so without this
        the classifier would read a wedged device as a plain dropped link. The
        fault must be copied HERE rather than read later: ``self.transport`` is
        nulled before the disconnect event is emitted, so by the time the
        DeviceManager classifies, the object holding it is gone.

        A no-op when there's no transport or it reports nothing, so it never
        overwrites a real cause with an empty one.
        """
        transport = self.transport
        if transport is not None:
            err = getattr(transport, "last_error", "") or ""
            if err:
                self._last_transport_error = err
            fault = getattr(transport, "last_fault", None)
            if fault is not None:
                self._last_fault = fault

    @property
    def last_fault(self) -> ConnectionFault | None:
        """A typed offline reason stashed by the driver, if any.

        Read by the DeviceManager when classifying a disconnect; a typed
        fault wins over string matching of error text.
        """
        return self._last_fault

    def _stash_fault(self, code: str, message: str = "") -> None:
        """Record a typed offline reason for a failure with no exception to
        carry the cause — a liveness watchdog that stopped hearing replies, a
        health loop forcing a reconnect. Call just before triggering the
        disconnect. Cleared at the start of each connect() attempt.
        """
        self._last_fault = ConnectionFault(
            code, message or default_fault_message(code)
        )

    def _init_state_variables(self) -> None:
        """Create a state key for every variable this driver declares.

        The keys exist from construction so a binding picker, the IDE's live
        state list and a ``$device.…`` reference all know the reading is on
        offer. The values do not: each starts at ``None`` — "declared, never
        reported" — until the device says otherwise. See
        :meth:`_default_for_var_def` for why the platform does not invent one.

        ``connected`` is the exception, and it is not a reading: the platform
        knows for a fact that a driver being constructed is not connected yet,
        so ``False`` is a statement rather than a guess.
        """
        state_vars = self.DRIVER_INFO.get("state_variables", {})
        for prop_name, prop_info in state_vars.items():
            self.set_state(prop_name, self._default_for_var_def(prop_info))
        # Always set a connected state
        self.set_state("connected", False)

    @staticmethod
    def _default_for_var_def(var_def: dict[str, Any]) -> Any:
        """What a declared state variable holds before the device has spoken —
        per-device and per-child seeding both come through here, and the answer
        is always ``None``.

        The key is created; the value is not invented. Those are two different
        facts and the store already keeps them apart: ``has()`` says the driver
        declares this reading, ``get()`` returning ``None`` says nobody has
        reported it. A binding picker, the IDE's live-state list and a
        ``$device.…`` reference all read the first; every consumer that judges a
        reading reads the second.

        It used to seed a *typed* value — a numeric at its declared ``min``, an
        enum at its first value, a string at ``""`` — and downstream nothing
        could tell that from a reading. A projector that had never been reached
        published ``lamp_hours = 0`` against a true 450 and ``power = "off"``
        for a projector that might be on; the relay shipped both to the cloud as
        fresh readings, and a monitor declaring ``normal_min`` above zero fired
        on the invented one.

        ``None`` is not a new convention invented here — it is the one the rest
        of the platform already documents and was being denied its input:

        * ``core/monitors.py`` — ``None`` is ``NO_VALUE``, drawn "--", never 0.
        * ``cloud/alert_monitor.py`` — a ``None`` reading matches no threshold
          and is "neither in the set nor out of it", so nothing fires on it.
        * ``core/condition_eval.py`` — ``None`` is "no decision", so
          ``power != "on"`` cannot fire a shutdown for a device that simply has
          not polled yet.
        * ``configurable.py``'s ``count_from_state`` — ``None`` means "not
          reported yet", and the roster waits rather than building 0 children.

        ``var_def`` is unused and stays in the signature deliberately: it is
        what a caller has in hand, and taking it away would say the answer might
        one day depend on the declaration again. It must not. A driver that
        wants a value on a child at registration passes ``initial_state``.

        The old rule still exists as ``compiled_protocol.state_var_default`` and
        is still the right answer for the two consumers that must produce a
        value rather than report one — the auto-generated simulator seeding the
        wire values it will serve, and the validator working out what a variable
        would hold.
        """
        return None

    # --- Connection lifecycle (concrete with auto-transport) ---

    async def connect(self) -> None:
        """
        Establish connection to the device — the one place the connection
        lifecycle lives.

        Stages, in order:

        1. Clean slate: reset fault classification, drop stale push
           subscriptions / driver sessions / transports from a previous
           attempt.
        2. Resolve the transport type (config override, then DRIVER_INFO);
           bridge-routed devices mirror their bridge and return early.
        3. Establish: _pre_connect() hook, then _create_transport() builds
           ``self.transport`` (constructor kwargs pass through
           _transport_kwargs(); drivers that own their session override
           _create_transport() and leave ``self.transport`` None).
        4. Verify: connectionless transports (OSC/HTTP) probe reachability
           before the device may report connected.
        5. Handshake: _post_connect() runs logins/negotiation before
           `connected` is declared; then the canonical declare — internal
           flag, ``connected`` state key, the ``device.connected.<id>``
           event. A connection that drops anywhere in stages 3 to 5 ends the
           handshake and fails the connect at once (_run_handshake).
        6. Tail: push subscription, _initial_sync() hook (identity reads,
           rosters, keep-alive loops), then polling and the liveness
           watchdog.

        One connect runs at a time. A disconnect, or another connect, while
        this one is still running ends it at once (the Reconnect button
        pressed while a device stays silent): it closes what it built and
        raises :class:`ConnectEndedError`, which the caller reports nothing
        for, since the door that ended it owns the device now.

        Prefer overriding the hooks over overriding this method — a driver
        that re-implements connect() must keep every stage above by hand
        (clean-slate reset, the canonical event topic, watchdog restart),
        and history shows most copies drop some of them.
        """
        self._end_connect_in_flight()
        attempt = object()
        self._connect_attempt = attempt
        async with self._connect_lock:
            try:
                self._raise_if_ended(attempt)
                await self._connect_once(attempt)
            finally:
                if self._connect_attempt is attempt:
                    self._connect_attempt = None

    def _end_connect_in_flight(self) -> None:
        """End the connect running on this driver, or waiting to run: it
        fails with :class:`ConnectEndedError` instead of finishing."""
        self._connect_attempt = None
        dropped = self._connect_drop
        if dropped is not None and not dropped.done():
            dropped.set_result(_CONNECT_ENDED)

    def _raise_if_ended(self, attempt: object) -> None:
        if self._connect_attempt is not attempt:
            raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))

    async def _close_transport_quietly(self) -> None:
        transport, self.transport = self.transport, None
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                pass

    async def _close_session_quietly(self) -> None:
        """A driver-owned session an ended connect may have built after the
        disconnect closed it. The disconnect may still be closing it too, so
        a second close that fails is not this connect's failure."""
        try:
            await self._close_session()
        except Exception:
            log.debug(f"[{self.device_id}] Closing an ended connect's session", exc_info=True)

    async def _unwind_ended_connect(self) -> None:
        """Take down what an ended connect started after the disconnect that
        ended it had already cleaned up."""
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        await self._close_transport_quietly()
        await self._close_session_quietly()
        self._connected = False

    async def _end_if_ended(self, attempt: object) -> None:
        """Past the declare: stop here if a disconnect ended this connect."""
        if self._connect_attempt is not attempt:
            await self._unwind_ended_connect()
            raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))

    async def _connect_once(self, attempt: object) -> None:
        """The body of :meth:`connect`, run under its lock."""
        # Start each attempt with a clean slate so a previous failure's cause
        # can't be misattributed to this one by the fault classifier.
        self._last_transport_error = ""
        self._last_fault = None
        # A poll from before this attempt says nothing about the state now.
        self._connect_began_at = time.monotonic()
        self._push_receives = 0
        # Only the automatic reconnect asks for a held connection, and only
        # for this attempt (every other door is a fresh one).
        hold = self._hold_next_connect
        self._hold_next_connect = False
        self._held = False
        self._settle_answer(False)
        # A reconnect attempt may arrive with a stale push subscription if the
        # async cleanup hasn't run yet; drop it so we never hold two. Same for
        # a driver-owned session (an httpx client, a secondary socket) left
        # behind by a previous attempt.
        await self._stop_push()
        await self._close_session()
        if self.transport:
            try:
                await self.transport.close()
            except Exception:
                pass
            self.transport = None

        transport_type = self._effective_transport()

        # A bridge-routed device (e.g. an IR device bound to a bridge's emitter
        # port) has no transport of its own — it emits through the live bridge
        # instance (see emit_via_bridge). Its liveness mirrors the bound
        # bridge: online iff the bridge is currently online. The DeviceManager
        # keeps this in sync as the bridge connects/disconnects; here we seed it
        # from the bridge's current state so a device added after its bridge is
        # already up starts online (and one whose bridge is down starts
        # offline, with a bridge_offline reason set by the DeviceManager).
        if transport_type == "bridge":
            self._bridge_routed = True
            bridge_id = self.config.get("bridge")
            bridge_port = self.config.get("bridge_port")
            online = bool(
                bridge_id
                and bridge_port
                and self.state.get(f"device.{bridge_id}.connected")
            )
            self._connected = online
            self.set_state("connected", online)
            if online:
                await self.events.emit(f"device.connected.{self.device_id}")
                log.info(
                    f"[{self.device_id}] Connected (bridge-routed via {bridge_id})"
                )
            else:
                log.info(
                    f"[{self.device_id}] Bridge-routed; bound bridge "
                    f"{bridge_id or '(none)'} is offline or unbound"
                )
            return

        # A network device with no address is refused here, before any
        # transport or driver-owned session is built: an empty address dials
        # this computer, and the card then blamed a refused port on a device
        # nobody had given an address. Every driver passes through here.
        # A bridge with no port chosen leaves the binding unresolved, so the
        # device would dial its own transport with nothing to dial, and the
        # card asked for an IP address its Edit Device form does not show.
        if bridge_port_missing(self.config):
            raise ConnectionFaultError(bridge_port_missing_message(), code=INVALID_CONFIG)
        if self.address_missing():
            raise ConnectionFaultError(no_address_message(), code=INVALID_CONFIG)
        # A setting still holding the "saved on this system" marker had no
        # saved login to fill it (core/device_logins.py). Refused here, before
        # anything is sent, so the device is never sent the marker as its
        # login. A bridge-routed device returned above: it sends through its
        # bridge's own connection and signs in to nothing.
        if self.login_missing():
            raise ConnectionFaultError(code=LOGIN_MISSING)

        # Stage 3 — establishment: driver-owned setup hook, then the
        # transport (or driver-owned session) itself.
        await self._pre_connect()
        self._raise_if_ended(attempt)
        # From the moment the transport exists until the handshake settles, a
        # drop of this connection is this connect's to report (_run_handshake).
        dropped: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        self._connect_drop = dropped
        try:
            await self._create_transport(transport_type)
            # Ended while it was being built: the disconnect found no
            # transport to close, so this one is closed below.
            self._raise_if_ended(attempt)

            # For connectionless transports (OSC, HTTP), verify the remote
            # host is actually reachable before reporting connected. TCP and
            # serial validate during open/create. UDP is genuinely
            # connectionless and has no transport-level probe: a UDP device is
            # noticed going away by its polls drawing no replies (the poll
            # loop's silence check, for a driver that reads replies; an
            # awaited poll that raises does the same) or by a liveness probe
            # (a YAML `liveness:` block / a Python override of
            # _liveness_probe). A UDP driver with none of those reads
            # connected against a dead host forever.
            # Set verify_timeout: 0 in config to skip the pre-connect probe on
            # OSC/HTTP. It is skipped too while the device's state holds
            # polling back (_connect_check_held).
            verify_timeout = self.config.get("verify_timeout", 3.0)
            if (
                verify_timeout > 0
                and hasattr(self.transport, "verify")
                and not self._connect_check_held()
            ):
                if not await self.transport.verify(timeout=verify_timeout):
                    self._raise_if_ended(attempt)
                    # Retain the transport's underlying cause (refused /
                    # timeout) before tearing it down: on its own the raise
                    # below says "not responding", which the classifier would
                    # read as no_response. A transport that keeps the
                    # exception (HTTP) passes it on as this one's cause, since
                    # a refusal can live in its chain alone.
                    cause = getattr(self.transport, "verify_error", None)
                    if not isinstance(cause, BaseException):
                        cause = None
                    self._stash_transport_error()
                    if self.transport:
                        await self.transport.close()
                        self.transport = None
                    raise _verify_failure_error(
                        self._last_transport_error, cause,
                        self.config.get("host", "?"), self.config.get("port", "?"),
                        transport_type,
                    ) from cause
            self._raise_if_ended(attempt)
        except BaseException as exc:
            if self._connect_drop is dropped:
                self._connect_drop = None
            if (
                self._connect_attempt is not attempt
                and not isinstance(exc, asyncio.CancelledError)
            ):
                # The disconnect that ended this connect found neither to
                # close: a driver that owns its session built it here too.
                await self._close_transport_quietly()
                await self._close_session_quietly()
                if isinstance(exc, ConnectEndedError):
                    raise
                raise ConnectEndedError(
                    default_fault_message(TRANSPORT_DISCONNECTED)
                ) from exc
            raise

        try:
            # Device-specific session setup (e.g. an SSH/CLI driver entering
            # privileged mode and disabling output paging) runs before we
            # report connected so the first poll sees a ready session. A raise
            # here aborts the connection and the transport is cleaned up below.
            await self._run_handshake()
            self._raise_if_ended(attempt)
            self._connected = True
            if hold and (self._health_enabled() or self._watches_for_silence()):
                # Sends, polling and the liveness probe run as usual;
                # `connected` and the event wait for the first poll or probe
                # the device answers (_announce_held).
                self._held = True
                self._answer_waiter = asyncio.get_running_loop().create_future()
                log.info(
                    f"[{self.device_id}] Connected via {transport_type}; the "
                    f"device stopped answering last time, so it stays offline "
                    f"until it answers"
                )
            else:
                self._dropped_for_silence = False
                self._silence_verdict = None
                self.set_state("connected", True)
                await self.events.emit(f"device.connected.{self.device_id}")
                log.info(f"[{self.device_id}] Connected via {transport_type}")
        except Exception as exc:
            if self._connect_attempt is not attempt:
                # Ended by a disconnect or a newer connect, which owns the
                # device now: close what is left of this one, report nothing.
                await self._close_transport_quietly()
                await self._close_session_quietly()
                self._connected = False
                if isinstance(exc, ConnectEndedError):
                    raise
                raise ConnectEndedError(
                    default_fault_message(TRANSPORT_DISCONNECTED)
                ) from exc
            # Clean up transport if post-connect setup fails. Stash the
            # transport's last error first (e.g. an SSH auth failure surfaces
            # in ssh's log on the transport, not in this exception).
            self._stash_transport_error()
            if self.transport:
                await self.transport.close()
                self.transport = None
            await self._close_session()
            self._connected = False
            raise

        # Push notifications (DRIVER_INFO["push"]): subscribe as soon as the
        # session is up — BEFORE any subclass connect() stage runs on_connect
        # arming commands, so the listener never misses the first frame a
        # freshly-armed device sends. Failure is non-fatal (logged inside);
        # polling still covers the device.
        await self._start_push()
        await self._end_if_ended(attempt)

        # Stage 6 — driver-specific initial sync (identity reads, roster
        # registration, keep-alive loops), after `connected` is declared and
        # push is armed but BEFORE polling starts, so the first poll cycle
        # sees a fully-initialized device. A raise here tears the whole
        # connection back down — the device was briefly connected, so the
        # disconnect event is emitted for symmetry. A driver that prefers a
        # degraded-but-connected outcome catches inside its override.
        try:
            await self._initial_sync()
        except Exception as exc:
            if self._connect_attempt is not attempt:
                # The disconnect that ended it already said so.
                await self._unwind_ended_connect()
                if isinstance(exc, ConnectEndedError):
                    raise
                raise ConnectEndedError(
                    default_fault_message(TRANSPORT_DISCONNECTED)
                ) from exc
            self._stash_transport_error()
            # The disconnect event emitted below reaches the device manager's
            # handler with no exception to read, so a typed cause (a refused
            # credential) is kept where it looks, or the handler would start
            # a reconnect that the caller of connect() is about to hold.
            typed = typed_fault_from_exc(
                exc, host=self.config.get("host", ""), port=self.config.get("port"),
            )
            if typed is not None:
                self._last_fault = typed
            await self._stop_push()
            transport = self.transport
            self.transport = None
            if transport is not None:
                try:
                    await transport.close()
                except Exception:
                    pass
            await self._close_session()
            self._connected = False
            self.set_state("connected", False)
            if self._held:
                # Never announced, so there is nothing to take back.
                self._held = False
                self._settle_answer(False)
                raise
            try:
                await self.events.emit(f"device.disconnected.{self.device_id}")
            except Exception:
                log.debug(
                    f"[{self.device_id}] Failed to emit disconnect event",
                    exc_info=True,
                )
            raise
        await self._end_if_ended(attempt)

        # Start polling if configured
        poll_interval = self.config.get("poll_interval", 0)
        if poll_interval > 0:
            await self.start_polling(poll_interval)
            await self._end_if_ended(attempt)

        # Start the liveness watchdog if the driver supplies a probe. Started
        # after `connected` is reported; the loop sleeps a full interval before
        # the first probe, so subclass connect() stages that run after
        # super().connect() (logins, subscriptions) aren't raced. If such a
        # stage fails and tears the transport down, the loop notices the dead
        # transport and exits on its own.
        if self._health_enabled():
            self._start_health_loop()

    async def disconnect(self) -> None:
        """
        Gracefully close the connection.

        Stops polling, closes transport, and updates state. A connect still
        running is ended first (:meth:`connect`).
        Override for custom disconnect logic.
        """
        self._end_connect_in_flight()
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        await self._close_session()
        self._connected = False
        # A deliberate disconnect ends any hold: whatever connects next is a
        # fresh attempt, announced as soon as it is up.
        self._held = False
        self._dropped_for_silence = False
        self._silence_verdict = None
        self._unread_replies_hold = False
        self._settle_answer(False)
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")
        log.info(f"[{self.device_id}] Disconnected")

    # --- Abstract: drivers must implement ---

    @abstractmethod
    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        """
        Execute a named command.

        Translates the command name + params into protocol-specific bytes
        and sends via the transport.
        """

    # --- Device Settings ---

    async def set_device_setting(self, key: str, value: Any) -> Any:
        """
        Write a device setting value to the device.

        Override in subclasses to handle device-specific write logic.
        The default implementation raises NotImplementedError so callers
        know the driver hasn't implemented settings writes.

        Args:
            key: The setting key from DRIVER_INFO["device_settings"].
            value: The new value to write.

        Returns:
            Result of the write operation (driver-specific).
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement set_device_setting"
        )

    # --- Setup / provisioning actions ---
    #
    # A "setup action" is a driver-declared provisioning wizard (an action of
    # kind:"setup", see openavc/drivers/actions.py). Unlike a command, it can run
    # while the device is OFFLINE, brings its own out-of-band transport, reports
    # multi-step progress, and may rewrite the device's connection config and
    # reconnect on success. The platform invokes run_setup_action with a live
    # `progress` callback and, for the duration of the call, a setup context that
    # backs request_config_update / request_reconnect. Everything device-specific
    # (which transport, which commands, what the config delta is) lives in the
    # driver's handler; the platform stays generic.

    async def run_setup_action(
        self,
        action_id: str,
        params: dict[str, Any],
        progress: Callable[..., Awaitable[None]],
    ) -> dict[str, Any]:
        """Run a declared setup action. May run while the device is offline.

        ``progress(step, pct=None)`` is awaitable and streams a live progress
        line to the UI (``pct`` is an optional 0-100 percentage). The handler
        may open its own transports independent of the device's normal one, and
        may call ``await self.request_config_update({...})`` to persist new
        connection settings and ``await self.request_reconnect()`` to bring the
        device back online over them. Default raises NotImplementedError.

        Return a result dict. A ``"message"`` in it is the sentence the wizard
        shows the operator when the run finishes, so write it for them: what
        was found, and any count worth sanity-checking ("Found 5 endpoints —
        2 encoders, 3 decoders").

        Two ways to report failure, and they are not interchangeable:

        - **Raise** when the action could not run — the host was unreachable,
          a transport blew up, the driver has nothing to do for this action id.
          The exception is logged with a traceback.
        - **Return ``{"success": False, "message": ...}``** when the action ran
          and the answer is no: a community string the device ignores, a model
          this driver doesn't drive, a declared value the device has no OID
          for. The message is the whole report, so say what to check. There is
          no traceback — an operator typing the wrong IP is not a platform
          fault.

        Either way the run terminates as ``error`` on the ``action.progress``
        channel and the wizard renders it as a failure; only the log differs.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"run_setup_action"
        )

    async def request_config_update(self, delta: dict[str, Any]) -> None:
        """Persist a connection/config delta for this device (from a setup
        action handler). Connection fields land in the project's connections
        table, the rest in the device config; the live driver's ``self.config``
        is updated so the next connect() uses the new settings.

        Only callable from inside ``run_setup_action`` — the platform installs
        the backing context for the duration of the run.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_config_update is only available during a setup action"
            )
        await self._setup_context.apply_config_update(delta)

    async def request_reconnect(self) -> None:
        """Reconnect the device using its current (possibly just-updated)
        config. Typically the final step of a setup action. Only callable from
        inside ``run_setup_action``.
        """
        if self._setup_context is None:
            raise RuntimeError(
                "request_reconnect is only available during a setup action"
            )
        await self._setup_context.reconnect()

    def _set_setup_context(self, context: Any) -> None:
        """Platform-only: install (or clear, with None) the setup context that
        backs request_config_update / request_reconnect for one setup run.
        """
        self._setup_context = context

    # --- Bridge support ---
    #
    # A "bridge" is a device that exposes typed ports (serial / IR / relay)
    # which OTHER devices connect *through* (the pro-AV port-binding model).
    # A bridge driver declares its ports in DRIVER_INFO["bridge"]["ports"].
    # The platform resolves a downstream device's connection to the bridge's
    # pass-through endpoint (engine.resolved_device_config) and calls
    # prepare_bridge_port() on the live bridge so the hardware is configured
    # for the downstream first — before that downstream connects, and again
    # whenever the bridge itself reconnects.

    @property
    def is_bridge(self) -> bool:
        """True if this driver advertises bridge ports."""
        return bool((self.DRIVER_INFO.get("bridge") or {}).get("ports"))

    async def prepare_bridge_port(
        self, port_id: str, params: dict[str, Any]
    ) -> None:
        """Configure one of this bridge's ports for a device bound to it.

        Called by the platform on the *bridge* driver. For a serial port a
        bridge pushes the downstream's baud/parity to the hardware here, so the
        transparent pass-through carries bytes at the right line settings;
        ``params`` is the downstream's resolved connection config (baudrate,
        parity, bytesize, stopbits, ...). Default: no-op — a bridge that needs
        no per-port setup, or a port kind that routes commands at send time
        (IR / relay), does nothing. Raising does NOT block the downstream
        connect (the platform logs and proceeds), so a transient bridge-side
        failure can't strand the downstream device offline.

        **Make it idempotent.** It runs before a downstream connects, and again
        for every bound port whenever the bridge itself reconnects — otherwise
        a bridge that lost its settings to a power cut would carry a device that
        was added while it was down at whatever its memory held. That second
        call can therefore arrive while the downstream is already connected
        through the pass-through, so do the work on the bridge's own command
        connection rather than anything that would disturb the data path.
        """

    # A bridge that emits commands for downstream devices (IR, and any future
    # non-pass-through kind) owns its command socket and multiplexes it. The
    # platform speaks a vendor-neutral payload; the bridge driver translates to
    # its own wire format. For IR: kind == "ir", payload == {"pronto": <hex>,
    # "repeat": <int>}. Default: not an emitting bridge.
    async def bridge_emit(
        self, port_id: str, kind: str, payload: dict[str, Any]
    ) -> Any:
        """Emit a downstream device's command through one of this bridge's ports.

        Called on the *bridge* driver by the DeviceManager router when a
        bridge-routed downstream device sends a command. Override in an emitting
        bridge (e.g. IR) to convert ``payload`` to the hardware wire format and
        send it on the bridge's command socket. Default raises.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not emit through bridge ports"
        )

    async def bridge_import_code(self, wire: str) -> str:
        """Convert a bridge-native wire code (as an integrator might paste from a
        manual, e.g. a Global Cache ``sendir`` string) to vendor-neutral Pronto
        hex for storage in an IR device's code-set.

        Called on the *bridge* driver so the (vendor-specific) wire format is
        parsed by the code that owns it; the platform stores only Pronto. The
        default has no wire format to import from. Raise ValueError for an
        unparseable code.
        """
        raise NotImplementedError(
            f"{type(self).__name__} cannot import a native IR code"
        )

    @property
    def can_learn(self) -> bool:
        """True if this bridge can capture codes from a remote (IR learner).

        Override to return True on a bridge that implements bridge_learn_*.
        """
        return False

    async def bridge_learn_start(self) -> None:
        """Begin a learn session (e.g. enable the IR learner on a dedicated
        socket and pause polling). Override on a learning bridge. Default raises.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support learning"
        )

    async def bridge_learn_poll(self, timeout: float) -> str | None:
        """Wait up to ``timeout`` seconds for the next captured code.

        Returns the captured code as vendor-neutral Pronto hex, or None on
        timeout (so the caller can loop for continuous auto-capture). Override
        on a learning bridge.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support learning"
        )

    async def bridge_learn_stop(self) -> None:
        """End the learn session (disable the learner, close the socket, resume
        polling). Override on a learning bridge. Must be safe to call twice.
        """

    async def emit_via_bridge(self, kind: str, payload: dict[str, Any]) -> Any:
        """Emit ``payload`` through the bridge this device is bound to.

        For a bridge-routed device (its config carries ``bridge`` +
        ``bridge_port``): looks up the live bridge via the injected router and
        calls its bridge_emit. Raises ConnectionError if the device is not
        bridge-bound or the bridge is unavailable.
        """
        bridge_id = self.config.get("bridge")
        port_id = self.config.get("bridge_port")
        if not bridge_id or not port_id:
            raise ConnectionError(f"[{self.device_id}] not bound to a bridge port")
        if self._bridge_router is None:
            raise ConnectionError(
                f"[{self.device_id}] bridge routing unavailable"
            )
        return await self._bridge_router(bridge_id, port_id, kind, payload)

    # --- UDP beside the transport ---

    #: Where a datagram this driver sends beside its transport actually goes
    #: while the device is simulated: the simulator's own UDP receiver, set
    #: by core/simulation.py together with the connection redirect and cleared
    #: with it. None means the datagram goes where the driver aimed it. A
    #: simulated display's wake therefore lands in the Simulator UI's protocol
    #: log instead of waking whatever real display owns that MAC.
    udp_redirect: tuple[str, int] | None = None

    async def _open_udp(self, local_addr: str | None = None) -> Any:
        """An opened ad-hoc UDP socket for one side-send, bound to
        ``local_addr`` when Reach devices through chose an adapter.

        A method so the Driver Builder's dry run can hand back a transport
        that records instead of transmitting; nothing else overrides it.
        """
        udp = UDPTransport(name=self.device_id)
        await udp.open(allow_broadcast=True, local_addr=local_addr)
        return udp

    async def send_udp(
        self,
        payload: bytes,
        host: str | None = None,
        port: int | None = None,
        broadcast: bool = False,
    ) -> list[tuple[str, int]]:
        """Send one UDP datagram beside the driver's main transport.

        Opens a socket, sends, closes: the side channel holds nothing open and
        needs no connection, so it works while the device is offline (pair the
        command with ``available_offline``). ``host`` defaults to the device's
        own host; ``broadcast`` sends to 255.255.255.255 instead. The bytes are
        in the device log as the transport's TX line. Returns the addresses the
        datagram went to.

        A datagram to 255.255.255.255 with no adapter chosen in Reach devices
        through goes out once through every connected adapter, from its
        address (``reach_devices.broadcast_sources``): sent unbound, it would
        leave through the one adapter the OS picks. An adapter that cannot
        send is skipped; with none to send from, it goes once, unbound. With
        an adapter chosen it goes from that adapter alone (its 169.254 address
        when it holds no other), and when it has nothing to send from it is
        not sent: ``reach_devices.AdapterUnavailable`` (a ConnectionError),
        never another adapter (``reach_devices.pinned``).

        This is the primitive under a YAML command's ``udp:`` block and under
        :meth:`wake_on_lan`; a Python driver that needs a datagram beside its
        transport (a message to a signage presentation's UDP receiver, say)
        calls it rather than opening its own socket.
        """
        if port is None or int(port) <= 0:
            raise ValueError(
                f"[{self.device_id}] send_udp needs a port to send to"
            )
        port = int(port)
        if broadcast:
            host = LIMITED_BROADCAST
        else:
            host = str(host or self.config.get("host") or "").strip()
            if not host:
                raise ValueError(
                    f"[{self.device_id}] send_udp needs a host: the device has "
                    f"no host configured and none was given"
                )
        target = (host, port)
        if self.udp_redirect is not None:
            target = self.udp_redirect
        data = bytes(payload)
        sources: list[str] = []
        chosen = None
        if target[0] == LIMITED_BROADCAST:
            from openavc import reach_devices

            chosen = await asyncio.to_thread(reach_devices.pinned)
        if chosen is not None:
            # Asked before local_address(), whose refusal is a connection's:
            # a broadcast with nothing to send from says the adapter isn't
            # connected.
            bound = chosen.broadcast_source or None
            if bound is None:
                raise reach_devices.AdapterUnavailable(chosen.adapter_name)
        else:
            # Bound for where it really goes: the simulator's redirect is local.
            bound = self.local_address(target[0])
        if bound is None and target[0] == LIMITED_BROADCAST:
            from openavc import reach_devices

            for source in await asyncio.to_thread(reach_devices.broadcast_sources):
                try:
                    await self._send_udp_from(source, data, target)
                    sources.append(source)
                except OSError as exc:
                    log.debug(
                        f"[{self.device_id}] UDP broadcast from {source} failed: {exc}"
                    )
        if not sources:
            await self._send_udp_from(bound, data, target)
        log.info(
            f"[{self.device_id}] Sent {len(payload)} bytes over UDP to "
            f"{target[0]}:{target[1]}"
            + (f" from {', '.join(sources)}" if sources else "")
        )
        return [target]

    async def _send_udp_from(
        self, local_addr: str | None, data: bytes, target: tuple[str, int],
    ) -> None:
        """One datagram from a socket opened for it and closed after."""
        udp = await self._open_udp(local_addr=local_addr)
        try:
            await udp.send_to(data, target[0], target[1])
        finally:
            await udp.close()

    async def wake_on_lan(
        self,
        mac: str,
        host: str | None = None,
        port: int = WAKE_ON_LAN_PORT,
    ) -> str:
        """Send the Wake-on-LAN magic packet for ``mac``.

        Goes to the broadcast address and directly to ``host`` (default: the
        device's own host), which is what wakes a display whether or not a
        switch between here and there passes broadcast. The direct send is
        best effort: an unroutable host is logged, not raised, because the
        broadcast already went out. Raises :class:`WakeAddressError` for a
        MAC that is not one. Returns the MAC in normalized form.
        """
        packet = magic_packet(mac)
        normalized = normalize_mac(mac) or mac
        sent = await self.send_udp(packet, port=port, broadcast=True)
        direct = str(host or self.config.get("host") or "").strip()
        if self.udp_redirect is not None:
            direct = ""  # both sends land on the simulator; one is enough
        if direct:
            try:
                sent += await self.send_udp(packet, host=direct, port=port)
            except OSError as exc:
                log.warning(
                    f"[{self.device_id}] Wake-on-LAN direct send to "
                    f"{direct}:{port} failed ({exc}); the broadcast went out"
                )
        log.info(
            f"[{self.device_id}] Wake-on-LAN magic packet sent for {normalized}"
        )
        return normalized

    # --- Optional overrides ---

    async def on_data_received(self, data: bytes) -> None:
        """
        Called by the transport when data arrives from the device.

        Override in the driver to implement protocol-specific parsing.
        Default: no-op.
        """

    def _reads_replies(self) -> bool:
        """True when this driver does something with what the device sends.

        Default: the subclass overrides on_data_received. ConfigurableDriver
        answers from its response rules instead (it always overrides the
        handler, but a definition with no rules reads nothing).
        """
        return type(self).on_data_received is not BaseDriver.on_data_received

    async def on_mqtt_message(self, topic: str, payload: bytes) -> None:
        """
        Called by the MQTT transport when a message arrives on a subscribed
        topic. The pub/sub analogue of on_data_received — topic-aware because
        MQTT routing is topic-based, not a single byte stream.

        Override in MQTT drivers to parse inbound messages. Subscribe to the
        topics you care about in _post_connect(). Default: no-op.
        """

    async def poll(self) -> None:
        """
        Called periodically to request device status.

        Override to send status query commands. Default: no-op.

        Contract: implementations MUST propagate transport-level errors
        (ConnectionError, TimeoutError, OSError, httpx.ConnectError,
        httpx.TimeoutException). The polling loop catches these and counts
        them toward the missed-poll watchdog. Swallowing transport errors
        here causes `device.<id>.connected` to lie when the device is
        unreachable.

        Protocol-level errors (unexpected response shape, expected device
        states like "in standby") may be handled inside poll() — those
        indicate the device is reachable but not in a queryable state.

        A poll that writes its queries and returns without awaiting replies
        is watched for silence when the driver reads replies over the
        platform's tcp/udp/osc transport (see _watches_for_silence): polls in
        a row that draw nothing back take the device offline. So poll
        something the device answers in every state, standby included.

        The loop runs the first poll as soon as polling starts, unless this
        connect already polled (from _initial_sync); then it waits one
        interval from that poll.
        """

    async def _pre_connect(self) -> None:
        """Hook: runs after the clean-slate reset, before the transport (or
        driver-owned session) is created.

        The place to arm anything that must be ready the instant the link
        opens — e.g. an auth-capture buffer for a device that emits its login
        prompt immediately on connect, or seeding a protocol parameter from
        config before the first byte flows. Default: no-op.
        """

    async def _post_connect(self) -> None:
        """Hook for device-specific session setup after the transport opens and
        before the device is marked connected.

        CLI-over-SSH/telnet drivers override this to read the login banner,
        enter privileged mode, and disable output paging; drivers that own
        their session (httpx, websockets) authenticate and verify the device
        actually answers here, so `connected` never lies. Raising aborts the
        connection (the caller closes the transport and the driver session).
        Default: no-op.
        """

    async def _run_handshake(self) -> None:
        """Run :meth:`_post_connect`, ending it the moment the connection drops.

        A handshake waits for the device (a banner, a prompt, a login
        verdict). When the connection closes under it, waiting on would only
        run out its own timeout and fail with its own complaint, the cause
        long gone with the transport, while the drop's handler started a
        retry beside a connect that was still running. So the drop wins: the
        handshake is cancelled and the connect fails at once with what the
        transport said (:meth:`_dropped_error`). A handshake that had already
        failed with a typed fault (a refused login) keeps it. A drop that
        lands as the handshake finishes still fails the connect, so a device
        is never declared connected on a connection already gone.

        ``_connect_drop`` is cleared before this returns or raises, so a drop
        after it (a held connection, a running session) takes the usual path.
        """
        dropped = self._connect_drop
        if dropped is None:
            await self._post_connect()
            return
        handshake: asyncio.Future[None] | None = None
        try:
            if not dropped.done():
                handshake = asyncio.ensure_future(self._post_connect())
                await asyncio.wait(
                    (handshake, dropped), return_when=asyncio.FIRST_COMPLETED,
                )
                if not dropped.done():
                    handshake.result()  # its own outcome: returns or raises
                    return
            if dropped.result() is _CONNECT_ENDED:
                raise ConnectEndedError(default_fault_message(TRANSPORT_DISCONNECTED))
            if (
                handshake is not None
                and handshake.done()
                and not handshake.cancelled()
                and typed_fault_from_exc(handshake.exception()) is not None
            ):
                raise handshake.exception()  # type: ignore[misc]
            raise self._dropped_error()
        finally:
            # Only ever this connect's own: connects run one at a time.
            if self._connect_drop is dropped:
                self._connect_drop = None
            if handshake is not None and not handshake.done():
                handshake.cancel()
                await asyncio.wait((handshake,))
                if not handshake.cancelled():
                    handshake.exception()  # seen, so it is not logged as lost

    def _dropped_error(self) -> ConnectionError:
        """Why the connection closed under a connect, in the transport's words.

        A fault the driver stashed as it dropped the link
        (:meth:`_force_disconnect`, a refused login read by its reader) is
        raised typed, so it stays a refused login and the retries pause. Else
        the transport's last line, or the taxonomy's own sentence when the
        device closed without a word (a plain goodbye leaves none).
        """
        fault = self._last_fault
        if fault is not None:
            return ConnectionFaultError(fault.message, code=fault.code)
        said = ""
        if self.transport is not None:
            said = getattr(self.transport, "last_error", "") or ""
        lines = [line.strip() for line in said.splitlines() if line.strip()]
        if lines:
            return ConnectionError(lines[-1])
        return ConnectionError(default_fault_message(TRANSPORT_DISCONNECTED))

    async def _initial_sync(self) -> None:
        """Hook: one-time initialization after the device is declared
        connected — identity reads, child-roster registration, an initial
        poll(), starting driver-specific keep-alive loops.

        Runs after `connected` is True and push is subscribed, but before
        polling and the liveness watchdog start, so the first poll cycle sees
        a fully-initialized device. Raising tears the connection back down
        and fails the connect attempt; catch inside the override if a failed
        sync should leave the device connected anyway. Default: no-op.

        A ``poll()`` that returns here counts as the poll loop's first cycle:
        the loop polls next a full interval after it. Without one, the loop
        polls as soon as it starts, so state is read at connect either way.
        """

    async def _close_session(self) -> None:
        """Hook: close driver-owned connections that live outside
        ``self.transport`` — an httpx client, a secondary status socket, a
        websocket.

        Called on every teardown path (fresh connect attempts, failed
        handshakes, graceful disconnect, the transport-drop cleanup), so an
        override must tolerate being called when nothing is open and must
        null its references (the health loop keeps running until
        _link_alive() goes false). Default: no-op.
        """

    def login_missing(self) -> bool:
        """True when a setting still holds the "saved on this system" marker:
        the project says a login is saved, and this system had none to fill it
        in (``core.device_logins``). Retrying cannot supply one."""
        return bool(marked_fields(self.config))

    def address_missing(self) -> bool:
        """True when this device dials a network address it does not have.

        Asked of the address the driver would dial now, which a running
        simulation has pointed at the simulator, and only when the driver
        marks its ``host`` field required: a driver whose blank address means
        something (a multicast default, a broadcast) leaves it unmarked.
        Serial, bridge-routed and IR devices dial no address of their own.
        """
        if self._effective_transport() not in _ADDRESSED_TRANSPORTS:
            return False
        field = (self.DRIVER_INFO.get("config_schema") or {}).get("host")
        if not (isinstance(field, dict) and field.get("required") is True):
            return False
        return not str(self.config.get("host") or "").strip()

    def _effective_transport(self) -> str:
        """The transport this device connects over.

        A device's config may override the driver's default (e.g. a CLI
        driver that defaults to "ssh" in production but connects over raw
        "tcp" to a CLI simulator); unset, it is the driver's own.
        """
        return self.config.get("transport") or self.DRIVER_INFO.get("transport", "tcp")

    def _link_alive(self) -> bool:
        """Predicate: is the underlying link (platform transport or
        driver-owned session) still up?

        Backs the `connected` property and keeps the liveness watchdog
        running. The default reads the platform transport; a driver that owns
        its session (httpx client, websocket) overrides this to report that
        session instead — without it, `connected` is permanently False and
        the health loop exits immediately for such drivers.
        """
        if self.transport is None:
            return False
        return bool(getattr(self.transport, "connected", False))

    def _connect_check_held(self) -> bool:
        """True when the device's own state holds polling back, so connect()
        skips the transport's check (an OSC ``/info``, an HTTP HEAD) as well:
        a unit in standby that any message would wake is left in standby.

        Asked once per connect, before the check would go out. The device
        then reads connected without having answered, as it stays connected
        while its polls are held. Default: never.
        """
        return False

    async def send_raw(self, data: str) -> None:
        """Send a raw command string to the device immediately.

        Encodes escape sequences (so a typed ``\\r`` becomes a CR) and appends
        the device's line terminator unless the string already ends with it —
        matching how saved commands are sent. Used by the device page's
        "Send raw" box for quick one-offs and diagnostics; works on any
        byte-stream transport (TCP / serial / UDP), not request/response HTTP.
        """
        from openavc.transport.binary_helpers import encode_escape_sequences

        if not self.transport or not getattr(self.transport, "connected", False):
            raise ConnectionError(f"[{self.device_id}] Not connected")
        send = getattr(self.transport, "send", None)
        if send is None:
            raise NotImplementedError(
                f"[{self.device_id}] this device's transport does not support "
                f"raw send"
            )
        payload = encode_escape_sequences(str(data))
        delim = self._resolve_delimiter()
        if delim and not payload.endswith(delim):
            payload += delim
        await send(payload)
        log.debug(f"[{self.device_id}] Sent raw: {payload!r}")

    async def _verify_reachable(
        self, host: str, port: int, timeout: float = 3.0
    ) -> bool:
        """
        Verify that a TCP host:port is reachable.

        Drivers that don't use a platform transport (raw httpx clients,
        websocket clients, etc.) should call this in connect() before
        setting connected=True, so that loading the project against an
        unreachable host fails fast instead of declaring a phantom
        connection that has to time out via the watchdog.

        Returns True if a TCP connection can be opened within the timeout,
        False otherwise. Dials from :meth:`local_address`, as the driver's own
        connection will.
        """
        ip = self.local_address(host)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    host, port, local_addr=(ip, 0) if ip else None,
                ),
                timeout=timeout,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            return False

    def _handle_transport_disconnect(self) -> None:
        """
        Standard disconnect handler for transport callbacks.

        Sets connected state to False, then schedules cleanup (stop polling,
        close the now-dead transport, emit the disconnect event). Override for
        custom disconnect behavior.

        A drop while connect() is still building or handshaking ends that
        connect instead (_run_handshake): it closes what it opened and its
        failure is the one report, so nothing is scheduled here and no
        disconnect is emitted for a connection that never came up.
        """
        self._connected = False
        self.set_state("connected", False)
        dropped = self._connect_drop
        if dropped is not None:
            if not dropped.done():
                dropped.set_result(None)
            return
        if self._held:
            log.info(f"[{self.device_id}] Connection closed before the device answered")
        else:
            log.warning(f"[{self.device_id}] Connection lost")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning(
                f"[{self.device_id}] No event loop during disconnect — "
                f"polling/transport may not be cleaned up"
            )
            return
        # Keep a strong reference until the task finishes — a bare create_task
        # is only weakly held and can be GC'd before it runs, orphaning the
        # poll loop or skipping the disconnect event.
        task = loop.create_task(self._on_disconnect_cleanup())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _on_disconnect_cleanup(self) -> None:
        """Stop polling, close the dead transport, and emit the disconnect event.

        Closing the transport here (not only on the graceful ``disconnect()``
        path) releases the socket / bound UDP port / pooled HTTP client
        immediately instead of holding it for the whole reconnect-backoff
        window (up to ~1h). The live ref is nulled so auto-reconnect rebuilds
        it; the disconnect event is emitted last so the reconnect's connect()
        doesn't race the close. Closing is safe in the watchdog path too — a
        transport's close() sets its own connected=False before tearing down,
        so it never re-enters this handler.
        """
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        # Capture the transport's last error before nulling it, so the
        # DeviceManager can classify the offline reason from the event handler
        # (which runs after this teardown).
        self._stash_transport_error()
        transport = self.transport
        self.transport = None
        if transport is not None:
            try:
                await transport.close()
            except Exception:
                log.debug(
                    f"[{self.device_id}] Error closing transport on disconnect",
                    exc_info=True,
                )
        try:
            await self._close_session()
        except Exception:
            log.debug(
                f"[{self.device_id}] Error closing driver session on "
                f"disconnect",
                exc_info=True,
            )
        if self._held:
            # This connection was never announced, so nothing else heard it
            # come up; the reconnect loop waiting on it tries again.
            self._held = False
            self._settle_answer(False)
            return
        try:
            await self.events.emit(f"device.disconnected.{self.device_id}")
        except Exception:
            log.debug(
                f"[{self.device_id}] Failed to emit disconnect event",
                exc_info=True,
            )

    # --- Convenience helpers ---

    # Flat state keys the platform writes on every device regardless of what
    # the driver declares, so an author is never asked to declare them.
    # `connected` is seeded by _init_state_variables() and thereafter owned by
    # the DeviceManager; a driver that also declares it is fine either way.
    _PLATFORM_STATE_PROPS: frozenset[str] = frozenset({"connected"})

    def set_state(self, property_name: str, value: Any) -> None:
        """Set a state value under this device's namespace.

        The property should be declared in ``DRIVER_INFO["state_variables"]``;
        an undeclared one still writes, but is reported (see
        :meth:`_check_undeclared_state`).

        Writing ``connected`` also carries every registered child with it (see
        :meth:`_children_follow_parent`). This method is the hook rather than
        the half-dozen lifecycle sites that flip the flag, because a driver
        overriding ``connect`` or ``disconnect`` still writes the key through
        here — and a child left claiming presence under an unreachable parent
        is the one failure this platform cannot ship.
        """
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(property_name)
        if var_def is None:
            self._check_undeclared_state(property_name)
        self._warn_on_type_mismatch(property_name, value, var_def)
        if property_name == self.LAST_ERROR_PROPERTY:
            self._last_error_writes += 1
        self.state.set(
            f"device.{self.device_id}.{property_name}",
            value,
            source=f"device.{self.device_id}",
        )
        if property_name == "connected" and self._children:
            self._children_follow_parent(bool(value))

    @property
    def strict_state(self) -> bool:
        """Whether a contract violation raises for *this* driver.

        ``OPENAVC_STRICT_DRIVER_STATE`` is the default and is read per call, so
        it can be turned on around a test run. But it is a *process* variable,
        and the surface that wants strictness most — the Builder's Test tab —
        runs inside a live server that is simultaneously polling real devices.
        Flipping the env var there would make every unrelated driver raise
        mid-poll for as long as somebody has the panel open: a device could go
        offline because a colleague was testing a different driver.

        So the flag is scoped to the instance instead. This is the same check,
        the same exception and the same message as the env var produces — only
        the question "is it on *here*" has a narrower answer. Setting it is the
        caller's job, and the only caller that does is the one-shot driver the
        live test builds, which is attached to nothing.
        """
        if self._strict_state_override is not None:
            return self._strict_state_override
        return strict_driver_state()

    @strict_state.setter
    def strict_state(self, value: bool | None) -> None:
        self._strict_state_override = value

    def _check_undeclared_state(self, property_name: str) -> None:
        """Report a write to a state variable the driver never declared.

        Deliberately *not* the same posture as the child-entity equivalent.
        ``_validate_child_prop`` raises, and ``set_child_state`` skips writes to
        an unregistered child, because those writes have nowhere to go — the
        key they would create is an orphan nothing lists. A flat key does have
        somewhere to go: the write lands, the value is live and correct, and
        the only thing missing is the driver's declaration of it. Raising here
        at runtime would take a working device offline over an author's
        omission and make the end user pay for it.

        Silence is not the alternative — silent inert state is the failure mode
        this platform exists to kill. Undeclared state is real state that no
        binding picker offers, no type is known for, and nothing in the project
        can be built against. So:

        * **Runtime** — ``log.warning`` once per key, which is what someone
          tailing the server log during bring-up sees.
        * **Strict mode** — raise, so the author's own test suite fails on it.
          That is where the author is iterating and where this is actionable.
          See :attr:`strict_state` for why that is per-driver and not only the
          process-wide env var.
        """
        if property_name in self._PLATFORM_STATE_PROPS:
            return
        # Every write, not once per key: an observer counts them.
        self._observe(UNDECLARED_STATE, state=property_name)
        driver_id = self.DRIVER_INFO.get("id", "?")
        summary = (
            f"[{self.device_id}] driver '{driver_id}' wrote state "
            f"'{property_name}', which it does not declare in "
            f'DRIVER_INFO["state_variables"]'
        )
        if self.strict_state:
            # Before the seen-set, so every write raises, not just the first.
            raise UndeclaredStateError(
                f"{summary}. Declare it, or stop writing it. (Reported as a "
                f"warning when {STRICT_DRIVER_STATE_ENV} is not set.)"
            )
        if property_name in self._undeclared_state_seen:
            return
        self._undeclared_state_seen.add(property_name)
        log.warning(
            f"{summary} — the value is live, but nothing knows its type and "
            f"no binding picker will offer it"
        )

    def _warn_on_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict | None
    ) -> None:
        """Report a state value that doesn't match its declared type: to the
        contract observer when there is one, and in a debug message. Helps
        catch driver bugs without being load-bearing — listeners still
        receive the value as written.
        """
        if not var_def or value is None:
            return
        declared = var_def.get("type", "string")
        if self.contract_observer is not None:
            self._observe_type_mismatch(prop_label, value, var_def, declared)
        if not _type_mismatch(declared, value, var_def, bool_is_number=True):
            return
        if declared == "enum":
            log.debug(
                f"[{self.device_id}] State '{prop_label}' value "
                f"{value!r} not in declared enum values {var_def['values']}"
            )
        else:
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"{declared} but got {type(value).__name__}: {value!r}"
            )

    def _observe_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict[str, Any], declared: str,
    ) -> None:
        """Tell the contract observer about a value not of its declared type."""
        problem = _type_problem(declared, value, var_def)
        if problem:
            self._observe(
                TYPE_MISMATCH, state=prop_label, declared=declared,
                value=value if is_flat_primitive(value) else repr(value),
                problem=problem,
            )

    def set_states(self, updates: dict[str, Any]) -> None:
        """Set multiple state values atomically (listeners see all changes at once)."""
        declared = self.DRIVER_INFO.get("state_variables", {})
        # Check every key before writing any, so strict mode fails the batch
        # whole rather than half-applied — the same rule set_child_state_batch
        # follows for an undeclared child prop.
        for k in updates:
            if k not in declared:
                self._check_undeclared_state(k)
        if self.LAST_ERROR_PROPERTY in updates:
            self._last_error_writes += 1
        namespaced = {
            f"device.{self.device_id}.{k}": v for k, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")
        # Same rule as set_state: reachability carries the children with it,
        # whichever door the driver used to write it.
        if "connected" in updates and self._children:
            self._children_follow_parent(bool(updates["connected"]))

    def get_state(self, property_name: str) -> Any:
        """Get a state value from this device's namespace."""
        return self.state.get(f"device.{self.device_id}.{property_name}")

    def delete_state(self, property_name: str) -> None:
        """Remove a state key from this device's namespace entirely.

        Unlike set_state(prop, None), the key disappears from the store (and
        from every consumer — the IDE's live state table, the cloud relay,
        WS clients — which are all notified of the removal). For drivers that
        adapt their surface at runtime: after narrowing the instance
        DRIVER_INFO to what the connected hardware actually supports, delete
        the state keys _init_state_variables() seeded for the dropped
        variables so they don't linger as phantom values.
        """
        self.state.delete(
            f"device.{self.device_id}.{property_name}",
            source=f"device.{self.device_id}",
        )
