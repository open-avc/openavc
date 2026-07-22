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
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from server.core.connection_fault import (
    NO_RESPONSE,
    TRANSPORT_DISCONNECTED,
    ConnectionFault,
    ConnectionFaultError,
    classify_connection_fault,
    default_fault_message,
)
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.transport.frame_parsers import FrameParser
from server.utils.logger import get_logger

# Re-exported for drivers: raise ConnectionFaultError(msg, code=...) instead of
# wording a plain ConnectionError to hit the classifier's signature tables.
__all__ = [
    "BaseDriver",
    "CommandParamError",
    "ConnectionFaultError",
    "DeviceSettingValueError",
    "normalize_and_validate_command_params",
    "validate_device_setting_value",
]

log = get_logger(__name__)


class CommandParamError(ValueError):
    """A command parameter value failed the driver's declared validation
    (wrong type, out of min/max range, or not matching a declared pattern).

    Distinct from a generic ValueError so the API can map it to a 400 (bad
    request) with the message instead of a misleading 404 "device not found".
    Subclasses ValueError so existing ``except ValueError`` handlers still
    catch it. The message is user-facing and actionable — surface it verbatim.
    """


class DeviceSettingValueError(ValueError):
    """A device-setting value failed the setting's declared validation
    (wrong type, out of min/max range, not one of the declared values, or
    a regex mismatch).

    The min/max/values/regex on a ``device_settings`` entry used to be
    enforced only by the IDE's editor; the runtime is the source of truth
    (a script, macro, cloud command, or raw REST call can send anything —
    and an unchecked value can even transmit a literal ``{value:d}``
    placeholder to the device when a format spec fails). Same 400-mapping
    rationale as CommandParamError.
    """


def validate_device_setting_value(key: str, sdef: Any, value: Any) -> Any:
    """Validate + coerce one device-setting write against its declared schema.

    Returns the coerced value; raises :class:`DeviceSettingValueError` with a
    user-facing message. Mirrors the IDE editor's rules so no caller can push
    past them: boolean → a real bool (tolerant of "true"/"false"/0/1 for REST
    callers), integer/number → numeric honoring ``min``/``max``, declared
    ``values`` → membership, accepting a {value, label} entry's
    label and normalizing to the wire value (settings are persisted device config — there is
    no forgiving-free-text rationale like command pickers), string → trimmed,
    full-matching ``regex`` when declared (``pattern`` accepted as an alias —
    command params use that spelling and the mix-up was a silent no-op).
    """
    if not isinstance(sdef, dict):
        return value
    if value is None:
        raise DeviceSettingValueError(f"'{key}': a value is required")
    stype = sdef.get("type", "string")

    if stype == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("true", "1", "yes", "on"):
                return True
            if v in ("false", "0", "no", "off"):
                return False
        raise DeviceSettingValueError(
            f"'{key}' must be true or false, got {value!r}"
        )

    if stype in ("integer", "number", "float"):
        num: float | int | None = None
        if isinstance(value, bool):
            num = None
        elif isinstance(value, (int, float)):
            num = value
        elif isinstance(value, str):
            try:
                num = float(value.strip())
            except ValueError:
                num = None
        if num is not None and stype == "integer":
            if float(num).is_integer():
                num = int(num)
            else:
                num = None
        if num is None:
            kind = "whole number" if stype == "integer" else "number"
            raise DeviceSettingValueError(
                f"'{key}' must be a {kind}, got {value!r}"
            )
        mn, mx = sdef.get("min"), sdef.get("max")
        if isinstance(mn, (int, float)) and num < mn:
            raise DeviceSettingValueError(
                f"'{key}' must be at least {mn}, got {num:g}"
            )
        if isinstance(mx, (int, float)) and num > mx:
            raise DeviceSettingValueError(
                f"'{key}' must be at most {mx}, got {num:g}"
            )
        return num

    sval = str(value).strip() if not isinstance(value, str) else value.strip()

    values = sdef.get("values")
    if isinstance(values, list) and values:
        # An enum list may carry {value, label} entries (label shown in the
        # editor, wire value written). Accept either the wire value or the
        # label and normalize to the wire value. Unlike a command picker, a
        # device setting is persisted config — there is no forgiving-free-text
        # path, so anything not resolving into the set is rejected.
        wire = _enum_wire_values(values)
        resolved = _resolve_enum_param_value(sval, values)
        if resolved not in wire:
            raise DeviceSettingValueError(
                f"'{key}' must be one of: {', '.join(wire)} — got {value!r}"
            )
        return resolved

    regex = sdef.get("regex") or sdef.get("pattern")
    if regex and sval != "":
        try:
            matched = re.fullmatch(regex, sval) is not None
        except re.error:
            # A malformed declared regex shouldn't block writes; load-time
            # validation owns rejecting it.
            matched = True
        if not matched:
            raise DeviceSettingValueError(
                f"'{key}' value {sval!r} does not match the required format "
                f"({regex})"
            )
    return sval


def _as_number(value: Any, ptype: str) -> float | None:
    """Coerce a command-param value to a float for range checking, or None when
    it isn't a valid number of the declared type. Booleans never count as
    numbers; an integer param rejects a non-integral value (5.0 is fine, 5.5 is
    not)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if ptype == "integer" and f != int(f):
        return None
    return f


def _resolve_enum_param_value(value: str, options: list[Any]) -> str:
    """Map a command-param value against an ``enum`` option list whose entries
    may be plain strings or ``{value, label}`` objects.

    Returns the wire value: a value already matching an option's ``value`` is
    kept; otherwise a human ``label`` maps to its ``value``; an unrecognized
    value passes through unchanged (a ``$var`` may resolve to a computed wire
    value outside the authored set). Lets an author label a hex/code set once
    (``{value: "0f", label: "Multi Channel Stereo"}``) instead of defining one
    command per code.
    """
    label_map: dict[str, str] = {}
    for opt in options:
        if isinstance(opt, dict):
            v = opt.get("value")
            if v is None:
                continue
            vs = str(v)
            if value == vs:
                return value
            lbl = opt.get("label")
            if isinstance(lbl, str) and lbl:
                label_map.setdefault(lbl, vs)
        elif value == str(opt):
            return value
    return label_map.get(value, value)


def _enum_wire_values(options: list[Any]) -> list[str]:
    """The wire values of an enum option list — each entry a plain value or a
    ``{value, label}`` object (a label-only / value-less dict is skipped, the
    same entries :func:`_resolve_enum_param_value` can map to)."""
    out: list[str] = []
    for opt in options:
        if isinstance(opt, dict):
            v = opt.get("value")
            if v is not None:
                out.append(str(v))
        else:
            out.append(str(opt))
    return out


def normalize_and_validate_command_params(
    command: str,
    param_defs: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Trim and validate user-supplied command params against the command's
    declared param schema, returning a normalized copy.

    This is the runtime gate for command values. The IDE's pickers and inline
    validation are an authoring aid, but the runtime is the source of truth —
    it must never trust that the client narrowed the value correctly (a macro
    can resolve a ``$var`` to anything, the cloud/REST API can post a raw value,
    an old client may predate the validation). So every command value passes
    through here regardless of caller. The DeviceManager dispatch path applies
    it to every driver (Python drivers included — declared ``min``/``max``/
    ``pattern`` are enforced, not cosmetic); ConfigurableDriver also runs it
    internally so direct YAML-driver use (the Driver Builder test harness)
    stays gated.

    Normalization: string values are whitespace-trimmed (the overwhelmingly
    correct default for AV text protocols). A param whose edge whitespace is
    protocol-meaningful — a raw passthrough payload with a trailing line
    terminator, text typed verbatim into an on-screen keyboard — can declare
    ``trim: false`` to opt out. Validation, per the declared type:
      - integer/number/float -> must parse as a number and honor min/max.
      - string (and any type carrying a ``pattern``) -> must fullmatch the
        declared regex.
    Only declared params are checked; config-passthrough keys and params with
    no schema entry are left untouched, and empty optional values are skipped
    (required-param presence is enforced by the caller/UI, not here).

    Raises ``CommandParamError`` (a ValueError) with a user-facing message.
    """
    if not isinstance(param_defs, dict) or not params:
        return params
    out = dict(params)
    for name, pdef in param_defs.items():
        if not isinstance(pdef, dict) or name not in out:
            continue
        value = out[name]
        if value is None:
            continue
        ptype = pdef.get("type", "string")

        # Trim string values, then skip empties (an optional left blank).
        if isinstance(value, str):
            if pdef.get("trim", True) is not False:
                value = value.strip()
                out[name] = value
            if value == "":
                continue

        if ptype in ("integer", "number", "float"):
            num = _as_number(value, ptype)
            if num is None:
                kind = "whole number" if ptype == "integer" else "number"
                raise CommandParamError(
                    f"'{command}': '{name}' must be a {kind}, got {value!r}"
                )
            mn, mx = pdef.get("min"), pdef.get("max")
            if isinstance(mn, (int, float)) and num < mn:
                raise CommandParamError(
                    f"'{command}': '{name}' must be at least {mn}, got {num:g}"
                )
            if isinstance(mx, (int, float)) and num > mx:
                raise CommandParamError(
                    f"'{command}': '{name}' must be at most {mx}, got {num:g}"
                )
            # Normalize a *numeric* value to the declared type so a scaled
            # control value lands on the wire the way the protocol needs — an
            # `integer` param sends 26, not 26.0, whether a slider, a macro, or
            # the REST API produced it. `decimals` (number only) rounds to that
            # many places; `decimals: 0` yields a whole number. A string is left
            # untouched (validation keeps the long-standing string convention:
            # a value already shaped as text, e.g. a zero-padded id, is not
            # reformatted); only floats/ints — what arithmetic produces — are
            # normalized. A `number` with no `decimals` rule is left as-is too.
            decimals = pdef.get("decimals")
            if not isinstance(value, str):
                if ptype == "integer":
                    out[name] = int(num)
                elif isinstance(decimals, int):
                    out[name] = int(round(num)) if decimals <= 0 else round(num, decimals)
        else:
            # An enum `values` list may carry {value, label} entries. Accept the
            # caller passing either the wire value or the human label and
            # normalize to the wire value before it goes on the wire. Applies to
            # any param that declares `values` (type: enum, or a string param
            # with a dropdown), so labels work from every caller — picker, macro
            # $var, or the REST/cloud API.
            options = pdef.get("values")
            if isinstance(options, list) and options and isinstance(value, str):
                value = _resolve_enum_param_value(value, options)
                out[name] = value

            pattern = pdef.get("pattern")
            if pattern and isinstance(value, str):
                try:
                    matched = re.fullmatch(pattern, value) is not None
                except re.error:
                    # A malformed pattern is caught at driver load; if one slips
                    # through, don't block the command on it.
                    matched = True
                if not matched:
                    raise CommandParamError(
                        f"'{command}': '{name}' value {value!r} does not match "
                        f"the required format ({pattern})"
                    )
    return out


