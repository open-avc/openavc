"""The ``{name}`` placeholders a driver writes into its wire templates.

``safe_substitute`` leaves a placeholder that names nothing as it is rather
than raising, so a JSON body full of braces cannot crash a send. The cost is
that a typo goes to the device as literal text and nothing says so: the
command appears to do nothing, or a response pattern can never match. The
runtime stays lenient; this is the authoring gate that makes the typo visible
(and ``ConfigurableDriver._substitute_for_wire`` warns about what only a send
can settle).

What may fill a placeholder depends on where its template sits. Every send
(a command, a device setting's write, the liveness probe, an OSC start-up
message) carries the templates the contract's one table says it does, read
through ``send_shape.send_templates``, and fills them from what
``spec.SEND_FILLS`` says its origin fills from: a command from its own
parameters, the config and the platform's values, a setting's write from
``{value}``, the config and the platform's values, a probe and a start-up
message from the config alone. A response pattern and the query lists fill
from the config, because a pattern is matched and a query sent long after
any command's parameters are gone. A placeholder is ``spec.PLACEHOLDER``.

Two doors. The templates every door has always judged (a command's send,
path, body, address, headers, read-back queries and ``udp:`` block, the
response patterns and the query lists) are ``substitution_issues``; every
other template a send carries is ``authored_substitution_issues``, refused
where a driver is written and named where one loads, so a driver that loaded
before still loads.

Each message opens with where its template sits, and ``substitution_issues``
hands that place back beside it, so the walk
(``avcdriver_semantic.validate_driver_definition``) tags each message with the
template it was found in, never with text cut from the message (a command
named ``zone:set`` holds a colon).

Purity contract: standard library plus the sibling contract modules, like the
rest of the validator, which the community catalog and the SDK run copies of.
"""

from __future__ import annotations

from typing import Any, Iterator, NamedTuple

from openavc.drivers.declared_names import (
    PLATFORM_SUBSTITUTIONS,
    UNEVALUATED_KEY,
    closest_name,
    config_field_names,
)
from openavc.drivers.driver_model import READ_BACK, read_driver, runs_as_command
from openavc.drivers.send_shape import (
    COMMAND,
    START_UP_MESSAGE,
    Send,
    Template,
    osc_value_issues,
    send_templates,
    sends,
)
from openavc.drivers.spec import SEND_FILLS, placeholder_names

# What each position is allowed to interpolate, phrased for the message.
_CMD_SOURCES = "no parameter of this command and no config field"
_CFG_SOURCES = "no config field (only config values substitute here)"
_SOURCES = {
    COMMAND: _CMD_SOURCES,
    "setting_write": "no config field and is not {value}",
    "liveness": _CFG_SOURCES,
    START_UP_MESSAGE: _CFG_SOURCES,
}

# The command fields whose placeholders every door has always refused. Every
# other template a send carries is refused at the author's doors only and
# named where a driver loads, so a driver that loaded before still loads.
_EVERY_DOOR_FIELDS = ("send", "path", "body", "address", "headers")

# A command's udp: block's fields that carry a template.
_UDP_FIELDS = ("payload", "host", "port")


class _Fills(NamedTuple):
    """What a template in one position may fill from: the names that resolve,
    those sources phrased for the message, and what a placeholder naming
    nothing would do there."""

    names: set[str]
    sources: str
    effect: str = "it would be sent to the device as written"


class _Position(NamedTuple):
    """One template, where it sits, what may fill it, and whether every door
    refuses a placeholder there that names nothing."""

    where: str
    template: Any
    fills: _Fills
    every_door: bool = True


def validate_substitutions(driver_def: dict[str, Any]) -> list[str]:
    """Every ``{name}`` a driver writes into a wire template must resolve.

    Reads the templates every door judges: a command's send, path, body,
    address, headers, read-back queries and ``udp:`` block, a response's
    ``match`` pattern, and the query lists (the poll, the start-up and each
    response rule's ``then_query``).
    """
    return [message for _, message in substitution_issues(driver_def)]


def substitution_issues(driver_def: dict[str, Any]) -> list[tuple[str, str]]:
    """``validate_substitutions``' messages, each with the place of the
    template it was found in, in the same order."""
    return _issues(driver_def, every_door=True)


