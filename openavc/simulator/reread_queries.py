"""The queries a driver sends besides its poll and start-up, for the simulator to answer.

A response rule's ``then_query:`` and a command's ``read_back:`` send queries
of their own. The runtime sends them (``openavc.drivers.rereads``); the
auto-generated simulator (``yaml_auto``) needs a reply handler for each, and
the validator (``validate``) checks that it has one. ``follow_up_queries`` and
``read_back_queries`` list them in the shapes a poll entry takes, leaving out
what the poll, the start-up or an earlier entry already sends, so each reply
gets one handler.
``every_query_entry`` puts the poll and the start-up in front of them, the
list the simulator's per-child and list reads are answered from.

A rule's ``then_query`` is read through the compile's own reader
(``rule_follow_ups`` in ``openavc.drivers.compiled_protocol``), and a query
line's placeholders through ``send_templates``, the token the command
templates are read with.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from openavc.drivers.compiled_protocol import rule_follow_ups
from openavc.simulator.send_templates import declared_placeholders, rename_placeholder


_CHILD_ID_TOKEN = re.compile(r"\{child_id(?::[^{}]*)?\}")


def _poll_and_start_up(definition: dict[str, Any]) -> list[Any]:
    """The entries a driver's poll and its start-up send, as authored: what
    ``follow_up_queries`` and ``read_back_queries`` leave out, so the
    simulator builds one handler per reply rather than one per sender."""
    polling = definition.get("polling")
    sent: list[Any] = []
    if isinstance(polling, dict) and isinstance(polling.get("queries"), list):
        sent += polling["queries"]
    if isinstance(definition.get("on_connect"), list):
        sent += definition["on_connect"]
    return sent


def _first_of_each(items: Iterable[Any], exclude: list[Any] | tuple[()] = ()) -> list[Any]:
    """Each item once, in the order first seen, leaving out any in ``exclude``
    (compared whole, as ``==`` compares them)."""
    kept: list[Any] = []
    for item in items:
        if item not in kept and item not in exclude:
            kept.append(item)
    return kept


def _is_text_rule(resp: Any) -> bool:
    """A regex rule: neither a JSON-body nor an OSC address rule."""
    if not isinstance(resp, dict):
        return False
    return not (resp.get("json") or "address" in resp)


def _single_child_type(resp: dict[str, Any]) -> str | None:
    """The one child type a rule's ``child_set`` writes, or None when it
    names none or several."""
    routes = resp.get("child_set")
    types = {
        str(e.get("type")) for e in routes if isinstance(e, dict)
    } if isinstance(routes, list) else set()
    return next(iter(types)) if len(types) == 1 else None


def _names_the_child(send: Any) -> bool:
    """A query text carrying a ``{child_id}`` placeholder."""
    return isinstance(send, str) and bool(_CHILD_ID_TOKEN.search(send))


def _routed_follow_up(entry: Any, child_type: str | None) -> Any:
    """A follow-up as the simulator answers it: an entry sent once whose
    ``{child_id}`` names the child the rule writes, for every child of that
    type (``each_child``); any other entry as written."""
    if isinstance(entry, dict) and "each_child" in entry:
        return entry
    send = entry.get("send") if isinstance(entry, dict) else entry
    if child_type is None or not _names_the_child(send):
        return entry
    routed = dict(entry) if isinstance(entry, dict) else {"send": send}
    routed["each_child"] = child_type
    return routed


def _rules_follow_ups(definition: dict[str, Any]) -> Iterator[Any]:
    """Every regex rule's follow-ups, routed, in rule order."""
    for resp in definition.get("responses") or []:
        if not _is_text_rule(resp):
            continue
        follow = rule_follow_ups(resp) or []
        child_type = _single_child_type(resp) if follow else None
        for entry in follow:
            yield _routed_follow_up(entry, child_type)


def follow_up_queries(definition: dict[str, Any]) -> list[Any]:
    """Every regex rule's ``then_query`` entries, in the shapes a poll entry
    takes: the queries a driver sends besides its poll and its start-up, for
    the simulator to answer and its checks to cover.

    An entry sent once whose ``{child_id}`` names the child the rule writes
    comes back as ``{each_child: <that child type>, ...}``: which child a
    report will name is not known ahead, so the simulator answers for every
    child of the type. An entry whose rule names no single child type is
    left as written (the validator refuses it).

    An entry the poll, the start-up or an earlier rule already sends is left
    out, compared whole: a driver that reads again what its poll reads (every
    tie, on a front panel notice) would otherwise get each reply's handler
    twice."""
    return _first_of_each(_rules_follow_ups(definition), exclude=_poll_and_start_up(definition))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass
class _ReadBackScope:
    """What one driver's read-back queries are built against."""

    commands: dict[str, Any]
    child_types: dict[str, Any]
    #: The driver's own state variables.
    declared: dict[str, Any]
    #: The text of every query the poll, start-up and follow-ups already send.
    sent_texts: set[str]


@dataclass
class _ReadBackCommand:
    """What one command's read-back queries are answered from."""

    params: dict[str, Any]
    #: The variables the command ``sets``, in order.
    set_vars: list[Any]


