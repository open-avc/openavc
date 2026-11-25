"""How a macro step reads to people.

Three things are said about a step, and all of them are built here: the
sentence it is announced with while it runs (``describe``), the context a
failed one is logged with (``failure_context``: which step, which command,
which device or group), and the failure itself, twice over (``failure``): once
as the error a log and the Programmer's run history show, and once as the
sentence somebody standing at a panel can act on.

The rule worth knowing before changing any of it: a failed macro step and the
same command pressed directly on a panel are one failure, so a device step's
failure goes through ``describe_command_failure``, the rule every command door
uses, and an offline or missing device reads the way a direct press would.
Nothing here runs a step or writes state; it reads the state store (device
names and hosts), the loaded project and the plugin actions registered now.

THE sentence for a press naming a macro the project does not have is here too
(``no_such_macro``): a control's macro action and the panel's ``macro.execute``
and ``macro.cancel`` all answer with it.

THE reasons a trigger fire, a macro run or a step did not go ahead are here
too (``SKIP_REASONS``, read through ``skip_reason``): every ``trigger.skipped``
and ``macro.skipped``, and a step's ``skipped`` progress, names one of its codes
and sends its sentence, and no other module words a skip. A new way for a fire,
a run or a step to stop is a new row, which ``tests/test_skip_reasons.py`` holds
the modules that send them to.
"""

from __future__ import annotations

from typing import Any, Callable, TYPE_CHECKING

from openavc.core.state_store import StateStore

if TYPE_CHECKING:
    from openavc.api.error_messages import CommandFailure
    from openavc.core.device_manager import DeviceManager

# (handler, plugin_id, label) for a registered plugin action, else None.
PluginActionLookup = Callable[[str], "tuple[Any, str, str] | None"]


def _wait_until_text(step: dict[str, Any]) -> str:
    key = (step.get("condition") or {}).get("key", "?")
    timeout = step.get("timeout")
    tmo = "no timeout" if timeout is None else f"{timeout}s"
    return f"Waiting for {key} ({tmo})"


def _navigate_text(step: dict[str, Any]) -> str:
    page = step.get("page", "?")
    if page == "$back":
        return "Going back"
    if page == "$dismiss":
        return "Dismissing overlay"
    return f"Navigating panel to {page}"


# The announcement for each built-in step. A step that never filled a field in
# still carries the key, holding null, so a nested field reads through `or {}`:
# a `{}` default would not apply, and this runs while ANNOUNCING the step, so
# a conditional with no condition would end the macro before any of it ran.
_ANNOUNCED: dict[str, Callable[[dict[str, Any]], str]] = {
    "device.command": lambda s: f"Sending {s.get('command', '?')} to {s.get('device', '?')}",
    "group.command": lambda s: f"Sending {s.get('command', '?')} to group {s.get('group', '?')}",
    "delay": lambda s: f"Waiting {s.get('seconds', 0)} seconds",
    "state.set": lambda s: f"Setting {s.get('key', '?')}",
    "macro": lambda s: f"Running macro {s.get('macro', '?')}",
    "event.emit": lambda s: f"Emitting {s.get('event', '?')}",
    "conditional": lambda s: f"Checking {(s.get('condition') or {}).get('key', '?')}",
    "wait_until": _wait_until_text,
    "help.request": lambda _s: "Asking for help",
    "help.clear": lambda _s: "Clearing the help request",
    "ui.navigate": _navigate_text,
}


#: What a macro that stopped says to the room when no step said why.
MACRO_STOPPED = "The macro stopped."


class StepFailed(RuntimeError):
    """A step that failed under ``stop_on_error``, ending its run.

    Its text is the Programmer's: the step's place, then its ``error``.
    ``message`` is the sentence that step's ``macro.step_error`` gave the room,
    carried up so the run's ``macro.error``, and a step that failed because of
    it (a Run Macro step, a conditional whose branch stopped), say the same.
    """

    def __init__(self, detail: str, message: str) -> None:
        super().__init__(detail)
        self.message = message


def stopped_message(exc: BaseException) -> str:
    """The sentence a run that stopped on ``exc`` ends with: the failing
    step's, else ``MACRO_STOPPED``. Never the exception's own text."""
    return exc.message if isinstance(exc, StepFailed) else MACRO_STOPPED