def authored_substitution_issues(driver_def: dict[str, Any]) -> list[tuple[str, str]]:
    """The placeholders only the author's doors refuse, each with its place:
    those in a command's ``query_params`` and OSC args, a device setting's
    write, the liveness probe and an OSC start-up message's args."""
    return _issues(driver_def, every_door=False)


def authored_send_issues(driver_def: dict[str, Any]) -> list[tuple[str, str]]:
    """What a send carries that only the author's doors refuse, each with its
    place: an OSC argument whose written value its type cannot take
    (``send_shape.osc_value_issues``), then each placeholder in a template
    no door judged before (``authored_substitution_issues``)."""
    return osc_value_issues(driver_def) + authored_substitution_issues(driver_def)


def _issues(driver_def: dict[str, Any], *, every_door: bool) -> list[tuple[str, str]]:
    return [
        (position.where, message)
        for position in _template_positions(driver_def)
        if position.every_door == every_door
        for message in _unresolved_substitutions(position.where, position.template, position.fills)
    ]


def _unresolved_substitutions(where: str, template: Any, fills: _Fills) -> list[str]:
    """The placeholders in one template that resolve to nothing, each with a
    did-you-mean when a name it may fill from is close."""
    if not isinstance(template, str):
        return []
    errors: list[str] = []
    for name in placeholder_names(template):
        if name in fills.names:
            continue
        suggestion = closest_name(name, fills.names)
        hint = f" (did you mean '{suggestion}'?)" if suggestion else ""
        errors.append(
            f"{where}: '{{{name}}}' resolves to nothing{hint}. It names "
            f"{fills.sources}, so {fills.effect}"
        )
    return errors


def _template_positions(driver_def: dict[str, Any]) -> Iterator[_Position]:
    """Every template the driver writes, with where it sits and what may fill
    it, in the order the check reports them: each send's
    (``send_shape.sends``: the commands', the setting writes', the probe's,
    the OSC start-up messages'), the response patterns', then the query
    lists'."""
    config_names = {name for name in config_field_names(driver_def) if isinstance(name, str)}
    judged = {name: params for name, _, params in _commands_with_params(driver_def.get("commands"))}
    for send in sends(driver_def):
        yield from _send_positions(send, config_names, judged, driver_def.get("commands"))
    patterns = _Fills(config_names | PLATFORM_SUBSTITUTIONS, _CFG_SOURCES, "the pattern would look for it as written")
    for where, template in _response_patterns(driver_def.get("responses")):
        yield _Position(where, template, patterns)
    queries = _Fills(config_names | PLATFORM_SUBSTITUTIONS, _CFG_SOURCES)
    for where, template in _query_templates(driver_def):
        yield _Position(where, template, queries)


def _send_positions(
    send: Send, config_names: set[str], judged: dict[Any, dict[Any, Any]], commands: Any,
) -> Iterator[_Position]:
    """One send's templates, filled from what ``spec.SEND_FILLS`` says its
    origin fills from. A command is judged only when it declares params
    (``_commands_with_params``), and its read-back queries and ``udp:``
    block follow its own fields. An OSC start-up message's address is a
    query line, read with the query lists."""
    fills = _send_fills(send, config_names, judged)
    if fills is None:
        return
    for template in send_templates(send):
        if not (send.origin == START_UP_MESSAGE and template.field == "address"):
            yield _Position(template.place, template.text, fills, _every_door(send, template))
    if send.origin == COMMAND:
        yield from _command_positions(send, fills, commands)


def _send_fills(send: Send, config_names: set[str], judged: dict[Any, dict[Any, Any]]) -> _Fills | None:
    """What one send's templates may fill from; None for a command whose
    placeholders are not judged."""
    if send.origin != COMMAND:
        return _Fills(_fill_names(send.origin, config_names, {}), _SOURCES[send.origin])
    if send.name not in judged:
        return None
    return _Fills(_fill_names(COMMAND, config_names, judged[send.name]), _CMD_SOURCES)


