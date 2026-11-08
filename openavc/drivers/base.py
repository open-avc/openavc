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

BaseDriver is assembled from mixins, one module each, and every name stays on
the driver object: connect, disconnect and a drop of the connection
(connect_lifecycle.py), the transport a config builds (transport_ladder.py),
the poll loop (polling.py), the liveness probe and the hold after a device
stopped answering (liveness.py), the push channel (push.py), child entities
(child_entities.py), a datagram beside the transport and Wake-on-LAN
(udp_side.py), a bridge's ports (bridge_ports.py), setup actions
(setup_wizard.py), and state writes with their contract checks
(state_writes.py). What stays here is the driver object itself: its
constructor, the hooks a driver overrides, and the faults and traffic it
reports. The names drivers have always imported from this module stay
importable from it.
"""

from __future__ import annotations

import asyncio
import functools
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from openavc.core.connection_fault import (
    ConnectionFault,
    ConnectionFaultError,
    default_fault_message,
)
from openavc.core.device_logins import SAVED_LOGIN, marked_fields
from openavc.core.device_traffic import record_traffic
from openavc.core.event_bus import EventBus
from openavc.drivers.bridge_ports import BridgePorts
from openavc.drivers.child_entities import ChildEntities
# connect(), disconnect() and a drop of the connection live in their own
# module; these names stay importable from here.
from openavc.drivers.connect_lifecycle import (  # noqa: F401
    ConnectEndedError,
    ConnectLifecycle,
    _verify_failure_error,
)
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
# A datagram beside the transport and Wake-on-LAN live in their own module;
# these names stay importable from here, the path drivers use.
from openavc.drivers.udp_side import (  # noqa: F401
    LIMITED_BROADCAST,
    WAKE_ON_LAN_PORT,
    UdpSideSend,
    WakeAddressError,
    magic_packet,
    normalize_mac,
)
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
from openavc.drivers.setup_wizard import SetupWizard
# A driver's state writes and the contract checks on them live in their own
# module; these names stay importable from here, the path drivers use.
from openavc.drivers.state_writes import (  # noqa: F401
    STRICT_DRIVER_STATE_ENV,
    StateWrites,
    UndeclaredStateError,
    strict_driver_state,
)
from openavc.core.state_store import StateStore
# Drivers have imported FrameParser from here; it stays importable.
from openavc.transport.frame_parsers import FrameParser  # noqa: F401
from openavc.transport.http_client import capture_driver_clients, capture_driver_websockets
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


class BaseDriver(
    ChildEntities, PushChannel, LivenessWatch, PollLoop, TransportLadder, ConnectLifecycle,
    UdpSideSend, BridgePorts, SetupWizard, StateWrites, ABC,
):
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
        # The connect in flight, and a drop while it runs
        # (drivers/connect_lifecycle.py).
        self._init_connect_lifecycle()
        # The registered child entities (drivers/child_entities.py).
        self._init_child_entities()
        # Set by the platform only for the duration of a run_setup_action call;
        # backs request_config_update / request_reconnect. None at all other times.
        self._setup_context: Any = None
        # A state key for every declared variable, and the bookkeeping for
        # what the driver writes without declaring (drivers/state_writes.py).
        self._init_state_writes()

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