#: Why a trigger fire (``trigger.skipped``), a macro run (``macro.skipped``)
#: or a step (a ``macro.progress`` whose status is ``skipped``) did not go
#: ahead: each code with its sentence. The trigger's codes came first and
#: readers already match on them, so they keep their names; the macro's own
#: refusals start ``macro_`` because the setting to change is the macro's, not
#: the trigger's; a step's start with what the step does (``help_``). A
#: ``{field}`` is filled in by the exit that stops the fire, the run or the
#: step; ``{seconds}`` reads as ``_seconds_text`` writes it.
SKIP_REASONS: dict[str, str] = {
    # A trigger's fire.
    "cooldown": "The trigger's Cooldown had not passed since it last fired.",
    "conditions_not_met": "The trigger's conditions were not met.",
    "guard_not_met": "The trigger's conditions were no longer met when its wait ended.",
    "state_reverted": "The value changed back before the trigger's delay ended.",
    "invalid_condition": (
        "A condition on the trigger uses '{operator}', an operator this system does not know."
    ),
    "macro_running": "The macro was still running, and the trigger's Overlap is set to Skip.",
    "queue_timeout": (
        "The macro was still running after {seconds}, so the trigger's queued fire was "
        "dropped."
    ),
    "trigger_disabled": "The trigger was turned off while its fire waited in the queue.",
    "circular": "The trigger set itself off again while it was still firing.",
    "max_depth": (
        "The trigger was already running {limit} times at once. Its macro may be setting "
        "it off again."
    ),
    # A macro's run, refused by the macro's own settings or by the shutdown.
    "macro_cooldown": (
        "The macro's Cooldown ({seconds}) had not passed since it last started."
    ),
    "macro_overlap": "The macro was already running, and the macro's Overlap is set to Skip.",
    "macro_queue_timeout": (
        "The macro was still running after {seconds}, so this queued run was dropped."
    ),
    "stopping": "The system is stopping.",
    # A step that did not do its work, in a run that went on.
    "help_cooldown": (
        "Help was asked for less than {seconds} ago, and this step sends at most once "
        "in that time, so no new request was sent."
    ),
}


def _seconds_text(seconds: float) -> str:
    """A number of seconds as a sentence says it: "1 second", "0.5 seconds",
    "86,400 seconds". Never an exponent, which ``:g`` gives from a million on."""
    number = f"{seconds:,.3f}".rstrip("0").rstrip(".")
    return f"{number} second" if number == "1" else f"{number} seconds"


# How a field is written into a sentence, where ``str`` would not do.
_FIELD_TEXT: dict[str, Callable[[Any], str]] = {"seconds": _seconds_text}


def skip_reason(code: str, **values: Any) -> dict[str, str]:
    """THE words for a fire or a run that did not go ahead: ``code`` (a key of
    ``SKIP_REASONS``, which a client matches on) and ``message``, its sentence
    with ``values`` filled in. A code the table does not hold raises
    ``KeyError``: a reason nobody wrote down is a bug, not a new reason."""
    words = {name: _FIELD_TEXT.get(name, str)(value) for name, value in values.items()}
    return {"code": code, "message": SKIP_REASONS[code].format(**words)}


def no_such_macro(macro_id: str) -> str:
    """THE sentence for a press that names a macro the project does not have:
    a control's macro action, a preset's ``macro.execute`` and its
    ``macro.cancel``. One sentence for every reader, since the id in it is the
    only thing anybody can go and look up."""
    return f"No macro named '{macro_id}'."


