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
import time
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
from openavc.core.device_reconnect import DeviceReconnectMixin
from openavc.core.device_web_ui import DeviceWebUiMixin
from openavc.core.pending_settings import PendingSettingsMixin
from openavc.drivers.base import (
    ConnectEndedError,
    UnknownDeviceSettingError,
    validate_device_setting_value,
)
from openavc.drivers.registry import get_driver_class, is_driver_registered
from openavc.core.device_config import DeviceConnection, bridge_first, connection_of
from openavc.core.device_logins import SAVED_LOGIN, login_fields, marked_fields
from openavc.core.event_bus import EventBus
from openavc.core.installed_equipment import driver_label, missing_driver_reason
from openavc.core.state_store import StateStore
from openavc.core.device_traffic import get_traffic_recorder
from openavc.utils.log_redaction import get_secret_registry, redact_config
from openavc.utils.logger import get_logger

log = get_logger(__name__)


class DeviceNotFoundError(ValueError):
    """No device with this id is registered — the id itself is what is wrong.

    Every "no such device" raise in this module used to be a bare ValueError,
    and the doors above mapped *any* ValueError to a 404 naming the device. So
    a driver's own ValueError — a response its handler could not parse, a
    helper deep inside a protocol — was answered ``Device 'x' not found`` about
    a device that was connected and rendering its own page, which sends an
    integrator to check cabling that is fine. That mapping had already claimed
    two faults it did not have: :class:`UnknownCommandError` exists because a
    typo'd command name answered this way, and a non-finite parameter value did
    too. Naming the case makes the message a fact rather than a guess about
    what could have raised.

    Subclasses ValueError, so the callers that catch the broad type and report
    the message — scripts, macros, the ISC and cloud command paths, the panel
    WebSocket — are unchanged. The doors catch THIS to say "not found", and
    everything else answers for itself.
    """


def device_restarting(device_id: str, seconds_left: int) -> ConnectionError:
    """The "we asked it to restart, it is not back yet" refusal.

    A sibling of :func:`not_connected` rather than the same error, because the
    two send the reader to different places: "not connected" means go and look
    at the device, and this means wait. Both carry ``device_id`` so a surface
    can name the device the way somebody in the room knows it;
    ``restart_seconds`` is what ``friendly_error`` turns into the sentence, and
    is checked as an attribute rather than sniffed out of the message.
    """
    exc = ConnectionError(
        f"Device '{device_id}' is restarting ({seconds_left}s left)"
    )
    exc.device_id = device_id
    exc.restart_seconds = max(1, int(seconds_left))
    return exc


def device_asleep(device_id: str, wake_label: str) -> ConnectionError:
    """The "we put it to sleep" refusal: the device is off the network because
    a sleeping command (``sleeps_device``) ran, and ``wake_label`` names the
    command that brings it back. ``asleep_wake`` is what ``friendly_error``
    turns into the sentence, read as an attribute like ``restart_seconds``.
    """
    exc = ConnectionError(f"Device '{device_id}' is asleep")
    exc.device_id = device_id
    exc.asleep_wake = wake_label
    return exc


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


def mark_failed_command(exc: BaseException, device_id: str, command: str) -> None:
    """Note on ``exc`` which device command raised it.

    For a caller that meets the exception with neither in hand: a control's
    script call, whose function sent the command, words it with
    ``describe_command_failure`` as a press on the same command would. Not
    ``device_id``: that attribute marks the device manager's own refusals,
    whose text is written for the reader, and a driver's text is not.
    """
    try:
        exc.failed_command = (device_id, command)
    except (AttributeError, TypeError):
        # A driver's own exception class that takes no attributes: unmarked,
        # it is still raised as it was, and reads as the function's failure.
        pass


def failed_command(exc: BaseException | None) -> tuple[str, str] | None:
    """The ``(device_id, command)`` that raised ``exc``, when
    :meth:`DeviceManager.send_command` sent it."""
    sent = getattr(exc, "failed_command", None)
    return sent if isinstance(sent, tuple) and len(sent) == 2 else None


