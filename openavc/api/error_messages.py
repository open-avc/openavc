"""
User-friendly error message mapping.

Maps common Python exceptions to actionable messages that AV integrators
can understand and act on, instead of raw tracebacks.
"""

from __future__ import annotations

import errno
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from openavc.drivers.command_outcome import CommandOutcomeError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.state_store import StateStore

# Logs only device command failures, each under "[<device_id>]", and is
# categorised as a device logger (utils/log_buffer.py) so the line is kept
# with the device's own lines: the sentence below sends the reader there.
log = get_logger(__name__)


def device_error_label(
    device_id: str, state: StateStore, project: ProjectConfig | None = None,
    *, exc: Exception | None = None,
) -> str:
    """Use the configured name even after disabling removes live device state."""
    from openavc.core.device_manager import DeviceNotFoundError

    for device in getattr(project, "devices", ()):
        if device.id == device_id and device.name:
            return str(device.name)
    fallback = "The requested equipment" if isinstance(exc, DeviceNotFoundError) else device_id
    return str(state.get(f"device.{device_id}.name") or fallback)


@dataclass(frozen=True)
class CommandFailure:
    """A device command that did not run, worded for each reader.

    ``programmer`` is what Send Command answers. ``panel`` is the same failure
    for somebody standing at a panel, who can open no setting and no log.
    """

    programmer: str
    panel: str

    def for_reader(self, *, at_panel: bool) -> str:
        return self.panel if at_panel else self.programmer


# The ConnectionError subclasses friendly_error answers with a fixed sentence.
_WORDED_CONNECTION_ERRORS = (
    ConnectionRefusedError, ConnectionResetError, BrokenPipeError, ConnectionAbortedError,
)


def _command_label(driver: Any, command: str) -> str:
    info = getattr(driver, "DRIVER_INFO", None)
    commands = info.get("commands") if isinstance(info, dict) else None
    cmd_def = commands.get(command) if isinstance(commands, dict) else None
    label = cmd_def.get("label") if isinstance(cmd_def, dict) else None
    return str(label or command or "the command")


def describe_command_failure(
    exc: Exception, device_id: str, command: str, *,
    state: StateStore, project: ProjectConfig | None = None, driver: Any = None,
) -> CommandFailure:
    """What every door that sent a device command says when it did not run.

    One rule for Send Command, a device action, a panel press, a macro or group
    step, the WebSocket ``command`` frame, a peer system, the cloud and the
    cloud AI. The exceptions the platform raises for a command, and a
    ``CommandRefusedError`` (the device saying no, in a sentence written for
    the reader), keep their own sentence. Anything else a driver raises, a
    ``ValueError`` or ``KeyError`` included, was not written for the reader and
    may hold what should stay private: its text is logged under the device's id,
    which puts it in the Device Log on the device's page, and the answer names
    the device and the command instead.

    ``driver`` supplies the command's label; ``project`` the device's name.
    """
    from openavc.core.device_manager import DeviceNotFoundError, not_connected
    from openavc.drivers.base import (
        CommandParamError,
        CommandPartialError,
        CommandRefusedError,
        UnknownCommandError,
        WakeAddressError,
    )
    from openavc.drivers.command_files import FileCommandError
    from openavc.reach_devices import AdapterUnavailable

    name = device_error_label(device_id, state, project, exc=exc)
    host = str(state.get(f"device.{device_id}.host") or "")

    def for_the_room(worded: Exception) -> str:
        return friendly_error(worded, device=name, host=host, at_panel=True)

    if isinstance(exc, (
        CommandParamError, UnknownCommandError, AdapterUnavailable,
        CommandPartialError, CommandOutcomeError,
    )):
        return CommandFailure(str(exc), for_the_room(exc))
    if isinstance(exc, FileCommandError):
        # A refusal of the caller, not a fault, so a warning.
        log.warning(f"[{device_id}] {exc}")
        return CommandFailure(str(exc), for_the_room(exc))
    if isinstance(exc, DeviceNotFoundError):
        return CommandFailure(f"Device '{device_id}' not found", for_the_room(exc))
    if isinstance(exc, WakeAddressError):
        return CommandFailure(friendly_error(exc, device=name), for_the_room(exc))
    if isinstance(exc, ConnectionError):
        # The device manager's own refusals (not connected, restarting, no
        # simulator) carry the device's id and keep their sentence. One a
        # driver or transport raised reads as not connected: its text is not
        # the reader's.
        own = getattr(exc, "device_id", None) is not None
        programmer = str(exc) if own else f"Device '{device_id}' is not connected"
        if own or isinstance(exc, _WORDED_CONNECTION_ERRORS):
            return CommandFailure(programmer, for_the_room(exc))
        return CommandFailure(programmer, for_the_room(not_connected(device_id)))

    refused = isinstance(exc, CommandRefusedError) and str(exc).strip()
    (log.warning if refused else log.error)(
        f"[{device_id}] Command '{command}' failed: {type(exc).__name__}: {exc}"
    )
    if refused:
        return CommandFailure(str(exc), str(exc))
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        # Every timeout reads the same; friendly_error knows only some of
        # httpx's by type.
        timeout = TimeoutError()
        return CommandFailure(
            friendly_error(timeout, device=name, host=host), for_the_room(timeout),
        )
    label = _command_label(driver, command)
    return CommandFailure(
        f"{name} couldn't run {label}. The Device Log on its page has the details.",
        f"{name} couldn't run {label}. Contact support.",
    )


