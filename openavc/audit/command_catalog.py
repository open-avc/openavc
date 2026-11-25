"""The command list: what a driver says about each of its commands.

The driver's own, as the audit reads it (``audit/driver_reading``: the live
instance's while it runs, so commands a Python driver builds once connected
are there too), in the order the driver declares them, with what the driver
says about each: its label, help and parameters; whether it is a **status
query** (``query_for``, the variable its reply reports, or a command the
driver's polling runs by name); what it says it changes (``sets``); whether it
works with the device offline (``available_offline``); how long it takes the
device away (``restarts_device_for``).

A command the driver puts on a device page (a quick action, or a command
action) is **suggested**, one to try first, unless it asks for a confirmation
or restarts the device. The confirmation text is the command's own
``confirm``, else the one its device-page button asks, each read by
``command_model.confirm_sentence``, for the wizard to show first; the server
sends the command either way, as on the device page.

A command is **sendable without asking** when it needs no value or file and
neither confirms nor restarts the device: the command's own reading
(``drivers/command_model``) joined with its page action's confirmation. "Run
all status queries", the read before a setting is written and the IDE's count
of the queries that run together ask this one field.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from openavc.audit.driver_reading import AuditedDriver
from openavc.drivers.command_model import CommandModel
from openavc.drivers.spec import file_param_names


@dataclass
class _PageActions:
    """What the driver's command actions say about its commands."""

    # The confirmation each asks for before it runs, by command name.
    confirms: dict[str, str] = field(default_factory=dict)
    # The commands the driver puts on a device page.
    promoted: set[str] = field(default_factory=set)


def command_catalog(driver: AuditedDriver) -> list[dict[str, Any]]:
    """The driver's commands, in its own order, with what it says about each."""
    polled = driver.polled_commands()
    actions = _page_actions(driver)
    return [
        _entry(cdef, command, polled, actions)
        for _name, cdef, command in driver.commands()
    ]


def _entry(
    cdef: dict[str, Any], command: CommandModel, polled: set[str], actions: _PageActions,
) -> dict[str, Any]:
    name = command.name
    params = _typed(cdef, "params", dict, {})
    query_for = _typed(cdef, "query_for", str, "")
    return {
        "name": name,
        "label": str(cdef.get("label") or name),
        "help": str(cdef.get("help") or ""),
        "params": params,
        "query": bool(query_for) or name in polled,
        "query_for": query_for,
        "polled": name in polled,
        "sets": dict(_typed(cdef, "sets", dict, {})),
        "available_offline": command.available_offline,
        # Whole seconds the runtime holds the device away for; 0 for none.
        "restarts_device_for": command.restarts_device_for or 0,
        "needs_input": command.needs_input,
        # A command that takes or hands back a file: sent with the file
        # chosen in the wizard, never by "Run all status queries".
        "takes_file": bool(file_param_names(cdef)),
        "returns_file": cdef.get("returns_file") is True,
        "confirm": command.confirm or actions.confirms.get(name, ""),
        "suggested": _suggested(command, actions),
        "sendable_without_asking": command.sendable_without_asking and name not in actions.confirms,
    }


def _typed(cdef: dict[str, Any], key: str, kind: type, default: Any) -> Any:
    value = cdef.get(key)
    return value if isinstance(value, kind) else default


def _suggested(command: CommandModel, actions: _PageActions) -> bool:
    """One to try first: on a device page, and nothing to warn about."""
    if command.name not in actions.promoted or command.name in actions.confirms:
        return False
    return not command.restarts_device_for and not command.confirm


def _page_actions(driver: AuditedDriver) -> _PageActions:
    """The confirmation each of the driver's command actions asks for before
    it runs (as the resolver words it), and the commands the driver puts on a
    device page (its quick actions and command actions)."""
    actions = _PageActions()
    for name, confirm in _command_actions(driver):
        actions.promoted.add(name)
        if confirm:
            actions.confirms.setdefault(name, confirm)
    return actions


def _command_actions(driver: AuditedDriver) -> Iterator[tuple[str, str | None]]:
    """Each command action on the driver's device page: the command it sends
    and what it asks first (None for nothing). The resolver names the command
    of every command action it serves."""
    from openavc.drivers.actions import resolve_device_actions

    config = getattr(driver.driver, "config", None)
    for action in resolve_device_actions(dict(driver.info), config if isinstance(config, dict) else None):
        if action["kind"] == "command":
            yield action["command"], action["confirm"]
