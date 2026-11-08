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
        whichever control ran them. Any other step's exception is the
        platform's or a plugin's own, and keeps its text.
        """
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
        return self.device_message(str(step.get("device") or ""), exc)

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
