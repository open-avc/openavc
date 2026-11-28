"""
User-friendly error message mapping.

Maps common Python exceptions to actionable messages that AV integrators
can understand and act on, instead of raw tracebacks. THE words for a device
command that did not run (``describe_command_failure``): every door that
sends one answers from them, a script's ``devices.send`` through
``script_command_error``. THE words for what a
project change did (``describe_change_failure``, ``describe_not_running``):
every door that changes the project answers from them. THE words for a press
whose own handling failed (``describe_press_failure``): the WebSocket door
and the cloud AI's simulated press answer from it.
"""

from __future__ import annotations

import errno
import functools
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterable

import httpx

from openavc.drivers.command_outcome import CommandOutcomeError
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.core.project_loader import ProjectConfig
    from openavc.core.project_reconcile import FailedPart
    from openavc.core.state_store import StateStore

# Logs only device command and setting failures, each under "[<device_id>]", and is
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
    """A device command or a press that did not run, worded for each reader.

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


def _declared_label(driver: Any, section: str, key: str, fallback: str) -> str:
    info = getattr(driver, "DRIVER_INFO", None)
    entries = info.get(section) if isinstance(info, dict) else None
    entry = entries.get(key) if isinstance(entries, dict) else None
    label = entry.get("label") if isinstance(entry, dict) else None
    return str(label or key or fallback)


def describe_command_failure(
    exc: Exception, device_id: str, command: str, *,
    state: StateStore, project: ProjectConfig | None = None, driver: Any = None,
) -> CommandFailure:
    """What every door that sent a device command says when it did not run.

    One rule for Send Command, a device action, a panel press, a control's
    script call, a script's ``devices.send`` (``script_command_error``), a
    macro or group step, the WebSocket ``command`` frame, a peer system, the
    cloud and the cloud AI. The exceptions the platform raises for
    a command, and a ``CommandRefusedError`` (the device saying no, in a
    sentence written for the reader), keep their own sentence. Anything else a
    driver raises, a ``ValueError`` or ``KeyError`` included, was not written
    for the reader and may hold what should stay private: its text is logged
    under the device's id, which puts it in the Device Log on the device's
    page, and the answer names the device and the command instead.

    ``driver`` supplies the command's label; ``project`` the device's name.
    """
    return _describe_device_failure(
        exc, device_id, state=state, project=project,
        logged_as=f"Command '{command}'",
        doing=lambda: f"run {_declared_label(driver, 'commands', command, 'the command')}",
    )


def script_command_error(
    exc: Exception, device_id: str, command: str, *,
    state: StateStore, project: ProjectConfig | None = None, driver: Any = None,
) -> Exception:
    """The exception a script's ``devices.send`` raises for a command that did
    not run: the Programmer's half of ``describe_command_failure``, since a
    script's reader is whoever wrote it, as an exception of the type that was
    raised, so a script's ``except TimeoutError`` or ``except ConnectionError``
    catches it as before.

    ``exc`` itself when its text already is the sentence (the platform's own
    refusals, a device's refusal), so nothing it carries changes. Otherwise a
    new exception of its class holding the sentence and everything ``exc``
    carried; the caller raises it from ``exc``, so the traceback keeps the
    driver's text. A class whose text is not its argument (``KeyError`` quotes
    it, a driver's class may write its own) is given the sentence through a
    subclass of the same name, which the same ``except`` catches. A class that
    cannot be built from one sentence is answered with ``exc`` as it was.

    Either way the answer carries the whole failure (``carried_command_failure``),
    so a control that called the script words it for the room without
    describing it, and logging it, a second time.
    """
    failure = describe_command_failure(
        exc, device_id, command, state=state, project=project, driver=driver,
    )
    worded = exc if str(exc) == failure.programmer else _carrying(exc, failure.programmer)
    try:
        worded.command_failure = failure
    except (AttributeError, TypeError):
        # A class that takes no attributes: the control's script call words
        # it from the exception instead.
        pass
    return worded


def carried_command_failure(exc: BaseException | None) -> CommandFailure | None:
    """The failure ``script_command_error`` built for ``exc``, when it did."""
    failure = getattr(exc, "command_failure", None)
    return failure if isinstance(failure, CommandFailure) else None


def _carrying(exc: Exception, sentence: str) -> Exception:
    """``exc``'s class, or a subclass of the same name, holding ``sentence``
    and ``exc``'s attributes; ``exc`` itself when neither can be built."""
    try:
        worded = type(exc).__new__(type(exc), sentence)
        worded.__dict__.update(vars(exc))
    except Exception:  # Catch-all: a driver's class may refuse to be built so
        return exc
    if _reads(worded, sentence):
        return worded
    sentence_class = _sentence_class(type(exc))
    if sentence_class is None:
        return exc
    worded = sentence_class.__new__(sentence_class, sentence)
    worded.__dict__.update(vars(exc))
    return worded


def _reads(exc: Exception, sentence: str) -> bool:
    try:
        return str(exc) == sentence
    except Exception:  # Catch-all: a driver's __str__ may raise anything
        return False


@functools.lru_cache(maxsize=32)
def _sentence_class(cls: type[Exception]) -> type[Exception] | None:
    """A subclass of ``cls`` whose text is its argument, named as ``cls`` is so
    a traceback reads the same; None for a class that takes no subclass."""
    try:
        return type(cls.__name__, (cls,), {
            "__str__": _first_argument,
            "__module__": cls.__module__,
            "__qualname__": cls.__qualname__,
        })
    except TypeError:
        return None


def _first_argument(self: BaseException) -> str:
    return str(self.args[0]) if self.args else ""


def describe_setting_failure(
    exc: Exception, device_id: str, key: str, *,
    state: StateStore, project: ProjectConfig | None = None, driver: Any = None,
) -> str:
    """What every door that writes a device setting says when the write failed.

    The command rule above, for Change Setting on the device's page and the
    cloud AI's setting tool: the platform's refusals of the write (no such
    setting, a value the setting does not accept, a driver with no writable
    settings) keep their sentence, and so does everything the command rule
    keeps, since a driver may write a setting by running a command. Anything
    else a driver raises is logged under the device's id and the answer names
    the device and the setting instead. Only the Programmer writes a setting,
    so there is one reader.

    ``driver`` supplies the setting's label; ``project`` the device's name.
    """
    from openavc.drivers.base import DeviceSettingValueError

    if isinstance(exc, DeviceSettingValueError):
        # UnknownDeviceSettingError too, which subclasses it.
        return str(exc)
    if isinstance(exc, NotImplementedError):
        # A fixed sentence: a Python driver may raise one with its own text.
        return f"Device '{device_id}' does not support writable settings"
    return _describe_device_failure(
        exc, device_id, state=state, project=project,
        logged_as=f"Setting '{key}'",
        doing=lambda: f"change {_declared_label(driver, 'device_settings', key, 'the setting')}",
    ).programmer


def _describe_device_failure(
    exc: Exception, device_id: str, *,
    state: StateStore, project: ProjectConfig | None, logged_as: str,
    doing: Callable[[], str],
) -> CommandFailure:
    """The rule both functions above answer with. ``doing`` is what failed, as
    the sentence names it ("run Flash LED"); ``logged_as`` the same for the log
    line ("Command 'flash_led'")."""
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
        return _for_the_room(worded, name=name, host=host, doing=doing)

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
        f"[{device_id}] {logged_as} failed: {type(exc).__name__}: {exc}"
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
    what = doing()
    return CommandFailure(
        f"{name} couldn't {what}. The Device Log on its page has the details.",
        f"{name} couldn't {what}. Contact support.",
    )


def describe_press_failure(exc: BaseException) -> CommandFailure:
    """THE words for a press whose own handling failed, outside every action
    its binding ran: the interaction raised, the server failed on the message
    carrying it, a preset's macro raised before its steps ran, or the cloud
    AI's simulated press raised. Every action that reaches a device has
    already answered through ``describe_command_failure`` by then, and a
    script call through its own sentence.

    Nothing in such an exception was written for its reader, and its text may
    hold a value that must stay private, so neither half quotes it. The room
    is told what a control's script call that raised tells it; the Programmer
    and the cloud AI, the exception's type and where the details are. The
    caller logs the exception with its traceback.
    """
    from openavc.core.script_engine import COULD_NOT_FINISH

    return CommandFailure(
        f"Server error: {type(exc).__name__}. The Programmer's Log has the details.",
        COULD_NOT_FINISH,
    )


def _for_the_room(
    exc: Exception, *, name: str, host: str, doing: Callable[[], str],
) -> str:
    """A failure in words for somebody at a panel. A command that was not sent
    because of how the driver writes it is the Programmer's to correct, so the
    panel names the command and whom to call."""
    if isinstance(exc, CommandOutcomeError) and not exc.for_the_panel:
        return f"{name} couldn't {doing()}. Contact support."
    return friendly_error(exc, device=name, host=host, at_panel=True)


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
        A human-readable error string with actionable guidance: the sentence
        written for ``exc`` (``worded_error``), else its own text framed by
        its kind (``quoted_error``).
    """
    worded = worded_error(exc, device, host, at_panel=at_panel)
    return worded if worded is not None else quoted_error(exc, device)


def worded_error(
    exc: Exception, device: str = "", host: str = "", *, at_panel: bool = False
) -> str | None:
    """The sentence the platform wrote for ``exc``, or None when the only
    words there are for it are its own text (``quoted_error``). A reader who
    must never be shown an exception's own text (the room, for a macro step
    with no device behind it) asks this one. The first kind ``exc`` is, in
    ``_WORDED``'s order, answers."""
    for kinds, word in _WORDED:
        if isinstance(exc, kinds):
            return word(exc, device, host, at_panel)
    return None