def _command_positions(send: Send, fills: _Fills, commands: Any) -> Iterator[_Position]:
    """A command's read-back queries, then its ``udp:`` block's templates."""
    for where, text in _read_back_templates(send.place, send.block.get("read_back"), commands):
        yield _Position(where, text, fills)
    for where, text in _udp_templates(send.place, send.block.get("udp")):
        yield _Position(where, text, fills)


def _fill_names(origin: str, config_names: set[str], params: dict[Any, Any]) -> set[str]:
    """The names a template of ``origin`` may fill from."""
    sources = {
        "config": config_names,
        "platform": set(PLATFORM_SUBSTITUTIONS),
        "params": {key for key in params if isinstance(key, str)},
        "value": {"value"},
    }
    return set().union(*(sources[source] for source in SEND_FILLS[origin]))


def _every_door(send: Send, template: Template) -> bool:
    return send.origin == COMMAND and template.field in _EVERY_DOOR_FIELDS


def _commands_with_params(commands: Any) -> Iterator[tuple[Any, dict[str, Any], dict[Any, Any]]]:
    """Each command whose placeholders are judged, with its parameters.

    A computed commands or params block (``UNEVALUATED_KEY``) is a SUBSET of
    the real one, so "not in it" proves nothing: it is skipped rather than a
    working driver reported as broken, the same rule as the actions check.

    A command that declares no params is making no claim about its
    placeholders, and the runtime substitutes whatever the caller passed:
    ``send: "IN{input}"`` with no params block works, because a macro or
    script can supply ``input`` at call time. That is a loose shape (an
    undeclared param gets no type coercion, no range gate and no UI control)
    but not a wrong one, so this check has no standing to call it an error.
    Once a command DOES declare params it has named its inputs, and a token
    outside that set sitting beside a near-identical one inside it is the typo
    this rule exists to catch.
    """
    if not isinstance(commands, dict) or UNEVALUATED_KEY in commands:
        return
    for name, cmd in commands.items():
        if not isinstance(cmd, dict):
            continue
        params = _declared_params(cmd.get("params"))
        if params:
            yield name, cmd, params


def _declared_params(params: Any) -> dict[Any, Any] | None:
    """A params block that names the command's inputs, else None."""
    if not isinstance(params, dict) or UNEVALUATED_KEY in params:
        return None
    return params


def _udp_templates(at: str, udp: Any) -> Iterator[tuple[str, Any]]:
    """The ``udp:`` block's payload, host and port."""
    if isinstance(udp, dict):
        for field in _UDP_FIELDS:
            yield f"{at}.udp.{field}", udp.get(field)


def _read_back_templates(
    at: str, read_back: Any, commands: dict[Any, Any]
) -> Iterator[tuple[str, str]]:
    """A command's read-back queries, filled from its parameters too. One
    that names a command is that command's to fill; a single query is placed
    without an index."""
    listed = isinstance(read_back, list)
    entries = read_back if listed else [read_back] if isinstance(read_back, str) else []
    for i, entry in enumerate(entries):
        if isinstance(entry, str) and not runs_as_command(commands, entry):
            yield at + ".read_back" + (f"[{i}]" if listed else ""), entry


def _response_patterns(responses: Any) -> Iterator[tuple[str, Any]]:
    """Each response rule's ``match`` pattern."""
    if not isinstance(responses, list):
        return
    for i, response in enumerate(responses):
        if isinstance(response, dict):
            yield f"responses[{i}].match", response.get("match")


def _query_templates(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """What each query entry sends, where it sits, in the order they report:
    the poll's, the start-up's, then each response rule's ``then_query``
    (the driver model's ``wire.queries``, every entry, as written). A
    command's read-back is that command's template, filled from its
    parameters too (``_command_templates``)."""
    for line in read_driver(driver_def).wire.queries:
        if line.origin != READ_BACK:
            yield line.place, _entry_template(line.written)


def _entry_template(entry: Any) -> Any:
    """The template one query entry sends: the entry itself when it is text,
    a mapping's ``send``, else its ``address``."""
    if isinstance(entry, dict):
        return entry.get("send") or entry.get("address")
    return entry if isinstance(entry, str) else None
