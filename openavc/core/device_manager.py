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
import contextlib
import random
import time
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.connection_fault import (
    UNREACHABLE,
    ConnectionFaultError,
    classify_connection_fault,
    is_permanent_fault,
    no_simulator_fault,
    typed_fault_from_exc,
)
from openavc.drivers.avcdriver_semantic import undeclared_child_type_reason
from openavc.drivers.base import (
    CommandParamError,
    DeviceSettingValueError,
    UnknownCommandError,
    normalize_and_validate_command_params,
    validate_device_setting_value,
)
from openavc.drivers.child_ids import (
    child_id_kind,
    child_id_range_error,
    coerce_child_local_id,
)
from openavc.drivers.registry import get_driver_class, is_driver_registered
from openavc.core.device_config import bridge_first
from openavc.core.event_bus import EventBus, detach_emit_chain
from openavc.core.state_store import StateStore
from openavc.utils.log_redaction import get_secret_registry, redact_config
from openavc.utils.logger import get_logger

log = get_logger(__name__)


def not_connected(device_id: str) -> ConnectionError:
    """The "this device is offline" error, carrying which device it is about.

    The id is in the message because that is what a log reader needs. The
    attribute is for the surfaces that turn the failure into a sentence
    somebody standing in the room reads, and they need the device's NAME: a
    macro step and a panel press both look it up and hand it to
    ``friendly_error``, and a script a control called has to be able to do the
    same. Without the attribute the only route back to the device is parsing
    the message, so the script path did not try -- and put the id on the glass
    instead, where "switcher1" is the one label nobody in the room has seen.
    """
    exc = ConnectionError(f"Device '{device_id}' is not connected")
    exc.device_id = device_id
    return exc


# How many reconnect attempts a permanent fault gets before the loop stops.
# One retry past the first classification: enough to shrug off a device that
# was mid-reboot when we read its certificate or host key, few enough that a
# genuinely misconfigured device stops churning almost immediately. auth_failed
# is stricter still (zero retries — see _pause_reconnect_for_auth).
_MAX_PERMANENT_FAULT_ATTEMPTS = 2

# --- Retry policy for a device that is simply ABSENT -----------------------
# A transient fault gets retried for as long as the device is in the project.
# There is deliberately no attempt ceiling: the address of a projector on a
# wall is a permanent fact about the room, so "we gave up an hour ago" is never
# the right answer to someone plugging the cable back in the next morning. Only
# the permanent set above ever stops, because only those need a human.
#
# The ramp is short and then flat. Its whole job is to catch a blip in a second
# or two; past that, the steady interval is what a returning device waits for,
# and slower does not help anyone.
_RECONNECT_RAMP_SECONDS = (1.0, 2.0)

# Steady-state seconds between attempts. Two things set this floor, and neither
# is CPU. Repeated SYNs to a non-responding address, continuing indefinitely,
# is the signature of a slow horizontal port scan, and docs/it-network-guide.md
# tells IT departments we don't probe their network — so this is the number
# that has to stay defensible to somebody's IDS. The unreachable path also ARPs,
# which is broadcast, and now runs around the clock. Overridable per-site via
# system.json ("devices" -> "reconnect_interval_seconds").
_RECONNECT_INTERVAL_DEFAULT = 5.0

# Spread simultaneous retries so a rack full of devices doesn't redial in
# lockstep after a switch reboot — the one moment when they are ALL offline.
_RECONNECT_JITTER = 0.2  # +/- 20%

# Transports where the real connect is expensive enough to be worth gating
# behind a cheap reachability check first. ssh spawns an `ssh` subprocess and
# negotiates keys; mqtt does a TLS handshake; http can carry an auth exchange.
# Everything else (tcp, serial, udp, osc) connects about as cheaply as any
# probe would, so probing first would just do the work twice — and on a
# single-session device, an extra socket is not free.
_PROBE_FIRST_TRANSPORTS = frozenset({"ssh", "mqtt", "http"})
_PROBE_TIMEOUT = 1.5

# A reconnect that succeeds and then drops again inside this window is
# flapping, not recovering. THIS is what backoff is for: an absent device costs
# one cheap syscall to check, but a device that connects and dies on a loop can
# genuinely hurt — so the escalating delay lives here instead of on absence,
# which is where it used to be and where it did nothing but hide recoveries.
_FLAP_WINDOW_SECONDS = 30.0
_MAX_FLAP_ESCALATIONS = 4  # 5s -> 10 -> 20 -> 40 -> 80, then holds