def _outcome_sentence(exc: Exception, _device: str, _host: str, _at_panel: bool) -> str:
    return str(exc)


def _refused_sentence(_exc: Exception, device: str, host: str, _at_panel: bool) -> str:
    device_label = f" '{device}'" if device else ""
    return (
        f"Could not connect to{device_label}. "
        f"Check that the device is powered on and the IP address ({host or 'the device'}) is correct."
    )


def _timed_out_sentence(_exc: Exception, device: str, host: str, _at_panel: bool) -> str:
    target = f"{device} ({host})" if device and host else device or host or "The device"
    return (
        f"{target} did not respond in time. "
        "Check its power, network connection and configured port, then retry."
    )


def _reset_sentence(_exc: Exception, device: str, _host: str, _at_panel: bool) -> str:
    device_label = f" '{device}'" if device else ""
    return (
        f"Lost connection to{device_label}. "
        "The device closed the connection unexpectedly. It may have rebooted or the network dropped."
    )


def _aborted_sentence(_exc: Exception, device: str, _host: str, _at_panel: bool) -> str:
    device_label = f" '{device}'" if device else ""
    return (
        f"Connection to{device_label} was aborted. "
        "The device or network terminated the connection."
    )


def _worded_connection_error(
    exc: Exception, device: str, _host: str, at_panel: bool,
) -> str | None:
    """A ConnectionError the platform raised on purpose: a refusal that is
    already a sentence, or a device's own state (restarting, asleep, not
    connected)."""
    refusal = _worded_refusal(exc, device, at_panel)
    return refusal if refusal is not None else _worded_device_state(exc, device)