class StepWording:
    """Builds every sentence the macro engine says about a step."""

    def __init__(
        self,
        state: StateStore,
        devices: DeviceManager,
        project_provider: Callable[[], Any] | None,
        plugin_action: PluginActionLookup,
    ) -> None:
        self._state = state
        self._devices = devices
        self._project_provider = project_provider
        self._plugin_action = plugin_action

    def describe(self, step: dict[str, Any]) -> str:
        """The sentence a step is announced with while it runs: its own
        ``description`` when it has one, else one made from what it does."""
        return step.get("description") or self._made_up_description(step)

    def _made_up_description(self, step: dict[str, Any]) -> str:
        action = step.get("action", "")
        announce = _ANNOUNCED.get(action)
        if announce is not None:
            return announce(step)
        plugin_action = self._plugin_action(action)
        if plugin_action is not None:
            _handler, _plugin_id, label = plugin_action
            return label
        return action

    def failure_context(self, step: dict[str, Any], index: int, total: int) -> str:
        """Which step failed, for the log and the run's own error: its place,
        its action, and what it was aimed at."""
        action = step.get("action", "unknown")
        parts = [f"step {index + 1}/{total}", action]
        command = step.get("command", "")
        if action in ("device.command", "group.command") and command:
            parts.append(f"command '{command}'")
        target = self._failure_target(action, step)
        if target:
            parts.append(target)
        return ", ".join(parts)

    def _failure_target(self, action: str, step: dict[str, Any]) -> str:
        if action == "device.command":
            device_id = step.get("device", "")
            if not device_id:
                return ""
            name = self._state.get(f"device.{device_id}.name") or device_id
            return f"on '{name}'"
        if action == "group.command":
            group_id = step.get("group", "")
            return f"on group '{group_id}'" if group_id else ""
        if action == "macro":
            sub = step.get("macro", "")
            return f"calling '{sub}'" if sub else ""
        plugin_action = self._plugin_action(action)
        if plugin_action is None:
            return ""
        _handler, plugin_id, _label = plugin_action
        return f"plugin '{plugin_id}'"

    def error_frame(
        self, macro_id: str, step: dict[str, Any], index: int, total: int,
        *, error: str, message: str, call_chain: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """One ``macro.step_error`` frame for a failed step, whoever noticed it.

        Two renderings of the failure, deliberately: ``error`` is what the
        exception said, which is what a log and the Programmer's run history
        are for (for a device command, Send Command's answer, which keeps a
        driver's own text out), and ``message`` is the same failure written for
        somebody standing at a panel. The panel has no way to translate the
        first into the second -- it holds no device names, no host and no
        exception type -- so the sentence is built here, where all three still
        exist.

        ``call_chain`` is every macro this failure happened inside, this one
        included. A panel deciding whether the failure belongs to something
        somebody there pressed needs it: press "System On", which calls
        "Projector On", and ``macro_id`` names the sub-macro nobody has heard
        of. Sorted for a stable frame -- the engine tracks the chain as a set
        (concurrent chains, not one stack), so it answers "was this inside X"
        and cannot be read back as the order they were called in.
        """
        chain = set(call_chain or ()) | {macro_id}
        return {
            "macro_id": macro_id,
            "call_chain": sorted(chain),
            "step_index": index,
            "total_steps": total,
            "action": step.get("action", ""),
            "device": step.get("device", ""),
            "group": step.get("group", ""),
            "command": step.get("command", ""),
            "error": error,
            "message": message,
            "description": self.describe(step),
        }

    def failure(self, step: dict[str, Any], exc: Exception) -> tuple[str, str]:
        """A failed step's ``error`` and ``message`` (see ``error_frame``).

        A device command step answers with the rule every command door uses,
        so a macro failure and a direct press failure read the same way
        whichever control ran them. A step that failed because a step inside
        it stopped (``StepFailed``) says what that step said. Any other step's
        exception is the platform's or a plugin's own: the Programmer's
        ``error`` keeps its text, and the room's ``message`` never quotes it.
        """
        if isinstance(exc, StepFailed):
            return str(exc), exc.message
        if step.get("action") == "device.command":
            failure = self.command_failure(
                str(step.get("device") or ""), str(step.get("command") or ""), exc,
            )
            return failure.programmer, failure.panel
        return str(exc), self._step_message(step, exc)

    def command_failure(self, device_id: str, command: str, exc: Exception) -> CommandFailure:
        """``describe_command_failure`` with this device's name, host and
        command label filled in. Deferred import, as below."""
        from openavc.api.error_messages import describe_command_failure

        return describe_command_failure(
            exc, device_id, command, state=self._state, project=self._project(),
            driver=self._devices.get_driver(device_id),
        )

    def _step_message(self, step: dict[str, Any], exc: Exception) -> str:
        """Why the step did not do what it says, in words somebody can act on.

        The same translation, with the same device context, that a panel press
        already gets -- a macro failure and a direct press failure are the same
        failure and must not read two different ways because of which control
        ran them.
        """
        if step.get("action") == "wait_until" and isinstance(exc, TimeoutError):
            # This timer watches a condition, which may be a variable rather
            # than device feedback. It does not establish a connection failure.
            description = str(step.get("description") or "").strip().rstrip(".")
            message = (
                f"Timed out: {description}." if description
                else "The requested status was not reached in time."
            )
            return f"{message} Check the system and try again."
        device_id = str(step.get("device") or "")
        if not device_id:
            return self.deviceless_message(exc)
        return self.device_message(device_id, exc)

    @staticmethod
    def deviceless_message(exc: Exception) -> str:
        """A step with no device behind it (a plugin action, Emit Event, Run
        Macro, a malformed Wait Until) that raised, for the room: the sentence
        the platform wrote for that failure (``worded_error``), else what a
        control whose script call raised says. Never the exception's own text,
        which the Programmer reads in ``error``: nothing in it was written for
        the room, and a plugin action's can hold a path or an address."""
        from openavc.api.error_messages import worded_error
        from openavc.core.script_engine import COULD_NOT_FINISH

        return worded_error(exc, at_panel=True) or COULD_NOT_FINISH

    def device_message(self, device_id: str, exc: Exception) -> str:
        """``friendly_error`` with this device's name and host filled in.

        The name matters more here than anywhere else: a macro step names the
        device by id, and the id is the one label nobody in the room has ever
        seen. Deferred import to keep the macro engine free of API imports at
        load time, matching ``ui_events`` and the project loader.
        """
        from openavc.api.error_messages import device_error_label, friendly_error

        if not device_id:
            return friendly_error(exc, at_panel=True)
        name = device_error_label(device_id, self._state, self._project(), exc=exc)
        host = self._state.get(f"device.{device_id}.host") or ""
        return friendly_error(exc, device=str(name), host=str(host), at_panel=True)

    def _project(self) -> Any:
        return self._project_provider() if self._project_provider else None
