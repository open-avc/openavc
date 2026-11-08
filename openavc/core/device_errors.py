"""What the device manager raises when a device cannot do what it was asked,
and what each failure carries for the sentence somebody reads.

The doors above the manager (the REST routes, the panel, macros, scripts, the
cloud) turn these into the words a person sees, through ``friendly_error``.
They read the facts off attributes, never out of the message: ``device_id``
names the device the way somebody in the room knows it, ``restart_seconds``
and ``asleep_wake`` say why it is away, and ``failed_command`` says which
command a driver's own exception came from.

The rule worth knowing before changing it: a ``device_id`` attribute marks the
manager's own refusals, whose text is written for the reader. A driver's
exception never gets one (``mark_failed_command`` marks it differently), because
its text was written for whoever wrote the driver.

core/device_manager.py re-exports every name here, the path the rest of the
platform imports them from.
"""

from __future__ import annotations


class DeviceNotFoundError(ValueError):
    """No device with this id is registered — the id itself is what is wrong.

    Every "no such device" raise in the device manager used to be a bare ValueError,
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