def _worded_refusal(exc: Exception, device: str, at_panel: bool) -> str | None:
    # A broadcast or multicast refused because the adapter chosen in Reach
    # devices through has no address: the device is not what is missing,
    # and the refusal already says what to do -- in Settings, which is
    # the integrator's to open, not the room's.
    from openavc.reach_devices import AdapterUnavailable

    from openavc.core.device_manager import NoSimulatorError

    # Refused because a running simulation has no simulator for this
    # device: the device card's own sentence, which says what to do.
    if isinstance(exc, NoSimulatorError):
        return str(exc)
    if not isinstance(exc, AdapterUnavailable):
        return None
    if at_panel:
        return (
            f"{device or 'The equipment'} can't be reached: the network "
            "connection to it is down. Contact support."
        )
    return str(exc)


def _worded_device_state(exc: Exception, device: str) -> str | None:
    # Paused for Live Test or a Device Audit: every command is refused until
    # the pause ends, the wake command too, so this comes before "asleep".
    paused_for = getattr(exc, "paused_for", None)
    if paused_for is not None:
        held = f" for {paused_for}" if paused_for else ""
        return f"{device or 'The equipment'} is paused{held}. It takes commands again when the pause ends."
    # A commanded restart, refused while the device is still coming back.
    # Same refusal as "not connected" and a different instruction: nobody
    # needs to go and look at anything, they need to wait. Read off the
    # attribute rather than the message so the sentence can be reworded
    # without this branch quietly stopping to match.
    restart_seconds = getattr(exc, "restart_seconds", None)
    if restart_seconds is not None:
        return _restarting_sentence(device, restart_seconds)
    # A device put to sleep on purpose (sleeps_device): the same refusal,
    # and the instruction is the wake command, which still works.
    asleep_wake = getattr(exc, "asleep_wake", None)
    if asleep_wake is not None:
        return f"{device or 'The equipment'} is asleep. {asleep_wake} wakes it."
    # Our own refusal is already a sentence somebody can read -- the device
    # manager raises "Device 'x' is not connected" when a command is sent
    # to a device that is not there. Wrapping that produced "Connection
    # error for 'Ceiling Projector': Device 'projector' is not connected",
    # which says the same thing twice and hands the reader an internal id
    # they have never seen. It is the commonest failure a panel reports, so
    # it is the sentence that has to be right.
    if device and "not connected" in str(exc):
        return f"{device} is not connected."
    return None