def friendly_error(
    exc: Exception, device: str = "", host: str = "", *, at_panel: bool = False
) -> str:
    """
    Convert a Python exception to a user-friendly error message.

    Args:
        exc: The exception to translate.
        device: Optional device name/ID for context.
        host: Optional host/IP for context.
        at_panel: The sentence is for somebody standing at a panel, who can
            reach no setting: a failure only the Programmer can fix says so
            in words for the room.

    Returns:
        A human-readable error string with actionable guidance.
    """
    if isinstance(exc, CommandOutcomeError):
        return str(exc)

    device_label = f" '{device}'" if device else ""
    host_label = host or "the device"

    # Connection refused
    if isinstance(exc, ConnectionRefusedError):
        return (
            f"Could not connect to{device_label}. "
            f"Check that the device is powered on and the IP address ({host_label}) is correct."
        )

    # Timeout
    if isinstance(exc, (
        TimeoutError, asyncio.TimeoutError,
        httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    )):
        target = f"{device} ({host})" if device and host else device or host or "The device"
        return (
            f"{target} did not respond in time. "
            "Check its power, network connection and configured port, then retry."
        )

    # Connection reset / broken pipe
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return (
            f"Lost connection to{device_label}. "
            "The device closed the connection unexpectedly. It may have rebooted or the network dropped."
        )

    # Connection aborted
    if isinstance(exc, ConnectionAbortedError):
        return (
            f"Connection to{device_label} was aborted. "
            "The device or network terminated the connection."
        )

    # General connection error (catch-all for ConnectionError subclasses)
    if isinstance(exc, ConnectionError):
        msg = str(exc)
        # A commanded restart, refused while the device is still coming back.
        # Same refusal as "not connected" and a different instruction: nobody
        # needs to go and look at anything, they need to wait. Read off the
        # attribute rather than the message so the sentence can be reworded
        # without this branch quietly stopping to match.
        # A broadcast or multicast refused because the adapter chosen in Reach
        # devices through has no address: the device is not what is missing,
        # and the refusal already says what to do -- in Settings, which is
        # the integrator's to open, not the room's.
        from openavc.reach_devices import AdapterUnavailable

        from openavc.core.device_manager import NoSimulatorError

        # Refused because a running simulation has no simulator for this
        # device: the device card's own sentence, which says what to do.
        if isinstance(exc, NoSimulatorError):
            return msg
        if isinstance(exc, AdapterUnavailable):
            if at_panel:
                return (
                    f"{device or 'The equipment'} can't be reached: the network "
                    "connection to it is down. Contact support."
                )
            return msg
        restart_seconds = getattr(exc, "restart_seconds", None)
        if restart_seconds is not None:
            unit = "second" if restart_seconds == 1 else "seconds"
            return (
                f"{device} is restarting. It should be back in about "
                f"{restart_seconds} {unit}."
            ) if device else msg
        # Our own refusal is already a sentence somebody can read -- the device
        # manager raises "Device 'x' is not connected" when a command is sent
        # to a device that is not there. Wrapping that produced "Connection
        # error for 'Ceiling Projector': Device 'projector' is not connected",
        # which says the same thing twice and hands the reader an internal id
        # they have never seen. It is the commonest failure a panel reports, so
        # it is the sentence that has to be right.
        if "not connected" in msg:
            return f"{device} is not connected." if device else msg
        return f"Connection error for{device_label}: {msg}"

    # OS-level network errors
    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)

        # No route to host
        if err == errno.EHOSTUNREACH or _matches_errno(exc, 113):
            return (
                f"No route to host. Check that {host_label} is on the same network "
                "and the IP address is correct."
            )

        # Network unreachable
        if err == errno.ENETUNREACH:
            return (
                "Network unreachable. Check that the server has a valid network connection."
            )

        # Address already in use
        if err == errno.EADDRINUSE:
            return (
                f"Address already in use. Another process may be using the same port for {host_label}."
            )

        # Permission denied (file or network)
        if isinstance(exc, PermissionError):
            return (
                "Permission denied. Check credentials, file permissions, or firewall rules."
            )

        # Disk-related errors
        if err == errno.ENOSPC:
            return "Disk is full. Free up disk space and try again."

        if err == errno.EROFS:
            return "File system is read-only. Check disk mount permissions."

    # ValueError from our own code (e.g., "Device 'x' not found")
    if isinstance(exc, ValueError):
        from openavc.core.device_manager import DeviceNotFoundError

        from openavc.drivers.base import WakeAddressError

        if isinstance(exc, DeviceNotFoundError):
            target = device or "The requested equipment"
            return f"{target} is unavailable. Contact support."
        # A wake with no usable MAC address: the device's setup, which only
        # the Programmer can finish, so a panel says only that.
        if isinstance(exc, WakeAddressError):
            if at_panel:
                return (
                    f"{device or 'The equipment'} can't be turned on until its "
                    "setup is finished. Contact support."
                )
            return exc.sentence(device)
        msg = str(exc)
        # Already human-readable messages from device_manager / macro_engine
        if "not found" in msg or "not connected" in msg or "blocked" in msg:
            return msg
        return f"Invalid value: {msg}"

    # RuntimeError (e.g., conditional depth limit)
    if isinstance(exc, RuntimeError):
        return str(exc)

    # Generic fallback: type name + message
    return f"Unexpected error: {exc}"


def friendly_save_error(exc: Exception) -> str:
    """
    Convert a save/file-write exception to a user-friendly message.
    """
    if isinstance(exc, PermissionError):
        return (
            "Could not save the project. Permission denied. "
            "Ensure the project directory has write permissions."
        )

    if isinstance(exc, OSError):
        err = getattr(exc, "errno", None)
        if err == errno.ENOSPC:
            return (
                "Could not save the project. The disk is full. "
                "Free up disk space and try again."
            )
        if err == errno.EROFS:
            return "Could not save the project. The file system is read-only."
        return (
            f"Could not save the project: {exc}. "
            "Check disk space and directory permissions."
        )

    return f"Could not save the project: {exc}"


def _matches_errno(exc: Exception, code: int) -> bool:
    """Check if an OSError matches a specific errno, including in the string repr."""
    if getattr(exc, "errno", None) == code:
        return True
    # Some platforms embed errno in the message string
    return bool(re.search(rf"\[Errno {code}\]", str(exc)))


# Lazy import to avoid circular dependency at module level
import asyncio  # noqa: E402