def _sent_texts(definition: dict[str, Any]) -> set[str]:
    """The text of every query the poll, the start-up and the rules'
    follow-ups send."""
    texts: set[str] = set()
    for entry in _poll_and_start_up(definition) + follow_up_queries(definition):
        text = entry.get("send") if isinstance(entry, dict) else entry
        if isinstance(text, str):
            texts.add(text)
    return texts


def _read_back_lines(cmd_def: dict[str, Any]) -> list[Any]:
    """A command's ``read_back``, one query or a list of them, as a list."""
    raw = cmd_def.get("read_back")
    return [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []


def _is_query_line(line: Any, commands: dict[str, Any]) -> bool:
    """A read-back entry that is a query of its own: text, not a command's
    name (the simulator answers that command already)."""
    if not isinstance(line, str) or not line:
        return False
    return line not in commands


def _fills_one_child_id(used: list[str], pdef: Any) -> bool:
    """A line whose every placeholder is the same ``child_id`` parameter."""
    return len(set(used)) == 1 and isinstance(pdef, dict) and pdef.get("type") == "child_id"


def _sets_one_child_variable(command: _ReadBackCommand, cvars: Any) -> bool:
    """A command that sets exactly one variable, which the child type declares."""
    return (
        isinstance(cvars, dict)
        and len(command.set_vars) == 1
        and command.set_vars[0] in cvars
    )


def _each_child_read_back(
    line: str, used: list[str], command: _ReadBackCommand, scope: _ReadBackScope
) -> dict[str, Any] | None:
    """A query filled from the command's child_id parameter, answered for
    every child of its type with the value the command set; None when the
    line cannot be enumerated ahead, or its template is already sent."""
    name = used[0]
    pdef = command.params.get(name)
    ctype = pdef.get("child_type") if isinstance(pdef, dict) else None
    tdef = scope.child_types.get(ctype) if isinstance(ctype, str) else None
    cvars = tdef.get("state_variables") if isinstance(tdef, dict) else None
    if not (_fills_one_child_id(used, pdef) and _sets_one_child_variable(command, cvars)):
        return None
    template = rename_placeholder(line, name, "child_id")
    if template in scope.sent_texts:
        return None
    return {"each_child": ctype, "send": template, "query_for": command.set_vars[0]}


def _plain_read_back(line: str, command: _ReadBackCommand, scope: _ReadBackScope) -> Any:
    """A query with no parameter in it: answered with the value the command
    set when it sets exactly one declared variable, else as written; None when
    it is already sent."""
    if line in scope.sent_texts:
        return None
    set_vars = command.set_vars
    if len(set_vars) == 1 and set_vars[0] in scope.declared:
        return {"send": line, "query_for": set_vars[0]}
    return line


def _read_back_entry(line: str, command: _ReadBackCommand, scope: _ReadBackScope) -> Any:
    """One read-back query as the simulator answers it, or None when it is
    left out."""
    used = declared_placeholders(line, command.params)
    if used:
        return _each_child_read_back(line, used, command, scope)
    return _plain_read_back(line, command, scope)


def _command_read_backs(cmd_def: dict[str, Any], scope: _ReadBackScope) -> Iterator[Any]:
    """One command's read-back queries that are not left out, in order."""
    sets = cmd_def.get("sets")
    command = _ReadBackCommand(
        params=_dict_or_empty(cmd_def.get("params")),
        set_vars=list(sets) if isinstance(sets, dict) else [],
    )
    for line in _read_back_lines(cmd_def):
        query = _is_query_line(line, scope.commands)
        entry = _read_back_entry(line, command, scope) if query else None
        if entry is not None:
            yield entry


def _driver_read_backs(scope: _ReadBackScope) -> Iterator[Any]:
    """Every command's read-back queries that are not left out, in order."""
    for cmd_def in scope.commands.values():
        if isinstance(cmd_def, dict):
            yield from _command_read_backs(cmd_def, scope)


def read_back_queries(definition: dict[str, Any]) -> list[Any]:
    """Every command's ``read_back`` queries, in the shapes a poll entry
    takes: what a driver sends after a command, for the simulator to answer
    and its checks to cover.

    A query that reads what the command ``sets`` (exactly one variable)
    carries that variable as its ``query_for``, so the simulator answers it
    with the value the command has just set. One filled from the command's
    child_id parameter is answered for every child of that type
    (``each_child``), the parameter's placeholder written as ``{child_id}``;
    one filled from any other parameter of the command cannot be enumerated
    ahead and is left out. A query naming a command, and ``true``, are left
    out too: the simulator answers the command, and an OSC or HTTP command's
    own address or path, already.

    A query the poll, the start-up or a response rule's follow-ups already
    send is left out, compared by its text (``follow_up_queries`` compares
    whole entries)."""
    commands = definition.get("commands")
    if not isinstance(commands, dict):
        return []
    scope = _ReadBackScope(
        commands=commands,
        sent_texts=_sent_texts(definition),
        child_types=_dict_or_empty(definition.get("child_entity_types")),
        declared=_dict_or_empty(definition.get("state_variables")),
    )
    return _first_of_each(_driver_read_backs(scope))


def every_query_entry(definition: dict[str, Any]) -> list[Any]:
    """Every entry the driver sends to read values, as authored: its poll,
    its start-up, the response rules' follow-up queries and the commands'
    read-back queries, in that order."""
    return (
        _poll_and_start_up(definition)
        + follow_up_queries(definition)
        + read_back_queries(definition)
    )