def _restarting_sentence(device: str, restart_seconds: Any) -> str | None:
    if not device:
        return None
    unit = "second" if restart_seconds == 1 else "seconds"
    return f"{device} is restarting. It should be back in about {restart_seconds} {unit}."


# OS-level failures whose sentence needs nothing from the device.
_ERRNO_SENTENCES = {
    errno.ENETUNREACH: "Network unreachable. Check that the server has a valid network connection.",
    errno.ENOSPC: "Disk is full. Free up disk space and try again.",
    errno.EROFS: "File system is read-only. Check disk mount permissions.",
}


def _worded_os_error(exc: Exception, _device: str, host: str, _at_panel: bool) -> str | None:
    host_label = host or "the device"
    err = getattr(exc, "errno", None)
    # No route to host
    if err == errno.EHOSTUNREACH or _matches_errno(exc, 113):
        return (
            f"No route to host. Check that {host_label} is on the same network "
            "and the IP address is correct."
        )
    # Address already in use
    if err == errno.EADDRINUSE:
        return (
            f"Address already in use. Another process may be using the same port for {host_label}."
        )
    # Permission denied (file or network)
    if isinstance(exc, PermissionError):
        return "Permission denied. Check credentials, file permissions, or firewall rules."
    return _ERRNO_SENTENCES.get(err)


def _worded_value_error(exc: Exception, device: str, _host: str, at_panel: bool) -> str | None:
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
    return None


def quoted_error(exc: Exception, device: str = "") -> str:
    """``exc``'s own text, framed by its kind: what ``friendly_error`` says
    when the platform wrote no sentence for it (``worded_error`` is None)."""
    msg = str(exc)
    if isinstance(exc, ConnectionError):
        return _quoted_connection_error(exc, msg, device)
    if isinstance(exc, ValueError):
        # Already human-readable messages from device_manager / macro_engine
        return msg if _reads_as_a_sentence(msg) else f"Invalid value: {msg}"
    # RuntimeError (e.g., conditional depth limit)
    if isinstance(exc, RuntimeError):
        return msg
    # Generic fallback: type name + message
    return f"Unexpected error: {exc}"


