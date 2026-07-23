"""
OpenAVC DeviceManager — manages all device driver instances.

Handles:
- Instantiating drivers from project config
- Connection lifecycle (connect, reconnect on failure, disconnect)
- Routing commands to the correct device
- Exposing device metadata
"""

from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from server.core.connection_fault import (
    classify_connection_fault,
    is_permanent_fault,
    typed_fault_from_exc,
)
from server.drivers.base import (
    CommandParamError,
    DeviceSettingValueError,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.utils.logger import get_logger

log = get_logger(__name__)

# How many reconnect attempts a permanent fault gets before the loop stops.
# One retry past the first classification: enough to shrug off a device that
# was mid-reboot when we read its certificate or host key, few enough that a
# genuinely misconfigured device stops churning almost immediately. auth_failed
# is stricter still (zero retries — see _pause_reconnect_for_auth).
_MAX_PERMANENT_FAULT_ATTEMPTS = 2


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in background task: %s", exc, exc_info=exc)


if TYPE_CHECKING:
    from server.drivers.base import BaseDriver

# Driver registry — maps driver ID strings to driver classes
_DRIVER_REGISTRY: dict[str, type[BaseDriver]] = {}


def register_driver(driver_class: type[BaseDriver]) -> None:
    """Register a driver class in the global registry."""
    driver_id = driver_class.DRIVER_INFO.get("id", "")
    if driver_id:
        _DRIVER_REGISTRY[driver_id] = driver_class
        log.debug(f"Registered driver: {driver_id}")


def unregister_driver(driver_id: str) -> bool:
    """Remove a driver class from the global registry. Returns True if removed."""
    removed = _DRIVER_REGISTRY.pop(driver_id, None) is not None
    if removed:
        log.info(f"Unregistered driver: {driver_id}")
    return removed


def is_driver_registered(driver_id: str) -> bool:
    """Check if a driver ID is registered in the global registry."""
    return driver_id in _DRIVER_REGISTRY


def get_driver_default_config(driver_id: str) -> dict[str, Any]:
    """Return the registered driver's ``default_config``, or ``{}`` if unknown.

    Used by ``Engine.resolved_device_config`` to layer driver-declared
    defaults under saved device config. Unknown / orphaned drivers return
    an empty dict so a missing driver behaves the same as today (the
    device will fail to instantiate, but resolution stays well-defined).
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return {}
    defaults = cls.DRIVER_INFO.get("default_config", {}) or {}
    return dict(defaults)


def get_driver_transport(driver_id: str) -> str:
    """Return the registered driver's declared transport (``DRIVER_INFO
    ['transport']``, defaulting to ``tcp`` like the connect path), or ``""``
    if the driver is unknown. Used to resolve a device's effective transport
    when its saved config omits one, so a stray ``usb_serial`` on a network
    device can't hijack its port.
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return ""
    return cls.DRIVER_INFO.get("transport", "tcp")


def get_driver_bridge_ports(driver_id: str) -> dict[str, dict[str, Any]]:
    """Return a registered bridge driver's advertised ports as
    ``{port_id: {kind, passthrough_port?, label?}}``, or ``{}`` if the driver
    is unknown or not a bridge.

    A *bridge* driver declares ``DRIVER_INFO["bridge"]["ports"]``: a list of
    typed ports (``serial`` / ``ir`` / ``relay``) that other devices connect
    *through*. Serial ports carry a ``passthrough_port`` (the TCP port on the
    bridge host that transparently pipes that serial line, e.g. 4999); IR /
    relay ports route commands through the bridge's command socket instead and
    omit it. Used by ``Engine.resolved_device_config`` to rewrite a
    bridge-bound downstream device's transport, and by the device manager to
    order bridges ahead of their dependents.
    """
    cls = _DRIVER_REGISTRY.get(driver_id)
    if cls is None:
        return {}
    bridge = cls.DRIVER_INFO.get("bridge") or {}
    ports = bridge.get("ports") or []
    result: dict[str, dict[str, Any]] = {}
    for port in ports:
        pid = port.get("id")
        if pid:
            result[pid] = dict(port)
    return result


def get_driver_registry() -> list[dict[str, Any]]:
    """Return metadata for all registered drivers."""
    return [
        {
            "id": driver_class.DRIVER_INFO.get("id", ""),
            "name": driver_class.DRIVER_INFO.get("name", ""),
            "manufacturer": driver_class.DRIVER_INFO.get("manufacturer", ""),
            "category": driver_class.DRIVER_INFO.get("category", ""),
            "description": driver_class.DRIVER_INFO.get("description", ""),
            "version": driver_class.DRIVER_INFO.get("version", ""),
            "author": driver_class.DRIVER_INFO.get("author", ""),
            "transport": driver_class.DRIVER_INFO.get("transport", "tcp"),
            # Multi-transport drivers ([tcp, serial]) and bridge port
            # declarations — the connection picker offers "through a bridge"
            # for serial-capable drivers and lists bridge devices + their ports.
            "transports": driver_class.DRIVER_INFO.get("transports", []),
            "bridge": driver_class.DRIVER_INFO.get("bridge", {}),
            "commands": driver_class.DRIVER_INFO.get("commands", {}),
            "config_schema": driver_class.DRIVER_INFO.get("config_schema", {}),
            "default_config": driver_class.DRIVER_INFO.get("default_config", {}),
            "state_variables": driver_class.DRIVER_INFO.get("state_variables", {}),
            "help": driver_class.DRIVER_INFO.get("help", {}),
            "discovery": driver_class.DRIVER_INFO.get("discovery", {}),
            "device_settings": driver_class.DRIVER_INFO.get("device_settings", {}),
            # Action strip + child types, so pre-device UIs (driver browser
            # detail) can show a driver's full surface — device-level views
            # get the resolved form via get_device_info.
            "actions": driver_class.DRIVER_INFO.get("actions", []),
            "quick_actions": driver_class.DRIVER_INFO.get("quick_actions", []),
            "child_entity_types": driver_class.DRIVER_INFO.get("child_entity_types", {}),
        }
        for driver_class in _DRIVER_REGISTRY.values()
    ]


def _load_builtin_drivers() -> None:
    """Import and register all built-in and community drivers."""
    # Load .avcdriver YAML definitions and .py Python drivers from
    # both the built-in definitions directory and driver_repo/. The generic
    # devices (generic_tcp / generic_serial / generic_http) ship as
    # .avcdriver definitions in the built-in definitions directory.
    from server.drivers.driver_loader import load_all_drivers
    from server.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    driver_dirs = [
        DRIVER_DEFINITIONS_DIR,
        DRIVER_REPO_DIR,
    ]
    loaded = load_all_drivers(driver_dirs)
    if loaded:
        log.info(f"Loaded {loaded} driver(s) from definition/driver files")


# Load built-in drivers on module import
_load_builtin_drivers()


# Backstop for a test-panel pause whose owner never resumes it (tab closed or
# crashed, request lost). The panel refreshes the pause while it stays open,
# so expiry only fires for genuinely abandoned pauses — without it a paused
# production device stays offline indefinitely with auto-reconnect suppressed.
PAUSE_TTL = 600.0