# Log the first few attempts, then go quiet apart from a periodic heartbeat.
# Retrying forever at INFO would be ~17k lines a day for ONE offline device,
# into both the ring buffer and the log file; the recovery is always logged.
_RECONNECT_LOG_FIRST = 3
_RECONNECT_LOG_EVERY = 60  # roughly every 5 minutes at the default interval


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.error("Unhandled exception in background task: %s", exc, exc_info=exc)


if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver


# Backstop for a test-panel pause whose owner never resumes it (tab closed or
# crashed, request lost). The panel refreshes the pause while it stays open,
# so expiry only fires for genuinely abandoned pauses — without it a paused
# production device stays offline indefinitely with auto-reconnect suppressed.
PAUSE_TTL = 600.0


# Masking a device config for a caller uses the same credential-field names as
# masking a credential out of the device log — see openavc/utils/log_redaction.py,
# which owns both. Re-exported here because get_device_info's orphaned-device
# branch is the only path that returns raw connection config, and that payload
# flows to the cloud AI (cloud/tools/device_tools.py::_get_device_info): a
# missing driver must not turn a simple get_device_info into a credential dump.
_redact_config = redact_config


class DeviceManager:
    """Manages all device driver instances."""

    def __init__(self, state: StateStore, events: EventBus):
        self.state = state
        self.events = events
        # What the loaded project says the retry interval should be, or None
        # when it says nothing. Set by the Engine wherever a project is
        # applied, so it follows a save or a cloud push without a restart.
        self.project_reconnect_interval: float | None = None
        self._devices: dict[str, BaseDriver] = {}
        self._device_configs: dict[str, dict[str, Any]] = {}
        self._reconnect_tasks: dict[str, asyncio.Task] = {}
        # Flapping bookkeeping — see _FLAP_WINDOW_SECONDS. Lives here rather
        # than in the reconnect loop because the loop EXITS on success: a
        # device that connects and drops again starts a brand new loop, so a
        # counter inside one could never see the pattern it exists to catch.
        self._last_connect_at: dict[str, float] = {}
        self._flap_counts: dict[str, int] = {}
        self._orphaned_devices: dict[str, dict[str, Any]] = {}  # devices with missing drivers
        self._intentional_disconnect: set[str] = set()  # suppress auto-reconnect
        # Registered but not yet dialed — see add_device(defer_connect=True)
        # and bring_up(). A project save registers its devices inside the
        # request and connects them after it has answered.
        self._deferred_connects: set[str] = set()
        self._pause_expiry_tasks: dict[str, asyncio.Task] = {}  # pause TTL backstops
        # When each armed pause expires (monotonic). Kept beside the task
        # because a task can't be asked how much of its sleep is left, and a
        # pause carried across an instance replacement has to carry the time
        # it had remaining rather than restart its term.
        self._pause_deadlines: dict[str, float] = {}
        # Auto-detected Open Web UI URLs, keyed by device id (see web_ui_probe).
        # Ephemeral by design — re-detected on add/connect, never persisted.
        self._detected_web_ui_urls: dict[str, str] = {}
        # In-flight web-UI probes, keyed by device id — holds a task reference
        # (so it isn't GC'd) and dedupes the add-time and connect-time triggers.
        self._web_ui_probe_tasks: dict[str, asyncio.Task] = {}
        # Set by SimulationManager while a simulated bench is running: given a
        # device id, returns its driver id when that device has no simulator
        # and so was never redirected. Such a device fails against its REAL
        # address, which classifies as a perfectly accurate connection_refused
        # and sends the author to check a port that was never the problem.
        # A callable rather than an import, so the device manager goes on
        # knowing nothing about simulation; None whenever it isn't running.
        self.unsimulated_driver: Callable[[str], str | None] | None = None

        # Auto-reconnect when a device transport drops mid-session
        self.events.on(
            "device.disconnected.*", self._on_device_disconnected
        )
        # Mirror a bridge's online state onto the bridge-routed devices bound to
        # it (an IR device on an emitter port has no transport of its own).
        self.events.on(
            "device.connected.*", self._on_device_connected
        )

    async def add_device(
        self,
        device_config: dict[str, Any],
        carry_pause: float | None = None,
        *,
        defer_connect: bool = False,
    ) -> None:
        """
        Instantiate a driver, register its state variables, and connect.

        Args:
            device_config: Dict with id, driver, name, config keys.
            carry_pause: Seconds left on a pause this device is being re-added
                under (see ``update_device``). The driver is registered but not
                connected, and the pause is re-armed for that long. Only the
                remove-and-re-add paths pass it; a genuine add is never paused.
            defer_connect: Register the device but leave it on the deferred
                list for :meth:`bring_up` to dial. The project reconcile uses
                this so a save answers before the fleet is on the wire.
        """
        device_id = device_config["id"]
        driver_id = device_config["driver"]
        name = device_config.get("name", device_id)
        # The driver gets its OWN dict, never the one we keep in
        # _device_configs. A live driver writes into its config — the
        # simulation redirect swaps host/port/transport, an inbound-push
        # driver records the listener port it bound, a YAML driver zeroes
        # poll_interval across a login — and _device_configs is what
        # Engine._sync_devices compares against the project to decide which
        # devices actually changed. Sharing one dict made every one of those
        # runtime writes read as a config edit, so the next save removed and
        # re-added the device for a change nobody made: under simulation that
        # is the WHOLE fleet on every save, which is where renaming one
        # device on a 107-device project came to cost 109 seconds.
        # routes/devices.py says the same thing about the pending-settings
        # write it makes ("a skewed copy would re-add the device on the next
        # device-section reconcile for a change that is already live").
        config = dict(device_config.get("config", {}))

        # Adding a device is a fresh start, so it must not inherit a
        # suppress-auto-reconnect mark left by whatever happened to this id
        # before — a shutdown, or a removal. remove_device says the same thing
        # about its own discard: a stale entry silently disables auto-reconnect
        # for the device added under that id next.
        self._intentional_disconnect.discard(device_id)

        enabled = device_config.get("enabled", True)
        if not enabled:
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.enabled", False, source="config")
            log.info(f"Device {device_id} is disabled, skipping connection")
            return

        # Look up driver class
        driver_class = get_driver_class(driver_id)
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

        # A pause carried across the instance swap: the registration above still
        # runs (and bring_up still preps the bridge port for whenever it
        # resumes) — only the connect is suppressed, which is the whole harm.
        # Whoever holds the pause still holds the session it was taken to
        # protect. The backstop is re-armed with the time the pause had left.
        if carry_pause is not None:
            self._intentional_disconnect.add(device_id)
            self.state.set(f"device.{device_id}.paused", True, source="device_manager")
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            self._schedule_pause_expiry(device_id, carry_pause)
            log.info(
                f"Device '{device_id}' was re-added while paused — staying "
                f"paused ({carry_pause:.0f}s left on the backstop)"
            )
            # The bridge port is prepared and the web UI probed even while
            # paused: the pause is about the control session, and
            # remove_device dropped the detected URL on the way through.
            await self._prepare_bridge_for(device_id, config)
            self._schedule_web_ui_probe(device_id)
            return

        if defer_connect:
            # Registered, not yet dialed. The caller runs bring_up() once the
            # rest of the reconcile has settled — which is what lets a project
            # save answer before the fleet is on the wire, and what lets the
            # simulation redirect land before the first connect instead of
            # after a failed one.
            self.state.set(
                f"device.{device_id}.connected", False, source="device_manager"
            )
            self._deferred_connects.add(device_id)
            return

        await self._bring_up_device(device_id)

    async def _bring_up_device(self, device_id: str) -> None:
        """Prepare the bridge port, connect, and probe for a web UI.

        The second half of ``add_device``, split out so it can run later (and
        concurrently with its peers) than the registration. Never raises: a
        failed connect is normal and is reported through ``offline_reason``
        plus the reconnect loop, exactly as it always was.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            return
        config = driver.config or {}

        # If this device routes through a bridge, let the bridge configure the
        # port (e.g. push serial baud/parity) before we open the connection, so
        # the transparent pass-through carries bytes at the right line settings.
        await self._prepare_bridge_for(device_id, config)

        # Attempt connection
        try:
            await driver.connect()
            if not await self._discard_if_superseded(device_id, driver):
                return
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
            if not await self._discard_if_superseded(device_id, driver):
                return
            log.warning(f"Failed to connect '{device_id}': {e}")
            if self._set_offline_reason(device_id, driver, exc=e) == "auth_failed":
                self._pause_reconnect_for_auth(device_id)
            else:
                self._start_reconnect(device_id)

        # Auto-detect an Open Web UI for the device. Runs whether or not the
        # control protocol connected — a device can be offline for control yet
        # still serve a reachable admin page.
        self._schedule_web_ui_probe(device_id)

    async def _discard_if_superseded(self, device_id: str, driver: Any) -> bool:
        """True if ``driver`` is still this device's instance.

        A bring-up runs outside the reconcile lock, so a second save can
        remove the device — or replace its driver — while a connect is still
        in flight. Whatever that connect opened belongs to nobody: close it,
        and leave nothing behind about a device that has moved on. Returns
        False (and disconnects) when the instance has been superseded.

        Both the connect that just ran and the disconnect below write state
        under this device's id, which is not theirs to write any more, so the
        namespace is put right afterwards: emptied when the device was removed
        outright (remove_device already cleared it once), and re-stated from
        the live instance when the device was merely replaced.
        """
        current = self._devices.get(device_id)
        if current is driver:
            return True
        log.info(
            f"Device '{device_id}' changed while it was being brought up — "
            f"discarding the connection that was in flight"
        )
        try:
            await driver.disconnect()
        except Exception:
            log.debug(f"Error discarding superseded '{device_id}'", exc_info=True)
        prefix = f"device.{device_id}."
        if device_id not in self._device_configs:
            for key in self.state.get_namespace(prefix):
                self.state.delete(f"{prefix}{key}")
        elif current is not None:
            self.state.set(
                f"{prefix}connected",
                bool(getattr(current, "_connected", False)),
                source="device_manager",
            )
        return False

    def is_connect_deferred(self, device_id: str) -> bool:
        """True while this device is registered but not yet dialed.

        Asked by anything that would otherwise reconnect a device on its
        behalf — the simulation redirect most of all, which has no reason to
        cycle a transport that has never been opened.
        """
        return device_id in self._deferred_connects

    async def bring_up(self) -> None:
        """Connect every device registered with ``defer_connect=True``.

        Bridges first, then their dependents (a bridge-bound device's port prep
        needs its bridge live), and concurrently within each batch — sequential
        awaits here would serialize one connect timeout per device, which is
        the cost this whole split exists to take out of the save.
        """
        pending = [d for d in self._deferred_connects if d in self._devices]
        self._deferred_connects.clear()
        if not pending:
            return
        configs = {
            did: self._device_configs[did]
            for did in pending
            if did in self._device_configs
        }
        for batch in bridge_first(list(configs), configs):
            results = await asyncio.gather(
                *(self._bring_up_device(did) for did in batch),
                return_exceptions=True,
            )
            for did, result in zip(batch, results):
                if isinstance(result, Exception):
                    log.error(f"Bringing up device '{did}' failed: {result}")

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
        # A device removed before the deferred bring-up reached it must not be
        # dialed afterwards — bring_up re-checks the registry, but dropping the
        # entry here keeps a removed id from surviving in the set at all.
        self._deferred_connects.discard(device_id)
        # Forget the flap history too — a device re-added under the same id is
        # a fresh device, and inheriting a backoff it never earned would make
        # its first reconnect mysteriously slow.
        self._last_connect_at.pop(device_id, None)
        self._flap_counts.pop(device_id, None)
        # Forget this device's credentials, so a password that is no longer
        # configured anywhere stops masking text in an unrelated device's log.
        get_secret_registry().forget(device_id)

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

    async def update_device(
        self,
        device_id: str,
        new_config: dict[str, Any],
        *,
        defer_connect: bool = False,
    ) -> None:
        """
        Update a device by disconnecting and re-adding with new config.

        Handles both active devices and orphaned devices (driver reassignment).

        A pause survives the swap. The pause is owned by whoever took it — the
        driver test panel, still holding the competing session — and is released
        by them or by its own TTL. A project save is neither, so reconnecting
        here would drop a production device back onto a port under test without
        anyone asking for it, which is the collision the pause exists to
        prevent. Captured before the removal, which deletes the state key.

        Args:
            device_id: The existing device ID.
            new_config: Full device config dict (id, driver, name, config).
            defer_connect: Passed through to :meth:`add_device` — leave the
                re-added device for :meth:`bring_up` to dial.
        """
        carry_pause = self._pause_remaining(device_id)
        if device_id in self._devices or device_id in self._orphaned_devices:
            await self.remove_device(device_id)
        elif device_id in self._device_configs:
            # Disabled device — just clean up config
            self._device_configs.pop(device_id, None)
        else:
            raise ValueError(f"Device '{device_id}' not found")
        # A device the edit disables has no instance to resume into, so the
        # pause has nothing left to protect: let add_device take its disabled
        # path and leave the backstop cancelled.
        if not new_config.get("enabled", True):
            carry_pause = None
        await self.add_device(
            new_config, carry_pause=carry_pause, defer_connect=defer_connect
        )

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
        # Before the connected-gate on purpose: a command name the driver does
        # not declare is wrong whether or not the device happens to be online,
        # and "Device 'x' is not connected" would send the author to look at
        # the network instead of at the name they typed.
        self._check_command_declared(driver, device_id, command)
        # The connected-gate is skipped for commands the driver declares
        # available_offline — a handler that needs no live connection (e.g. a
        # Wake-on-LAN power_on) so a macro, panel button, or schedule can wake a
        # device that has gone fully off the network. Param validation below
        # still runs for every command.
        if not driver.get_state("connected") and not self._command_available_offline(
            driver, command
        ):
            # Someone just asked for this device by name. That is the clearest
            # signal we ever get that it's wanted back, and most of the people
            # who send it are standing at a panel with no access to the IDE —
            # so retry now instead of leaving them to wait out the interval.
            self.kick_reconnect(device_id)
            raise not_connected(device_id)
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
    def _check_command_declared(
        driver: BaseDriver, device_id: str, command: str
    ) -> None:
        """Refuse a command the driver's own ``commands`` block never declares.

        Nothing checked this, and the two driver formats failed differently
        while looking the same from outside: a YAML driver logged
        ``Unknown command: <name>`` and returned success, and a Python driver
        raised whatever its handler raised — usually a ValueError, which the
        REST layer above turned into ``Device '<id>' not found`` on a device
        that was connected and rendering. Either way the author was pointed
        away from the typo. A Quick Action is the common route in, because an
        ``actions`` entry may name a command that does not exist (the catalog
        rejects that now; a driver dropped straight into ``driver_repo/`` only
        warns at load, so the button still renders).

        Reads the *instance* DRIVER_INFO, so a driver that builds its command
        set at runtime — discovered controls, channel strips, the inline
        protocol generics merging a device's own configured commands — is
        judged on what it actually declares by the time the command is sent.

        Enforced only when that set is a **non-empty dict**: 20 of the 92
        shipped drivers declare no literal ``commands`` block at all and hand
        every name straight to their own dispatch, and an empty set means
        "this driver did not tell us", not "this driver has no commands".
        """
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        commands = info.get("commands")
        if not isinstance(commands, dict) or not commands:
            return
        if command not in commands:
            raise UnknownCommandError(
                f"Command '{command}' not found on device '{device_id}'"
            )

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

        Runs when the caller supplied no params at all, not only when it
        supplied some: a command invoked with nothing is precisely the case a
        missing ``required`` param has to be caught in, and the early return on
        falsy params is why it never was.
        """
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
        types pass through untouched — the driver receives what the caller
        sent, and only the integer kind has a wire form to normalize.

        The coercion rule itself lives in ``drivers/child_ids``; this method
        owns which params it applies to and what a failure means here.
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
            if value is None:
                continue
            declared_type = pdef.get("child_type")
            # A child_type naming a type the driver never defined used to fall
            # through to the default integer kind and be reported as a bad
            # VALUE — "'component' must be a child id number, got 'Pgm_Gain'"
            # — when the value was fine and the driver's own declaration was
            # the typo. Say which declaration is wrong, in the same sentence
            # the catalog, the file checker, openavc.simulator.validate and the loader
            # already use for the static form of this fault.
            if isinstance(declared_type, str) and declared_type not in child_types:
                raise CommandParamError(
                    f"'{command}': '{name}': "
                    + undeclared_child_type_reason(declared_type, child_types)
                )
            type_def = child_types.get(declared_type)
            if child_id_kind(type_def) != "integer":
                continue
            coerced = coerce_child_local_id(type_def, value)
            if coerced is None:
                raise CommandParamError(
                    f"'{command}': '{name}' must be a child id number, "
                    f"got {value!r}"
                )
            # Coercion proves the id is the right KIND; the declared range is
            # a separate fact and used to go unchecked. An out-of-range id
            # reached the wire and the command reported success — the device
            # answered with its own error and nothing surfaced it. Enforced
            # here beside the min/max checks the same gate already runs for
            # ordinary numeric params, so a child id is no longer the one
            # parameter kind whose declared bounds mean nothing.
            range_error = child_id_range_error(type_def, coerced)
            if range_error is not None:
                raise CommandParamError(
                    f"'{command}': '{name}' {range_error}, got {coerced}"
                )
            out[name] = coerced
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

        from openavc.drivers.actions import resolve_device_actions

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

    def merge_live_config(self, device_id: str, delta: dict[str, Any]) -> None:
        """Apply a connection/protocol delta to the live driver AND to the
        stored config the reconcile compares against.

        Both halves or neither. The driver's copy is what the next connect
        dials; the stored copy is what ``Engine._sync_devices`` compares to
        the project, so a caller that writes a setting straight into the
        project has to move this one too — otherwise the very next reconcile
        reads the skew as an edit and tears the device down for a change that
        is already live. (A setup action does exactly that mid-wizard, which
        would invalidate the running handler's ``self``.)
        """
        driver = self._devices.get(device_id)
        if driver is not None and driver.config is not None:
            driver.config.update(delta)
        stored = self._device_configs.get(device_id)
        if stored is not None:
            stored.setdefault("config", {}).update(delta)

    async def retry_orphaned_device(
        self, device_id: str, *, defer_connect: bool = False
    ) -> bool:
        """Re-attempt adding an orphaned device (e.g., after installing its driver).

        Returns True if the device was successfully activated, False if still orphaned.
        """
        if device_id not in self._orphaned_devices:
            raise ValueError(f"Device '{device_id}' is not orphaned")

        config = self._orphaned_devices[device_id]
        driver_id = config.get("driver", "")

        # Check if the driver is now available
        if not is_driver_registered(driver_id):
            return False

        # Remove from orphan tracking and re-add normally
        await self.remove_device(device_id)
        await self.add_device(config, defer_connect=defer_connect)
        return device_id not in self._orphaned_devices

    async def retry_all_orphans(self, *, defer_connect: bool = False) -> list[str]:
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
            if not is_driver_registered(driver_id):
                continue
            try:
                ok = await self.retry_orphaned_device(
                    device_id, defer_connect=defer_connect
                )
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
            raise not_connected(device_id)

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
                # Same rule as update_device: a driver save must not reconnect a
                # device the test panel has paused. This is the likelier of the
                # two collisions — saving the driver from the Builder is what
                # the panel's own user does next.
                carry_pause = self._pause_remaining(device_id)
                await self.remove_device(device_id)
                await self.add_device(config, carry_pause=carry_pause)
                reconnected.append(device_id)
                log.info(
                    f"Reconnected device '{device_id}' after driver reload"
                    if carry_pause is None
                    else f"Device '{device_id}' picked up the reloaded driver "
                         f"and stayed paused"
                )
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

        # Mark every device as an intentional disconnect BEFORE closing it.
        # driver.disconnect() closes the transport, whose drop callback emits
        # device.disconnected.<id>, which _on_device_disconnected reads as a
        # device that fell over and answers by starting a reconnect loop — so
        # without this, shutting down starts one loop per device, right after
        # the cancel pass above cleared them. Nothing normally notices because
        # the only caller is Engine.stop() and the process exits behind it;
        # a stop that isn't followed by process exit leaves the loops retrying
        # for about an hour. This is the set that exists for exactly this. It
        # is not cleared afterwards, because the transport's drop callback
        # emits its event through a scheduled task that can land well after
        # this method returns; add_device clears the mark instead, so a manager
        # that is started again reconnects normally.
        self._intentional_disconnect.update(self._devices)

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

        # A device a running simulation could not simulate is failing against
        # its real address, so every signal below is about a socket that was
        # never meant to be reached. Answer the question the author is actually
        # asking before the classifier answers a different one correctly.
        gap = self.unsimulated_driver(device_id) if self.unsimulated_driver else None
        fault = no_simulator_fault(gap) if gap else None
        if fault is None:
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
        from openavc.core.web_ui_probe import probe_web_ui

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
        from openavc.core.connection_fault import bridge_offline_fault

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
        self._note_flap(device_id)
        task = asyncio.create_task(self._reconnect_loop(device_id))
        task.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = task

    def _note_flap(self, device_id: str) -> None:
        """Count this drop as a flap if the device only just came up.

        Called as a reconnect loop starts, which is the one place that sees
        both halves of the pattern: how long ago we last got this device
        online, and the fact that it has fallen over again.
        """
        last = self._last_connect_at.get(device_id)
        if last is not None and (time.monotonic() - last) < _FLAP_WINDOW_SECONDS:
            self._flap_counts[device_id] = self._flap_counts.get(device_id, 0) + 1
            log.warning(
                "[%s] Dropped again %.1fs after connecting — backing off "
                "(flap %d)", device_id, time.monotonic() - last,
                self._flap_counts[device_id],
            )
        else:
            self._flap_counts.pop(device_id, None)

    def _reconnect_delay(self, device_id: str, attempt: int) -> float:
        """Seconds to wait before ``attempt`` (0-indexed), jittered.

        Short ramp to catch a blip, then the steady interval forever. The only
        thing that stretches it is flapping, never absence.
        """
        if attempt < len(_RECONNECT_RAMP_SECONDS):
            base = _RECONNECT_RAMP_SECONDS[attempt]
        else:
            base = self._reconnect_interval()
        flaps = min(self._flap_counts.get(device_id, 0), _MAX_FLAP_ESCALATIONS)
        base *= 2 ** flaps
        return base * (1.0 + random.uniform(-_RECONNECT_JITTER, _RECONNECT_JITTER))

    def _reconnect_interval(self) -> float:
        """The configured steady-state retry interval, in seconds.

        The PROJECT wins where it says anything, because the rate of
        connection attempts an IT department sees is a property of the site
        rather than of one box -- so a customer sets it once and deploys it to
        every panel. A project that says nothing (the normal case) defers to
        this instance's own system.json, which is where the setting lived
        before and still works for a box configured by hand.
        """
        if self.project_reconnect_interval is not None:
            try:
                return min(max(float(self.project_reconnect_interval), 1.0), 300.0)
            except (TypeError, ValueError):
                pass
        try:
            from openavc.system_config import get_system_config

            value = float(
                get_system_config().get(
                    "devices", "reconnect_interval_seconds",
                    _RECONNECT_INTERVAL_DEFAULT,
                )
            )
        except Exception:
            return _RECONNECT_INTERVAL_DEFAULT
        # A zero or negative interval would spin; an absurdly large one would
        # silently recreate the problem this whole loop exists to fix.
        return min(max(value, 1.0), 300.0)

    async def _probe_reachable(self, device_id: str, driver: BaseDriver) -> bool:
        """Cheap "is it back?" check, for transports whose connect is costly.

        Returns True when the expensive connect is worth attempting — which
        includes every case where we can't cheaply tell, so a probe failure
        never becomes the reason a device stays offline.
        """
        host, port, transport = self._connection_descriptor(driver)
        if (transport or "").lower() not in _PROBE_FIRST_TRANSPORTS:
            return True
        if not host or port in (None, "", 0):
            return True
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, int(port)), timeout=_PROBE_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError, ValueError):
            return False
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True

    def kick_reconnect(self, device_id: str) -> bool:
        """Retry this device now instead of waiting out the interval.

        The customer standing at the panel pressing a button on a dead device
        is the strongest "I want this back" signal the system gets, and it used
        to be thrown away. Cancels the sleeping loop and starts a fresh one, so
        the next attempt is immediate. Does nothing for a device that isn't
        retrying (connected, disabled, or deliberately stopped) — a permanent
        fault still needs its human. Returns True if an attempt was triggered.
        """
        if device_id not in self._devices:
            return False
        if device_id not in self._reconnect_tasks:
            return False
        task = self._reconnect_tasks.pop(device_id)
        task.cancel()
        fresh = asyncio.create_task(self._reconnect_loop(device_id, immediate=True))
        fresh.add_done_callback(_log_task_exception)
        self._reconnect_tasks[device_id] = fresh
        return True

    async def _cancel_reconnect(self, device_id: str) -> None:
        """Cancel a running reconnect task and wait for it to finish."""
        task = self._reconnect_tasks.pop(device_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _reconnect_loop(self, device_id: str, immediate: bool = False) -> None:
        """Reconnect a disconnected device, for as long as it takes.

        A short ramp (1s, 2s) then the steady interval, jittered, with **no
        attempt ceiling** — see the retry-policy constants above for why the
        old ~1 hour cap was the wrong answer for fixed AV installations. The
        only exits are success, the device going away, a permanent fault, and
        cancellation.

        ``immediate`` skips the first wait: used by ``kick_reconnect`` when a
        human has just asked for this device by pressing something.
        """
        # This loop is spawned from inside the disconnect emit chain but outlives
        # it, so it must not keep being charged to it — see detach_emit_chain.
        detach_emit_chain()
        attempt = 0
        permanent_attempts = 0

        try:
            while True:
                # Check device still exists before each attempt
                driver = self._devices.get(device_id)
                if driver is None:
                    log.debug(f"[{device_id}] Device removed, stopping reconnect")
                    return

                # Quiet by construction: the first few attempts are the
                # interesting ones, then a heartbeat. This loop can run for
                # days, so a line (and a state write, which fans out over the
                # WebSocket to every panel) per attempt would be pure noise
                # for a number nothing counts down from any more.
                noisy = attempt < _RECONNECT_LOG_FIRST or attempt % _RECONNECT_LOG_EVERY == 0
                if noisy:
                    self.state.set(
                        f"device.{device_id}.reconnect_attempt", attempt + 1,
                        source="device_manager",
                    )
                if attempt == 0 and immediate:
                    delay = 0.0
                else:
                    delay = self._reconnect_delay(device_id, attempt)
                    if noisy:
                        log.info(
                            "[%s] Reconnect attempt %d in %.1fs...",
                            device_id, attempt + 1, delay,
                        )
                    await asyncio.sleep(delay)

                # Re-check after sleep — device may have been removed
                if device_id not in self._devices:
                    log.debug(f"[{device_id}] Device removed during wait, stopping reconnect")
                    return

                # Stop polling before anything else touches the transport.
                # This sits ahead of the probe on purpose: the probe can skip
                # the rest of the cycle, and a poll loop left running against a
                # dead transport would then never be stopped at all — which,
                # now that the loop has no ceiling, means never.
                try:
                    await driver.stop_polling()
                except Exception:
                    log.debug(f"[{device_id}] stop_polling failed", exc_info=True)

                # Cheap reachability first for the transports whose connect is
                # expensive. A device that is still absent costs one short
                # socket here instead of an ssh subprocess every few seconds.
                if not await self._probe_reachable(device_id, driver):
                    # The control port didn't answer, so the expensive connect
                    # would only fail slower. Still classify — skipping the
                    # attempt must not leave the device card showing a stale
                    # reason for a device that is now simply not there.
                    self._set_offline_reason(
                        device_id, driver,
                        exc=ConnectionFaultError("", code=UNREACHABLE),
                    )
                    attempt += 1
                    continue

                try:
                    self._refresh_usb_serial_port(device_id, driver)
                    await driver.connect()
                    log.info(f"[{device_id}] Reconnected successfully")
                    self._last_connect_at[device_id] = time.monotonic()
                    self._clear_offline_reason(device_id)
                    self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
                    await self._apply_pending_settings(device_id)
                    return
                except Exception as e:
                    if noisy:
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
                        # can briefly present one of these — then stop, since
                        # nothing about waiting longer will clear them.
                        permanent_attempts += 1
                        if permanent_attempts >= _MAX_PERMANENT_FAULT_ATTEMPTS:
                            self._stop_reconnect_for_permanent_fault(device_id, code)
                            return
                    else:
                        permanent_attempts = 0
                    attempt += 1
        except asyncio.CancelledError:
            log.debug(f"[{device_id}] Reconnect cancelled")
        finally:
            # kick_reconnect replaces the entry before cancelling this task, so
            # only clear it if it is still ours — otherwise the cancelled loop
            # would delete the fresh one on its way out and leave the device
            # with no retry at all.
            if self._reconnect_tasks.get(device_id) is asyncio.current_task():
                self._reconnect_tasks.pop(device_id, None)

    def _refresh_usb_serial_port(self, device_id: str, driver) -> None:
        """Re-resolve a usb_serial-bound adapter to its current OS path.

        A replug can move the adapter (ttyUSB0 -> ttyUSB1) while the stored
        path goes stale; the stable USB serial number is the truth, so each
        reconnect attempt follows the adapter instead of redialing the old
        path. No-op for anything but a local usb_serial-bound serial device.
        """
        try:
            from openavc.transport.serial_transport import resolve_usb_binding

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
        self._pause_deadlines[device_id] = time.monotonic() + ttl

    def _cancel_pause_expiry(self, device_id: str) -> None:
        self._pause_deadlines.pop(device_id, None)
        task = self._pause_expiry_tasks.pop(device_id, None)
        if task is not None:
            task.cancel()

    def is_paused(self, device_id: str) -> bool:
        """True while this device is held disconnected by a pause.

        One reader for the flag, so a caller deciding whether it may reconnect
        a device asks the same question the pause itself answers.
        """
        return bool(self.state.get(f"device.{device_id}.paused"))

    def _pause_remaining(self, device_id: str) -> float | None:
        """Seconds left on this device's pause backstop, or None if unpaused.

        Never negative: a deadline already past means the backstop is mid-fire,
        and a carried pause of zero resumes immediately — which is what would
        have happened anyway.
        """
        deadline = self._pause_deadlines.get(device_id)
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    async def _pause_expiry(self, device_id: str, ttl: float) -> None:
        try:
            await asyncio.sleep(ttl)
            # Drop our own registration BEFORE resuming, so resume_device's
            # _cancel_pause_expiry doesn't cancel the very task running it.
            self._pause_expiry_tasks.pop(device_id, None)
            self._pause_deadlines.pop(device_id, None)
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
                self._pause_deadlines.pop(device_id, None)

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
