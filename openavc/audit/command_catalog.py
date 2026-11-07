"""The command list: what a driver says about each of its commands.

The live driver's own (``DRIVER_INFO["commands"]`` on the instance, so commands
a Python driver builds once connected are there too), in the order the driver
declares them, with what the driver says about each: its label, help and
parameters; whether it is a **status query** (``query_for``, the variable its
reply reports, or a command the driver's polling runs by name); what it says it
changes (``sets``); whether it works with the device offline
(``available_offline``); how long it takes the device away
(``restarts_device_for``).

A command the driver puts on a device page (a quick action, or a command
action) is **suggested**, one to try first, unless it asks for a confirmation
or restarts the device. The confirmation text is the command's own
``confirm``, else an ``actions`` entry's, for the wizard to show first; the
server sends the command either way, as on the device page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openavc.drivers.param_validation import missing_required_params
from openavc.drivers.spec import file_param_names


@dataclass
class _PageActions:
    """What the driver's command actions say about its commands."""

    # The confirmation each asks for before it runs, by command name.
    confirms: dict[str, str] = field(default_factory=dict)
    # The commands the driver puts on a device page.
    promoted: set[str] = field(default_factory=set)


def command_catalog(driver: Any) -> list[dict[str, Any]]:
    """The driver's commands, in its own order, with what it says about each."""
    info = getattr(driver, "DRIVER_INFO", {}) or {}
    commands = info.get("commands") or {}
    if not isinstance(commands, dict):
        return []
    polled = _polled_command_names(driver)
    actions = _page_actions(driver)
    return [
        _entry(name, cdef if isinstance(cdef, dict) else {}, polled, actions)
        for name, cdef in commands.items()
    ]


def _entry(
    name: str, cdef: dict[str, Any], polled: set[str], actions: _PageActions,
) -> dict[str, Any]:
    params = _typed(cdef, "params", dict, {})
    query_for = _typed(cdef, "query_for", str, "")
    restarts = _restart_seconds(cdef)
    own_confirm = _confirm_text(cdef, name)
    return {
        "name": name,
        "label": str(cdef.get("label") or name),
        "help": str(cdef.get("help") or ""),
        "params": params,
        "query": bool(query_for) or name in polled,
        "query_for": query_for,
        "polled": name in polled,
        "sets": dict(_typed(cdef, "sets", dict, {})),
        "available_offline": bool(cdef.get("available_offline")),
        "restarts_device_for": restarts,
        "needs_input": bool(missing_required_params(params, {})),
        # A command that takes or hands back a file: sent with the file
        # chosen in the wizard, never by "Run all status queries".
        "takes_file": bool(file_param_names(cdef)),
        "returns_file": cdef.get("returns_file") is True,
        "confirm": own_confirm or actions.confirms.get(name, ""),
        "suggested": _suggested(name, restarts, own_confirm, actions),
    }


def _typed(cdef: dict[str, Any], key: str, kind: type, default: Any) -> Any:
    value = cdef.get(key)
    return value if isinstance(value, kind) else default


def _restart_seconds(cdef: dict[str, Any]) -> int:
    try:
        return int(cdef.get("restarts_device_for") or 0)
    except (TypeError, ValueError):
        return 0


def _suggested(name: str, restarts: int, own_confirm: str, actions: _PageActions) -> bool:
    """One to try first: on a device page, and nothing to warn about."""
    if name not in actions.promoted or name in actions.confirms:
        return False
    return not restarts and not own_confirm


def _polled_command_names(driver: Any) -> set[str]:
    """The commands a YAML driver's polling runs by name (its status queries)."""
    sent = _polled_sends(driver)
    commands = (getattr(driver, "DRIVER_INFO", {}) or {}).get("commands") or {}
    return {text for text in sent if isinstance(text, str) and text in commands}


def _polled_sends(driver: Any) -> list[Any]:
    """What each of a YAML driver's poll queries sends: a command's name, or
    the text itself."""
    definition = getattr(driver, "_definition", None) or {}
    queries = (definition.get("polling") or {}).get("queries") or []
    return [query.get("send") if isinstance(query, dict) else query for query in queries]


def _page_actions(driver: Any) -> _PageActions:
    """The confirmation each of the driver's command actions asks for before
    it runs (the driver's own words, or a plain one for ``confirm: true``),
    and the commands the driver puts on a device page (its quick actions and
    command actions)."""
    from openavc.drivers.actions import resolve_device_actions

    info = getattr(driver, "DRIVER_INFO", {}) or {}
    config = getattr(driver, "config", None)
    actions = _PageActions()
    for action in resolve_device_actions(info, config if isinstance(config, dict) else None):
        name = action.get("command")
        if action.get("kind") != "command" or not isinstance(name, str):
            continue
        actions.promoted.add(name)
        confirm = action.get("confirm")
        if confirm:
            actions.confirms.setdefault(name, _action_confirm(confirm, action, name))
    return actions


def _action_confirm(confirm: Any, action: dict[str, Any], name: str) -> str:
    if isinstance(confirm, str):
        return confirm
    return f"The driver asks for a confirmation before {action.get('label') or name} runs."


def _confirm_text(cdef: dict[str, Any], name: str) -> str:
    """The command's own ``confirm``: its sentence, a plain one for
    ``confirm: true``, or "" for none."""
    confirm = cdef.get("confirm")
    if isinstance(confirm, str) and confirm:
        return confirm
    if confirm is True:
        return f"The driver asks for a confirmation before {cdef.get('label') or name} runs."
    return ""