# String child local IDs are embedded directly in a flat state key
# (device.<id>.<type>.<local_id>.<prop>) and matched by fnmatch glob
# subscriptions, so they're restricted to this charset — no dots (the key
# separator), whitespace, or glob metacharacters. A driver that keys children
# by a device-native name (a Q-SYS Code Name, an MQTT topic leaf) must
# sanitize to this set and keep the original in the child's `label`.
_CHILD_STRING_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CHILD_STRING_ID_MAX_LEN = 128


class BaseDriver(ABC):
    """Abstract base class for all device drivers."""

    # Subclasses MUST override this with their metadata dict
    DRIVER_INFO: dict[str, Any] = {}

    # Liveness watchdog knobs (see _liveness_probe). Class attributes so a
    # driver can tune them wholesale (`HEALTH_INTERVAL_S = 20.0`) or per
    # instance; the defaults suit most request/response protocols. INTERVAL is
    # the gap between probes, TIMEOUT the per-probe reply deadline, and after
    # MAX_FAILURES consecutive misses the transport is torn down with a typed
    # ``no_response`` fault (FAULT_MESSAGE) so the platform reconnects and the
    # device card shows the real cause.
    HEALTH_INTERVAL_S: float = 30.0
    HEALTH_TIMEOUT_S: float = 5.0
    HEALTH_MAX_FAILURES: int = 2
    HEALTH_FAULT_MESSAGE: str = (
        "Connected, but the device stopped answering keep-alive probes."
    )

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
        self._poll_task: asyncio.Task | None = None
        self._connected = False
        self._last_poll_success: float = 0.0
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
        # Liveness watchdog task + consecutive-miss counter (see
        # _liveness_probe). Started by connect() when the driver supplies a
        # probe; stopped on disconnect / transport drop.
        self._health_task: asyncio.Task | None = None
        self._health_failures = 0
        # Push-notification subscription (DRIVER_INFO["push"] — a multicast
        # group membership, an inbound TCP dial-back listener, a list of SSE
        # event-stream handles, or an HTTP push-listener registration).
        # Started by connect() once the session
        # is up — before any on_connect arming commands run — and stopped on
        # both disconnect paths. None when the driver declares no push block
        # or the subscription failed (polling still covers it).
        self._push_subscription: Any = None
        # http_listener shape: the callback URL the device must deliver to,
        # rebuilt on every (re)connect. Exposed as push_callback_url so
        # registration commands can hand it to the device.
        self._push_callback_url: str = ""
        # Registered child entities: {child_type: {local_id: register_epoch}}.
        # The inner mapping is a dict (not a set) so it preserves insertion
        # order, which makes list_children() output stable for tests and IDE
        # displays. The value is a monotonic registration epoch (always
        # truthy) used by poll_children to detect a child that was
        # deregistered+re-registered mid-poll (its state was reset) so a stale
        # write doesn't clobber the reset (ABA guard).
        self._children: dict[str, dict[int | str, int]] = {}
        self._child_register_seq = 0
        # Per-child dynamic state-variable schemas, for child types declared
        # `dynamic: true`. {(child_type, local_id): {prop: var_def}}. Populated
        # at register_child when a `schema=` is supplied, and consulted by
        # _effective_child_schema so a dynamic type's controls can be discovered
        # at runtime (e.g. a Q-SYS component's controls, an MQTT topic's fields)
        # instead of declared statically up-front. Empty for static child types.
        self._child_schemas: dict[
            tuple[str, int | str], dict[str, dict[str, Any]]
        ] = {}
        # Set by the platform only for the duration of a run_setup_action call;
        # backs request_config_update / request_reconnect. None at all other times.
        self._setup_context: Any = None
        # Project-side child metadata: {child_type: {local_id_padded: {label, config}}}.
        # Populated by DeviceManager.add_device after construction (via
        # set_project_child_entities) so existing driver subclasses with a
        # fixed __init__ signature don't need to change. register_child
        # consults this to seed the per-child `label` state key without
        # the driver having to plumb the project schema itself.
        self._project_child_entities: dict[str, dict[str, dict[str, Any]]] = {}

        # Initialize state variables from DRIVER_INFO
        self._init_state_variables()

    def set_project_child_entities(
        self, child_entities: dict[str, dict[str, dict[str, Any]]] | None,
    ) -> None:
        """Set the project-side child metadata for this driver instance.

        Called by DeviceManager from the project's DeviceConfig.child_entities
        after construction. Keeps the BaseDriver __init__ signature stable
        for existing driver subclasses that define their own __init__.
        """
        self._project_child_entities = dict(child_entities or {})

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
        if self.transport is None:
            return False
        return getattr(self.transport, "connected", False)

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
        """Capture the live transport's ``last_error`` before it's torn down.

        A no-op when there's no transport or it reports no error, so it never
        overwrites a real cause with an empty string.
        """
        transport = self.transport
        if transport is not None:
            err = getattr(transport, "last_error", "") or ""
            if err:
                self._last_transport_error = err

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

    @staticmethod
    def _numeric_default(var_def: dict[str, Any], *, as_int: bool) -> int | float:
        """Default for an integer/number/float state var: its declared ``min``
        (or 0), coerced to the right numeric type.

        A non-numeric ``min`` (e.g. a hand-edited driver with ``min: "low"``)
        is an authoring bug; fall back to 0 with a warning rather than crashing
        driver instantiation with an uncaught ValueError.
        """
        raw = var_def.get("min", 0)
        try:
            return int(raw) if as_int else float(raw)
        except (TypeError, ValueError):
            log.warning(
                "state variable declares a non-numeric 'min' %r; defaulting to 0",
                raw,
            )
            return 0 if as_int else 0.0

    def _init_state_variables(self) -> None:
        """Register all state variables from DRIVER_INFO with default values."""
        state_vars = self.DRIVER_INFO.get("state_variables", {})
        for prop_name, prop_info in state_vars.items():
            var_type = prop_info.get("type", "string")
            if var_type == "boolean":
                default: Any = False
            elif var_type == "integer":
                default = self._numeric_default(prop_info, as_int=True)
            elif var_type in ("number", "float"):
                # 'float' is an accepted type alias for 'number' (driver loader
                # + schema). Both must seed a numeric 0.0, not '' — otherwise a
                # consumer reading the var before the first poll gets a string
                # where a number is expected.
                default = self._numeric_default(prop_info, as_int=False)
            elif var_type == "enum":
                values = prop_info.get("values", [])
                default = values[0] if values else ""
            else:
                default = ""
            self.set_state(prop_name, default)
        # Always set a connected state
        self.set_state("connected", False)

    # --- Connection lifecycle (concrete with auto-transport) ---

    async def connect(self) -> None:
        """
        Establish connection to the device using auto-transport.

        Reads DRIVER_INFO["transport"] and self.config to create the
        appropriate transport (TCP or serial). Override this method for
        custom connection logic (e.g., greeting handshakes).
        """
        # Start each attempt with a clean slate so a previous failure's cause
        # can't be misattributed to this one by the fault classifier.
        self._last_transport_error = ""
        self._last_fault = None
        # A reconnect attempt may arrive with a stale push subscription if the
        # async cleanup hasn't run yet; drop it so we never hold two.
        await self._stop_push()
        if self.transport:
            try:
                await self.transport.close()
            except Exception:
                pass
            self.transport = None

        # A device's config may override the driver's default transport (e.g. a
        # CLI driver that defaults to "ssh" in production but connects over raw
        # "tcp" to a CLI simulator). Falls back to the driver default when unset,
        # so existing devices are unaffected.
        transport_type = self.config.get("transport") or self.DRIVER_INFO.get(
            "transport", "tcp"
        )

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

        frame_parser = self._create_frame_parser()
        delimiter = self._resolve_delimiter()

        # Get control interface binding (if configured)
        from server.system_config import get_system_config
        control_ip = get_system_config().get("network", "control_interface")

        if transport_type == "tcp":
            from server.transport.tcp import TCPTransport

            host = self.config.get("host", "")
            port = self._required_port()
            delay = self.config.get("inter_command_delay", 0.0)

            # Forward TLS + connect-timeout from config so a declarative driver
            # can talk to a device on TLS-wrapped TCP with `ssl: true` instead
            # of overriding connect(). Same config vocabulary as the http
            # branch (`ssl` to enable, `verify_ssl` for cert checking); the
            # defaults match TCPTransport.create() so plain-TCP devices that
            # set none of these are unaffected.
            self.transport = await TCPTransport.create(
                host=host,
                port=port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                name=self.device_id,
                local_addr=(control_ip, 0) if control_ip else None,
                timeout=self.config.get("timeout", 5.0),
                ssl=self.config.get("ssl", False),
                ssl_verify=self.config.get("verify_ssl", True),
                keepalive=bool(self.config.get("tcp_keepalive", False)),
            )
        elif transport_type == "serial":
            from server.transport.serial_transport import SerialTransport

            serial_port = self.config.get("port", "")
            delay = self.config.get("inter_command_delay", 0.0)
            baudrate, bytesize, parity, stopbits, rtscts, xonxoff = (
                self._coerce_serial_params(self.config)
            )

            self.transport = await SerialTransport.create(
                port=serial_port,
                baudrate=baudrate,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                delimiter=delimiter,
                frame_parser=frame_parser,
                inter_command_delay=delay,
                bytesize=bytesize,
                parity=parity,
                stopbits=stopbits,
                rtscts=rtscts,
                xonxoff=xonxoff,
                name=self.device_id,
            )
        elif transport_type == "udp":
            from server.transport.udp import UDPTransport

            host = self.config.get("host", "")
            port = self._required_port()
            delay = self.config.get("inter_command_delay", 0.0)

            self.transport = UDPTransport(
                host=host,
                port=port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                inter_command_delay=delay,
                name=self.device_id,
            )
            await self.transport.open(
                local_addr=control_ip or None,
            )
        elif transport_type == "osc":
            from server.transport.osc import OSCTransport

            host = self.config.get("host", "")
            port = self._required_port()
            listen_port = self.config.get("listen_port", 0)
            delay = self.config.get("inter_command_delay", 0.0)
            # OSC over TCP+SLIP when the device opts in (e.g. QLab's reliable
            # large-reply path). Defaults to UDP, so existing OSC drivers that
            # don't set transport_mode are unaffected.
            osc_tcp = str(
                self.config.get("transport_mode", "udp")
            ).lower() == "tcp"

            self.transport = OSCTransport(
                host=host,
                port=port,
                listen_port=listen_port,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                inter_command_delay=delay,
                name=self.device_id,
                tcp=osc_tcp,
            )
            await self.transport.open(
                local_addr=control_ip or None,
            )
        elif transport_type == "http":
            from server.transport.http_client import HTTPClientTransport

            host = self.config.get("host", "")
            # Don't use .get("port", 80) — the sentinel-default makes an
            # explicit `port: 80, ssl: true` indistinguishable from "not set",
            # so the next branch silently rewrites it to 443 (A66). Read
            # without a default and apply the scheme-appropriate fallback only
            # when port is genuinely missing.
            port = self.config.get("port")
            use_ssl = self.config.get("ssl", False)
            scheme = "https" if use_ssl else "http"
            if port is None:
                port = 443 if use_ssl else 80
            base_url = f"{scheme}://{host}:{port}"

            # Build credentials from config
            auth_type = self.config.get("auth_type", "none")
            credentials = {}
            if auth_type in ("basic", "digest"):
                credentials["username"] = self.config.get("username", "")
                credentials["password"] = self.config.get("password", "")
            elif auth_type == "bearer":
                credentials["token"] = self.config.get("token", "")
            elif auth_type == "api_key":
                credentials["header"] = self.config.get("api_key_header", "X-API-Key")
                credentials["key"] = self.config.get("api_key", "")

            self.transport = HTTPClientTransport(
                base_url=base_url,
                auth_type=auth_type,
                credentials=credentials,
                verify_ssl=self.config.get("verify_ssl", True),
                default_headers=self.config.get("default_headers", {}),
                timeout=self.config.get("timeout", 10.0),
                name=self.device_id,
                local_address=control_ip or None,
                max_response_bytes=self._http_max_response_bytes(),
            )
            await self.transport.open()
        elif transport_type == "ssh":
            from server.transport.ssh import SSHTransport

            host = self.config.get("host", "")
            port = int(self.config.get("port", 22) or 22)
            username = self.config.get("username", "")
            auth_method = self.config.get("ssh_auth_method", "key")
            known_hosts = self.config.get("known_hosts_path")
            if not known_hosts:
                from server.system_config import get_data_dir
                known_hosts = str(get_data_dir() / "ssh" / "known_hosts")

            self.transport = await SSHTransport.create(
                host=host,
                port=port,
                username=username,
                on_data=self.on_data_received,
                on_disconnect=self._handle_transport_disconnect,
                auth_method=auth_method,
                password=self.config.get("password") or None,
                key_path=self.config.get("key_path") or None,
                known_hosts_path=known_hosts,
                host_key_policy=self.config.get("host_key_policy", "accept-new"),
                connect_timeout=float(self.config.get("connect_timeout", 15.0)),
                inter_command_delay=self.config.get("inter_command_delay", 0.0),
                name=self.device_id,
            )
        elif transport_type == "mqtt":
            from server.transport.mqtt import MQTTTransport

            host = self.config.get("host", "")
            port = int(self.config.get("port", 1883) or 1883)

            # Pub/sub, not a byte stream: inbound messages arrive topic-tagged
            # via on_mqtt_message (subscribe in _post_connect). TLS vocabulary
            # matches the tcp/http branches (`ssl`, `verify_ssl`); MQTT also
            # accepts a client certificate for devices that require one.
            self.transport = await MQTTTransport.create(
                host=host,
                port=port,
                client_id=self.config.get("client_id") or None,
                username=self.config.get("username") or None,
                password=self.config.get("password") or None,
                use_tls=bool(
                    self.config.get("ssl", self.config.get("use_tls", False))
                ),
                verify_ssl=bool(self.config.get("verify_ssl", True)),
                client_cert=self.config.get("client_cert") or None,
                client_key=self.config.get("client_key") or None,
                ca_cert=self.config.get("ca_cert") or None,
                ciphers=self.config.get("ciphers") or None,
                keepalive=int(self.config.get("keepalive", 60) or 60),
                protocol_version=str(self.config.get("mqtt_version", "3.1.1")),
                on_message=self.on_mqtt_message,
                on_disconnect=self._handle_transport_disconnect,
                name=self.device_id,
            )
        else:
            raise ValueError(f"Unsupported transport type: {transport_type}")

        # For connectionless transports (OSC, HTTP), verify the remote host
        # is actually reachable before reporting connected. TCP and serial
        # validate during open/create. UDP is genuinely connectionless and
        # has no transport-level probe — a UDP driver MUST either make its
        # poll() await a device reply and raise on silence (so the missed-poll
        # watchdog becomes the reachability signal) or supply a liveness probe
        # (a YAML driver's `liveness:` block / a Python override of
        # _liveness_probe); without one of those, `connected` stays True
        # against a dead host forever (A68). A poll_interval alone is NOT
        # enough: a fire-and-forget poll (e.g. a YAML driver's UDP queries,
        # which never await replies) provides no liveness.
        # Set verify_timeout: 0 in config to skip the pre-connect probe on
        # OSC/HTTP.
        verify_timeout = self.config.get("verify_timeout", 3.0)
        if verify_timeout > 0 and hasattr(self.transport, "verify"):
            if not await self.transport.verify(timeout=verify_timeout):
                # Retain the transport's underlying cause (refused / timeout)
                # before tearing it down — the raise below only says "not
                # responding", which the classifier would read as no_response.
                self._stash_transport_error()
                if self.transport:
                    await self.transport.close()
                    self.transport = None
                raise ConnectionError(
                    f"Device at {self.config.get('host', '?')}:"
                    f"{self.config.get('port', '?')} is not responding"
                )

        try:
            # Device-specific session setup (e.g. an SSH/CLI driver entering
            # privileged mode and disabling output paging) runs before we
            # report connected so the first poll sees a ready session. A raise
            # here aborts the connection and the transport is cleaned up below.
            await self._post_connect()
            self._connected = True
            self.set_state("connected", True)
            await self.events.emit(f"device.connected.{self.device_id}")
            log.info(f"[{self.device_id}] Connected via {transport_type}")
        except Exception:
            # Clean up transport if post-connect setup fails. Stash the
            # transport's last error first (e.g. an SSH auth failure surfaces
            # as ssh stderr on the transport, not in this exception).
            self._stash_transport_error()
            if self.transport:
                await self.transport.close()
                self.transport = None
            self._connected = False
            raise

        # Push notifications (DRIVER_INFO["push"]): subscribe as soon as the
        # session is up — BEFORE any subclass connect() stage runs on_connect
        # arming commands, so the listener never misses the first frame a
        # freshly-armed device sends. Failure is non-fatal (logged inside);
        # polling still covers the device.
        await self._start_push()

        # Start polling if configured
        poll_interval = self.config.get("poll_interval", 0)
        if poll_interval > 0:
            await self.start_polling(poll_interval)

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

        Stops polling, closes transport, and updates state.
        Override for custom disconnect logic.
        """
        self._stop_health_loop()
        await self._stop_push()
        await self.stop_polling()
        if self.transport:
            await self.transport.close()
            self.transport = None
        self._connected = False
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
    # kind:"setup", see server/drivers/actions.py). Unlike a command, it can run
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
        device back online over them. Return a result dict; raise to report
        failure. Default raises NotImplementedError.
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
    # pass-through endpoint (engine.resolved_device_config) and, just before
    # that downstream connects, calls prepare_bridge_port() on the live bridge
    # so the hardware is configured for the downstream first.

    @property
    def is_bridge(self) -> bool:
        """True if this driver advertises bridge ports."""
        return bool((self.DRIVER_INFO.get("bridge") or {}).get("ports"))

    async def prepare_bridge_port(
        self, port_id: str, params: dict[str, Any]
    ) -> None:
        """Configure one of this bridge's ports for a downstream device that is
        about to connect through it.

        Called by the platform on the *bridge* driver. For a serial port a
        bridge pushes the downstream's baud/parity to the hardware here, so the
        transparent pass-through carries bytes at the right line settings;
        ``params`` is the downstream's resolved connection config (baudrate,
        parity, bytesize, stopbits, ...). Default: no-op — a bridge that needs
        no per-port setup, or a port kind that routes commands at send time
        (IR / relay), does nothing. Raising does NOT block the downstream
        connect (the platform logs and proceeds), so a transient bridge-side
        failure can't strand the downstream device offline.
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

    # --- Optional overrides ---

    async def on_data_received(self, data: bytes) -> None:
        """
        Called by the transport when data arrives from the device.

        Override in the driver to implement protocol-specific parsing.
        Default: no-op.
        """

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
        """

    async def _post_connect(self) -> None:
        """Hook for device-specific session setup after the transport opens and
        before the device is marked connected.

        CLI-over-SSH/telnet drivers override this to read the login banner,
        enter privileged mode, and disable output paging. Raising aborts the
        connection (the caller closes the transport). Default: no-op.
        """

    # --- Liveness watchdog (opt-in awaited probe) ---

    async def _liveness_probe(self) -> None:
        """Optional hook: send a cheap request and await the device's reply.

        Override in drivers whose link can die silently — push/receive-mostly
        TCP (no FIN when the device vanishes), UDP (genuinely connectionless),
        anything where neither polling nor the transport surfaces a dead peer.
        Return normally when the device answered; raise (TimeoutError /
        ConnectionError / OSError / a protocol error) on a miss. The base
        class runs the probe every HEALTH_INTERVAL_S under a HEALTH_TIMEOUT_S
        deadline and, after HEALTH_MAX_FAILURES consecutive misses, tears the
        transport down with a typed ``no_response`` fault so the platform
        reconnects and the device card shows the real cause. Overriding this
        is the whole opt-in — connect() starts the loop, disconnect and the
        transport-drop cleanup stop it.
        """
        raise NotImplementedError

    def _health_enabled(self) -> bool:
        """True when this driver supplies a liveness probe.

        Default: the subclass overrides _liveness_probe. ConfigurableDriver
        overrides this to key off the YAML ``liveness:`` block instead (it
        always overrides the probe, but only some definitions declare one).
        """
        return type(self)._liveness_probe is not BaseDriver._liveness_probe

    def _start_health_loop(self) -> None:
        """Start the liveness watchdog (idempotent while one is running)."""
        if self._health_task is None or self._health_task.done():
            self._health_failures = 0
            self._health_task = asyncio.ensure_future(self._health_loop())

    def _stop_health_loop(self) -> None:
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
        self._health_task = None

    async def _health_loop(self) -> None:
        """Probe the device every HEALTH_INTERVAL_S and force a reconnect when
        it stops answering.

        The probe is awaited under HEALTH_TIMEOUT_S so a hung implementation
        can't stall the loop. Any exception (timeout, transport failure,
        protocol error) counts as a miss; a clean return resets the counter.
        """
        interval = float(self.HEALTH_INTERVAL_S)
        timeout = float(self.HEALTH_TIMEOUT_S)
        max_failures = max(int(self.HEALTH_MAX_FAILURES), 1)
        try:
            while self.transport is not None and getattr(
                self.transport, "connected", False
            ):
                await asyncio.sleep(interval)
                if not (
                    self.transport is not None
                    and getattr(self.transport, "connected", False)
                ):
                    return
                try:
                    await asyncio.wait_for(self._liveness_probe(), timeout)
                    self._health_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._health_failures += 1
                    log.warning(
                        f"[{self.device_id}] Liveness probe failed "
                        f"({self._health_failures}/{max_failures}): {exc}"
                    )
                    if self._health_failures >= max_failures:
                        log.warning(
                            f"[{self.device_id}] Device unresponsive — "
                            f"dropping the connection so the platform can "
                            f"reconnect"
                        )
                        self._force_disconnect(
                            NO_RESPONSE, self.HEALTH_FAULT_MESSAGE
                        )
                        return
        except asyncio.CancelledError:
            return

    # --- Push notifications (DRIVER_INFO["push"]) ---

    def _resolve_push_value(self, value: Any) -> Any:
        """Resolve a push-block value: ``{config_field}`` tokens substitute
        from the device config; unknown fields are left verbatim so the
        warning in _start_push names exactly what didn't resolve."""
        if isinstance(value, str) and "{" in value:
            return re.sub(
                r"\{(\w+)\}",
                lambda m: str(self.config.get(m.group(1), m.group(0))),
                value,
            )
        return value

    async def _start_push(self) -> None:
        """Subscribe to the driver's declared push channel, if any.

        ``multicast``, ``sse``, ``tcp_listener`` and ``http_listener`` all
        exist today. Never raises: a device whose push channel can't be
        opened still connects and polls — the gap is logged so the user can
        see why changes aren't instant.
        """
        push_def = self.DRIVER_INFO.get("push")
        if not isinstance(push_def, dict):
            return
        ptype = push_def.get("type")
        if ptype == "multicast":
            await self._start_push_multicast(push_def)
        elif ptype == "sse":
            self._start_push_sse(push_def)
        elif ptype == "tcp_listener":
            await self._start_push_tcp_listener(push_def)
        elif ptype == "http_listener":
            await self._start_push_http_listener(push_def)
        else:
            # The loader rejects unknown/unsupported types for catalog
            # drivers; this is the runtime backstop for hand-installed files.
            log.warning(
                f"[{self.device_id}] push: type "
                f"{ptype!r} is not supported at runtime"
            )

    async def _start_push_multicast(self, push_def: dict[str, Any]) -> None:
        """Join the device's multicast notification group."""
        from server.transport.multicast_listener import (
            is_multicast_group,
            subscribe,
        )

        group = str(self._resolve_push_value(push_def.get("group", "")) or "")
        raw_port = self._resolve_push_value(push_def.get("port"))
        try:
            port = int(str(raw_port).strip())
        except (TypeError, ValueError):
            port = 0
        if not is_multicast_group(group) or not (0 < port < 65536):
            log.warning(
                f"[{self.device_id}] push: cannot subscribe — group "
                f"{group!r} / port {raw_port!r} did not resolve to a "
                f"multicast address and port (check the device's "
                f"notification settings fields)"
            )
            return
        try:
            self._push_subscription = await subscribe(
                group=group,
                port=port,
                source_ip=str(self.config.get("host", "") or ""),
                callback=self._handle_push_datagram,
                name=self.device_id,
            )
        except OSError as e:
            log.warning(
                f"[{self.device_id}] push: could not open multicast "
                f"listener on {group}:{port}: {e}"
            )

    async def _start_push_tcp_listener(self, push_def: dict[str, Any]) -> None:
        """Open the shared inbound listener the device dials back to.

        The subscription starts before on_connect / registration commands run,
        so the first frame a freshly-registered device pushes is never missed.
        The actual bound port is injected into the device config as
        ``listener_port`` — the reserved substitution token registration
        commands use (``my_port={listener_port}``). When the push block names
        a ``register`` command it runs here, which also re-arms the device on
        every reconnect (device-side registrations don't survive a reboot or
        a link cut).
        """
        from server.transport import tcp_listener
        from server.transport.frame_parsers import build_frame_parser

        raw_port = self._resolve_push_value(push_def.get("port"))
        try:
            port = int(str(raw_port).strip())
        except (TypeError, ValueError):
            port = -1
        if not (0 <= port < 65536):
            log.warning(
                f"[{self.device_id}] push: cannot listen — port {raw_port!r} "
                f"did not resolve to a TCP port (check the device's "
                f"notification settings fields)"
            )
            return

        frame_cfg = push_def.get("frame_parser")
        if isinstance(frame_cfg, dict):
            def factory() -> Any:
                return build_frame_parser(frame_cfg)
        else:
            factory = None

        try:
            sub = await tcp_listener.subscribe(
                port=port,
                source_ip=str(self.config.get("host", "") or ""),
                callback=self._handle_push_datagram,
                name=self.device_id,
                frame_parser_factory=factory,
            )
        except OSError as e:
            log.warning(
                f"[{self.device_id}] push: could not open TCP listener on "
                f"port {port}: {e} (is another program using it?)"
            )
            return
        self._push_subscription = sub
        # Reserved substitution token: commands, on_connect entries, and poll
        # queries can reference {listener_port} to tell the device where to
        # dial back (resolves an ephemeral port-0 bind to the real port).
        self.config["listener_port"] = sub.port

        register = push_def.get("register")
        if register:
            try:
                await self.send_command(str(register))
            except Exception as e:
                log.warning(
                    f"[{self.device_id}] push: registration command "
                    f"{register!r} failed: {e} — the device will not push "
                    f"until it reconnects (polling still covers it)"
                )

    def _start_push_sse(self, push_def: dict[str, Any]) -> None:
        """Open the driver's declared SSE event stream(s).

        SSE rides the driver's own HTTP session (auth + TLS settings apply),
        so it needs the HTTP transport — no listener, no source demux. The
        stream owns reconnect/backoff; a stream that can't connect is a
        logged gap covered by polling, never a device fault.
        """
        transport = self.transport
        if transport is None or not hasattr(transport, "open_event_stream"):
            log.warning(
                f"[{self.device_id}] push: type 'sse' requires the HTTP "
                f"transport"
            )
            return
        raw_path = push_def.get("path")
        paths = [raw_path] if isinstance(raw_path, str) else list(raw_path or [])
        try:
            idle_timeout = float(push_def.get("idle_timeout") or 0)
        except (TypeError, ValueError):
            idle_timeout = 0.0
        streams = []
        for raw in paths:
            path = str(self._resolve_push_value(raw) or "").strip()
            if not path.startswith("/"):
                log.warning(
                    f"[{self.device_id}] push: event-stream path {raw!r} "
                    f"did not resolve to a URL path (check the device's "
                    f"config fields)"
                )
                continue
            streams.append(
                transport.open_event_stream(
                    path,
                    self._handle_push_event,
                    idle_timeout=idle_timeout,
                    name=self.device_id,
                )
            )
        if streams:
            self._push_subscription = streams

    async def _start_push_http_listener(self, push_def: dict[str, Any]) -> None:
        """Register an inbound HTTP callback for this device.

        The platform's web listener receives the device's POSTs (webhook
        registrations, GENA NOTIFY) at a per-device path; the body feeds the
        normal response path. The callback URL the device must be told about
        is exposed as :attr:`push_callback_url` — send it in an ``on_connect``
        registration command (``{push_callback_url}`` substitutes in YAML
        command bodies).
        """
        from server.transport.http_listener import callback_url, subscribe

        host = str(self.config.get("host", "") or "")
        try:
            self._push_subscription = await subscribe(
                device_id=self.device_id,
                source_ip=host,
                callback=self._handle_push_http,
                name=self.device_id,
            )
        except Exception:
            log.warning(
                f"[{self.device_id}] push: could not register the inbound "
                f"HTTP callback",
                exc_info=True,
            )
            return
        self._push_callback_url = callback_url(
            host, self._push_subscription.path
        )
        log.info(
            f"[{self.device_id}] push: inbound HTTP callback at "
            f"{self._push_callback_url}"
        )

    @property
    def push_callback_url(self) -> str:
        """The URL a device must deliver push notifications to (http_listener
        shape), or ``""`` outside an active subscription. Python drivers that
        manage their own device-side registration (e.g. GENA SUBSCRIBE) read
        it after ``super().connect()``."""
        return self._push_callback_url

    async def _stop_push(self) -> None:
        """Drop the push subscription(s) (no-op when none is active)."""
        sub = self._push_subscription
        self._push_subscription = None
        self._push_callback_url = ""
        if sub is None:
            return
        await self._push_unregister()
        for handle in sub if isinstance(sub, list) else [sub]:
            try:
                await handle.close()
            except Exception:
                log.debug(
                    f"[{self.device_id}] Error closing push subscription",
                    exc_info=True,
                )

    async def _push_unregister(self) -> None:
        """Best-effort de-registration on a graceful disconnect.

        Devices in the dial-back shape hold a limited subscriber list (the
        Panasonic cameras allow 5) and keep a slot busy as long as deliveries
        succeed — so a device that is being removed while its shared listener
        port stays open for others would hold its slot indefinitely. Only
        attempted while the session is still up: on transport loss (and on
        the stale-subscription drop at reconnect) there is nobody to talk to.
        """
        push_def = self.DRIVER_INFO.get("push")
        if not isinstance(push_def, dict):
            return
        unregister = push_def.get("unregister")
        if (
            not unregister
            or not self._connected
            or self.transport is None
            or not self.transport.connected
        ):
            return
        try:
            await asyncio.wait_for(
                self.send_command(str(unregister)), timeout=5.0
            )
        except Exception as e:
            log.debug(
                f"[{self.device_id}] push: de-registration command "
                f"{unregister!r} failed: {e}"
            )

    async def _handle_push_datagram(
        self, data: bytes, source: tuple[str, int]
    ) -> None:
        """Feed a push datagram through the normal response path.

        One datagram may carry several protocol frames; when the driver
        declares a delimiter, split on it and dispatch each frame separately
        (first-match-wins response matching would otherwise apply only one
        rule to the whole datagram).
        """
        delimiter = self._resolve_delimiter()
        if delimiter:
            for part in data.split(delimiter):
                if part.strip():
                    await self.on_data_received(part)
        elif data:
            await self.on_data_received(data)

    async def _handle_push_event(self, data: bytes) -> None:
        """Feed one SSE event's data block through the normal response path.

        Unlike a datagram, an SSE event is already one complete framed
        message (the transport assembled its data lines), so it dispatches
        whole — exactly like an HTTP poll response body. No delimiter split:
        SSE payloads are JSON bodies, and splitting one on a line delimiter
        would break multi-field parsing.
        """
        if data.strip():
            await self.on_data_received(data)

    async def _handle_push_http(self, request: Any) -> None:
        """Feed one inbound HTTP push body through the normal response path.

        Like an SSE event, a push request's body is one complete framed
        message (an XML/JSON document per delivery), so it dispatches whole —
        exactly like an HTTP poll response body. Python drivers that need
        the request's headers (e.g. GENA's SID/SEQ) override this.
        """
        body = request.body
        if body and body.strip():
            await self.on_data_received(body)

    def _force_disconnect(self, code: str = NO_RESPONSE, message: str = "") -> None:
        """Tear down a dead transport and fire the disconnect path so the
        DeviceManager auto-reconnects / classifies the device offline.

        Callable from inside the health loop, so the task ref is dropped
        first — the disconnect cleanup would otherwise cancel the still-running
        loop out from under us. The typed fault is stashed because this
        disconnect carries no exception: a silently-dead device leaves no
        transport error, so without the stash the device card would show the
        generic "connection dropped".
        """
        self._health_task = None
        self._stash_fault(code, message)
        self._handle_transport_disconnect()

    @staticmethod
    def _coerce_serial_params(
        config: dict[str, Any],
    ) -> tuple[int, int, str, int | float, bool, bool]:
        """Coerce + validate serial params from untyped project config.

        Project config is untyped JSON, so an integrator / AI tool / hand-edit
        can store ``bytesize: "8"`` or ``stopbits: "1.5"`` as strings, or a
        flat-out invalid value. pyserial does exact membership tests and raises
        a bare ValueError at connect that the device manager then buries under
        ~120 generic reconnect attempts. Coerce string forms to the right type
        and raise a clear, actionable error for genuinely invalid values.

        Returns ``(baudrate, bytesize, parity, stopbits, rtscts, xonxoff)``.
        """
        try:
            baudrate = int(config.get("baudrate", 9600))
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid baudrate {config.get('baudrate')!r} (must be an integer)"
            )
        try:
            bytesize = int(config.get("bytesize", 8))
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid bytesize {config.get('bytesize')!r} (must be 5, 6, 7, or 8)"
            )
        if bytesize not in (5, 6, 7, 8):
            raise ValueError(f"Invalid bytesize {bytesize} (must be 5, 6, 7, or 8)")

        parity = str(config.get("parity", "N")).upper()
        if parity not in ("N", "E", "O", "M", "S"):
            raise ValueError(
                f"Invalid parity {config.get('parity')!r} (must be one of N, E, O, M, S)"
            )

        raw_stopbits = config.get("stopbits", 1)
        try:
            stopbits_f = float(raw_stopbits)
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid stopbits {raw_stopbits!r} (must be 1, 1.5, or 2)"
            )
        # pyserial wants ints for 1/2 and the float 1.5 for one-and-a-half.
        stopbits: int | float
        if stopbits_f == 1.0:
            stopbits = 1
        elif stopbits_f == 2.0:
            stopbits = 2
        elif stopbits_f == 1.5:
            stopbits = 1.5
        else:
            raise ValueError(
                f"Invalid stopbits {raw_stopbits!r} (must be 1, 1.5, or 2)"
            )
        # Flow control: the UI offers none / hardware (RTS/CTS); accept the
        # software (XON/XOFF) spelling too for forward-compat. An unrecognised
        # value falls back to no flow control rather than raising — a stray
        # value shouldn't make the device unconnectable.
        flow = str(config.get("flow_control", "none")).strip().lower()
        rtscts = flow in ("hardware", "rtscts", "rts/cts")
        xonxoff = flow in ("software", "xonxoff", "xon/xoff")
        return baudrate, bytesize, parity, stopbits, rtscts, xonxoff

    def _required_port(self) -> int:
        """Return ``config['port']`` for TCP/UDP/OSC, or raise a clear error.

        Driver ``default_config.port`` is layered in by
        ``Engine.resolved_device_config`` before instantiation, so a
        properly-declared driver always has a port here. A missing port
        means the driver definition skipped declaring one — surface that
        as a config error instead of silently dialing port 23.
        """
        port = self.config.get("port")
        if port is None or port == "":
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': missing 'port' in config "
                f"(driver '{driver_id}' must declare default_config.port "
                f"or the device must override it)"
            )
        try:
            return int(port)
        except (TypeError, ValueError) as e:
            driver_id = self.DRIVER_INFO.get("id", "?")
            raise ConnectionError(
                f"Device '{self.device_id}': invalid port {port!r} "
                f"(driver '{driver_id}')"
            ) from e

    def _http_max_response_bytes(self) -> int:
        """Resolve the HTTP response-body cap from config.

        The 32 MB default clears any realistic device payload; a driver whose
        device legitimately returns a larger body (a firmware or log export)
        raises it via the ``max_response_bytes`` config key. A missing,
        non-numeric, or non-positive value falls back to the default rather
        than handing the transport a nonsense cap.
        """
        from server.transport.http_client import DEFAULT_MAX_RESPONSE_BYTES

        raw = self.config.get("max_response_bytes")
        if raw is None:
            return DEFAULT_MAX_RESPONSE_BYTES
        try:
            value = int(raw)
        except (TypeError, ValueError):
            log.warning(
                f"[{self.device_id}] Ignoring invalid max_response_bytes "
                f"{raw!r}; using default {DEFAULT_MAX_RESPONSE_BYTES}"
            )
            return DEFAULT_MAX_RESPONSE_BYTES
        if value <= 0:
            log.warning(
                f"[{self.device_id}] Ignoring non-positive max_response_bytes "
                f"{raw!r}; using default {DEFAULT_MAX_RESPONSE_BYTES}"
            )
            return DEFAULT_MAX_RESPONSE_BYTES
        return value

    def _create_frame_parser(self) -> FrameParser | None:
        """
        Hook: return a custom FrameParser for this driver.

        Override for binary protocols that need length-prefix or callable
        parsing. Return None to use delimiter-based framing (the default).
        """
        return None

    def _resolve_delimiter(self) -> bytes | None:
        """
        Hook: resolve the message delimiter for this driver.

        Checks DRIVER_INFO["delimiter"] first, then self.config["delimiter"],
        then falls back to b"\\r". Override for custom logic.
        """
        from server.transport.binary_helpers import encode_escape_sequences

        # Check DRIVER_INFO
        delim = self.DRIVER_INFO.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Check config
        delim = self.config.get("delimiter")
        if delim is not None:
            if isinstance(delim, bytes):
                return delim
            return encode_escape_sequences(delim)

        # Default
        return b"\r"

    async def send_raw(self, data: str) -> None:
        """Send a raw command string to the device immediately.

        Encodes escape sequences (so a typed ``\\r`` becomes a CR) and appends
        the device's line terminator unless the string already ends with it —
        matching how saved commands are sent. Used by the device page's
        "Send raw" box for quick one-offs and diagnostics; works on any
        byte-stream transport (TCP / serial / UDP), not request/response HTTP.
        """
        from server.transport.binary_helpers import encode_escape_sequences

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
        False otherwise.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
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
        """
        self._connected = False
        self.set_state("connected", False)
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
            await self.events.emit(f"device.disconnected.{self.device_id}")
        except Exception:
            log.debug(
                f"[{self.device_id}] Failed to emit disconnect event",
                exc_info=True,
            )

    # --- Polling ---

    async def start_polling(self, interval: float) -> None:
        """Start a background polling loop at the given interval (seconds)."""
        if interval <= 0:
            return
        await self.stop_polling()
        self._poll_task = asyncio.create_task(self._poll_loop(interval))
        log.debug(f"[{self.device_id}] Polling started (every {interval}s)")

    async def stop_polling(self) -> None:
        """Cancel the polling background task."""
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
            log.debug(f"[{self.device_id}] Polling stopped")

    async def _poll_loop(self, interval: float) -> None:
        """Background loop that calls self.poll() periodically.

        Tracks whether each poll() returned cleanly. When N consecutive polls
        raise a transport-level error (ConnectionError, TimeoutError, OSError,
        or any httpx HTTP error), marks the device disconnected and exits.
        Protocol-level errors (e.g., ValueError on unexpected response shape)
        are logged via device.error.<id> but do not penalize the watchdog —
        the device is reachable, just misbehaving.

        Drivers MUST propagate transport errors from poll(). Swallowing
        httpx.ConnectError and friends in a driver's poll() causes connected
        state to lie.
        """
        import time
        try:
            import httpx
            httpx_errors: tuple = (httpx.HTTPError,)
        except ImportError:
            httpx_errors = ()

        # Clamp to >= 1: a config value of 0 (or negative, or non-numeric) would
        # make `dry_polls >= max_dry_polls` true after the first poll and mark a
        # healthy device disconnected. The watchdog can't be disabled by setting
        # 0 — the minimum is one missed poll.
        try:
            max_dry_polls = max(int(self.config.get("max_missed_polls", 3)), 1)
        except (TypeError, ValueError):
            max_dry_polls = 3
        dry_polls = 0
        # Seed at loop start so we don't false-positive before the first poll.
        self._last_poll_success = time.monotonic()

        last_poll_exc: BaseException | None = None
        try:
            while True:
                try:
                    await self.poll()
                    self._last_poll_success = time.monotonic()
                    dry_polls = 0
                    last_poll_exc = None
                except (ConnectionError, TimeoutError, OSError) as exc:
                    log.warning(
                        f"[{self.device_id}] Poll failed (connection): {exc}"
                    )
                    dry_polls += 1
                    last_poll_exc = exc
                except httpx_errors as exc:
                    log.warning(
                        f"[{self.device_id}] Poll failed (HTTP): {exc}"
                    )
                    dry_polls += 1
                    last_poll_exc = exc
                except Exception as exc:
                    log.exception(
                        f"[{self.device_id}] Unexpected error during poll"
                    )
                    try:
                        await self.events.emit(
                            f"device.error.{self.device_id}",
                            {"device_id": self.device_id, "error": str(exc)},
                        )
                    except Exception:
                        log.exception(
                            f"[{self.device_id}] Failed to emit device.error"
                        )

                if dry_polls >= max_dry_polls:
                    log.warning(
                        f"[{self.device_id}] No response for "
                        f"{dry_polls} poll cycles — marking disconnected"
                    )
                    # Record WHY for the fault classifier: the disconnect
                    # event carries no exception, and a silently-dead peer
                    # leaves no transport error — without this the card
                    # shows the generic "connection dropped". A typed fault
                    # raised by poll() itself wins; otherwise this is the
                    # canonical stopped-answering case.
                    # Classify the specific cause from the last poll error when
                    # it's more informative than the generic stopped-answering
                    # wording: a plain ConnectionError that really means
                    # "connection refused" should surface as connection_refused,
                    # not no_response. The classifier's generic codes
                    # (no_response, transport_disconnected) fall through to the
                    # poll-cycle message below — it says the device WAS answering
                    # and then stopped, which reads better than either.
                    fault: ConnectionFault | None = None
                    if last_poll_exc is not None:
                        classified = classify_connection_fault(
                            last_error=None,
                            exc=last_poll_exc,
                            host=self.config.get("host", ""),
                            port=self.config.get("port"),
                            transport=self.config.get("transport", ""),
                        )
                        if classified.code not in (
                            NO_RESPONSE,
                            TRANSPORT_DISCONNECTED,
                        ):
                            fault = classified
                    if fault is None:
                        fault = ConnectionFault(
                            NO_RESPONSE,
                            f"Connected, but the device stopped answering "
                            f"({dry_polls} poll cycles without a response).",
                        )
                    self._last_fault = fault
                    self._handle_transport_disconnect()
                    return

                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    # --- Convenience helpers ---

    def set_state(self, property_name: str, value: Any) -> None:
        """Set a state value under this device's namespace."""
        var_def = self.DRIVER_INFO.get("state_variables", {}).get(property_name)
        self._warn_on_type_mismatch(property_name, value, var_def)
        self.state.set(
            f"device.{self.device_id}.{property_name}",
            value,
            source=f"device.{self.device_id}",
        )

    def _warn_on_type_mismatch(
        self, prop_label: str, value: Any, var_def: dict | None
    ) -> None:
        """Log a debug message when a state value doesn't match its declared
        type. Helps catch driver bugs without being load-bearing — listeners
        still receive the value as written.
        """
        if not var_def or value is None:
            return
        declared = var_def.get("type", "string")
        if declared == "integer" and not isinstance(value, int):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"integer but got {type(value).__name__}: {value!r}"
            )
        elif declared in ("number", "float") and not isinstance(value, (int, float)):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"{declared} but got {type(value).__name__}: {value!r}"
            )
        elif declared == "boolean" and not isinstance(value, bool):
            log.debug(
                f"[{self.device_id}] State '{prop_label}' declared as "
                f"boolean but got {type(value).__name__}: {value!r}"
            )
        elif declared == "enum" and "values" in var_def:
            if str(value) not in [str(v) for v in var_def["values"]]:
                log.debug(
                    f"[{self.device_id}] State '{prop_label}' value "
                    f"{value!r} not in declared enum values {var_def['values']}"
                )

    def set_states(self, updates: dict[str, Any]) -> None:
        """Set multiple state values atomically (listeners see all changes at once)."""
        namespaced = {
            f"device.{self.device_id}.{k}": v for k, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")

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

    # --- Child entities ---
    #
    # A "child entity" is a sub-unit owned by this device: an encoder/decoder
    # on a video matrix controller, a zone on a DSP, a video wall slot on
    # a presentation switcher, etc. Drivers declare child types in
    # DRIVER_INFO["child_entity_types"] and register live instances via
    # register_child(). State for each child lives under the convention
    #
    #     device.<parent_id>.<child_type>.<local_id_padded>.<property>
    #
    # so existing fnmatch subscribers and the IDE can address children
    # without driver authors inventing their own key shapes. The platform
    # always injects a boolean `online` key per registered child so that
    # listeners can distinguish "configured but offline" from "not configured".

    # State keys the platform always provides for every registered child,
    # in addition to whatever the driver declares in
    # child_entity_types[<type>].state_variables. `online` is driver-managed
    # (the driver sets it as it observes connectivity). `label` is the
    # user-set friendly name, sourced from the project file on registration
    # and writable through the IDE / REST.
    _CHILD_RESERVED_PROPS: tuple[str, ...] = ("online", "label")

    def _child_type_def(self, child_type: str) -> dict[str, Any]:
        """Return the DRIVER_INFO child_entity_types[<type>] definition.

        Raises ValueError if the driver didn't declare this child type.
        """
        types = self.DRIVER_INFO.get("child_entity_types", {})
        if child_type not in types:
            raise ValueError(
                f"Driver {self.DRIVER_INFO.get('id', '?')} did not declare "
                f"child_entity_types[{child_type!r}]"
            )
        return types[child_type]

    def _effective_child_schema(
        self, child_type: str, local_id: int | str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Schema for one child instance's state variables, with the
        platform-managed `online` and `label` keys injected if the driver
        didn't already declare them.

        For a child type declared ``dynamic: true``, a per-child schema
        supplied at ``register_child(schema=...)`` takes precedence when
        ``local_id`` is given; otherwise the type-level ``state_variables``
        (which may be empty for a dynamic type) is used.
        """
        type_def = self._child_type_def(child_type)
        declared: dict[str, dict[str, Any]] | None = None
        if type_def.get("dynamic") and local_id is not None:
            declared = self._child_schemas.get((child_type, local_id))
        if declared is None:
            declared = dict(type_def.get("state_variables", {}))
        else:
            declared = dict(declared)
        declared.setdefault("online", {"type": "boolean"})
        declared.setdefault("label", {"type": "string"})
        return declared

    def _format_child_id(self, child_type: str, local_id: int | str) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its string form.

        Two id kinds are supported:
          * ``integer`` (default) — zero-padded to ``id_format.pad_width``;
            must lie inside [id_format.min (default 1), id_format.max
            (optional, unbounded)].
          * ``string`` — used verbatim; must be non-empty, match
            ``[A-Za-z0-9_-]`` only (so it's safe in a flat state key and in
            glob subscriptions), and be at most ``id_format.max_length``
            (default 128) characters. For devices that key children by a
            native name (Q-SYS Code Name, MQTT topic), sanitize to this set
            and keep the original in the child's ``label``.
        """
        type_def = self._child_type_def(child_type)
        id_format = type_def.get("id_format", {})
        id_kind = id_format.get("type", "integer")

        if id_kind == "string":
            if not isinstance(local_id, str):
                raise TypeError(
                    f"Child {child_type} local_id must be str (id_format.type "
                    f"is 'string'), got {type(local_id).__name__}: {local_id!r}"
                )
            if not _CHILD_STRING_ID_RE.match(local_id):
                raise ValueError(
                    f"Child {child_type} local_id {local_id!r} is not a valid "
                    f"string id (allowed characters: letters, digits, '_', '-')"
                )
            max_len = id_format.get("max_length", _CHILD_STRING_ID_MAX_LEN)
            if max_len and len(local_id) > max_len:
                raise ValueError(
                    f"Child {child_type} local_id {local_id!r} exceeds "
                    f"id_format.max_length {max_len}"
                )
            return local_id

        if id_kind != "integer":
            raise ValueError(
                f"Child type {child_type!r} id_format.type {id_kind!r} not "
                f"supported (only 'integer' and 'string' are supported)"
            )
        # bool is a subclass of int in Python; reject it so that
        # register_child("encoder", True) doesn't silently land at ID 1.
        if not isinstance(local_id, int) or isinstance(local_id, bool):
            raise TypeError(
                f"Child {child_type} local_id must be int, got "
                f"{type(local_id).__name__}: {local_id!r}"
            )
        min_id = id_format.get("min", 1)
        max_id = id_format.get("max")
        if local_id < min_id:
            raise ValueError(
                f"Child {child_type} local_id {local_id} < min {min_id}"
            )
        if max_id is not None and local_id > max_id:
            raise ValueError(
                f"Child {child_type} local_id {local_id} > max {max_id}"
            )
        pad = id_format.get("pad_width", 0)
        return f"{local_id:0{pad}d}" if pad else str(local_id)

    def _child_state_key(
        self, child_type: str, local_id: int | str, prop: str,
    ) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}.{prop}"

    def _child_state_prefix(self, child_type: str, local_id: int | str) -> str:
        padded = self._format_child_id(child_type, local_id)
        return f"device.{self.device_id}.{child_type}.{padded}"

    def _validate_child_prop(
        self, child_type: str, local_id: int | str, prop: str,
    ) -> None:
        schema = self._effective_child_schema(child_type, local_id)
        if prop not in schema:
            raise ValueError(
                f"Child {child_type} property {prop!r} not declared in "
                f"child_entity_types[{child_type!r}].state_variables"
                + (" (or this child's dynamic schema)"
                   if self._child_type_def(child_type).get("dynamic") else "")
            )

    @staticmethod
    def _default_for_var_def(var_def: dict[str, Any]) -> Any:
        """Default value for a declared state variable, matching the rules
        used by _init_state_variables() so per-child defaults are consistent
        with per-device ones.
        """
        var_type = var_def.get("type", "string")
        if var_type == "boolean":
            return False
        if var_type == "integer":
            return BaseDriver._numeric_default(var_def, as_int=True)
        if var_type in ("number", "float"):
            return BaseDriver._numeric_default(var_def, as_int=False)
        if var_type == "enum":
            values = var_def.get("values", [])
            return values[0] if values else ""
        return ""

    def register_child(
        self,
        child_type: str,
        local_id: int | str,
        initial_state: dict[str, Any] | None = None,
        schema: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Tell the platform a child entity exists. Creates its state keys
        in one atomic batch.

        Subsequent calls with the same (child_type, local_id) are a silent
        no-op so drivers can call this opportunistically from a poll loop
        without re-initializing state. To overwrite a child's state, use
        ``set_child_state`` / ``set_child_state_batch`` instead; to change a
        dynamic child's schema, ``deregister_child`` then register again.

        ``initial_state`` overrides per-prop defaults. The platform-managed
        ``online`` key defaults to True if not specified in ``initial_state``.
        Unknown props in ``initial_state`` raise ValueError.

        ``schema`` supplies a per-child state-variable map for child types
        declared ``dynamic: true`` — used when the child's controls are
        discovered at runtime (a Q-SYS component's controls, an MQTT topic's
        fields) rather than declared statically. Each value is a var-def dict
        (``{"type": "number"|"boolean"|"integer"|"string"|"enum", "label":
        ..., ...}``), the same shape as a static ``state_variables`` entry.
        Passing ``schema`` for a non-dynamic type raises ValueError.
        """
        self._format_child_id(child_type, local_id)   # validates id range

        type_def = self._child_type_def(child_type)
        if schema is not None:
            if not type_def.get("dynamic"):
                raise ValueError(
                    f"Child type {child_type!r} is not declared "
                    f"`dynamic: true`; a per-child schema is only allowed for "
                    f"dynamic child types"
                )
            if not isinstance(schema, dict):
                raise TypeError(
                    f"Child {child_type} schema must be a dict, got "
                    f"{type(schema).__name__}"
                )
            for prop, var_def in schema.items():
                if not isinstance(var_def, dict):
                    raise TypeError(
                        f"Child {child_type} schema property {prop!r} must map "
                        f"to a var-def dict, got {type(var_def).__name__}"
                    )

        bucket = self._children.setdefault(child_type, {})
        if local_id in bucket:
            # Idempotent — already registered. But a DIFFERENT per-child
            # schema arriving under the same id is the signature of a
            # sanitized-id collision (two device-native names mapping to one
            # local id) or a schema change without deregister_child() — both
            # otherwise invisible (the second child just never appears).
            if schema is not None and dict(schema) != self._child_schemas.get(
                (child_type, local_id)
            ):
                log.warning(
                    f"[{self.device_id}] register_child({child_type!r}, "
                    f"{local_id!r}) ignored: id already registered with a "
                    f"different schema — likely an id collision after "
                    f"sanitization, or a schema change without "
                    f"deregister_child() first"
                )
            return
        # Stamp a fresh registration epoch. A deregister+re-register (which
        # resets the child's state) bumps it, so poll_children can tell a
        # re-registered child from the one it snapshotted (ABA guard).
        self._child_register_seq += 1
        bucket[local_id] = self._child_register_seq

        # Store the per-child dynamic schema before computing the effective
        # schema so validation + defaults below use the discovered controls.
        if schema is not None:
            self._child_schemas[(child_type, local_id)] = dict(schema)

        eff_schema = self._effective_child_schema(child_type, local_id)
        overrides = dict(initial_state or {})

        # Reject unknown props up-front so the driver sees the error before
        # we touch the state store.
        for prop in overrides:
            if prop not in eff_schema:
                # Roll back the registration record (and any stored schema) so
                # a retry can succeed after the driver fixes the call.
                del bucket[local_id]
                if not bucket:
                    del self._children[child_type]
                self._child_schemas.pop((child_type, local_id), None)
                raise ValueError(
                    f"Child {child_type} initial_state property {prop!r} "
                    f"not declared in child_entity_types[{child_type!r}]"
                    f".state_variables"
                    + (" (or this child's dynamic schema)"
                       if type_def.get("dynamic") else "")
                )

        # Project-side label: if the project file has a ChildEntityConfig
        # entry for this (type, padded_id), use its `label` as the default
        # so listeners see the user's name immediately, not "" then a
        # delayed update once the IDE re-pushes it.
        padded = self._format_child_id(child_type, local_id)
        project_entry = self._project_child_entities.get(child_type, {}).get(padded)
        project_label = project_entry.get("label", "") if project_entry else ""

        updates: dict[str, Any] = {}
        for prop, var_def in eff_schema.items():
            if prop == "online":
                value = overrides.get("online", True)
            elif prop == "label":
                value = overrides.get("label", project_label)
            elif prop in overrides:
                value = overrides[prop]
            else:
                value = self._default_for_var_def(var_def)
            updates[self._child_state_key(child_type, local_id, prop)] = value

        self.state.set_batch(updates, source=f"device.{self.device_id}")

    def deregister_child(self, child_type: str, local_id: int | str) -> None:
        """Remove a child entity and delete all of its state keys.

        Silent no-op if the child isn't registered (so drivers can call this
        eagerly during reconciliation without first checking).
        """
        bucket = self._children.get(child_type)
        if bucket is None or local_id not in bucket:
            return

        prefix_dot = self._child_state_prefix(child_type, local_id) + "."
        # Snapshot before mutating; iterating the live store while deleting
        # would be unsafe. The snapshot is cheap (dict copy) at the scale
        # we expect per child (~10-30 keys).
        keys_to_delete = [
            k for k in self.state.snapshot().keys() if k.startswith(prefix_dot)
        ]
        for k in keys_to_delete:
            self.state.delete(k, source=f"device.{self.device_id}")

        del bucket[local_id]
        if not bucket:
            del self._children[child_type]
        # Drop any per-child dynamic schema so a later re-register can supply
        # a fresh one (e.g. after the device's topology changed).
        self._child_schemas.pop((child_type, local_id), None)

    def list_children(self, child_type: str) -> list[int | str]:
        """Local IDs of currently-registered children of ``child_type``,
        in insertion order. Returns an empty list if none are registered or
        the type is unknown to the platform tracker.
        """
        bucket = self._children.get(child_type)
        if bucket is None:
            return []
        return list(bucket.keys())

    def set_child_state(
        self, child_type: str, local_id: int | str, prop: str, value: Any
    ) -> None:
        """Set one state key on a child entity, validated against its
        declared (or, for dynamic types, per-child) schema.

        Raises ValueError if ``prop`` is not in the child's effective schema
        (the synthetic ``online`` / ``label`` keys are always allowed).
        Writes to an unregistered child are skipped with a warning — they
        used to create orphan state keys visible in Live State and binding
        pickers but absent from the children listing.
        """
        if not self.is_child_registered(child_type, local_id):
            log.warning(
                f"[{self.device_id}] set_child_state for unregistered child "
                f"{child_type}/{local_id} (prop {prop!r}) skipped — call "
                f"register_child first"
            )
            return
        self._validate_child_prop(child_type, local_id, prop)
        schema = self._effective_child_schema(child_type, local_id)
        self._warn_on_type_mismatch(
            f"{child_type}.{self._format_child_id(child_type, local_id)}.{prop}",
            value,
            schema[prop],
        )
        self.state.set(
            self._child_state_key(child_type, local_id, prop),
            value,
            source=f"device.{self.device_id}",
        )

    def set_child_state_batch(
        self, child_type: str, local_id: int | str, updates: dict[str, Any]
    ) -> None:
        """Atomically set several state keys on one child entity.

        Validates every prop in ``updates`` before any write, so a single
        bad prop causes the entire batch to abort. Unregistered children are
        skipped with a warning (see set_child_state).
        """
        if not self.is_child_registered(child_type, local_id):
            log.warning(
                f"[{self.device_id}] set_child_state_batch for unregistered "
                f"child {child_type}/{local_id} skipped — call register_child "
                f"first"
            )
            return
        for prop in updates:
            self._validate_child_prop(child_type, local_id, prop)
        namespaced = {
            self._child_state_key(child_type, local_id, prop): v
            for prop, v in updates.items()
        }
        self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def set_children_state_batch(
        self, updates: list[tuple[str, int | str, dict[str, Any]]]
    ) -> None:
        """Atomically set state keys across many children in one transaction.

        Each entry is ``(child_type, local_id, {prop: value, ...})``. Listeners
        and the cloud relay see the complete delta, not a half-applied state.
        Use this for poll responses that touch dozens or hundreds of children
        at once. Entries for unregistered children are skipped with a warning
        (see set_child_state); the rest of the batch still applies.
        """
        live: list[tuple[str, int | str, dict[str, Any]]] = []
        for child_type, local_id, child_updates in updates:
            if not self.is_child_registered(child_type, local_id):
                log.warning(
                    f"[{self.device_id}] set_children_state_batch entry for "
                    f"unregistered child {child_type}/{local_id} skipped — "
                    f"call register_child first"
                )
                continue
            live.append((child_type, local_id, child_updates))
        for child_type, local_id, child_updates in live:
            for prop in child_updates:
                self._validate_child_prop(child_type, local_id, prop)
        namespaced: dict[str, Any] = {}
        for child_type, local_id, child_updates in live:
            for prop, value in child_updates.items():
                namespaced[self._child_state_key(child_type, local_id, prop)] = value
        if namespaced:
            self.state.set_batch(namespaced, source=f"device.{self.device_id}")

    def get_child_entity_types(self) -> dict[str, dict[str, Any]]:
        """Return the driver's declared child entity types as
        ``{type_name: definition}``, with each definition's
        ``state_variables`` replaced by the *effective* schema (the
        platform-managed ``online`` and ``label`` keys injected if the
        driver didn't already declare them).

        Returns ``{}`` if the driver doesn't declare ``child_entity_types``.
        Used by the REST API and IDE to expose the per-type schema to
        clients without leaking driver-private helpers.

        For a ``dynamic: true`` type, ``state_variables`` here is the
        type-level schema (often just the platform-managed ``online`` /
        ``label``); each child's discovered controls are exposed per-instance
        via :meth:`get_child_schema` and the children listing, since they vary
        from one child to the next.
        """
        raw = self.DRIVER_INFO.get("child_entity_types", {})
        result: dict[str, dict[str, Any]] = {}
        for ctype, definition in raw.items():
            merged_def = dict(definition)
            merged_def["state_variables"] = self._effective_child_schema(ctype)
            result[ctype] = merged_def
        return result

    def get_child_schema(
        self, child_type: str, local_id: int | str,
    ) -> dict[str, dict[str, Any]]:
        """Effective per-child state-variable schema (with ``online`` /
        ``label`` injected). For a ``dynamic: true`` type this reflects the
        schema supplied at ``register_child(schema=...)``; for a static type
        it's the declared type schema. Used by the REST API to render
        heterogeneous (dynamic) children, where each instance has its own
        control set.
        """
        return self._effective_child_schema(child_type, local_id)

    def is_child_type_dynamic(self, child_type: str) -> bool:
        """True if ``child_type`` is declared ``dynamic: true`` (its children
        carry per-instance schemas). False for unknown or static types.
        """
        return bool(
            self.DRIVER_INFO.get("child_entity_types", {})
            .get(child_type, {})
            .get("dynamic")
        )

    def format_child_id(self, child_type: str, local_id: int | str) -> str:
        """Validate ``local_id`` against the declared id_format and return
        its padded string form. Public wrapper around the internal helper.
        """
        return self._format_child_id(child_type, local_id)

    def is_child_registered(self, child_type: str, local_id: int | str) -> bool:
        """True if ``register_child(child_type, local_id)`` has been called
        and ``deregister_child`` hasn't been called since.
        """
        return local_id in self._children.get(child_type, {})

    def get_child_state(
        self, child_type: str, local_id: int | str,
    ) -> dict[str, Any]:
        """Return the live state for one registered child as
        ``{property: value}``. Returns an empty dict if the child isn't
        currently registered.
        """
        if not self.is_child_registered(child_type, local_id):
            return {}
        return self.state.get_namespace(
            self._child_state_prefix(child_type, local_id)
        )

    async def refresh_children(self) -> Any:
        """Re-discover child entities from the device.

        Default implementation raises ``NotImplementedError``. Drivers that
        can re-poll their controller's child list override this to reconcile
        ``register_child`` / ``deregister_child`` calls against the latest
        device state. Called from the REST API
        ``POST /api/devices/{id}/children/refresh`` endpoint.
        """
        raise NotImplementedError(
            f"Driver {self.DRIVER_INFO.get('id', '?')} does not implement "
            f"refresh_children"
        )

    async def poll_children(
        self,
        child_type: str,
        fetch: Callable[[list[int]], Awaitable[dict[int, dict[str, Any]]]],
        batch_size: int = 50,
        inter_batch_delay: float = 0.1,
    ) -> None:
        """Paginated polling helper.

        Splits the currently-registered ``child_type`` IDs into batches of
        ``batch_size``, awaits ``fetch(batch_ids)`` for each (pausing
        ``inter_batch_delay`` seconds between batches so polls don't saturate
        slow controllers), then applies the whole poll's results in ONE atomic
        ``set_children_state_batch``. Applying per-poll rather than per-batch
        means a subscriber never observes a half-applied multi-batch snapshot
        (some children on this poll's values, the rest still on the last
        poll's). The coalescing delay is bounded by the fetch time and is
        negligible against typical poll intervals.

        ``fetch`` returns ``{local_id: {prop: value, ...}}``. A result for a
        child that isn't registered, or that was deregistered (or
        deregistered+re-registered, which resets its state) since the poll
        started, is dropped — so a concurrent ``refresh_children`` can't have
        its reset clobbered by a stale write (ABA guard).
        """
        ids = self.list_children(child_type)
        if not ids:
            return
        # Snapshot each child's registration epoch at poll start. A child whose
        # live epoch later differs (re-registered) or is gone (deregistered) is
        # dropped at apply time.
        start_bucket = self._children.get(child_type, {})
        epochs = {lid: start_bucket.get(lid) for lid in ids}

        collected: list[tuple[int, dict[str, Any]]] = []
        last_start = (len(ids) - 1) // max(batch_size, 1) * max(batch_size, 1)
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i + batch_size]
            results = await fetch(batch_ids)
            collected.extend(results.items())
            if i != last_start and inter_batch_delay > 0:
                await asyncio.sleep(inter_batch_delay)

        live = self._children.get(child_type, {})
        updates: list[tuple[str, int, dict[str, Any]]] = []
        for lid, props in collected:
            expected = epochs.get(lid)
            # Keep only children that were in the start snapshot AND whose
            # registration epoch hasn't changed since (drops ghosts, removed,
            # and re-registered children).
            if expected is not None and live.get(lid) == expected:
                updates.append((child_type, lid, props))
        if updates:
            self.set_children_state_batch(updates)
