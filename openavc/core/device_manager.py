"""
OpenAVC DeviceManager — manages all device driver instances.

Handles:
- Instantiating drivers from project config
- Connection lifecycle (connect, reconnect on failure, disconnect)
- Routing commands to the correct device
- Exposing device metadata

Its other jobs live in their own modules, mixed in so every name stays on the
manager: retrying a dropped device (device_reconnect.py), saying why a device
is away (device_away.py), queued settings (pending_settings.py), the Open Web
UI page (device_web_ui.py), devices on a bridge port (device_bridges.py), a
device paused on purpose (device_pause.py), devices waiting for their driver
(device_orphans.py) and what the manager says about its devices
(device_info.py). The checks a command passes before its driver are
core/command_gates.py, and what the doors raise is core/device_errors.py,
whose names this module re-exports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from openavc.core.connection_fault import (
    ConnectionFault,
    no_simulator_fault,
)
from openavc.core.command_gates import (
    available_offline,
    check_command_declared,
    check_file_door,
    coerce_child_id_params,
    send_file_command,
    validate_command_params,
)
from openavc.core.device_away import DeviceAwayMixin
from openavc.core.device_bridges import DeviceBridgesMixin
from openavc.core.device_errors import (  # noqa: F401  (re-exported: the path the platform imports)
    DeviceNotFoundError,
    NoSimulatorError,
    device_asleep,
    device_restarting,
    failed_command,
    mark_failed_command,
    not_connected,
)
from openavc.core.device_info import DeviceInfoMixin
from openavc.core.device_orphans import DeviceOrphansMixin
from openavc.core.device_pause import (  # noqa: F401  (the constants re-exported)
    PAUSE_TTL,
    PAUSED_FOR_AUDIT,
    PAUSED_FOR_LIVE_TEST,
    DevicePauseMixin,
)
from openavc.core.device_reconnect import DeviceReconnectMixin
from openavc.core.device_web_ui import DeviceWebUiMixin
from openavc.core.pending_settings import PendingSettingsMixin
from openavc.drivers.base import (
    ConnectEndedError,
    UnknownDeviceSettingError,
    validate_device_setting_value,
)
from openavc.drivers.registry import get_driver_class
from openavc.core.device_config import bridge_first
from openavc.core.event_bus import EventBus
from openavc.core.installed_equipment import driver_label
from openavc.core.state_store import StateStore
from openavc.core.device_traffic import get_traffic_recorder
from openavc.utils.log_redaction import get_secret_registry
from openavc.utils.logger import get_logger

log = get_logger(__name__)


if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver


def _device_id_of(event: str) -> str | None:
    """The device id that ends a ``device.<what>.<id>`` event name."""
    parts = event.split(".", 2)
    return parts[2] if len(parts) == 3 else None


class DeviceManager(
    DeviceAwayMixin, DeviceReconnectMixin, PendingSettingsMixin, DeviceWebUiMixin,
    DeviceBridgesMixin, DevicePauseMixin, DeviceOrphansMixin, DeviceInfoMixin,
):
    """Manages all device driver instances."""

    def __init__(self, state: StateStore, events: EventBus):
        self.state = state
        self.events = events
        self.installed_equipment = None
        # What the loaded project says the retry interval should be, or None
        # when it says nothing. Set by the Engine wherever a project is
        # applied, so it follows a save or a cloud push without a restart.
        self.project_reconnect_interval: float | None = None
        self._devices: dict[str, BaseDriver] = {}
        self._device_configs: dict[str, dict[str, Any]] = {}
        self._init_reconnect()
        self._init_orphans()
        self._intentional_disconnect: set[str] = set()  # suppress auto-reconnect
        # Registered but not yet dialed — see add_device(defer_connect=True)
        # and bring_up(). A project save registers its devices inside the
        # request and connects them after it has answered.
        self._deferred_connects: set[str] = set()
        self._init_pause()
        self._init_web_ui()
        self._init_pending()
        self._init_away()
        # Set by SimulationManager while a simulated bench is running: given a
        # device id, returns its driver id when that device has no simulator
        # and so was never redirected. Such a device fails against its REAL
        # address, which classifies as a perfectly accurate connection_refused
        # and sends the author to check a port that was never the problem.
        # A callable rather than an import, so the device manager goes on
        # knowing nothing about simulation; None whenever it isn't running.
        self.unsimulated_driver: Callable[[str], str | None] | None = None
        # Set by the engine to SimulationManager.is_redirected: given a device
        # id, True while that device connects to the simulator rather than to
        # its own address. Asked before probing for a web page; None in a bare
        # manager.
        self.redirected_to_simulator: Callable[[str], bool] | None = None
        # Set by the engine to SimulationManager.real_connection_of: given a
        # device id, the connection settings the simulation redirect replaced,
        # or None when it is not redirected. Open Web UI links are built from
        # them; None in a bare manager.
        self.real_connection: Callable[[str], dict[str, Any] | None] | None = None
        # Set by the engine to its bring-up scheduler. reload_driver re-adds a
        # driver's devices registered-but-not-dialed and asks for a round, so
        # the engine puts the simulation redirect in place BEFORE the first
        # connect, the same order a project save gets; without it (tests, a
        # bare manager) reload_driver dials the devices itself.
        self.request_bringup: Callable[[], None] | None = None
        # Set by the engine: called after reload_driver, since a new version
        # of a driver can listen for pushes on a port the old one did not
        # (core/push_ports.py). None in a bare manager.
        self.on_driver_reloaded: Callable[[], None] | None = None

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
        carry_pause_for: str | None = None,
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
            carry_pause_for: Who held that pause (``paused_for``), carried with it.
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

        if not device_config.get("enabled", True):
            self._register_disabled(device_id, device_config, name)
            return
        driver_class = get_driver_class(driver_id)
        if driver_class is None:
            await self._register_orphan(device_id, device_config, name)
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

        self._attach_bridge_router(driver, config)

        # Set device name in state
        self.state.set(
            f"device.{device_id}.name", name, source=f"device.{device_id}"
        )

        self.state.set(f"device.{device_id}.enabled", True, source="config")
        log.info(f"Added device '{device_id}' ({name}) using {driver_label(driver_id)}")

        if carry_pause is not None:
            # A device re-added while paused stays paused: registered, its
            # bridge port prepared and its web page probed, but not connected,
            # which is the whole harm. remove_device dropped the detected page
            # on the way through.
            self._hold_carried_pause(device_id, carry_pause, carry_pause_for)
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

    def _register_disabled(
        self, device_id: str, device_config: dict[str, Any], name: str,
    ) -> None:
        """Keep a disabled device's record and publish it, connecting nothing."""
        self._device_configs[device_id] = device_config
        self.state.set(f"device.{device_id}.name", name, source="config")
        self.state.set(f"device.{device_id}.connected", False, source="config")
        self.state.set(f"device.{device_id}.enabled", False, source="config")
        log.info(f"Device {device_id} is disabled, skipping connection")


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
        # Attempt connection
        try:
            self._require_control_target(device_id)
            await self._prepare_bridge_for(device_id, config)
            await self._connect_driver(device_id, driver)
            if not await self._discard_if_superseded(device_id, driver):
                return
            # Apply pending settings after successful connect
            await self._apply_pending_settings(device_id)
            self._note_bridge_offline_if_down(device_id, driver, config)
        except ConnectEndedError:
            # Ended by a Reconnect, a pause or a removal while it waited: the
            # door that ended it owns the device now.
            if not await self._discard_if_superseded(device_id, driver):
                return
        except Exception as e:
            if not await self._discard_if_superseded(device_id, driver):
                return
            code = self._set_offline_reason(device_id, driver, exc=e)
            log.warning(f"Failed to connect '{device_id}': {self._failure_text(device_id, e)}")
            if not self._paused_for_refused_login(device_id, code):
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
        self._forget_flap_history(device_id)
        # Same reasoning for a restart window and a sleep: a device re-added
        # under this id while the old one's window was open, or while it
        # slept, would inherit a suppression it never asked for.
        self._forget_away(device_id)
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
        # And its recent traffic, after the disconnect's last bytes: a device
        # re-added under this id starts a fresh record (a reader still
        # subscribed keeps its subscription).
        get_traffic_recorder().forget(device_id)

        self._device_configs.pop(device_id, None)
        # Drop the detected web UI URL so a re-add under the same id re-detects
        # (config may have changed the host/scheme), and cancel any in-flight probe.
        self._forget_web_ui(device_id)
        # Nothing left to confirm a queued write against — the driver, its
        # state keys and the queue itself all go with the device.
        self._cancel_pending_confirm(device_id)

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

        A pause survives the swap (:meth:`_pause_to_carry`).

        Args:
            device_id: The existing device ID.
            new_config: Full device config dict (id, driver, name, config).
            defer_connect: Passed through to :meth:`add_device` — leave the
                re-added device for :meth:`bring_up` to dial.
        """
        carry_pause, carry_pause_for = self._pause_to_carry(device_id)
        if device_id in self._devices or device_id in self._orphaned_devices:
            await self.remove_device(device_id)
        elif device_id in self._device_configs:
            # Disabled device — just clean up config
            self._device_configs.pop(device_id, None)
        else:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        # A device the edit disables has no instance to resume into, so the
        # pause has nothing left to protect: let add_device take its disabled
        # path and leave the backstop cancelled.
        if not new_config.get("enabled", True):
            carry_pause = None
        await self.add_device(
            new_config, carry_pause=carry_pause, carry_pause_for=carry_pause_for,
            defer_connect=defer_connect,
        )

    async def send_command(
        self,
        device_id: str,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        files_allowed: bool = False,
    ) -> Any:
        """Send a command to a device by ID.

        Any exception raised by the driver's ``send_command`` is published as
        ``device.error.<device_id>`` (payload: ``{"device_id", "error"}``) and
        then re-raised. Transport-level loss is reported separately as
        ``device.disconnected.<device_id>`` from the transport callback; the
        two events are complementary — see ``event_bus.py`` for the policy.

        ``files_allowed`` is the caller saying it can give and take files:
        Send Command's background runs (``core/command_runs.py``) and the
        Device Audit. A command that takes a file or hands one back
        (``spec.is_file_command``) is refused everywhere else with
        :class:`FileCommandError`; this method is the one gate every door
        passes, so no door can run one by accident.
        """
        driver = self._live_driver(device_id)
        # Before the connected-gate on purpose: a command name the driver does
        # not declare is wrong whether or not the device happens to be online,
        # and "Device 'x' is not connected" would send the author to look at
        # the network instead of at the name they typed.
        check_command_declared(driver, device_id, command)
        # The same for a file command sent from a door with no file: the
        # sentence that helps is where it runs from, not that the device is
        # offline.
        file_command = check_file_door(driver, command, params, files_allowed)
        self._require_control_target(device_id)
        # The connected-gate is skipped for commands the driver declares
        # available_offline — a handler that needs no live connection (e.g. a
        # Wake-on-LAN power_on) so a macro, panel button, or schedule can wake a
        # device that has gone fully off the network. Param validation below
        # still runs for every command. A connection held offline until the
        # device answers is open, so it takes commands (accepts_commands).
        if not self.command_goes_out(device_id, command):
            # Someone just asked for this device by name. That is the clearest
            # signal we ever get that it's wanted back, and most of the people
            # who send it are standing at a panel with no access to the IDE —
            # so retry now instead of leaving them to wait out the interval.
            self.kick_reconnect(device_id)
            raise self._offline_refusal(device_id)
        try:
            params = coerce_child_id_params(driver, command, params)
            params = validate_command_params(driver, command, params)
            if file_command is None:
                result = await driver.send_command(command, params)
            else:
                result = await send_file_command(driver, device_id, file_command, params)
        except Exception as exc:
            mark_failed_command(exc, device_id, command)
            await self.events.emit(
                f"device.error.{device_id}",
                {"device_id": device_id, "error": str(exc)},
            )
            raise
        # Armed only on a send that did not raise. A command that never left
        # the building has not restarted anything, and arming on the attempt
        # would suppress the very fault that stopped it.
        self._note_command_sent(device_id, driver, command)
        return result

    def _offline_refusal(self, device_id: str) -> ConnectionError:
        """Why a command to this device cannot go out now.

        Inside a commanded-restart window the refusal is the same refusal but a
        different sentence: "not connected" sends the reader to look at the
        device, and the honest instruction there is to wait. The same for a
        device we put to sleep, which its wake command brings back.
        """
        remaining = self.restart_seconds_left(device_id)
        if remaining is not None:
            return device_restarting(device_id, remaining)
        sleep = self._sleep.get(device_id)
        if sleep is not None and sleep.asleep:
            return device_asleep(device_id, sleep.wake_label)
        return not_connected(device_id)

    def command_works_offline(self, device_id: str, command: Any) -> bool:
        """Whether ``command`` goes out to ``device_id`` with no live
        connection (``available_offline``). False for an unknown device or
        command. Asked by ui/action_devices.py for the panel's press marks."""
        driver = self._devices.get(device_id)
        if driver is None or not isinstance(command, str):
            return False
        return available_offline(driver, command)

    def accepts_commands(self, device_id: str) -> bool:
        """Whether a command sent to this device now would go out.

        True while it is connected, and also while its connection is open but
        held offline until it answers a poll or a liveness probe
        (BaseDriver._held, after the poll loop's silence check or the probe
        dropped it). A device that goes quiet in standby is held
        like that, and the Power On that wakes it has to reach it: refusing
        it there would leave the device off for good. The one rule for the
        setting gate; a command asks command_goes_out, which adds the
        commands that need no connection.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            # A device with no live driver (disabled, orphaned) has nothing to
            # send through; its published state is the whole answer.
            return bool(self.state.get(f"device.{device_id}.connected"))
        if driver.get_state("connected"):
            return True
        return getattr(driver, "_held", False) is True

    def command_goes_out(self, device_id: str, command: str) -> bool:
        """Whether this command sent to this device now would go out.

        The device accepts commands (accepts_commands), or the command
        declares available_offline. The one rule for send_command's gate, a
        macro's "skip if offline" and a group command's offline skip, so a
        skip never drops a command the send would have let through: the
        Wake-on-LAN Power On is needed exactly while the device is offline.
        """
        if self.accepts_commands(device_id):
            return True
        driver = self._devices.get(device_id)
        return driver is not None and available_offline(driver, command)


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


    async def set_device_setting(
        self, device_id: str, key: str, value: Any
    ) -> Any:
        """Set a device setting value on a device by ID."""
        driver = self._live_driver(device_id)
        self._require_control_target(device_id)
        if not self.accepts_commands(device_id):
            raise not_connected(device_id)

        # Validate the setting exists
        settings = driver.DRIVER_INFO.get("device_settings", {})
        if key not in settings:
            raise UnknownDeviceSettingError(
                f"Unknown device setting '{key}' for device '{device_id}'"
            )

        # Runtime value gate — the IDE editor's min/max/values/regex checks
        # are an authoring aid; scripts, macros, cloud, and raw REST bypass
        # them, so the write is validated (and coerced to the declared type)
        # here regardless of caller.
        value = validate_device_setting_value(key, settings[key], value)

        result = await driver.set_device_setting(key, value)
        # A live write settles the question the queue was holding. Without
        # this, a queued value the device never confirmed stays in the project
        # and is re-sent on the next connect — overwriting what was just set
        # here, days later, for no reason anyone could see.
        await self._clear_applied_pending(device_id, [key], {})
        return result

    async def await_setting_readback(
        self, device_id: str, key: str, expected: Any, *, timeout: float | None = None,
    ) -> tuple[bool, Any]:
        """Wait for a device setting just written to read back.

        The rule a queued write is confirmed by (:func:`_readback_confirms`
        within :meth:`_confirm_window`), for a caller that wrote the setting
        live and wants to know the same thing: ``(True, value)`` once the
        device reports ``expected`` (in its own form, if it likes), else
        ``(False, last value)`` when the window closes, the device drops or it
        goes. ``expected`` is the value as written, after
        ``validate_device_setting_value``. ``timeout`` shortens the window.

        A setting whose ``state_key`` is not a declared state variable has
        nothing to read back: ``(False, None)`` at once.
        """
        self._live_driver(device_id)
        return await self._wait_for_readback(device_id, key, expected, timeout)

    def _live_driver(self, device_id: str) -> BaseDriver:
        """The device's live driver, for a door that acts on it.

        :class:`DeviceNotFoundError` when no device by that id has one: never
        added, removed, disabled, or waiting for its driver to be installed.
        """
        driver = self._devices.get(device_id)
        if driver is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        return driver

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
        driver = self._live_driver(device_id)

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
        from openavc.drivers.registry import is_reserved_driver
        if is_reserved_driver(driver_id):
            from openavc.core.installed_equipment import InstalledEquipmentError
            raise InstalledEquipmentError("The manufacturer manages this integration. Use Reconnect to restore its connection.")
        reconnected = await self._readd_for_new_driver(driver_id)
        if reconnected:
            if self.request_bringup is not None:
                self.request_bringup()
            else:
                await self.bring_up()
        reconnected += await self._activate_orphans(
            lambda waiting_for: waiting_for == driver_id, note="after driver reload",
        )
        if self.on_driver_reloaded is not None:
            self.on_driver_reloaded()
        return reconnected

    async def _readd_for_new_driver(self, driver_id: str) -> list[str]:
        """Remove and re-add every live device on ``driver_id`` so it picks up
        the reloaded class, a paused one staying paused. The ids re-added; a
        device that fails is logged and the rest still run.

        Registered, not dialed: the caller asks for the bring-up round, which
        the engine runs after re-applying any simulation redirect to the new
        instance. Dialing here sent a simulated device to its REAL address on
        every driver update.
        """
        affected = [
            (did, cfg)
            for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id and did in self._devices
        ]
        readded: list[str] = []
        for device_id, config in affected:
            try:
                carry_pause, carry_pause_for = self._pause_to_carry(device_id)
                await self.remove_device(device_id)
                await self.add_device(
                    config, carry_pause=carry_pause, carry_pause_for=carry_pause_for,
                    defer_connect=True,
                )
                readded.append(device_id)
                log.info(
                    f"Reconnected device '{device_id}' after driver reload"
                    if carry_pause is None
                    else f"Device '{device_id}' picked up the reloaded driver "
                         f"and stayed paused"
                )
            except Exception:
                log.exception(f"Failed to reconnect '{device_id}' after driver reload")
        return readded

    def get_devices_using_driver(self, driver_id: str) -> list[str]:
        """Return list of device IDs that use the given driver."""
        return [
            did for did, cfg in self._device_configs.items()
            if cfg.get("driver") == driver_id
        ]

    async def disconnect_all(self) -> None:
        """Disconnect all devices gracefully (called at shutdown)."""
        # Cancel all reconnect tasks first — await each so loops finish cleanly
        await self._cancel_all_reconnects()
        self._cancel_all_pause_expiries()
        # Cancel any in-flight web-UI probes so they don't outlive shutdown.
        self._cancel_web_ui_probes()
        # Same for read-back watchers: a setting still unconfirmed at shutdown
        # stays queued and is re-sent on the next start, which is the answer
        # this whole mechanism exists to give.
        self._cancel_all_pending_confirms()

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

    async def store_pending_settings(
        self, device_id: str, settings: dict[str, Any]
    ) -> None:
        """Store pending settings for a device (will be applied on next connect).

        Validated against the driver's schema when it is installed; an
        orphaned device stores them as given (core/pending_settings.py).
        """
        config = self._device_configs.get(device_id)
        if config is None:
            raise DeviceNotFoundError(f"Device '{device_id}' not found")
        self._queue_pending(device_id, config, settings)

    # --- Reconnection ---

    async def _on_device_disconnected(self, event: str, payload: dict[str, Any]) -> None:
        """Handle device.disconnected.* events — trigger auto-reconnect."""
        device_id = _device_id_of(event)
        if device_id is None:
            return

        # Before the intentional-disconnect / still-connected guards below, so
        # a bridge being removed or updated still takes its dependents offline.
        await self._bridge_went_down(device_id)

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

        # Classify the drop from the transport's stashed last error (no connect
        # exception on this path) so the device card shows an actionable reason
        # instead of a bare code.
        if self._paused_for_refused_login(device_id, self._set_offline_reason(device_id, driver)):
            # The driver had its credential refused on the live connection,
            # and its own login there was this drop's one attempt.
            return
        log.info(f"[{device_id}] Transport disconnected — starting auto-reconnect")
        self._start_reconnect(device_id)

    def _reason_outlived(self, device_id: str) -> bool:
        """Connected, and still publishing why it was not."""
        driver = self._devices.get(device_id)
        if driver is None or driver.get_state("connected") is not True:
            return False
        return self.state.get(f"device.{device_id}.offline_reason") is not None

    async def _on_device_connected(self, event: str, payload: dict[str, Any]) -> None:
        """Handle device.connected.* events — mirror a bridge coming online onto
        the bridge-routed devices bound to it.

        Fires for every device connect (cheap: a no-op unless the connected
        device is a bridge with bridge-routed dependents). Covers the case where
        a bridge connects *after* its dependents were added; the add-time seed
        in ``BaseDriver.connect`` covers the reverse order.
        """
        device_id = _device_id_of(event)
        if device_id is None:
            return
        # A held connection (see _reconnect_loop) is announced by the driver
        # when the device answers, and the loop that opened it clears the
        # offline reason. If that loop was stopped meanwhile (a setup action
        # cancels it and leaves the connection up), nothing else would.
        if device_id not in self._reconnect_tasks and self._reason_outlived(device_id):
            self._clear_offline_reason(device_id)
        # A bridge that was down when a downstream was added, and heals now,
        # would carry that downstream's bytes at whatever its NVRAM holds -- the
        # factory default, or the last job's baud -- while the device reads
        # connected and every indicator is green.
        await self._bridge_came_up(device_id)
        # A device that was unreachable at add time may only now be serving its
        # web UI; the probe's own "already detected" guard makes this idempotent.
        self._schedule_web_ui_probe(device_id)

    def _simulation_gap(self, device_id: str) -> str | None:
        return self.unsimulated_driver(device_id) if self.unsimulated_driver else None

    def _no_simulator_fault(self, device_id: str, driver_id: str) -> ConnectionFault:
        """The card's sentence for a device a running simulation could not
        simulate: how to give its driver a simulator, except on product
        hardware, whose installed integration the programmer cannot edit."""
        if self.installed_equipment and self.installed_equipment.owns(device_id):
            return no_simulator_fault()
        return no_simulator_fault(driver_id)

    def _require_control_target(self, device_id: str) -> None:
        """A missing simulator must never fall through to physical control."""
        gap = self._simulation_gap(device_id)
        if gap is not None:
            raise NoSimulatorError(device_id, self._no_simulator_fault(device_id, gap).message)

    async def _connect_driver(self, device_id: str, driver: Any) -> None:
        self._require_control_target(device_id)
        await driver.connect()

    async def reconnect_device(self, device_id: str) -> None:
        """Force disconnect and reconnect a device."""
        driver = self._live_driver(device_id)
        # A manual reconnect overrides a test-panel pause — clear the pause
        # bookkeeping so the flag can't go stale and the TTL backstop can't
        # fire a redundant resume later.
        if self.is_paused(device_id):
            self._end_pause(device_id)
        # Cancel any existing auto-reconnect task first
        await self._cancel_reconnect(device_id)
        self.state.set(f"device.{device_id}.reconnect_failed", None, source="device_manager")
        self._clear_offline_reason(device_id)
        self.state.set(f"device.{device_id}.reconnect_attempt", None, source="device_manager")
        # Suppress auto-reconnect during intentional disconnect
        self._intentional_disconnect.add(device_id)
        try:
            await self._disconnect_keeping_silence(driver)
            await self._reconnect_once(device_id, driver)
            # Probed whether or not the control protocol connected, as at
            # bring-up. Stopping simulation hands a device back through here,
            # and one added while simulating has not been probed at its own
            # address yet.
            self._schedule_web_ui_probe(device_id)
        finally:
            self._intentional_disconnect.discard(device_id)

    @staticmethod
    async def _disconnect_keeping_silence(driver: BaseDriver) -> None:
        """Close the driver for Reconnect. Reconnect is "try again" with
        nothing changed: a device dropped for its silence still stopped
        answering if this attempt gets no answer either, so its sentence
        outlives the disconnect, which forgets it."""
        silence = getattr(driver, "_silence_verdict", None)
        try:
            await driver.disconnect()
        except Exception:
            pass
        if silence is not None:
            driver._silence_verdict = silence

    async def _reconnect_once(self, device_id: str, driver: BaseDriver) -> None:
        """Reconnect's one attempt. A failure is published and, unless the
        device refused its login, handed to the reconnect loop."""
        try:
            self._refresh_usb_serial_port(device_id, driver)
            await self._connect_driver(device_id, driver)
            log.info(f"Reconnected device: {device_id}")
            # The reason was cleared up front by reconnect_device.
            self._note_bridge_offline_if_down(device_id, driver, driver.config or {})
            # Flush the queue, like every other path that brings a device up,
            # or a setting queued while the device was away survives the
            # Reconnect button (and the simulator's redirect, which reconnects
            # through here) and waits for some later connect nobody asks for.
            await self._apply_pending_settings(device_id)
        except ConnectEndedError:
            pass  # ended by a newer press, a pause or a removal
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            code = self._set_offline_reason(device_id, driver, exc=e)
            log.warning(f"Reconnect failed for {device_id}: {self._failure_text(device_id, e)}")
            # The manual attempt was this action's one try.
            if not self._paused_for_refused_login(device_id, code):
                self._start_reconnect(device_id)

    async def begin_setup(self, device_id: str) -> None:
        """Suppress auto-reconnect for the duration of a setup action.

        A setup action opens its own out-of-band transport, independent of the
        device's normal (often failing) one. Cancel any running reconnect loop
        and add the device to the intentional-disconnect set so the
        auto-reconnect machinery doesn't race the handler's own connection. The
        device's live transport (if any) is left untouched — an offline device
        is already down, and the handler doesn't use it. Pair with ``end_setup``.
        """
        self._live_driver(device_id)
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
        driver = self._live_driver(device_id)
        self._clear_offline_reason(device_id)
        try:
            await driver.disconnect()
        except Exception:
            pass
        await self._connect_driver(device_id, driver)


