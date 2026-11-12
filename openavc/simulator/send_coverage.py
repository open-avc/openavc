"""Whether the simulator answers each line a text driver sends, for the validator.

The auto-generated simulator (``yaml_auto``) answers a line from its
hand-written ``command_handlers`` entries (each ``receive:`` or ``match:``),
the inline protocol's and child queries' handlers, the handlers it builds
from each command's ``send:`` template (read backwards by the same
inversion, ``send_templates.send_regex``) and its query handlers, in the
order its ``_HANDLER_CHAIN`` gives. ``SimulatorAnswers`` is the validator's
simpler model of that, once: the hand-written entries in list order (where
the simulator tries every ``respond:`` entry before any ``handler:`` entry),
then a built one. The checks differ only in which built handlers may answer:

* ``check_command_coverage``: a command's sample, by a hand-written handler
  or the one built from that command.
* ``check_poll_coverage``: a query, by a hand-written handler, a command
  whose plain ``send:`` is the query as written, or the handler built from
  any command.
* ``query_answering_handlers``: the hand-written handlers that answer a
  query the driver asks, which the response check holds to a stricter rule.

The lines themselves, and which queries each check reads, are
``driver_sends``. An OSC driver's addresses are ``osc_coverage``'s.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from openavc.simulator.driver_sends import (
    asked_queries,
    command_sample,
    effective_config,
    entry_text,
    polled_queries,
    query_line,
)
from openavc.simulator.send_templates import is_passthrough_send, send_regex
from openavc.simulator.validation_result import ValidationResult


def auto_handler_pattern(template: str, params: dict) -> re.Pattern | None:
    """The regex the auto-gen simulator builds for a command's send.

    Delegates to the shared inversion so this check stays byte-for-byte in
    step with the handler patterns yaml_auto actually builds.
    """
    try:
        return re.compile(f"^{send_regex(template, params)}$")
    except re.error:
        return None


class SimulatorAnswers:
    """Who in the simulator answers a line, as the validator models it: its
    hand-written handlers in list order, then the ones it builds from the
    driver's commands."""

    def __init__(self, sim_handlers: list, commands: dict):
        self._handlers = _compile_handler_patterns(sim_handlers)
        self._commands = commands

    def hand_written(self, line: str) -> dict | None:
        """The first hand-written handler whose pattern matches the line."""
        for pattern, handler in self._handlers:
            if pattern.match(line):
                return handler
        return None

    @staticmethod
    def built_for(send_template: str, params: dict, sample: str) -> bool:
        """Whether the handler built from this command answers its sample."""
        auto_pattern = auto_handler_pattern(send_template, params)
        return bool(auto_pattern and auto_pattern.match(sample))

    def built_from_any_command(self, query_text: Any, sample: str) -> bool:
        """Whether a handler built from any command answers a query: one whose
        plain ``send:`` is the query as written, or whose pattern matches it."""
        for cmd_def in self._commands.values():
            if not cmd_def.get("send"):
                continue
            if cmd_def.get("send") == query_text and not cmd_def.get("params"):
                return True
            if self.built_for(cmd_def["send"], cmd_def.get("params", {}), sample):
                return True
        return False


def _compile_handler_patterns(handlers: list) -> list[tuple[re.Pattern, dict]]:
    """Compile regex patterns from simulator command handlers."""
    patterns = []
    for h in handlers:
        if not isinstance(h, dict):
            continue  # _check_handler_syntax reports the malformed entry
        pattern_str = h.get("receive") or h.get("match", "")
        if not pattern_str:
            continue
        try:
            patterns.append((re.compile(f"^{pattern_str}$"), h))
        except re.error:
            pass
    return patterns


# ── Commands ──


def check_command_coverage(
    result: ValidationResult,
    commands: dict,
    sim_handlers: list,
    driver_def: dict,
) -> None:
    """Check every command has a matching handler in the simulator."""
    answers = SimulatorAnswers(sim_handlers, commands)
    prefix = driver_def.get("command_prefix") or ""

    for cmd_name, cmd_def in commands.items():
        send_template = _framed_send(cmd_def, prefix)
        if not send_template:
            continue
        params = cmd_def.get("params", {})

        # A raw passthrough sends whatever the caller typed; there is no
        # command shape to simulate, and the simulator builds no handler.
        if is_passthrough_send(send_template, params):
            continue

        sample = command_sample(send_template, params, driver_def)
        if sample is None:
            result.warning(
                "command_coverage",
                f"Could not generate sample for command '{cmd_name}' "
                f"(send: {send_template!r})"
            )
            continue
        if answers.hand_written(sample) or answers.built_for(send_template, params, sample):
            continue
        result.error(
            "command_coverage",
            f"No simulator handler matches command '{cmd_name}' "
            f"(sample: {sample!r})"
        )


