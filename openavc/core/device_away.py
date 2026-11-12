"""Why a device is away, said in state: the part of DeviceManager that publishes it.

Five ``device.<id>.`` keys say it: ``offline_reason`` (the stable code automation
matches on), ``offline_detail`` (the device card's sentence), ``restarting``,
``asleep`` and ``wake_command``. Every status is written as all five in one batch
(``_AwayStatus``), so none is left over from the status before it.

The rule worth knowing before changing it: absence we asked for is not a fault.
A command declaring ``restarts_device_for`` opens a commanded-restart window, and
one declaring ``sleeps_device`` puts the device to sleep (core/device_sleep.py).
While either holds, a failure that is only the device's absence publishes the
window or the sleep instead, with ``offline_reason`` left None, so alerts and
``offline_reason`` triggers stay quiet. A permanent fault (the device answered
and refused us) ends both, and is published as itself.

Which fault it is follows one order: a device a running simulation cannot
simulate, then a fault already typed (the exception chain, then the driver's
stash), then a down bridge under a bridged device, then the shared classifier in
core/connection_fault.py.

Mixed into DeviceManager (core/device_manager.py). Logs under the device
manager's name, which is how the log view files a line as the device's.
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from openavc.core.command_gates import command_definition
from openavc.core.connection_fault import (
    BRIDGE_OFFLINE,
    NO_RESPONSE,
    UNREACHABLE,
    ConnectionFault,
    classify_connection_fault,
    is_permanent_fault,
    link_local_fault,
    restarting_message,
    typed_fault_from_exc,
)
from openavc.core.device_sleep import SleepBook, asleep_message, command_label, wake_command_of
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.state_store import StateStore
    from openavc.drivers.base import BaseDriver

# The device manager's logger: utils/log_buffer.py files a line under the
# device category by this name, and the device page shows that category.
log = get_logger("openavc.core.device_manager")


# The longest a driver's `restarts_device_for` may hold back a real fault.
# The contract caps the declared value at the same figure, so this is the
# second of the two gates rather than the only one: a driver dropped straight
# into driver_repo/ skips catalog validation, and a window with no ceiling
# would let a typo'd 6000 hide a genuinely dead device for most of an hour.
_MAX_RESTART_WINDOW_SECONDS = 600.0


@dataclass(frozen=True)
class _AwayStatus:
    """Why a device is away, as the five ``device.<id>.`` keys that say it.

    Each status a device moves to (a fault, a commanded-restart window,
    asleep, back online) is written as all five at once, so none is left
    over from the status it moved from: a fault published after a window
    leaves no ``restarting``, a device that wakes keeps no ``wake_command``.
    The default is the device back online, all five cleared.

    ``sleep_first`` is for the two writes about a sleep (falling asleep,
    waking): they change ``asleep`` and ``wake_command`` before
    ``restarting``. The batch notifies in key order, so a project's
    state_change triggers on these keys fire in that order.
    """

    restarting: bool | None = None
    asleep: bool | None = None
    wake_command: str | None = None
    offline_reason: str | None = None
    offline_detail: str | None = None
    sleep_first: bool = False

    def state_keys(self, device_id: str) -> dict[str, Any]:
        restarting = {f"device.{device_id}.restarting": self.restarting}
        sleep = {
            f"device.{device_id}.asleep": self.asleep,
            f"device.{device_id}.wake_command": self.wake_command,
        }
        lead = {**sleep, **restarting} if self.sleep_first else {**restarting, **sleep}
        return {
            **lead,
            f"device.{device_id}.offline_reason": self.offline_reason,
            f"device.{device_id}.offline_detail": self.offline_detail,
        }


def _typed_fault(
    driver: Any, exc: BaseException | None, host: str, port: Any,
) -> ConnectionFault | None:
    """The fault already named before anything is classified, or None.

    Typed faults win over string matching: first a ConnectionFaultError in
    the exception chain (the freshest signal), then a fault the driver
    stashed before forcing a disconnect (liveness watchdogs).
    """
    fault = typed_fault_from_exc(exc, host=host, port=port)
    if fault is None and driver is not None:
        fault = getattr(driver, "last_fault", None)
    return fault


def _kept_silence_verdict(fault: ConnectionFault, driver: Any) -> ConnectionFault:
    """A device dropped because it stopped answering, whose reconnect gets no
    answer either, keeps the sentence the drop gave. The attempt's own failure
    reads as a generic no_response ("check the driver, transport and port"),
    which is wrong advice for a device that was working."""
    if fault.code != NO_RESPONSE or driver is None:
        return fault
    return getattr(driver, "_silence_verdict", None) or fault


def connection_descriptor(driver: BaseDriver) -> tuple[str, Any, str]:
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


class DeviceAwayMixin:
    """Publishing why a device is away, and the windows and sleeps that change it.

    Its state is set up by ``_init_away``. From its host (DeviceManager) it reads
    ``state`` and ``_devices`` and asks ``_simulation_gap``,
    ``_no_simulator_fault`` and ``_carrier_bridge_if_offline``.
    """

    state: StateStore
    _devices: dict[str, BaseDriver]

    def _init_away(self) -> None:
        # Commanded-restart windows: device id -> monotonic deadline, armed by
        # a successful send of a command declaring `restarts_device_for`.
        #
        # A LATENT permission, not a state: arming writes nothing, and the
        # `restarting` key only appears if the device actually goes away
        # (_set_offline_reason). A driver that over-declares the field on a
        # command the device shrugs off therefore costs nothing visible, which
        # is the right way round for a number an author is estimating.
        self._restart_windows: dict[str, float] = {}
        # The restart windows a wake command opened (sleeps_device): the same
        # window, worded "Waking up" rather than "Restarting".
        self._waking: set[str] = set()
        # Devices armed or asleep after a command declaring `sleeps_device`
        # (core/device_sleep.py): in memory and in {data_dir}/device_sleep.json.
        self._sleep = SleepBook()

    def _forget_away(self, device_id: str) -> None:
        """Drop a removed device's window and sleep."""
        self._end_restart_window(device_id)
        self._sleep.end(device_id)

    def _note_sleep_command(self, device_id: str, driver: BaseDriver, command: str) -> bool:
        """Arm the device after a command declaring ``sleeps_device``, or end
        its sleep after the wake command. True when the wake command ended a
        sleep the device was actually in (core/device_sleep.py)."""
        info = getattr(driver, "DRIVER_INFO", {}) or {}
        commands = info.get("commands") or {}
        wake = wake_command_of(commands, command)
        if wake is not None:
            self._sleep.arm(
                device_id, wake, command_label(commands, wake), str(info.get("id") or ""),
            )
            log.info("[%s] %s puts the device to sleep; %s wakes it", device_id, command, wake)
            return False
        sleep = self._sleep.get(device_id)
        if sleep is not None and command == sleep.wake:
            self._sleep.end(device_id)
            if sleep.asleep:
                self._waking.add(device_id)
                return True
        return False

    def _note_command_sent(self, device_id: str, driver: BaseDriver, command: str) -> None:
        """After a send that did not raise: arm what the command declares (a
        sleep, a restart window), and when it was the wake command that ended
        a sleep, say the device is waking."""
        woke = self._note_sleep_command(device_id, driver, command)
        self._arm_restart_window(device_id, driver, command)
        if woke:
            self._publish_waking(device_id)

    def _publish_waking(self, device_id: str) -> None:
        """Right after the wake command: the sleep is over, and the device is
        coming back for as long as the wake command's window says."""
        if self.state.get(f"device.{device_id}.connected"):
            return
        remaining = self.restart_seconds_left(device_id)
        self._publish_status(device_id, _AwayStatus(
            restarting=True if remaining is not None else None,
            offline_detail=(
                restarting_message(remaining, waking=True) if remaining is not None else None
            ),
            sleep_first=True,
        ))

    def sleep_of(self, device_id: str):
        """The device's sleep (core/device_sleep.Sleep), or None."""
        return self._sleep.get(device_id)

    def away_on_purpose(self, device_id: str) -> bool:
        """True while the device is, or is about to be, away because we asked:
        a commanded-restart window is open, or a sleeping command was sent and
        the device has not stayed awake past it. Asked by the alert monitor at
        the moment a drop writes ``connected``, before the restarting or asleep
        flag is published."""
        if self.restart_seconds_left(device_id) is not None:
            return True
        return self._sleep.get(device_id) is not None and not self._sleep.stayed_awake(device_id)

    def _arm_restart_window(
        self, device_id: str, driver: BaseDriver, command: str
    ) -> None:
        """Open the commanded-restart window for a command that declares one.

        Reads the instance-level DRIVER_INFO (core/command_gates.py), so a
        runtime-populated command set is covered too.
        """
        cmd_def = command_definition(driver, command)
        if cmd_def is None:
            return
        try:
            seconds = float(cmd_def.get("restarts_device_for") or 0)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        seconds = min(seconds, _MAX_RESTART_WINDOW_SECONDS)
        self._restart_windows[device_id] = time.monotonic() + seconds
        log.info(
            "[%s] %s restarts the device; holding the offline fault for %.0fs",
            device_id, command, seconds,
        )

    def _attach_restart_window(self, device_id: str, driver: BaseDriver) -> None:
        """Let the driver ask this device's commanded-restart window, so the
        polls and probes it misses inside one log as expected, not as a fault
        (drivers/liveness.py, _silence_log)."""
        driver._restart_window = functools.partial(self.restart_seconds_left, device_id)

    def restart_seconds_left(self, device_id: str) -> int | None:
        """Seconds remaining in this device's commanded-restart window.

        ``None`` when no window is open, which is the answer every caller
        branches on. Expiry is read from the clock rather than fired by a
        timer: the only thing that happens at the end of a window is that the
        next classification stops being suppressed, and the reconnect loop is
        already the thing that produces one.
        """
        deadline = self._restart_windows.get(device_id)
        if deadline is None:
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._restart_windows.pop(device_id, None)
            self._waking.discard(device_id)
            return None
        return max(1, int(round(remaining)))

    def _end_restart_window(self, device_id: str) -> None:
        """Drop the window without waiting for it to expire."""
        self._restart_windows.pop(device_id, None)
        self._waking.discard(device_id)

    def _failure_text(self, device_id: str, exc: BaseException) -> str:
        """What a failed connect's log line says after the colon: the
        exception's own text, else the card's sentence (so classify with
        _set_offline_reason first), else the exception's type. A timeout, or a
        product that did not answer, raises with no text and left the line
        ending in a colon while the card gave the reason."""
        return (
            str(exc)
            or self.state.get(f"device.{device_id}.offline_detail")
            or type(exc).__name__
        )

    def _set_offline_reason(
        self,
        device_id: str,
        driver: BaseDriver | None,
        exc: BaseException | None = None,
        fault: ConnectionFault | None = None,
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
        ``fault``, when given, is the reason and nothing is classified.
        """
        given = fault is not None
        last_error, host, port, transport = self._link_signals(driver)

        # A device a running simulation could not simulate is failing against
        # its real address, so every signal below is about a socket that was
        # never meant to be reached. Answer the question the author is actually
        # asking before the classifier answers a different one correctly.
        gap = self._simulation_gap(device_id)
        if gap:
            fault = self._no_simulator_fault(device_id, gap)
        if fault is None:
            fault = _typed_fault(driver, exc, host, port)
        # Before the classifier describes the socket: a device bound through a
        # bridge dials the BRIDGE's pass-through address, so what the classifier
        # would report is a host and port the integrator never typed ("Connection
        # refused on 127.0.0.1:4999"). When the bridge carrying this device is
        # down, that is the answer, and it is the same answer an IR device on the
        # same bridge has always got -- including its precedence over a commanded
        # restart window, which is why this delegates rather than building a
        # fault of its own. It sits AFTER the typed faults deliberately: those
        # mean the device itself answered, which pins a real failure to the
        # device rather than to its carrier, and one of them (auth_failed) steers
        # the reconnect policy.
        if fault is None:
            bridge_id = self._carrier_bridge_if_offline(device_id)
            if bridge_id is not None:
                self._set_bridge_offline_reason(device_id, bridge_id)
                return BRIDGE_OFFLINE
            fault = classify_connection_fault(
                last_error=last_error, exc=exc,
                host=host, port=port, transport=transport,
            )
        if not given:
            fault = _kept_silence_verdict(fault, driver)
        # A device at a 169.254 address, unreachable while the adapter chosen
        # in Reach devices through holds no 169.254 address of its own: say
        # that, not "check the IP address" (the address is right).
        if fault.code == UNREACHABLE:
            fault = link_local_fault(host, port) or fault
        return self._publish_offline(device_id, driver, fault)

    def _link_signals(self, driver: BaseDriver | None) -> tuple[str, str, Any, str]:
        """What the classifier reads about a device's link: the transport's
        last error, then the (host, port, transport) its message names.

        The live transport's error wins over the one BaseDriver keeps from a
        transport it tore down.
        """
        if driver is None:
            return "", "", None, ""
        last_error = getattr(driver, "last_transport_error", "") or ""
        live = getattr(driver, "transport", None)
        if live is not None:
            last_error = getattr(live, "last_error", "") or last_error
        host, port, transport = connection_descriptor(driver)
        return last_error, host, port, transport

    def _publish_offline(
        self, device_id: str, driver: BaseDriver | None, fault: ConnectionFault,
    ) -> str:
        """Publish a classified fault, unless the device is away because we
        asked it to be: then the window or the sleep is published instead.

        Returns the fault's code either way, because the reconnect policy
        hinges on this failure rather than on what is published about it.
        """
        if self._publish_restarting(device_id, fault):
            return fault.code
        self._end_restart_window(device_id)
        if self._publish_asleep(device_id, driver, fault):
            return fault.code
        self._publish_status(device_id, _AwayStatus(
            offline_reason=fault.code, offline_detail=fault.message,
        ))
        return fault.code

    def _publish_restarting(self, device_id: str, fault: ConnectionFault) -> bool:
        """Publish the commanded-restart window in place of ``fault``, while
        one is open. True when it did.

        A commanded restart is absence we asked for, so for as long as the
        driver said it would last the device reports a window instead of a
        fault: a sentence in `offline_detail`, `restarting` true, and
        `offline_reason` left None. None is what does the work — the cloud's
        error tally, every alert rule and every `offline_reason` condition go
        quiet without one of them learning a new value.

        A permanent fault breaks the window on the spot. Those mean the
        device is REACHABLE and refusing us (a rejected login, a certificate
        nobody trusts), which is positive evidence that it is not still
        booting.
        """
        remaining = self.restart_seconds_left(device_id)
        if remaining is None or is_permanent_fault(fault.code):
            return False
        self._publish_status(device_id, _AwayStatus(
            restarting=True,
            offline_detail=restarting_message(
                remaining, waking=device_id in self._waking,
            ),
        ))
        return True

    def _publish_asleep(
        self, device_id: str, driver: BaseDriver | None, fault: ConnectionFault,
    ) -> bool:
        """Publish the device asleep in place of ``fault``, when we put it to
        sleep. True when it did.

        A device we put to sleep (sleeps_device) is asleep, not faulty, for
        every failure that is only its absence: the same None offline_reason
        the restart window relies on. A permanent fault means it answered,
        and a device still connected long after the sleeping command stayed
        awake; either ends the sleep and the fault is published as itself.
        """
        sleep = self._sleep.get(device_id, self._driver_id(device_id, driver))
        if sleep is None:
            return False
        if is_permanent_fault(fault.code) or self._sleep.stayed_awake(device_id):
            self._sleep.end(device_id)
            return False
        if not sleep.asleep:
            log.info("[%s] Asleep; %s wakes it", device_id, sleep.wake_label)
        self._sleep.mark_asleep(device_id)
        self._publish_status(device_id, _AwayStatus(
            asleep=True,
            wake_command=sleep.wake,
            offline_detail=asleep_message(sleep.wake_label),
            sleep_first=True,
        ))
        return True

    def _driver_id(self, device_id: str, driver: BaseDriver | None) -> str | None:
        """The id of the driver behind ``device_id``, or None when unknown."""
        drv = driver if driver is not None else self._devices.get(device_id)
        info = getattr(drv, "DRIVER_INFO", None) or {}
        return str(info.get("id")) if info.get("id") else None

    def _clear_offline_reason(self, device_id: str) -> None:
        """Clear the offline-reason keys after a successful (re)connect.

        Also ends any commanded-restart window and any sleep: the device came
        back, which is the outcome both existed to wait for.
        """
        self._end_restart_window(device_id)
        self._sleep.end(device_id)
        self._publish_status(device_id, _AwayStatus())

    def _publish_status(self, device_id: str, status: _AwayStatus) -> None:
        """Write why a device is away, all five keys in one batch."""
        self.state.set_batch(status.state_keys(device_id), source="device_manager")

    def _set_bridge_offline_reason(self, device_id: str, bridge_id: str) -> None:
        """Publish the offline-reason keys for a bridge-routed device whose
        bridge is down (a direct taxonomy entry, not classified from an error).
        """
        from openavc.core.connection_fault import bridge_offline_fault

        bridge_name = self.state.get(f"device.{bridge_id}.name") or bridge_id
        fault = bridge_offline_fault(str(bridge_name))
        # The bridge being down is about a different box, so it outranks any
        # window this device happened to have open: no amount of waiting for
        # this device to finish booting fixes the one carrying its traffic.
        self._end_restart_window(device_id)
        self._publish_status(device_id, _AwayStatus(
            offline_reason=fault.code, offline_detail=fault.message,
        ))
