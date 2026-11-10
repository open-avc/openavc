"""The ``{name}`` placeholders a driver writes into its wire templates.

``safe_substitute`` leaves a placeholder that names nothing as it is rather
than raising, so a JSON body full of braces cannot crash a send. The cost is
that a typo goes to the device as literal text and nothing says so: the
command appears to do nothing, or a response pattern can never match. The
runtime stays lenient; this is the authoring gate that makes the typo visible
(and ``ConfigurableDriver._substitute_for_wire`` warns about what only a send
can settle).

What may fill a placeholder depends on where its template sits, so each
place the check reads is one entry in ``_template_positions``,
with what it may fill from (``_Fills``): a command's templates take its own
parameters and the config, a response pattern and the query lists the config
alone, because a pattern is matched and a query sent long after any command's
parameters are gone. A new field that carries a template is one entry there
and one in the runtime that fills it; nothing ties the two, and three places
the runtime fills are not read here yet (a liveness probe's line, a device
setting's write, an HTTP command's query parameters). A placeholder is
``spec.PLACEHOLDER``.

Each message opens with where its template sits; the walk
(``avcdriver_semantic.validate_driver_definition``) tags each message by that
opening.

Purity contract: standard library plus the sibling contract modules, like the
rest of the validator, which the community catalog and the SDK run copies of.
"""

from __future__ import annotations

from typing import Any, Iterator, NamedTuple

from openavc.drivers.declared_names import (
    UNEVALUATED_KEY,
    closest_name,
    substitution_names,
)
from openavc.drivers.spec import placeholder_names

# What each position is allowed to interpolate, phrased for the message.
_CMD_SOURCES = "no parameter of this command and no config field"
_CFG_SOURCES = "no config field (only config values substitute here)"

# A command's own fields that carry a template, and its udp: block's.
_COMMAND_FIELDS = ("send", "path", "body", "address")
_UDP_FIELDS = ("payload", "host", "port")


class _Fills(NamedTuple):
    """What a template in one position may fill from: the names that resolve,
    those sources phrased for the message, and what a placeholder naming
    nothing would do there."""

    names: set[str]
    sources: str
    effect: str = "it would be sent to the device as written"


def validate_substitutions(driver_def: dict[str, Any]) -> list[str]:
    """Every ``{name}`` a driver writes into a wire template must resolve.

    Reads a command's send, path, body, address, headers, read-back queries
    and ``udp:`` block, a response's ``match`` pattern, and the query lists
    (the poll, the start-up and each response rule's ``then_query``).
    """
    return [
        message
        for where, template, fills in _template_positions(driver_def)
        for message in _unresolved_substitutions(where, template, fills)
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


def _template_positions(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any, _Fills]]:
    """Every template the driver writes, with where it sits and what may fill
    it, in the order the check reports them: the commands', the response
    patterns', then the query lists'."""
    config_names = substitution_names(driver_def)
    commands = driver_def.get("commands")
    for name, cmd, params in _commands_with_params(commands):
        fills = _Fills(config_names | {k for k in params if isinstance(k, str)}, _CMD_SOURCES)
        for where, template in _command_templates(name, cmd, commands):
            yield where, template, fills
    patterns = _Fills(config_names, _CFG_SOURCES, "the pattern would look for it as written")
    for where, template in _response_patterns(driver_def.get("responses")):
        yield where, template, patterns
    queries = _Fills(config_names, _CFG_SOURCES)
    for where, template in _query_templates(driver_def):
        yield where, template, queries


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


def _command_templates(
    name: Any, cmd: dict[str, Any], commands: dict[Any, Any]
) -> Iterator[tuple[str, Any]]:
    """One command's templates, in the order they report: its own fields,
    each header, each read-back query, then its ``udp:`` block's."""
    at = f"commands.{name}"
    for field in _COMMAND_FIELDS:
        yield f"{at}.{field}", cmd.get(field)
    yield from _header_templates(at, cmd.get("headers"))
    yield from _read_back_templates(at, cmd.get("read_back"), commands)
    yield from _udp_templates(at, cmd.get("udp"))


def _header_templates(at: str, headers: Any) -> Iterator[tuple[str, Any]]:
    """Each header's value; headers that are not a mapping have none."""
    if isinstance(headers, dict):
        for key, value in headers.items():
            yield f"{at}.headers.{key}", value


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
        if isinstance(entry, str) and entry not in commands:
            yield at + ".read_back" + (f"[{i}]" if listed else ""), entry


def _response_patterns(responses: Any) -> Iterator[tuple[str, Any]]:
    """Each response rule's ``match`` pattern."""
    if not isinstance(responses, list):
        return
    for i, response in enumerate(responses):
        if isinstance(response, dict):
            yield f"responses[{i}].match", response.get("match")


def _query_templates(driver_def: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """What each entry of each query list sends."""
    for where, entries in _query_lists(driver_def):
        if not isinstance(entries, list):
            continue
        for i, entry in enumerate(entries):
            yield f"{where}[{i}]", _entry_template(entry)


def _query_lists(driver_def: dict[str, Any]) -> list[tuple[str, Any]]:
    """The query lists, in the order they report: the poll's, the
    start-up's, then each response rule's ``then_query``."""
    polling = driver_def.get("polling")
    lists: list[tuple[str, Any]] = [
        ("polling.queries", polling.get("queries") if isinstance(polling, dict) else None),
        ("on_connect", driver_def.get("on_connect")),
    ]
    responses = driver_def.get("responses")
    if isinstance(responses, list):
        lists += [
            (f"responses[{i}].then_query", response.get("then_query"))
            for i, response in enumerate(responses)
            if isinstance(response, dict)
        ]
    return lists


def _entry_template(entry: Any) -> Any:
    """The template one query entry sends: the entry itself when it is text,
    a mapping's ``send``, else its ``address``."""
    if isinstance(entry, dict):
        return entry.get("send") or entry.get("address")
    return entry if isinstance(entry, str) else None