class NoSimulatorError(ConnectionError):
    """A command or a setting refused because a running simulation has no
    simulator for this device. A ConnectionError, so whatever catches a failed
    send still does; the message is the device card's own sentence, which the
    command routes and ``friendly_error`` pass on as it is (the device is not
    what is missing)."""

    def __init__(self, device_id: str, message: str) -> None:
        super().__init__(message)
        self.device_id = device_id


if TYPE_CHECKING:
    from openavc.drivers.base import BaseDriver


# Backstop for a test-panel pause whose owner never resumes it (tab closed or
# crashed, request lost). The panel refreshes the pause while it stays open,
# so expiry only fires for genuinely abandoned pauses — without it a paused
# production device stays offline indefinitely with auto-reconnect suppressed.
PAUSE_TTL = 600.0

# Who holds a pause, published as ``device.<id>.paused_for`` so the device page
# can name it: the Driver Builder's Live Test (through POST /devices/{id}/pause)
# or a device audit. Set by the first pause and kept until the device resumes.
PAUSED_FOR_LIVE_TEST = "live_test"
PAUSED_FOR_AUDIT = "audit"


# Masking a device config for a caller uses the same credential-field names as
# masking a credential out of the device log — see openavc/utils/log_redaction.py,
# which owns both. Re-exported here because get_device_info's orphaned-device
# branch is the only path that returns raw connection config, and that payload
# flows to the cloud AI (cloud/tools/device_tools.py::_get_device_info): a
# missing driver must not turn a simple get_device_info into a credential dump.
_redact_config = redact_config


def _orphan_config(config: dict[str, Any]) -> dict[str, Any]:
    """An orphaned device's config with every credential masked by name, and
    each login saved on this system shown as the marker rather than masked."""
    masked = _redact_config(config)
    return {k: (SAVED_LOGIN if config.get(k) == SAVED_LOGIN else v) for k, v in masked.items()}