def _framed_send(cmd_def: dict, prefix: str) -> Any:
    """A command's send as it goes on the wire.

    A driver-level command_prefix is on the wire ahead of every command (a
    raw: true command excepted), and the simulator's auto handlers are built
    from the framed form, so the sample has to be framed too or a
    hand-written handler that expects the frame never matches it here.
    """
    send_template = cmd_def.get("send", "")
    if not (send_template and prefix):
        return send_template
    if cmd_def.get("raw") or send_template.startswith(prefix):
        return send_template
    return prefix + send_template


# ── Queries ──


def check_poll_coverage(
    result: ValidationResult,
    queries: list,
    sim_handlers: list,
    driver_def: dict,
) -> None:
    """Check every polling query, then_query entry and read_back query
    (``driver_sends.polled_queries``) has a matching simulator handler."""
    commands = driver_def.get("commands", {})
    answers = SimulatorAnswers(sim_handlers, commands)
    config = effective_config(driver_def)
    transport = driver_def.get("transport", "tcp")

    for label, query in _each_query(polled_queries(driver_def, queries)):
        query_text = _checked_query(query, commands)
        if query_text is None:
            continue
        # Substitute config variables in query text; each_child queries get a
        # sample child id (the runtime expands them per registered child).
        # Shared with the round-trip check, which has to normalize a query the
        # same way to tell a query's answer from a write's acknowledgement.
        sample = query_line(query_text, config, transport)
        # The simulator builds a handler for every command's send template,
        # so any query matching one of those patterns is answered too.
        if answers.hand_written(sample) or answers.built_from_any_command(query_text, sample):
            continue
        result.error(
            "poll_coverage",
            f"No simulator handler matches {label}: {sample!r}"
        )


def _each_query(query_sets: Iterator[tuple[str, Any]]) -> Iterator[tuple[str, Any]]:
    """Each entry of each set with its set's name, a set read only once the
    one before it has been checked."""
    for label, entries in query_sets:
        for query in entries:
            yield label, query


def _checked_query(query: Any, commands: dict) -> Any:
    """The line poll coverage checks for an entry, or None for one it passes
    over.

    A declared ``query_for`` pairing is covered by declaration: the
    auto-generated simulator builds a query handler that answers it with the
    named state variable's value. A query naming a declared command runs as
    that command on every transport, not just HTTP and UDP — which is the
    reason the guides tell an author to poll a framed command by name rather
    than retype its wire string: named that way it picks up command_prefix /
    command_suffix, and its reply is matched. Command coverage checks those,
    so checking the bare name here only ever false-fails.
    """
    if isinstance(query, dict):
        qf = query.get("query_for")
        if isinstance(qf, str) and qf:
            return None
    query_text = entry_text(query)
    if not query_text or query_text in commands:
        return None
    return query_text


def query_answering_handlers(sim_handlers: list, driver_def: dict) -> set[int]:
    """Handlers (by identity) that answer a question the driver asks, one of
    ``driver_sends.asked_queries``. Hand-written handlers only: they are the
    ones the response check reads replies from."""
    commands = driver_def.get("commands", {})
    answers = SimulatorAnswers(sim_handlers, commands)
    config = effective_config(driver_def)
    transport = driver_def.get("transport", "tcp")

    answering: set[int] = set()
    for entry in asked_queries(driver_def):
        query_text = _asked_line(entry, commands)
        if not query_text:
            continue
        handler = answers.hand_written(query_line(query_text, config, transport))
        if handler is not None:
            answering.add(id(handler))
    return answering


def _asked_line(entry: Any, commands: dict) -> Any:
    """The line an asked entry puts on the wire, or a blank for none.

    An OSC bring-up message (an entry with an ``address``) is the OSC path's
    to check. An http/udp query may name a command instead of typing its
    wire string; what goes out is that command's send template.
    """
    if isinstance(entry, dict) and "address" in entry:
        return ""
    query_text = entry_text(entry)
    if not query_text:
        return query_text
    named = commands.get(query_text)
    if isinstance(named, dict):
        return named.get("send", "")
    return query_text
