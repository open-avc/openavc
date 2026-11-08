"""Sending a command to a device: the order its checks run in, and the one
rule for whether a command goes out now.

``send_command`` is the one door every caller passes (a panel press, a macro, a
script, the cloud, REST). It refuses a command name the driver does not declare,
and a file command sent from a door that cannot give or take a file, BEFORE it
asks whether the device is connected: the sentence that helps there is about
the name or the door, not the network. A send the driver raises on is published
as ``device.error.<id>`` and raised again. The checks read off the driver's own
declaration are core/command_gates.py.

The rule worth knowing before changing it: whether a command goes out now is
one rule, ``command_goes_out``. The device accepts commands (connected, or
its connection held open until it answers, ``accepts_commands``, which is also
the gate a setting write passes), or the command declares
``available_offline``. send_command's gate, a macro's "skip if offline" and a
group command's offline skip all ask it, so a skip never drops a command the
send would have let through: the Wake-on-LAN Power On is needed exactly while
the device is offline.

Mixed into DeviceManager (core/device_manager.py).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from openavc.core.command_gates import (
    available_offline,
    check_command_declared,
    check_file_door,
    coerce_child_id_params,
    send_file_command,
    validate_command_params,
)
from openavc.core.device_errors import (
    device_asleep,
    device_restarting,
    mark_failed_command,
    not_connected,
)

if TYPE_CHECKING:
    from openavc.core.device_sleep import SleepBook
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver


class DeviceCommandsMixin:
    """The command door and the rule for whether a command goes out.

    Holds no state of its own. From its host (DeviceManager) it reads
    ``state``, ``events``, ``_devices`` and ``_sleep`` (core/device_away.py),
    and asks ``_live_driver``, ``_require_control_target``,
    ``restart_seconds_left`` and ``_note_command_sent`` (core/device_away.py)
    and ``kick_reconnect`` (core/device_reconnect.py).
    """

    state: StateStore
    events: EventBus
    _devices: dict[str, BaseDriver]
    _sleep: SleepBook

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