class DeviceManager(
    DeviceAwayMixin, DeviceReconnectMixin, PendingSettingsMixin, DeviceWebUiMixin,
    DeviceBridgesMixin,
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
            log.warning(f"Device '{device_id}' is orphaned: {missing_driver_reason(driver_id)}")
            self._orphaned_devices[device_id] = device_config
            self._device_configs[device_id] = device_config
            self.state.set(f"device.{device_id}.name", name, source="config")
            self.state.set(f"device.{device_id}.connected", False, source="config")
            self.state.set(f"device.{device_id}.orphaned", True, source="config")
            self.state.set(
                f"device.{device_id}.orphan_reason",
                missing_driver_reason(driver_id),
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

        self._attach_bridge_router(driver, config)

        # Set device name in state
        self.state.set(
            f"device.{device_id}.name", name, source=f"device.{device_id}"
        )

        self.state.set(f"device.{device_id}.enabled", True, source="config")
        log.info(f"Added device '{device_id}' ({name}) using {driver_label(driver_id)}")

        # A pause carried across the instance swap: the registration above still
        # runs (and bring_up still preps the bridge port for whenever it
        # resumes) — only the connect is suppressed, which is the whole harm.
        # Whoever holds the pause still holds the session it was taken to
        # protect. The backstop is re-armed with the time the pause had left.
        if carry_pause is not None:
            self._intentional_disconnect.add(device_id)
            self.state.set(f"device.{device_id}.paused", True, source="device_manager")
            self.state.set(
                f"device.{device_id}.paused_for", carry_pause_for, source="device_manager"
            )
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

    def get_device_info(self, device_id: str) -> dict[str, Any]:
        """Return device metadata, status, and capabilities.

        ``connection`` is where the device connects: its resolved transport,
        host, port and bridge (``core.device_config.connection_of``). It is
        read from the record this manager was handed, never from the live
        driver, whose host a running simulation points at 127.0.0.1.
        """
        info = self._device_info(device_id)
        if self.installed_equipment:
            info.update(self.installed_equipment.information(device_id))
        return info

    def _device_info(self, device_id: str) -> dict[str, Any]:
        """Build metadata for active, disabled and missing-driver devices."""
        # Check if orphaned first
        if device_id in self._orphaned_devices:
            config = self._orphaned_devices[device_id]
            return {
                "id": device_id,
                "name": config.get("name", device_id),
                "driver": config.get("driver", ""),
                "connected": False,
                "connection": connection_of(config).address(),
                "orphaned": True,
                "orphan_reason": missing_driver_reason(config.get("driver", "")),
                "state": self.state.get_namespace(f"device.{device_id}"),
                "commands": {},
                "driver_info": {},
                # Redact credentials: this is the only get_device_info branch
                # that returns raw connection config, and it reaches the cloud
                # AI. A missing driver must not leak the device's password /
                # API key (see _redact_config). A login saved on this system
                # reads as the marker, which is what the project holds.
                "config": _orphan_config(config.get("config", {})),
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
                    "connection": connection_of(config).address(),
                    "state": self.state.get_namespace(f"device.{device_id}"),
                    "commands": {},
                    "driver_info": {},
                }
            raise DeviceNotFoundError(f"Device '{device_id}' not found")

        from openavc.drivers.actions import resolve_device_actions

        config = self._device_configs.get(device_id, {})
        return {
            "id": device_id,
            "name": config.get("name", device_id),
            "driver": config.get("driver", ""),
            "connected": driver.get_state("connected"),
            "connection": connection_of(config).address(),
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
                self._link_config(device_id, driver),
                detected_web_ui_url=self._detected_web_ui_urls.get(device_id),
            ),
            "driver_info": driver.DRIVER_INFO,
            # The settings whose login is marked saved on this system and is
            # not: the ones the device page asks for under "Needs login".
            # Names only, never a value.
            "needs_login": marked_fields(driver.config),
        }

    def _link_config(self, device_id: str, driver: BaseDriver) -> dict[str, Any]:
        """The config a link action's URL is built from: the driver's own,
        with its real address put back while a simulation holds the device.
        Redirected, its host is this computer's loopback address and its port
        the simulator's, so a link built from them opens the simulator, or
        OpenAVC itself on 8080, instead of the equipment's page."""
        # The driver holds its logins filled in, and a link's URL is shown on
        # the device page and sent to the cloud AI, so a placeholder naming a
        # login is left as written.
        logins = login_fields(str(self._device_configs.get(device_id, {}).get("driver", "")))
        config = {k: v for k, v in (driver.config or {}).items() if k not in logins}
        real = self.real_connection(device_id) if self.real_connection is not None else None
        if not real:
            return config
        merged = dict(config)
        for key, value in real.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged

    def live_connections(self) -> list[DeviceConnection]:
        """Where each device whose driver is connected right now connects.

        Read from the record this manager was handed (``connection_of``), as
        :meth:`get_device_info` is, so a running simulation, which points the
        live driver at 127.0.0.1, still names the device's own address. A
        serial device, one that sends through a bridge with no connection of
        its own, and one with no network address are left out. A Discovery
        scan connects to none of these (``discovery/port_guard.py``).
        """
        out: list[DeviceConnection] = []
        for device_id, driver in self._devices.items():
            record = self._device_configs.get(device_id)
            if record is None or not driver.get_state("connected"):
                continue
            conn = connection_of(record)
            if conn.transport in ("serial", "bridge") or not conn.host:
                continue
            if not isinstance(conn.port, int):
                continue
            out.append(conn)
        return out

    def list_devices(self) -> list[dict[str, Any]]:
        items = self._list_devices()
        if self.installed_equipment:
            for item in items:
                item.update(self.installed_equipment.information(item["id"]))
        return items

    def _list_devices(self) -> list[dict[str, Any]]:
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
                    "connection": info["connection"],
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
                    "connection": connection_of(config).address(),
                    "orphaned": True,
                    "orphan_reason": missing_driver_reason(config.get("driver", "")),
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
                    "connection": connection_of(config).address(),
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

        Returns True if the device was successfully activated, False if still
        orphaned. "Activated" means registered: the connection is opened by
        the bring-up round asked for below, not before this returns, so a
        caller that needs the device on the wire awaits
        ``Engine.wait_for_device_bringup``.

        The two ways this can be asked about the wrong device are separate
        answers, because the door above them is: an id nobody has is not found,
        while a device that is running perfectly well has nothing to retry.
        """
        if device_id not in self._orphaned_devices:
            if device_id not in self._device_configs:
                raise DeviceNotFoundError(f"Device '{device_id}' not found")
            raise ValueError(f"Device '{device_id}' is not orphaned")

        config = self._orphaned_devices[device_id]
        driver_id = config.get("driver", "")

        # Check if the driver is now available
        if not is_driver_registered(driver_id):
            return False

        # Remove from orphan tracking and re-add normally.
        #
        # Registered, not dialed, whenever a bring-up round can open it: the
        # round re-applies the simulation redirect to the new instance before
        # anything is dialed, so a device activated while simulation is
        # running reaches the simulator on its FIRST attempt. Dialing here
        # sent it to its REAL address and left it reconnect-looping against
        # hardware that is not there, with no recovery an integrator could
        # guess (``/api/simulation/start`` refuses, "already active"; a
        # reconnect re-dials the real address). Same rule as reload_driver,
        # for the same reason.
        #
        # A caller that asked for the deferral owns its own round (the engine's
        # reconcile does); one that did not gets it asked for here, or the
        # device would sit registered and never dialed.
        ask_for_round = not defer_connect and self.request_bringup is not None
        await self.remove_device(device_id)
        await self.add_device(config, defer_connect=defer_connect or ask_for_round)
        activated = device_id not in self._orphaned_devices
        if activated and ask_for_round:
            self.request_bringup()
        return activated

    async def retry_all_orphans(self, *, defer_connect: bool = False) -> list[str]:
        """Promote every orphan whose driver is now in the registry.

        Called after the driver loader runs (project reload, community
        install) so devices that were stuck in orphan state because their
        driver wasn't loaded yet come online without a server restart.
        Returns the list of device IDs that successfully activated.
        """
        return await self._activate_orphans(
            is_driver_registered, defer_connect=defer_connect,
            note="(driver '{driver}' now installed)",
        )

    async def _activate_orphans(
        self, wanted: Callable[[str], bool], *, defer_connect: bool = False, note: str,
    ) -> list[str]:
        """Activate every orphan waiting for a driver id ``wanted`` accepts.

        The ids activated. One that fails is logged and the rest still run.
        ``note`` ends each "Activated" log line, with ``{driver}`` filled in.
        """
        activated: list[str] = []
        # Snapshot before iterating — retry_orphaned_device mutates the dict
        for device_id, config in list(self._orphaned_devices.items()):
            driver_id = config.get("driver", "")
            if wanted(driver_id) and await self._try_orphan(device_id, defer_connect):
                activated.append(device_id)
                log.info(f"Activated orphaned device '{device_id}' " + note.format(driver=driver_id))
        return activated

    async def _try_orphan(self, device_id: str, defer_connect: bool) -> bool:
        """:meth:`retry_orphaned_device`, with a failure logged as False."""
        try:
            return await self.retry_orphaned_device(device_id, defer_connect=defer_connect)
        except Exception:
            log.exception(f"Failed to activate orphaned device '{device_id}'")
            return False

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
        # Cancel pause-TTL backstops so none fires a resume mid-shutdown
        for device_id in list(self._pause_expiry_tasks.keys()):
            self._cancel_pause_expiry(device_id)
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
        # Extract device_id from event name: "device.disconnected.<id>"
        parts = event.split(".", 2)
        if len(parts) < 3:
            return
        device_id = parts[2]

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
        # A held connection (see _reconnect_loop) is announced by the driver
        # when the device answers, and the loop that opened it clears the
        # offline reason. If that loop was stopped meanwhile (a setup action
        # cancels it and leaves the connection up), nothing else would.
        if device_id not in self._reconnect_tasks:
            driver = self._devices.get(device_id)
            if (
                driver is not None
                and driver.get_state("connected") is True
                and self.state.get(f"device.{device_id}.offline_reason") is not None
            ):
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

    async def pause_device(
        self, device_id: str, ttl: float | None = None, purpose: str = PAUSED_FOR_LIVE_TEST,
    ) -> None:
        """Cleanly disconnect a device and suppress auto-reconnect (A81).

        Used by the driver test panel before it opens a competing TCP session
        against the same host:port on single-session devices, and by a device
        audit. The device stays paused until ``resume_device`` is called — or
        until ``ttl`` seconds pass without a re-pause (the holder keeps the
        pause alive while it runs), at which point the device auto-resumes so a
        closed/crashed tab can't strand it offline forever.
        ``device.<id>.paused`` is set so the UI can surface the state, and
        ``device.<id>.paused_for`` names the holder (``purpose``). Re-pausing an
        already-paused device just resets the TTL; the first holder's name stays.
        """
        driver = self._live_driver(device_id)
        await self._cancel_reconnect(device_id)
        # Add to intentional_disconnect BEFORE disconnect so the disconnected
        # event handler doesn't kick off a reconnect_loop.
        self._intentional_disconnect.add(device_id)
        try:
            await driver.disconnect()
        except Exception as e:
            log.warning(f"pause_device: disconnect raised for {device_id}: {e}")
        if not self.state.get(f"device.{device_id}.paused_for"):
            self.state.set(f"device.{device_id}.paused_for", purpose, source="device_manager")
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

    def _pause_to_carry(self, device_id: str) -> tuple[float | None, str | None]:
        """The pause a device keeps across being removed and added again (an
        edit, a driver reload): the seconds its backstop has left, and who
        holds it. Read before the removal, which deletes both.

        The pause belongs to whoever took it, still holding the competing
        session (the Driver Builder's Live Test, an audit), and ends when they
        release it or its backstop runs out. An edit or a driver reload is
        neither, so reconnecting there would put a production device back on a
        port under test without anyone asking. A driver reload is the likelier
        of the two: saving the driver from the Builder is what the Live Test's
        own user does next.
        """
        return self._pause_remaining(device_id), self.state.get(f"device.{device_id}.paused_for")

    def _end_pause(self, device_id: str) -> None:
        """The pause is over: its backstop cancelled and both flags cleared."""
        self._cancel_pause_expiry(device_id)
        self.state.set(f"device.{device_id}.paused", False, source="device_manager")
        self.state.set(f"device.{device_id}.paused_for", None, source="device_manager")

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
                f"[{device_id}] Pause not renewed within {ttl:.0f} s; reconnecting"
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
        driver = self._live_driver(device_id)
        self._intentional_disconnect.discard(device_id)
        self._end_pause(device_id)
        try:
            await self._connect_driver(device_id, driver)
            log.info(f"Resumed device: {device_id}")
            # Mirror the reconnect-loop success path: drop the stale offline
            # reason and flush anything queued while the device was away.
            self._clear_offline_reason(device_id)
            await self._apply_pending_settings(device_id)
        except ConnectEndedError:
            pass  # ended by a Reconnect, a pause or a removal meanwhile
        except Exception as e:
            self.state.set(f"device.{device_id}.connected", False, source="device_manager")
            self._set_offline_reason(device_id, driver, exc=e)
            log.warning(
                f"resume_device: connect failed for {device_id}: {self._failure_text(device_id, e)}"
            )
            self._start_reconnect(device_id)