# Device-config field names whose values are credentials and must never leave
# the device manager in cleartext. get_device_info's orphaned-device branch is
# the only path that returns raw connection config, and that payload flows to
# the cloud AI (cloud/tools/device_tools.py::_get_device_info) — a missing
# driver must not turn a simple get_device_info into a credential dump. Matched
# case-insensitively: exact names for ambiguous words (so a benign `user_label`
# isn't caught), substrings for the unambiguous secret markers. Mirrors the auth
# fields BaseDriver.connect reads (username/password/token/api_key) plus common
# variants.
_SECRET_KEY_EXACT = frozenset({"username", "user", "bearer"})
_SECRET_KEY_SUBSTRINGS = (
    "password", "passwd", "passphrase", "secret",
    "token", "api_key", "apikey", "credential", "private",
)


def _redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a device config with credential values masked.

    A present-but-masked value (``"***"``) still tells a caller the field is
    set without revealing it; empty/None values are left as-is so "not
    configured" stays visible. Nested dicts are redacted recursively.
    """
    redacted: dict[str, Any] = {}
    for key, value in config.items():
        if isinstance(value, dict):
            redacted[key] = _redact_config(value)
            continue
        key_l = str(key).lower()
        is_secret = key_l in _SECRET_KEY_EXACT or any(
            marker in key_l for marker in _SECRET_KEY_SUBSTRINGS
        )
        redacted[key] = "***" if (is_secret and value not in (None, "")) else value
    return redacted


class DeviceManager:
    """Manages all device driver instances."""

    def __init__(self, state: StateStore, events: EventBus):
        self.state = state
        self.events = events
        self._devices: dict[str, BaseDriver] = {}
        self._device_configs: dict[str, dict[str, Any]] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        self._orphaned_devices: dict[str, dict[str, Any]] = {}  # devices with missing drivers
        self._intentional_disconnect: set[str] = set()  # suppress auto-reconnect
        self._pause_expiry_tasks: dict[str, asyncio.Task] = {}  # pause TTL backstops
        # Auto-detected Open Web UI URLs, keyed by device id (see web_ui_probe).
        # Ephemeral by design — re-detected on add/connect, never persisted.
        self._detected_web_ui_urls: dict[str, str] = {}
        # In-flight web-UI probes, keyed by device id — holds a task reference
        # (so it isn't GC'd) and dedupes the add-time and connect-time triggers.
        self._web_ui_probe_tasks: dict[str, asyncio.Task] = {}

        # Auto-reconnect when a device transport drops mid-session
        self.events.on(
            "device.disconnected.*", self._on_device_disconnected
        )
        # Mirror a bridge's online state onto the bridge-routed devices bound to
        # it (an IR device on an emitter port has no transport of its own).
        self.events.on(
            "device.connected.*", self._on_device_connected
        )

    async def add_device(self, device_config: dict[str, Any]) -> None:
        """
        Instantiate a driver, register its state variables, and connect.

        Args:
            device_config: Dict with id, driver, name, config keys.
        """
        device_id = device_config["id"]
        driver_id = device_config["driver"]
        name = device_config.get("name", device_id)
        config = device_config.get("config", {})

        enabled = device_config.get("enabled", True)
        if not enabled:
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.enabled", False, source="config")
            log.info(f"Device {device_id} is disabled, skipping connection")
            return

        # Look up driver class
        driver_class = _DRIVER_REGISTRY.get(driver_id)
        if driver_class is None:
            log.warning(f"Driver '{driver_id}' not found for device '{device_id}' — device is orphaned")
            self._orphaned_devices[device_id] = device_config
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.orphaned", True, source="config")
            self.state.set(
                f"device.{device_id}.orphan_reason",
                f"Driver '{driver_id}' is not installed",
                source="config",
            )
            await self.events.emit("device.orphaned", {"device_id": device_id, "driver": driver_id})
            return

        # Create driver instance, then hand it the project-side
        # child_entities map (user labels, per-child config) which
        # register_child consults to seed the platform-managed `label`
        # state key. Done post-construction so existing driver subclasses
        # with a fixed __init__ signature don't need to change.
        driver = driver_class(device_id, config, self.state, self.events)
        driver.set_project_child_entities(device_config.get("child_entities") or {})
        self._devices[device_id] = driver
        self._device_configs[device_id] = device_config

        # A bridge-routed device (e.g. an IR device bound to an emitter port)
        # emits through the live bridge instance; hand it the router that
        # reaches that bridge at send time.
        if config.get("bridge") and config.get("bridge_port"):
            driver._bridge_router = self._route_bridge_command

        # Set device name in state
        self.state.set(
            f"device.{device_id}.name", name, source=f"device.{device_id}"
        )

        self.state.set(f"device.{device_id}.enabled", True, source="config")
        log.info(f"Added device '{device_id}' ({name}) using driver '{driver_id}'")

        # If this device routes through a bridge, let the bridge configure the
        # port (e.g. push serial baud/parity) before we open the connection, so
        # the transparent pass-through carries bytes at the right line settings.
        await self._prepare_bridge_for(device_id, config)

        # Attempt connection
        try:
            await driver.connect()
            # Apply pending settings after successful connect
            await self._apply_pending_settings(device_id)
            # A bridge-routed device (IR on an emitter port) connect()s without
            # a socket: it comes up online iff its bridge is already online. If
            # the bridge is offline at add time, surface a bridge_offline reason
            # on the card — the mirror handlers will clear it when the bridge
            # comes up. (Skipped for a device that connected normally.)
            if config.get("transport") == "bridge" and not driver.get_state(
                "connected"
            ):
                bridge_id = config.get("bridge")
                if bridge_id:
                    self._set_bridge_offline_reason(device_id, bridge_id)
        except Exception as e:
            log.warning(f"Failed to connect '{device_id}': {e}")
            if self._set_offline_reason(device_id, driver, exc=e) == "auth_failed":
                self._pause_reconnect_for_auth(device_id)
            else:
                self._start_reconnect(device_id)

        # Auto-detect an Open Web UI for the device. Runs whether or not the
        # control protocol connected — a device can be offline for control yet
        # still serve a reachable admin page.
        self._schedule_web_ui_probe(device_id)

    async def _prepare_bridge_for(
        self, device_id: str, config: dict[str, Any]
    ) -> None:
        """Best-effort: ask the live bridge driver to prepare a port before a
        bridge-bound downstream device connects.

        The bridge driver instance owns the bridge's command socket; preparing
        the port (for serial, pushing baud/parity via the bridge protocol) makes
        the transparent pass-through carry bytes at the right line settings. A
        missing / not-yet-live bridge is logged and skipped — bridges are
        ordered ahead of their dependents on load, and serial config persists on
        the hardware, so a transient miss self-heals on the next add/edit. Never
        raises: a bridge-side failure must not strand the downstream offline.
        """
        bridge_id = config.get("bridge")
        bridge_port = config.get("bridge_port")
        if not bridge_id or not bridge_port:
            return
        bridge = self._devices.get(bridge_id)
        if bridge is None or not getattr(bridge, "is_bridge", False):
            log.debug(
                "Device '%s' binds bridge '%s' but it isn't a live bridge "
                "instance yet — skipping port prep", device_id, bridge_id,
            )
            return
        try:
            await bridge.prepare_bridge_port(bridge_port, config)
        except Exception:
            log.warning(
                "Bridge '%s' failed to prepare port '%s' for device '%s' — "
                "connecting anyway", bridge_id, bridge_port, device_id,
                exc_info=True,
            )

    async def _route_bridge_command(
        self, bridge_id: str, port_id: str, kind: str, payload: dict[str, Any]
    ) -> Any:
        """Route a bridge-routed downstream device's command to its live bridge.

        Injected into bridge-routed drivers as their ``_bridge_router`` so a
        command (e.g. an IR device's code) reaches the bridge instance that owns
        the hardware socket. Raises ConnectionError with a clear message when the
        bridge is missing, not a bridge, or offline — surfaced to the caller as a
        command failure rather than a silent no-op.
        """
        bridge = self._devices.get(bridge_id)
        if bridge is None or not getattr(bridge, "is_bridge", False):
            raise ConnectionError(f"Bridge '{bridge_id}' is not available")
        if not getattr(bridge, "_connected", False):
            raise ConnectionError(f"Bridge '{bridge_id}' is offline")
        return await bridge.bridge_emit(port_id, kind, payload)

    async def remove_device(self, device_id: str) -> None:
        """Disconnect and remove a device (handles both active and orphaned)."""
        # Cancel reconnect if running — await so reconnect loop finishes
        await self._cancel_reconnect(device_id)
        # Drop pause bookkeeping: the TTL backstop must not fire a resume for
        # a device that no longer exists, and a stale intentional-disconnect
        # entry would suppress auto-reconnect for a future device re-added
        # under the same id.
        self._cancel_pause_expiry(device_id)
        self._intentional_disconnect.discard(device_id)

        driver = self._devices.pop(device_id, None)
        if driver:
            try:
                await driver.disconnect()
            except Exception:
                log.exception(f"Error disconnecting '{device_id}'")

        # Also clean up orphan tracking
        self._orphaned_devices.pop(device_id, None)

        self._device_configs.pop(device_id, None)
        # Drop the detected web UI URL so a re-add under the same id re-detects
        # (config may have changed the host/scheme), and cancel any in-flight probe.
        self._detected_web_ui_urls.pop(device_id, None)
        probe = self._web_ui_probe_tasks.pop(device_id, None)
        if probe is not None:
            probe.cancel()

        # Clear all state keys for this device
        device_keys = self.state.get_namespace(f"device.{device_id}.")
        for key in device_keys:
            self.state.delete(f"device.{device_id}.{key}")

        log.info(f"Removed device '{device_id}'")

    async def update_device(self, device_id: str, new_config: dict[str, Any]) -> None:
        """
        Update a device by disconnecting and re-adding with new config.

        Handles both active devices and orphaned devices (driver reassignment).

        Args:
            device_id: The existing device ID.
            new_config: Full device config dict (id, driver, name, config).
        """
        if device_id in self._devices or device_id in self._orphaned_devices:
            await self.remove_device(device_id)
        elif device_id in self._device_configs:
            # Disabled device — just clean up config
            self._device_configs.pop(device_id, None)
        else:
            raise ValueError(f"Device '{device_id}' not found")
        await self.add_device(new_config)

    async def send_command(
        self, device_id: str, command: str, params: dict[str, Any] | None = None
    ) -> Any:
        """Send a command to a device by ID.

        Any exception raised by the driver's ``send_command`` is published as
        ``device.error.<device_id>`` (payload: ``{"device_id", "error"}``) and
        then re-raised. Transport-level loss is reported separately as
        ``device.disconnected.<device_id>`` from the transport callback; the
        two events are complementary — see ``event_bus.py`` for the policy.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")
        # The connected-gate is skipped for commands the driver declares
        # available_offline — a handler that needs no live connection (e.g. a
        # Wake-on-LAN power_on) so a macro, panel button, or schedule can wake a
        # device that has gone fully off the network. Param validation below
        # still runs for every command.
        if not driver.get_state("connected") and not self._command_available_offline(
            driver, command
        ):
            raise ConnectionError(f"Device '{device_id}' is not connected")
        try:
            params = self._coerce_child_id_params(driver, command, params)
            params = self._validate_command_params(driver, command, params)
            return await driver.send_command(command, params)
        except Exception as exc:
            await self.events.emit(
                f"device.error.{device_id}",
                {"device_id": device_id, "error": str(exc)},
            )
            raise

    @staticmethod
    def _command_available_offline(driver: BaseDriver, command: str) -> bool:
        """True when the command declares ``available_offline`` — it may run
        with no live connection.

        Reads the instance-level DRIVER_INFO so runtime-populated command sets
        are covered too. An unknown command, or one without the flag, is not
        offline-capable (the connected-gate applies).
        """
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        cmd_def = (info.get("commands") or {}).get(command)
        return isinstance(cmd_def, dict) and bool(cmd_def.get("available_offline"))

    @staticmethod
    def _validate_command_params(
        driver: BaseDriver, command: str, params: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Run every command's params through the declared-schema gate,
        regardless of driver format.

        ConfigurableDriver has validated internally since the pickers work
        (and still does, covering direct-call paths like the Driver Builder
        test harness); Python drivers' declared ``min``/``max``/``pattern``
        were cosmetic until this dispatch-path gate. Reads the instance-level
        DRIVER_INFO so runtime-populated command sets (qsc_qrc's discovered
        controls, toa_9000m2's built commands) are gated too. Commands or
        params without a schema entry pass through untouched.
        """
        if not params:
            return params
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        cmd_def = (info.get("commands") or {}).get(command)
        if not isinstance(cmd_def, dict):
            return params
        pdefs = cmd_def.get("params")
        if not isinstance(pdefs, dict):
            return params
        return normalize_and_validate_command_params(command, pdefs, params)

    @staticmethod
    def _coerce_child_id_params(
        driver: BaseDriver, command: str, params: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Coerce ``child_id`` param values to the child type's declared id
        type before they reach the driver.

        The UI, macros, and REST supply the id as a string (often the padded
        form, e.g. "003"); an integer-id child type needs an int. Drivers
        used to hand-convert each param — now the platform does it, so a
        driver can pass ``params["outlet"]`` straight to the child API
        (``int(int)`` keeps hand-converting drivers working). String-id
        types pass through untouched.
        """
        if not params:
            return params
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        cmd_def = (info.get("commands") or {}).get(command)
        if not isinstance(cmd_def, dict):
            return params
        pdefs = cmd_def.get("params")
        if not isinstance(pdefs, dict):
            return params
        child_types = info.get("child_entity_types") or {}
        out = dict(params)
        for name, pdef in pdefs.items():
            if not isinstance(pdef, dict) or pdef.get("type") != "child_id":
                continue
            value = out.get(name)
            if value is None or isinstance(value, bool):
                continue
            type_def = child_types.get(pdef.get("child_type"))
            id_format = (type_def or {}).get("id_format") or {}
            if id_format.get("type", "integer") != "integer":
                continue
            if isinstance(value, int):
                continue
            try:
                out[name] = int(str(value).strip())
            except ValueError:
                raise CommandParamError(
                    f"'{command}': '{name}' must be a child id number, "
                    f"got {value!r}"
                ) from None
        return out

    def get_device_info(self, device_id: str) -> dict[str, Any]:
        """Return device metadata, status, and capabilities."""
        # Check if orphaned first
        if device_id in self._orphaned_devices:
            config = self._orphaned_devices[device_id]
            return {
                "id": device_id,
                "name": config.get("name", device_id),
                "driver": config.get("driver", ""),
                "connected": False,
                "orphaned": True,
                "orphan_reason": f"Driver '{config.get('driver', '')}' is not installed",
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "driver_info": {},
                # Redact credentials: this is the only get_device_info branch
                # that returns raw connection config, and it reaches the cloud
                # AI. A missing driver must not leak the device's password /
                # API key (see _redact_config).
                "config": _redact_config(config.get("config", {})),
            }

        driver = self._devices.get(device_id)
        if driver is None:
            # Check disabled devices
            if device_id in self._device_configs:
                config = self._device_configs[device_id]
                return {
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "state": self.state.get_namespace(f"device.{device_id}"),
                    "commands": {},
                    "driver_info": {},
                }
            raise ValueError(f"Device '{device_id}' not found")

        from server.drivers.actions import resolve_device_actions

        config = self._device_configs.get(device_id, {})
        return {
            "id": device_id,
            "name": config.get("name", device_id),
            "driver": config.get("driver", ""),
            "connected": driver.get_state("connected"),
            "state": self.state.get_namespace(f"device.{device_id}"),
            "commands": driver.DRIVER_INFO.get("commands", {}),
            # Quick Actions strip: driver-declared actions resolved (quick_actions
            # sugar folded in). The IDE filters by visible_when + availability.
            # Link (Open Web UI) URLs substitute {host}/{port} from the driver's
            # own config — the connection-merged dict it connects with. The
            # project-level entry here nests that under "config" and has no
            # host at its top level, so it can't substitute anything.
            "actions": resolve_device_actions(
                driver.DRIVER_INFO,
                driver.config,
                detected_web_ui_url=self._detected_web_ui_urls.get(device_id),
            ),
            "driver_info": driver.DRIVER_INFO,
        }

    def list_devices(self) -> list[dict[str, Any]]:
        """List all devices with summary info (including orphaned and disabled)."""
        result = []
        seen = set()

        # Active devices
        for device_id in self._devices:
            seen.add(device_id)
            try:
                info = self.get_device_info(device_id)
                entry: dict[str, Any] = {
                    "id": info["id"],
                    "name": info["name"],
                    "driver": info["driver"],
                    "connected": info["connected"],
                }
                # Include command names so callers don't need get_device_info per device
                if info.get("commands"):
                    entry["commands"] = list(info["commands"].keys())
                result.append(entry)
            except Exception:
                result.append({"id": device_id, "name": device_id, "connected": False})

        # Orphaned devices (driver not found)
        for device_id, config in self._orphaned_devices.items():
            if device_id not in seen:
                seen.add(device_id)
                result.append({
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "orphaned": True,
                    "orphan_reason": f"Driver '{config.get('driver', '')}' is not installed",
                })

        # Disabled devices
        for device_id, config in self._device_configs.items():
            if device_id not in seen:
                seen.add(device_id)
                result.append({
                    "id": device_id,
                    "name": config.get("name", device_id),
                    "driver": config.get("driver", ""),
                    "connected": False,
                    "enabled": False,
                })

        return result

    def get_device_configs(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of the device config dict (device_id → config)."""
        return dict(self._device_configs)

    def get_device_config(self, device_id: str) -> dict[str, Any] | None:
        """Return a single device's config dict, or None if not tracked."""
        return self._device_configs.get(device_id)

    async def retry_orphaned_device(self, device_id: str) -> bool:
        """Re-attempt adding an orphaned device (e.g., after installing its driver).

        Returns True if the device was successfully activated, False if still orphaned.
        """
        if device_id not in self._orphaned_devices:
            raise ValueError(f"Device '{device_id}' is not orphaned")

        config = self._orphaned_devices[device_id]
        driver_id = config.get("driver", "")

        # Check if the driver is now available
        if driver_id not in _DRIVER_REGISTRY:
            return False

        # Remove from orphan tracking and re-add normally
        await self.remove_device(device_id)
        await self.add_device(config)
        return device_id not in self._orphaned_devices

    async def retry_all_orphans(self) -> list[str]:
        """Promote every orphan whose driver is now in the registry.

        Called after the driver loader runs (project reload, community
        install) so devices that were stuck in orphan state because their
        driver wasn't loaded yet come online without a server restart.
        Returns the list of device IDs that successfully activated.
        """
        activated: list[str] = []
        # Snapshot before iterating — retry_orphaned_device mutates the dict
        for device_id, config in list(self._orphaned_devices.items()):
            driver_id = config.get("driver", "")
            if driver_id not in _DRIVER_REGISTRY:
                continue
            try:
                ok = await self.retry_orphaned_device(device_id)
                if ok:
                    activated.append(device_id)
                    log.info(
                        f"Activated orphaned device '{device_id}' "
                        f"(driver '{driver_id}' now installed)"
                    )
            except Exception:
                log.exception(f"Failed to activate orphaned device '{device_id}'")
        return activated

    def get_missing_drivers(self) -> list[str]:
        """Return the unique driver IDs that orphaned devices are waiting for."""
        seen: set[str] = set()
        result: list[str] = []
        for cfg in self._orphaned_devices.values():
            driver_id = cfg.get("driver", "")
            if driver_id and driver_id not in seen:
                seen.add(driver_id)
                result.append(driver_id)
        return result

    async def set_device_setting(
        self, device_id: str, key: str, value: Any
    ) -> Any:
        """Set a device setting value on a device by ID."""
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")
        if not driver.get_state("connected"):
            raise ConnectionError(f"Device '{device_id}' is not connected")

        # Validate the setting exists
        settings = driver.DRIVER_INFO.get("device_settings", {})
        if key not in settings:
            raise ValueError(f"Unknown device setting '{key}' for device '{device_id}'")

        # Runtime value gate — the IDE editor's min/max/values/regex checks
        # are an authoring aid; scripts, macros, cloud, and raw REST bypass
        # them, so the write is validated (and coerced to the declared type)
        # here regardless of caller.
        value = validate_device_setting_value(key, settings[key], value)

        return await driver.set_device_setting(key, value)

    def get_driver(self, device_id: str) -> BaseDriver | None:
        """Return the live driver instance for a device, or ``None`` if the
        device is unknown, orphaned (driver not installed), or disabled.

        Exposes the driver for callers that need to read driver-declared
        schema (child_entity_types, commands) or invoke public driver
        introspection helpers (``get_child_state``, ``format_child_id``,
        ``refresh_children``). Callers must not mutate driver internals.
        """
        return self._devices.get(device_id)

    def get_device_settings(self, device_id: str) -> dict[str, Any]:
        """Return device settings metadata with current values from state."""
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")

        settings_def = driver.DRIVER_INFO.get("device_settings", {})
        result: dict[str, Any] = {}
        for key, setting in settings_def.items():
            state_key = setting.get("state_key", key)
            current_value = driver.get_state(state_key)
            result[key] = {
                **setting,
                "current_value": current_value,
            }
        return result

    async def reload_driver(self, driver_id: str) -> list[str]:
        """
        Reconnect all devices using a given driver after it has been reloaded.

        Finds all active devices using the specified driver_id, disconnects them,
        and re-adds them so they pick up the new driver class from the registry.
        Also retries any orphaned devices that were waiting for this driver.

        Returns a list of device IDs that were reconnected.
        """
        reconnected: list[str] = []

        # Find active devices using this driver
        affected = [
            (did, cfg)
            for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id and did in self._devices
        ]

        for device_id, config in affected:
            try:
                await self.remove_device(device_id)
                await self.add_device(config)
                reconnected.append(device_id)
                log.info(f"Reconnected device '{device_id}' after driver reload")
            except Exception:
                log.exception(f"Failed to reconnect '{device_id}' after driver reload")

        # Retry orphaned devices that were waiting for this driver
        orphaned_for_driver = [
            did for did, cfg in self._orphaned_devices.items()
            if cfg.get("driver") == driver_id
        ]
        for device_id in orphaned_for_driver:
            try:
                activated = await self.retry_orphaned_device(device_id)
                if activated:
                    reconnected.append(device_id)
                    log.info(f"Activated orphaned device '{device_id}' after driver reload")
            except Exception:
                log.exception(f"Failed to activate orphaned device '{device_id}'")

        return reconnected

    def get_devices_using_driver(self, driver_id: str) -> list[str]:
        """Return list of device IDs that use the given driver."""
        return [
            did for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id
        ]

    async def connect_all(self) -> list[str]:
        """Connect all devices concurrently. Returns list of failed device IDs."""
        failed: list[str] = []

        async def _connect_one(device_id: str, driver: Any) -> None:
            try:
                await asyncio.wait_for(driver.connect(), timeout=30)
            except Exception as e:
                log.warning(f"Failed to connect '{device_id}': {e}")
                code = self._set_offline_reason(device_id, driver, exc=e)
                failed.append(device_id)
                if code == "auth_failed":
                    self._pause_reconnect_for_auth(device_id)
                else:
                    self._start_reconnect(device_id)

        tasks = [
            _connect_one(did, drv)
            for did, drv in self._devices.items()
            if not drv.get_state("connected")
        ]
        if tasks:
            await asyncio.gather(*tasks)
        return failed

    async def disconnect_all(self) -> None:
        """Disconnect all devices gracefully (called at shutdown)."""
        # Cancel all reconnect tasks first — await each so loops finish cleanly
        for device_id in list(self._reconnect_tasks.keys()):
            await self._cancel_reconnect(device_id)
        # Cancel pause-TTL backstops so none fires a resume mid-shutdown
        for device_id in list(self._pause_expiry_tasks.keys()):
            self._cancel_pause_expiry(device_id)
        # Cancel any in-flight web-UI probes so they don't outlive shutdown.
        for task in list(self._web_ui_probe_tasks.values()):
            task.cancel()
        self._web_ui_probe_tasks.clear()

        for device_id, driver in self._devices.items():
            try:
                await driver.disconnect()
            except Exception:
                log.exception(f"Error disconnecting '{device_id}'")

    # --- Pending Settings ---

    async def _apply_pending_settings(self, device_id: str) -> None:
        """Apply any pending device settings after a successful connect."""
        config = self._device_configs.get(device_id, {})
        pending = config.get("pending_settings", {})
        if not pending:
            return

        driver = self._devices.get(device_id)
        if driver is None:
            return

        defs = driver.DRIVER_INFO.get("device_settings", {})
        applied_keys: list[str] = []
        for key, value in pending.items():
            try:
                # Coerce against the schema before the value reaches the driver.
                # store_pending_settings coerces at intake, but a value can
                # enter the queue by a path that bypasses it (a project reload of
                # a hand-edited file), and set_device_setting doesn't validate.
                if key in defs:
                    value = validate_device_setting_value(key, defs[key], value)
                await driver.set_device_setting(key, value)
                applied_keys.append(key)
                log.info(f"[{device_id}] Applied pending setting '{key}' = {value!r}")
            except Exception as e:
                log.warning(f"[{device_id}] Failed to apply pending setting '{key}': {e}")
                # Surface the failure beyond the server log — the key stays
                # queued and is retried on the next connect, but silently
                # retrying forever hid real problems (a firmware that
                # rejects the value, a bad queued write).
                try:
                    await self.events.emit(
                        f"device.error.{device_id}",
                        {
                            "device_id": device_id,
                            "error": f"Pending setting '{key}' failed to apply: {e}",
                            "source": "pending_settings",
                        },
                    )
                except Exception:
                    log.exception(f"[{device_id}] Failed to emit device.error")

        if applied_keys:
            # Clear applied settings from pending
            for key in applied_keys:
                pending.pop(key, None)

            # If all pending settings were applied, remove the dict entirely
            if not pending:
                config.pop("pending_settings", None)

            # Notify the engine to persist the change
            await self.events.emit(
                "device.pending_settings_applied",
                {"device_id": device_id, "applied": applied_keys, "remaining": dict(pending)},
            )

    async def store_pending_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> None:
        """Store pending settings for a device (will be applied on next connect).

        Validated at intake when the driver is available: a typo'd key or an
        out-of-range value used to sit in the queue and fail on every
        reconnect with only a warn log. Orphaned devices (driver not
        installed) store as-is — there's no schema to check against yet.
        """
        config = self._device_configs.get(device_id)
        if config is None:
            raise ValueError(f"Device '{device_id}' not found")

        driver = self._devices.get(device_id)
        if driver is not None:
            defs = driver.DRIVER_INFO.get("device_settings", {})
            validated: dict[str, Any] = {}
            for key, value in settings.items():
                if key not in defs:
                    raise DeviceSettingValueError(
                        f"Unknown device setting '{key}' for device '{device_id}'"
                    )
                validated[key] = validate_device_setting_value(key, defs[key], value)
            settings = validated

        if "pending_settings" not in config:
            config["pending_settings"] = {}
        config["pending_settings"].update(settings)
        log.info(f"[{device_id}] Stored {len(settings)} pending setting(s)")

    # --- Offline reason classification ---

    @staticmethod
    def _connection_descriptor(driver: BaseDriver) -> tuple[str, Any, str]:
        """Return (host, port, transport) for a driver's connection, for the
        connection-fault classifier's message. Mirrors how BaseDriver.connect()
        resolves the transport (device config overrides the driver default).
        """
        cfg = getattr(driver, "config", {}) or {}
        transport = (
            cfg.get("transport")
            or driver.DRIVER_INFO.get("transport", "tcp")
            or ""
        ).lower()
        if transport == "serial":
            # Serial has no host; its "port" is the COM/tty path.
            return "", cfg.get("port", ""), transport
        host = cfg.get("host", "") or ""
        port = cfg.get("port")
        if port in (None, "") and transport == "http":
            port = 443 if cfg.get("ssl") else 80
        return host, port, transport

    def _set_offline_reason(
        self,
        device_id: str,
        driver: BaseDriver | None,
        exc: BaseException | None = None,
    ) -> str:
        """Classify why a device is offline and publish both the stable code
        (``device.<id>.offline_reason``, for triggers/automation) and the human
        message (``device.<id>.offline_detail``, for the device card).
        Returns the classified code so callers can branch on THIS failure
        (never on possibly-stale state) — the reconnect policy hinges on it.

        Reads the transport's last error from the driver — preferring the live
        transport, falling back to the value BaseDriver stashes before tearing
        a failed transport down — plus the connect exception, and runs the one
        shared classifier. No per-transport branching here. Typed faults win
        over string matching: first a ConnectionFaultError in the exception
        chain (the freshest signal), then a fault the driver stashed before
        forcing a disconnect (liveness watchdogs), then the classifier.
        """
        last_error = ""
        host, port, transport = "", None, ""
        if driver is not None:
            last_error = getattr(driver, "last_transport_error", "") or ""
            live = getattr(driver, "transport", None)
            if live is not None:
                fresh = getattr(live, "last_error", "") or ""
                if fresh:
                    last_error = fresh
            host, port, transport = self._connection_descriptor(driver)

        fault = typed_fault_from_exc(exc, host=host, port=port)
        if fault is None and driver is not None:
            fault = getattr(driver, "last_fault", None)
        if fault is None:
            fault = classify_connection_fault(
                last_error=last_error, exc=exc,
                host=host, port=port, transport=transport,
            )
        self.state.set_batch(
            {
                f"device.{device_id}.offline_reason": fault.code,
                f"device.{device_id}.offline_detail": fault.message,
            },
            source="device_manager",
        )
        return fault.code

    def _clear_offline_reason(self, device_id: str) -> None:
        """Clear both offline-reason keys after a successful (re)connect."""
        self.state.set_batch(
            {
                f"device.{device_id}.offline_reason": None,
                f"device.{device_id}.offline_detail": None,
            },
            source="device_manager",
        )

    def _pause_reconnect_for_auth(self, device_id: str) -> None:
        """Hold auto-reconnect after a credential rejection.

        A wrong password can't heal by retrying — the same login just fails
        again, and devices with brute-force lockouts (Crestron and others
        block the offending source IP after a handful of failures) punish
        every extra attempt, locking the legitimate user out too. Policy:
        one attempt per user action. The initial connect (which may carry
        driver-default credentials worth trying) counts as that attempt;
        after an auth_failed classification we stop and wait. Editing the
        device re-adds it (fresh attempt), and the Reconnect button forces
        one more try. ``reconnect_failed`` is set so the UI shows the
        not-retrying state.
        """
        log.warning(
            f"[{device_id}] Authentication failed — auto-reconnect paused so "
            f"repeated logins can't trip the device's lockout. Update the "
            f"device's credentials, or press Reconnect to try again."
        )
        self.state.set(
            f"device.{device_id}.reconnect_failed", True, source="device_manager"
        )

    def _stop_reconnect_for_permanent_fault(self, device_id: str, code: str) -> None:
        """Stop auto-reconnect after a fault only a human can clear.

        A rejected host key, an untrusted certificate, bad connection
        settings, or a missing client binary all fail identically on every
        retry — the device card already names the cause and the fix, so
        continuing to the full 120 attempts just churns. The reason and
        detail keys are left as classified (they carry the actionable
        wording); ``reconnect_failed`` tells the UI we've stopped.
        """
        log.warning(
            f"[{device_id}] Stopped reconnecting: '{code}' can't resolve by "
            f"retrying. Fix the cause shown on the device card, then press "
            f"Reconnect (editing the device also retries)."
        )
        self.state.set(
            f"device.{device_id}.reconnect_failed", True, source="device_manager"
        )

    # --- Reconnection ---

    async def _on_device_disconnected(self, event: str, payload: dict[str, Any]) -> None:
        """Handle device.disconnected.* events — trigger auto-reconnect."""
        # Extract device_id from event name: "device.disconnected.<id>"
        parts = event.split(".", 2)
        if len(parts) < 3:
            return
        device_id = parts[2]

        # If this device is a bridge, take its bridge-routed dependents (IR
        # devices on emitter ports) offline too — they have no transport of
        # their own and are reachable only while the bridge is. Done before the
        # intentional-disconnect / still-connected guards below so a bridge
        # being removed or updated still propagates offline to its dependents.
        deps = self._bridge_routed_dependents(device_id)
        if deps:
            await self._mirror_bridge_state(device_id, False, deps)

        # Only reconnect if device still exists and isn't being removed
        driver = self._devices.get(device_id)
        if driver is None:
            return

        # A bridge-routed device has no transport to reconnect — its connected
        # state is a pure mirror of its bridge (see _mirror_bridge_state), so
        # the transport auto-reconnect machinery doesn't apply to it.
        dev_cfg = self._device_configs.get(device_id, {}).get("config", {})
        if dev_cfg.get("transport") == "bridge":
            return

        # Skip if this is an intentional disconnect (reconnect_device, remove, update)
        if device_id in self._intentional_disconnect:
            return

        # Skip a stale/deferred disconnect event for a device that's already
        # back online. The transport schedules its drop emit via create_task
        # (base.py:_handle_transport_disconnect), so it can fire AFTER a manual
        # reconnect_device has already reconnected and cleared the intentional
        # flag — without this guard that stale event would spin up a redundant
        # reconnect loop against a live connection.
        if driver.get_state("connected"):
            return

        # Check the device isn't disabled
        config = self._device_configs.get(device_id, {})
        if not config.get("enabled", True):
            return

        log.info(f"[{device_id}] Transport disconnected — starting auto-reconnect")
        # Classify the drop from the transport's stashed last error (no connect
        # exception on this path) so the device card shows an actionable reason
        # instead of a bare code.
        self._set_offline_reason(device_id, driver)
        self._start_reconnect(device_id)

    async def _on_device_connected(self, event: str, payload: dict[str, Any]) -> None:
        """Handle device.connected.* events — mirror a bridge coming online onto
        the bridge-routed devices bound to it.

        Fires for every device connect (cheap: a no-op unless the connected
        device is a bridge with bridge-routed dependents). Covers the case where
        a bridge connects *after* its dependents were added; the add-time seed
        in ``BaseDriver.connect`` covers the reverse order.
        """
        parts = event.split(".", 2)
        if len(parts) < 3:
            return
        device_id = parts[2]
        deps = self._bridge_routed_dependents(device_id)
        if deps:
            await self._mirror_bridge_state(device_id, True, deps)
        # A device that was unreachable at add time may only now be serving its
        # web UI; the probe's own "already detected" guard makes this idempotent.
        self._schedule_web_ui_probe(device_id)

    def _schedule_web_ui_probe(self, device_id: str) -> None:
        """Kick off a one-shot Open Web UI probe for an auto-mode device.

        Idempotent and cheap to call at add time and on every (re)connect. Skips
        when the driver forced ``web_ui`` on or off, when a URL was already
        detected, when the device has no host to reach, or when it's an
        HTTP-transport device (its URL comes straight from config in the action
        resolver — no probe needed). The detected URL is stashed on
        ``_detected_web_ui_urls`` and surfaced by ``get_device_info``.
        """
        driver = self._devices.get(device_id)
        if driver is None or driver.DRIVER_INFO.get("web_ui") is not None:
            return
        if device_id in self._detected_web_ui_urls or device_id in self._web_ui_probe_tasks:
            return
        config = getattr(driver, "config", None) or {}
        host = config.get("host")
        if not host:
            return
        transport = config.get("transport") or driver.DRIVER_INFO.get("transport")
        if transport == "http":
            return
        task = asyncio.create_task(self._run_web_ui_probe(device_id, str(host)))
        self._web_ui_probe_tasks[device_id] = task
        task.add_done_callback(lambda t, d=device_id: self._web_ui_probe_tasks.pop(d, None))
        task.add_done_callback(_log_task_exception)

    async def _run_web_ui_probe(self, device_id: str, host: str) -> None:
        """Probe the device's host for a web UI and record any URL found."""
        from server.core.web_ui_probe import probe_web_ui

        url = await probe_web_ui(host)
        # Guard against a device removed while the probe was in flight, and don't
        # clobber a URL that arrived first (e.g. a discovery seed).
        if url and device_id in self._devices and device_id not in self._detected_web_ui_urls:
            self._detected_web_ui_urls[device_id] = url

    def seed_web_ui_url(self, device_id: str, url: str) -> None:
        """Record a web UI URL detected outside the probe (e.g. from a discovery
        scan's already-known open ports), so the button shows immediately.

        Only when the driver is in auto-detect mode (``web_ui`` unset); first
        writer wins, so a probe already in flight doesn't override it.
        """
        driver = self._devices.get(device_id)
        if driver is None or driver.DRIVER_INFO.get("web_ui") is not None:
            return
        if url:
            self._detected_web_ui_urls.setdefault(device_id, url)

    def _bridge_routed_dependents(self, bridge_id: str) -> list[str]:
        """Live device ids that route their commands through ``bridge_id`` and
        have no transport of their own (resolved ``transport == "bridge"``).

        These are the devices whose connected state mirrors the bridge (IR
        devices on emitter ports). A serial pass-through downstream is *not*
        here — it dials the bridge's TCP passthrough and tracks the bridge via
        its own socket, so it needs no mirroring.
        """
        out: list[str] = []
        for dev_id, dc in self._device_configs.items():
            if dev_id not in self._devices:
                continue
            cfg = dc.get("config", {})
            if cfg.get("bridge") == bridge_id and cfg.get("transport") == "bridge":
                out.append(dev_id)
        return out

    async def _mirror_bridge_state(
        self, bridge_id: str, online: bool, deps: list[str]
    ) -> None:
        """Set each bridge-routed dependent's connected state to ``online`` and
        emit its lifecycle event (only on an actual transition, so triggers see
        one edge, not a stream). On going offline, publish a ``bridge_offline``
        reason; on coming online, clear it.
        """
        for dev_id in deps:
            driver = self._devices.get(dev_id)
            if driver is None:
                continue
            was = bool(driver.get_state("connected"))
            driver._bridge_routed = True
            driver._connected = online
            driver.set_state("connected", online)
            if online:
                self._clear_offline_reason(dev_id)
                if not was:
                    await self.events.emit(f"device.connected.{dev_id}")
            else:
                self._set_bridge_offline_reason(dev_id, bridge_id)
                if was:
                    await self.events.emit(f"device.disconnected.{dev_id}")

    def _set_bridge_offline_reason(self, device_id: str, bridge_id: str) -> None:
        """Publish the offline-reason keys for a bridge-routed device whose
        bridge is down (a direct taxonomy entry, not classified from an error).
        """
        from server.core.connection_fault import bridge_offline_fault

        bridge_name = self.state.get(f"device.{bridge_id}.name") or bridge_id
        fault = bridge_offline_fault(str(bridge_name))
        self.state.set_batch(
            {
                f"device.{device_id}.offline_reason": fault.code,
                f"device.{device_id}.offline_detail": fault.message,
            },
            source="device_manager",
        )

    def _start_reconnect(self, device_id: str) -> None:
        """Start a background reconnect loop for a device."""
        if device_id in self._reconnect_tasks:
            return  # Already reconnecting
        task = asyncio.create_task(self._reconnect_loop(device_id))
        task.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = task

    async def _cancel_reconnect(self, device_id: str) -> None:
        """Cancel a running reconnect task and wait for it to finish."""
        task = self._reconnect_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _reconnect_loop(self, device_id: str, max_attempts: int = 120) -> None:
        """
        Background task that attempts to reconnect a disconnected device.
        Exponential backoff: 2s, 4s, 8s, 16s, 30s max.
        Gives up after max_attempts (default 120 = ~1 hour at 30s intervals).
        """
        delays = [2, 4, 8, 16, 30]
        attempt = 0
        permanent_attempts = 0

        try:
            while attempt < max_attempts:
                # Check device still exists before each attempt
                driver = self._devices.get(device_id)
                if driver is None:
                    log.debug(f"[{device_id}] Device removed, stopping reconnect")
                    return

                delay = delays[min(attempt, len(delays) - 1)]
                self.state.set(
                    f"device.{device_id}.reconnect_attempt", attempt + 1,
                    source="device_manager",
                )
                log.info(
                    f"[{device_id}] Reconnect attempt {attempt + 1}/{max_attempts} in {delay}s..."
                )
                await asyncio.sleep(delay)

                # Re-check after sleep — device may have been removed
                if device_id not in self._devices:
                    log.debug(f"[{device_id}] Device removed during wait, stopping reconnect")
                    return

                try:
                    # Stop polling before reconnect to prevent race conditions
                    # (poll firing while transport is being replaced)
                    await driver.stop_polling()
                    self._refresh_usb_serial_port(device_id, driver)
                    await driver.connect()
                    log.info(f"[{device_id}] Reconnected successfully")
                    self._clear_offline_reason(device_id)
                    self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
                    await self._apply_pending_settings(device_id)
                    return
                except Exception as e:
                    log.warning(f"[{device_id}] Reconnect failed: {e}")
                    # Refine the offline reason from this attempt's failure —
                    # the cause can change between attempts (auth vs unreachable).
                    code = self._set_offline_reason(device_id, driver, exc=e)
                    if code == "auth_failed":
                        # The device is reachable but rejecting the login. More
                        # attempts can only trip its lockout — stop here and
                        # wait for new credentials (an unreachable device that
                        # comes back with bad creds lands here on the attempt
                        # that discovers it).
                        self._pause_reconnect_for_auth(device_id)
                        return
                    if is_permanent_fault(code):
                        # Host key, TLS trust, connection settings, missing
                        # client: a human has to change something. Allow a
                        # couple of tries first — a device rebooting mid-scan
                        # can briefly present one of these — then stop rather
                        # than grinding through all 120 attempts.
                        permanent_attempts += 1
                        if permanent_attempts >= _MAX_PERMANENT_FAULT_ATTEMPTS:
                            self._stop_reconnect_for_permanent_fault(device_id, code)
                            return
                    else:
                        permanent_attempts = 0
                    attempt += 1

            # Exhausted all attempts
            log.warning(
                f"[{device_id}] Gave up reconnecting after {max_attempts} attempts. "
                f"Use the Reconnect button or restart the server to try again."
            )
            self.state.set(
                f"device.{device_id}.reconnect_failed", True,
                source="device_manager",
            )
        except asyncio.CancelledError:
            log.debug(f"[{device_id}] Reconnect cancelled")
        finally:
            self._reconnect_tasks.pop(device_id, None)

    def _refresh_usb_serial_port(self, device_id: str, driver) -> None:
        """Re-resolve a usb_serial-bound adapter to its current OS path.

        A replug can move the adapter (ttyUSB0 -> ttyUSB1) while the stored
        path goes stale; the stable USB serial number is the truth, so each
        reconnect attempt follows the adapter instead of redialing the old
        path. No-op for anything but a local usb_serial-bound serial device.
        """
        try:
            from server.transport.serial_transport import resolve_usb_binding

            driver_transport = driver.DRIVER_INFO.get("transport", "tcp")
            refreshed = resolve_usb_binding(driver.config, driver_transport)
            if refreshed is not driver.config:
                log.info(
                    f"[{device_id}] Serial adapter moved: port "
                    f"{driver.config.get('port')!r} -> {refreshed.get('port')!r}"
                )
                driver.config = refreshed
        except Exception:
            log.debug(
                f"[{device_id}] USB serial re-resolution failed", exc_info=True
            )

    async def reconnect_device(self, device_id: str) -> None:
        """Force disconnect and reconnect a device."""
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")
        driver = self._devices[device_id]
        # A manual reconnect overrides a test-panel pause — clear the pause
        # bookkeeping so the flag can't go stale and the TTL backstop can't
        # fire a redundant resume later.
        if self.state.get(f"device.{device_id}.paused"):
            self._cancel_pause_expiry(device_id)
            self.state.set(f"device.{device_id}.paused", False, source="device_manager")
        # Cancel any existing auto-reconnect task first
        await self._cancel_reconnect(device_id)
        self.state.set(f"device.{device_id}.reconnect_failed", None, source="device_manager")
        self._clear_offline_reason(device_id)
        self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
        # Suppress auto-reconnect during intentional disconnect
        self._intentional_disconnect.add(device_id)
        try:
            try:
                await driver.disconnect()
            except Exception:
                pass
            try:
                self._refresh_usb_serial_port(device_id, driver)
                await driver.connect()
                log.info(f"Reconnected device: {device_id}")
                # A bridge-routed device connect()s without raising even when
                # its bridge is down — it has no transport of its own and comes
                # up online only if the bridge is live. Mirror the add path:
                # re-surface bridge_offline (cleared up front above) so the card
                # and offline_reason automation stay accurate.
                cfg = driver.config or {}
                if cfg.get("transport") == "bridge" and not driver.get_state(
                    "connected"
                ):
                    bridge_id = cfg.get("bridge")
                    if bridge_id:
                        self._set_bridge_offline_reason(device_id, bridge_id)
            except Exception as e:
                self.state.set(f"device.{device_id}.connected", False, source="device_manager")
                log.warning(f"Reconnect failed for {device_id}: {e}")
                if self._set_offline_reason(device_id, driver, exc=e) == "auth_failed":
                    # The manual attempt was this action's one try.
                    self._pause_reconnect_for_auth(device_id)
                else:
                    self._start_reconnect(device_id)
        finally:
            self._intentional_disconnect.discard(device_id)

    async def begin_setup(self, device_id: str) -> None:
        """Suppress auto-reconnect for the duration of a setup action.

        A setup action opens its own out-of-band transport, independent of the
        device's normal (often failing) one. Cancel any running reconnect loop
        and add the device to the intentional-disconnect set so the
        auto-reconnect machinery doesn't race the handler's own connection. The
        device's live transport (if any) is left untouched — an offline device
        is already down, and the handler doesn't use it. Pair with ``end_setup``.
        """
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")
        await self._cancel_reconnect(device_id)
        self._intentional_disconnect.add(device_id)

    async def end_setup(self, device_id: str) -> None:
        """Re-enable auto-reconnect after a setup action. If the device didn't
        come back online during the run (the handler didn't reconnect, or its
        reconnect failed), resume the normal auto-reconnect loop so it keeps
        trying. Idempotent and safe if the device was removed mid-run.
        """
        self._intentional_disconnect.discard(device_id)
        driver = self._devices.get(device_id)
        if driver is not None and not driver.get_state("connected"):
            self._start_reconnect(device_id)

    async def reconnect_in_place(self, device_id: str) -> None:
        """Reconnect the existing driver instance using its current config.

        Used by a setup action's ``request_reconnect`` after a config update —
        it reconnects the *same* driver instance (so the handler's `self` stays
        valid) with whatever settings ``request_config_update`` merged into
        ``self.config``. Does not touch the intentional-disconnect set: the
        setup runner owns that suppression for the whole run. Raises on connect
        failure so the handler can see it.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            raise ValueError(f"Device '{device_id}' not found")
        self._clear_offline_reason(device_id)
        try:
            await driver.disconnect()
        except Exception:
            pass
        await driver.connect()

    async def pause_device(self, device_id: str, ttl: float | None = None) -> None:
        """Cleanly disconnect a device and suppress auto-reconnect (A81).

        Used by the driver test panel before it opens a competing TCP session
        against the same host:port on single-session devices. The device stays
        paused until ``resume_device`` is called — or until ``ttl`` seconds
        pass without a re-pause (the panel keeps the pause alive while open),
        at which point the device auto-resumes so a closed/crashed tab can't
        strand it offline forever. ``device.<id>.paused`` is set so the UI can
        surface the state. Re-pausing an already-paused device just resets
        the TTL.
        """
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")
        driver = self._devices[device_id]
        await self._cancel_reconnect(device_id)
        # Add to intentional_disconnect BEFORE disconnect so the disconnected
        # event handler doesn't kick off a reconnect_loop.
        self._intentional_disconnect.add(device_id)
        try:
            await driver.disconnect()
        except Exception as e:
            log.warning(f"pause_device: disconnect raised for {device_id}: {e}")
        self.state.set(f"device.{device_id}.paused", True, source="device_manager")
        self.state.set(f"device.{device_id}.connected", False, source="device_manager")
        self._schedule_pause_expiry(device_id, PAUSE_TTL if ttl is None else ttl)
        log.info(f"Paused device: {device_id}")

    def _schedule_pause_expiry(self, device_id: str, ttl: float) -> None:
        """(Re)arm the auto-resume backstop for a paused device."""
        self._cancel_pause_expiry(device_id)
        task = asyncio.create_task(self._pause_expiry(device_id, ttl))
        self._pause_expiry_tasks[device_id] = task

    def _cancel_pause_expiry(self, device_id: str) -> None:
        task = self._pause_expiry_tasks.pop(device_id, None)
        if task is not None:
            task.cancel()

    async def _pause_expiry(self, device_id: str, ttl: float) -> None:
        try:
            await asyncio.sleep(ttl)
            # Drop our own registration BEFORE resuming, so resume_device's
            # _cancel_pause_expiry doesn't cancel the very task running it.
            self._pause_expiry_tasks.pop(device_id, None)
            log.warning(
                f"[{device_id}] Test-panel pause expired after {ttl:.0f}s "
                f"without a resume — auto-resuming (the panel likely closed "
                f"without cleanup)"
            )
            await self.resume_device(device_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # resume_device handles connect failures itself; this guards the
            # device-removed race (ValueError) and anything unexpected.
            log.warning(f"[{device_id}] Pause-expiry auto-resume failed: {e}")
        finally:
            # current_task() raises if the loop is already gone (cancellation
            # during shutdown/teardown) — nothing to clean up in that case.
            try:
                current = asyncio.current_task()
            except RuntimeError:
                current = None
            if current is not None and self._pause_expiry_tasks.get(device_id) is current:
                self._pause_expiry_tasks.pop(device_id, None)

    async def resume_device(self, device_id: str) -> None:
        """Resume a paused device — clear the pause flag and reconnect.

        Idempotent: resuming a device that isn't paused just runs reconnect.
        On connect failure the normal auto-reconnect loop takes over.
        """
        if device_id not in self._devices:
            raise ValueError(f"Device '{device_id}' not found")
        driver = self._devices[device_id]
        self._cancel_pause_expiry(device_id)
        self._intentional_disconnect.discard(device_id)
        self.state.set(f"device.{device_id}.paused", False, source="device_manager")
        try:
            await driver.connect()
            log.info(f"Resumed device: {device_id}")
            # Mirror the reconnect-loop success path: drop the stale offline
            # reason and flush anything queued while the device was away.
            self._clear_offline_reason(device_id)
            await self._apply_pending_settings(device_id)
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            log.warning(f"resume_device: connect failed for {device_id}: {e}")
            self._set_offline_reason(device_id, driver, exc=e)
            self._start_reconnect(device_id)