# Words that mark a ValueError's text as the platform's own sentence.
_OWN_SENTENCE_WORDS = ("not found", "not connected", "blocked")


def _reads_as_a_sentence(msg: str) -> bool:
    return any(word in msg for word in _OWN_SENTENCE_WORDS)


def _quoted_connection_error(exc: Exception, msg: str, device: str) -> str:
    if "not connected" in msg:
        return msg
    if getattr(exc, "restart_seconds", None) is not None:
        return msg
    device_label = f" '{device}'" if device else ""
    return f"Connection error for{device_label}: {msg}"


# What worded_error asks, in order: the first kind an exception is answers.
# ConnectionError and TimeoutError are OSErrors, so they come before it.
_WORDED: tuple[tuple[Any, Callable[[Exception, str, str, bool], str | None]], ...] = (
    (CommandOutcomeError, _outcome_sentence),
    (ConnectionRefusedError, _refused_sentence),
    ((TimeoutError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout), _timed_out_sentence),
    ((ConnectionResetError, BrokenPipeError), _reset_sentence),
    (ConnectionAbortedError, _aborted_sentence),
    (ConnectionError, _worded_connection_error),
    (OSError, _worded_os_error),
    (ValueError, _worded_value_error),
)


_SAVE_FAILED = "Could not save the project."
_SAVE_REFUSED = "Permission denied. Ensure the project directory has write permissions."
_SAVE_UNWORDED = "Check disk space and directory permissions."

#: Why a save failed, worded without the operating system's own text, by errno.
_SAVE_FAILURES = {
    errno.ENOSPC: "The disk is full. Free up disk space and try again.",
    errno.EROFS: "The file system is read-only.",
}


def friendly_save_error(exc: Exception) -> str:
    """
    Convert a save/file-write exception to a user-friendly message. One this
    module does not word carries the exception's own text, path included:
    :func:`save_error_reason` is the same answer without it.
    """
    worded = _worded_save_why(exc)
    if worded:
        return f"{_SAVE_FAILED} {worded}"
    if isinstance(exc, OSError):
        return f"Could not save the project: {exc}. {_SAVE_UNWORDED}"
    return f"Could not save the project: {exc}"


def save_error_reason(exc: Exception) -> str:
    """Why a project save failed, in a sentence that names no path and quotes
    none of the operating system's text: what a reader off this system (the
    cloud AI's chat history) is told."""
    return f"{_SAVE_FAILED} {_save_why(exc)}"


def _save_why(exc: Exception) -> str:
    """Why a save failed, with no path and none of the operating system's
    text: the reason and the remedy when this module words it."""
    return _worded_save_why(exc) or _SAVE_UNWORDED


def _worded_save_why(exc: Exception) -> str | None:
    if isinstance(exc, PermissionError):
        return _SAVE_REFUSED
    return worded_write_failure(exc)


def backup_failure_why(exc: BaseException) -> str:
    """Why a project backup could not be saved (``backup_manager.BackupFailed``,
    or what it was raised from), as a failed save is said: the reason and
    the remedy, with no path and none of the operating system's text."""
    return _save_why(getattr(exc, "cause", exc))


def worded_write_failure(exc: BaseException) -> str | None:
    """Why a file could not be written, for a full disk or a read-only file
    system, with no path and none of the operating system's text; None for
    any other failure."""
    if isinstance(exc, OSError):
        return _SAVE_FAILURES.get(getattr(exc, "errno", None))
    return None


# --- What a project change did, in the words every door answers with ---


@dataclass(frozen=True)
class ChangeFailure:
    """A project change that did not run, as every door answers it: the
    HTTP status a route answers with, the ``code`` a client branches on
    (``conflict``, ``refused`` or ``not_applied``), and the sentence."""

    status: int
    code: str
    sentence: str


#: Why a change was not applied when the running system, not the file's
#: write, could not take it. The cause is in the log the sentence names.
_NOT_TAKEN = "the running system could not take the change. The Programmer's Log has the details."

#: Why a change that replaces the project was not applied when the backup it
#: takes first could not be saved (``backup_manager.capture``).
_BACKUP_NOT_SAVED = "a backup of the current project could not be saved first."


def describe_change_failure(exc: BaseException) -> ChangeFailure | None:
    """THE words for a project change that did not run, at every door that
    changes the project: Save and the section routes, a reload, the cloud
    AI's edit tools and a cloud config push.

    A conflict with a newer revision and a refusal of the change itself (the
    installed equipment, a word this version does not know) keep their own
    sentence, which was written for the reader; a change whose result is
    not a valid project names each field at fault, as Save names one in a
    project it was sent. A change the running system
    could not take (``ProjectNotAppliedError``) says it was not applied, why,
    and that the previous project is still running: a write that failed says
    why without the path or the operating system's text, and any other cause
    sends the reader to the Programmer's Log, where it is logged. A change
    whose backup could not be saved first (``backup_manager.BackupFailed``:
    an import, opening another project, a blank project, a restore, a cloud
    push) says so the same way. ``None`` for an exception that is not one
    of these.
    """
    from openavc.core.backup_manager import BackupFailed
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.project_changes import (
        ProjectInvalidError,
        ProjectNotAppliedError,
        ProjectRevisionConflictError,
    )
    from openavc.core.project_loader import ProjectSaveError
    from openavc.core.saved_values import UnknownWordError
    from openavc.ui.page_edits import PageIssueError

    if isinstance(exc, ProjectRevisionConflictError):
        return ChangeFailure(409, "conflict", str(exc))
    if isinstance(exc, InstalledEquipmentError):
        return ChangeFailure(409, "refused", str(exc))
    if isinstance(exc, UnknownWordError | PageIssueError):
        return ChangeFailure(422, "refused", str(exc))
    if isinstance(exc, ProjectInvalidError):
        return ChangeFailure(422, "refused", invalid_project(exc.cause))
    if isinstance(exc, BackupFailed):
        return ChangeFailure(500, "not_applied", not_applied(f"{_BACKUP_NOT_SAVED} {backup_failure_why(exc)}"))
    if not isinstance(exc, ProjectNotAppliedError):
        return None
    cause = exc.cause
    why = (
        f"the project file could not be written. {_save_why(cause.cause)}"
        if isinstance(cause, ProjectSaveError) else _NOT_TAKEN
    )
    return ChangeFailure(500, "not_applied", not_applied(why))


#: The heading over each field at fault in a project that is not valid.
INVALID_PROJECT = "Invalid project configuration"


def invalid_project(exc: Any) -> str:
    """A project that is not valid, as every door that refuses one says
    it: the heading, then each field at fault and why, one per line, never
    the value (``api/errors.validation_error_lines``)."""
    from openavc.api.errors import validation_error_lines

    lines = validation_error_lines(exc.errors())
    return f"{INVALID_PROJECT}:\n" + "\n".join(lines) if lines else INVALID_PROJECT


def not_applied(why: str) -> str:
    """The sentence for a project change that was not applied, around its
    ``why`` (lower case, a full stop at the end): every door's, so it reads
    the same wherever the reader meets it."""
    return f"Not applied: {why} The previous project is still running."


def describe_not_running(failed: Iterable[FailedPart]) -> list[dict[str, str]]:
    """The parts of an applied change the system could not run, as every
    door that answers an applied change lists them: each part's kind, its
    id, the reason as a stable code, and the sentence its own surface shows.
    Empty when every part runs."""
    return [
        {"kind": part.kind, "id": part.part_id, "reason": part.reason, "detail": part.detail}
        for part in failed
    ]


def _matches_errno(exc: Exception, code: int) -> bool:
    """Check if an OSError matches a specific errno, including in the string repr."""
    if getattr(exc, "errno", None) == code:
        return True
    # Some platforms embed errno in the message string
    return bool(re.search(rf"\[Errno {code}\]", str(exc)))
