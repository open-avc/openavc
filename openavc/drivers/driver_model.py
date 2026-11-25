"""The driver model: THE reading of what each part of a driver file means.

``read_driver(definition)`` reads a driver file once into a ``DriverModel``,
a reading of the whole file that a reader asks instead of reading the file
for itself. Each rule larger than a few lines lives in a leaf module of its
own beside this one, and the model composes them; a reader that wants one
rule imports its leaf.

Who asks it: the checker, for what the author's doors refuse
(``avcdriver_semantic``, ``model_problems``) and for where the query entries
sit (the walk's query part, ``substitution_checks``); the simulator, for the
lines it answers (``simulator/reread_queries``, ``query_handlers``) and the
validator's sets (``simulator/driver_sends``); the Device Audit, for the
commands a poll runs (``audit/driver_reading``); the runtime, for its reply
rules (``ConfigurableDriver``, once per driver file and again for a device
whose own protocol is merged over it). The runtime reads one query entry at a
time through the leaf, ``query_entry``.

What it holds so far:

* ``wire.queries``: every query entry a YAML driver sends on its own, where it
  sits (``QueryLine``): the poll's (``polling.queries``), the start-up's
  (``on_connect``), each response rule's ``then_query`` and each command's
  ``read_back`` text, in that order (the order the checker reports them),
  each read by ``query_entry.read_query_entry``. A reader that judges each
  place reads these.
* ``wire.replies``: every response rule, in file order (``ReplyRule``), each
  read by ``reply_rule.read_reply_rule``: its form, the flat mappings it
  writes with both of a rule's forms read once, and the parts a reader takes
  as written. The runtime's compile reads these
  (``compiled_protocol.compile_driver``).
* ``query_lines``: the same lines grouped by what they send (``SentLine``):
  each line once, in the order first sent, with every entry that sends it.
  A reader that answers or covers each line reads these, each with its
  filter stated.
* ``runs_as_command``: THE rule for a line that is the name of one of the
  driver's commands, which runs as that command rather than going out as
  its own text; the runtime's sender (``start_up_and_poll``), the checker's
  fill (``substitution_checks``), the simulator's coverage
  (``osc_coverage``, ``send_coverage``, ``reread_queries``) and the audit's
  status queries (``audit/driver_reading``) ask it.
* ``problems`` also holds the fields of a push listener's frame parser the
  frame table refuses where push does not refuse them at every door
  (``framing_checks.push_frame_field_issues``), an ``auth:`` timeout of 0
  or less, which every reader takes as the default (``auth_block``), a
  command whose only form is an address or IR code left empty, which sends
  nothing (``command_form.left_empty_forms``), and a command parameter or
  config field whose name no placeholder can carry, which is never filled in
  (``spec.is_placeholder_name``), a reply map's key, or a value it writes
  where its variable holds text, that YAML did not read as text
  (``yaml_text``), a regex rule's pattern that compiles as written and not
  once the driver's declared defaults are filled in, each as literal text
  (``pattern_fill``), and an enum ``values:`` entry naming an option an
  earlier entry names (``enum_options``), and a command's
  ``restarts_device_for`` that is not a whole number of seconds from 1 to
  600 (``restart_window``).

The rules worth knowing before changing anything here:

* **It never logs and never raises.** It reads every part that reads cleanly
  and goes on, so it also reads a file the checker refuses, since a door that
  loads a driver still runs one. What it cannot read, or reads but the
  author's doors refuse, is a ``ModelProblem`` on ``problems``: the place
  (the definition path the checker tags its messages with), a stable code
  and the words a person reads. Each caller decides what a problem is at its
  door: the checker says each one at the author's doors
  (``avcdriver_semantic``, ``model_problems``).
* **Frozen, and config-free.** Tuples and frozen dataclasses, so no reader
  edits a model another device shares; it holds templates, never lines filled
  with one device's config, so one model serves every device on a driver.
* **Members arrive with the rules that need them.** A part of the file no
  rule reads through the model yet has no member here.

Pure: the standard library, ``spec`` and the leaves it composes (``query_entry``,
``reply_rule``, ``framing_checks``, ``auth_block``, ``command_form``, ``yaml_text``,
``pattern_fill``, ``enum_options``, ``restart_window``), with
absolute ``openavc.`` imports (``tests/test_avcdriver_semantic_purity.py``).
Vendored with the driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.auth_block import TIMEOUT_NOT_POSITIVE, auth_problems
from openavc.drivers.command_form import FORM_LEFT_EMPTY, left_empty_forms
from openavc.drivers.enum_options import repeated_option_problems
from openavc.drivers.framing_checks import push_frame_field_issues
from openavc.drivers.pattern_fill import effective_config, unfillable_pattern
from openavc.drivers.query_entry import OSC_MESSAGE, QueryEntry, read_query_entry
from openavc.drivers.reply_rule import REGEX, ReplyRule, read_reply_rule, rule_form
from openavc.drivers.restart_window import RESTART_RANGE, restart_problems
from openavc.drivers.spec import is_placeholder_name
from openavc.drivers.yaml_text import reply_map_problems

YAML = "yaml"
PYTHON = "python"

# Where a query line sits: the poll, the start-up, a response rule's
# follow-up, a command's read-back.
POLL = "poll"
START_UP = "start_up"
THEN_QUERY = "then_query"
READ_BACK = "read_back"
ORIGINS = (POLL, START_UP, THEN_QUERY, READ_BACK)

# The code of a push listener's frame field the frame table refuses.
FRAME_FIELD = "frame_field"

# The code of a command parameter or config field whose name no placeholder
# can carry (spec.PLACEHOLDER), and the words said there.
PLACEHOLDER_NAME = "placeholder_name"
_PLACEHOLDER_NAME_WORDS = (
    "a {placeholder} cannot name it, so it is never filled in; use only letters, "
    "digits and underscores, starting with a letter or underscore"
)

# The code of a reply map's key, or a value it writes where its variable holds
# text, that YAML did not read as text (yaml_text).
NOT_TEXT = "not_text"

# The code of an enum values: list entry naming an option an earlier entry
# names (enum_options).
REPEATED_OPTION = "repeated_option"

# The code of a reply pattern that compiles as written and not once the
# declared defaults are filled in (pattern_fill), and the words said there.
PATTERN_FILL = "pattern_fill"
_PATTERN_FILL_WORDS = (
    "with the default settings filled in, the pattern does not compile ({error}); "
    "a setting's value goes in as literal text, so a {{placeholder}} cannot finish "
    "an escape or a group's name"
)

# The sections whose keys a placeholder fills from, beside each command's params.
_CONFIG_SECTIONS = ("config_schema", "default_config", "config_derived")


@dataclass(frozen=True)
class ModelProblem:
    """A part the model could not read, or reads but the author's doors
    refuse: at ``place``, a stable ``code``, and the ``words`` said there."""

    place: str
    code: str
    words: str


@dataclass(frozen=True)
class QueryLine:
    """One query entry where it sits: its ``origin``, its ``place`` in the
    definition (``polling.queries[0]``) and the entry as read.

    ``written`` is the entry as the file wrote it (a part of ``definition``,
    never edited), for a reader that judges what was written. ``source`` is
    what a follow-up or a read-back belongs to: the index of its response
    rule (``then_query``) or its command's name (``read_back``); None for
    the poll's and the start-up's."""

    origin: str
    place: str
    entry: QueryEntry
    written: Any = None
    source: int | str | None = None


@dataclass(frozen=True)
class SentLine:
    """One line a driver sends (``text``, the template as written, before any
    device's config) and every query entry that sends it (``lines``, in the
    order ``wire.queries`` holds them)."""

    text: str
    lines: tuple[QueryLine, ...]

    @property
    def origins(self) -> tuple[str, ...]:
        """Where the line is sent from, each once, in the order first sent."""
        return tuple(dict.fromkeys(line.origin for line in self.lines))


@dataclass(frozen=True)
class WireModel:
    """What a YAML driver sends and reads, before any device's config."""

    queries: tuple[QueryLine, ...]
    replies: tuple[ReplyRule, ...] = ()


@dataclass(frozen=True)
class DriverModel:
    """One driver file, read. ``definition`` is what it was read from, never
    copied or edited. ``wire`` is None for a Python driver, whose class owns
    what it sends."""

    kind: str
    definition: Mapping[str, Any]
    wire: WireModel | None
    problems: tuple[ModelProblem, ...]


def read_driver(definition: Mapping[str, Any], kind: str = YAML) -> DriverModel:
    """The model of one driver file: a YAML definition, or with ``kind``
    ``"python"`` a Python driver's ``DRIVER_INFO``. Never raises on a
    mapping; anything else reads as an empty file."""
    if not isinstance(definition, Mapping):
        definition = {}
    if kind == PYTHON:
        return DriverModel(PYTHON, definition, None, ())
    queries = tuple(_query_lines(definition))
    wire = WireModel(queries, _replies(definition))
    return DriverModel(YAML, definition, wire, _problems(definition, queries))


def _replies(definition: Mapping[str, Any]) -> tuple[ReplyRule, ...]:
    """Each response rule, read, in file order; an entry that is not a
    mapping, or a section that is not a list, is the checker's to name."""
    responses = definition.get("responses")
    state_variables = definition.get("state_variables")
    return tuple(
        read_reply_rule(rule, state_variables)
        for rule in (responses if isinstance(responses, list) else ())
        if isinstance(rule, Mapping)
    )


def _problems(definition: Mapping[str, Any], queries: tuple[QueryLine, ...]) -> tuple[ModelProblem, ...]:
    """What the author's doors refuse, in the order they are said: the query
    entries', then each leaf's, each a ``(place, words)`` under its code."""
    found = [
        ModelProblem(line.place, line.entry.problem.code, line.entry.problem.words)
        for line in queries if line.entry.problem is not None
    ]
    sources = (
        (FRAME_FIELD, push_frame_field_issues(definition)),
        (TIMEOUT_NOT_POSITIVE, auth_problems(definition.get("auth"))),
        (FORM_LEFT_EMPTY, left_empty_forms(definition.get("commands"))),
        (PLACEHOLDER_NAME, ((place, _PLACEHOLDER_NAME_WORDS) for place in _unfillable_names(definition))),
        (NOT_TEXT, reply_map_problems(definition)),
        (PATTERN_FILL, _unfillable_patterns(definition)),
        (REPEATED_OPTION, repeated_option_problems(definition)),
        (RESTART_RANGE, restart_problems(definition.get("commands"))),
    )
    for code, issues in sources:
        found += [ModelProblem(place, code, words) for place, words in issues]
    return tuple(found)


def query_lines(
    model: DriverModel,
    origins: tuple[str, ...] = ORIGINS,
    where: Callable[[QueryLine], bool] | None = None,
) -> tuple[SentLine, ...]:
    """The lines a driver sends from ``origins``, each once: the entries of
    ``wire.queries`` that send a line (``sent_text``) and pass ``where``,
    grouped by that line, in the order each is first sent (the poll's, the
    start-up's, the follow-ups', the read-backs'). Two entries that send the
    same line are one line however each is written: the line is what goes
    out. Empty for a Python driver, whose class owns what it sends (its
    ``wire`` is None)."""
    grouped: dict[str, list[QueryLine]] = {}
    for line in model.wire.queries if model.wire else ():
        text = sent_text(line)
        if text is not None and _asked_for(line, origins, where):
            grouped.setdefault(text, []).append(line)
    return tuple(SentLine(text, tuple(lines)) for text, lines in grouped.items())


def _asked_for(line: QueryLine, origins: tuple[str, ...], where: Callable[[QueryLine], bool] | None) -> bool:
    return line.origin in origins and (where is None or where(line))


def runs_as_command(commands: Any, text: Any) -> bool:
    """Whether a query line (a poll's, a start-up's, a follow-up's or a
    read-back's) runs as the declared command it names: its framing, the
    fill of its params and its reply rules. Any other line goes out as its
    own text. ``commands`` is the definition's section, as written."""
    return isinstance(text, str) and isinstance(commands, Mapping) and text in commands


def sent_text(line: QueryLine) -> str | None:
    """The line one entry sends, as written: its ``send`` (a line, a long
    line, a per-child template), or an OSC message's ``address``; None for
    an entry that sends no text (no form, or a value that is not text)."""
    sent = line.entry.address if line.entry.form == OSC_MESSAGE else line.entry.send
    return sent if isinstance(sent, str) and sent else None


def _unfillable_patterns(definition: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """The place and words of each regex rule's pattern that compiles as
    written and not once the driver's declared defaults are filled in
    (``pattern_fill.unfillable_pattern``), in file order. A ``default_config``
    that is no mapping is the shape walk's to name, and gives no defaults to
    try a pattern with."""
    responses = definition.get("responses")
    defaults = definition.get("default_config")
    if not isinstance(responses, list) or not isinstance(defaults, (Mapping, type(None))):
        return
    config = effective_config(definition)
    for i, rule in enumerate(responses):
        if isinstance(rule, Mapping) and rule_form(rule) == REGEX:
            error = unfillable_pattern(rule.get("match"), config)
            if error is not None:
                yield f"responses[{i}].match", _PATTERN_FILL_WORDS.format(error=error)


def _unfillable_names(definition: Mapping[str, Any]) -> Iterator[str]:
    """The place of each command parameter and config field whose name no
    placeholder can carry, in file order: commands first, then config."""
    commands = definition.get("commands")
    for name, block in commands.items() if isinstance(commands, Mapping) else ():
        params = block.get("params") if isinstance(block, Mapping) else None
        yield from _unfillable_keys(params, f"commands.{name}.params")
    for section in _CONFIG_SECTIONS:
        yield from _unfillable_keys(definition.get(section), section)


def _unfillable_keys(fields: Any, place: str) -> Iterator[str]:
    """A key YAML did not read as text is the shape walk's to name ("put it
    in quotes"), so only text keys are judged here."""
    if isinstance(fields, Mapping):
        yield from (
            f"{place}.{key}" for key in fields
            if isinstance(key, str) and not is_placeholder_name(key)
        )


def _query_lines(definition: Mapping[str, Any]) -> list[QueryLine]:
    polling = definition.get("polling")
    lines = _listed(POLL, "polling.queries", polling.get("queries") if isinstance(polling, Mapping) else None)
    lines += _listed(START_UP, "on_connect", definition.get("on_connect"))
    return lines + _follow_ups(definition.get("responses")) + _command_read_backs(definition.get("commands"))


def _follow_ups(responses: Any) -> list[QueryLine]:
    """Each response rule's ``then_query`` entries, in rule order."""
    lines: list[QueryLine] = []
    for i, rule in enumerate(responses if isinstance(responses, list) else ()):
        if isinstance(rule, Mapping):
            lines += _listed(THEN_QUERY, f"responses[{i}].then_query", rule.get("then_query"), i)
    return lines


def _command_read_backs(commands: Any) -> list[QueryLine]:
    """Each command's ``read_back`` lines, in command order."""
    lines: list[QueryLine] = []
    for name, command in commands.items() if isinstance(commands, Mapping) else ():
        if isinstance(command, Mapping):
            lines += _read_backs(name, command.get("read_back"))
    return lines


def _listed(origin: str, place: str, entries: Any, source: int | None = None) -> list[QueryLine]:
    """Each entry of one list (none when it is not a list: the checker says
    so). Only the start-up's list takes an OSC message."""
    if not isinstance(entries, list):
        return []
    osc_form = origin == START_UP
    return [
        QueryLine(origin, f"{place}[{i}]", read_query_entry(entry, osc_form=osc_form), entry, source)
        for i, entry in enumerate(entries)
    ]


def _read_backs(name: Any, read_back: Any) -> list[QueryLine]:
    """A command's ``read_back`` text: one line, placed without an index, or
    each text entry of a list. ``true`` sends the command again and is no
    line of its own; anything else is the checker's to name. A line naming a
    command sends that command, which is the sender's call, as for a poll."""
    place = f"commands.{name}.read_back"
    if isinstance(read_back, str):
        return [QueryLine(READ_BACK, place, read_query_entry(read_back), read_back, name)]
    if not isinstance(read_back, list):
        return []
    return [
        QueryLine(READ_BACK, f"{place}[{i}]", read_query_entry(entry), entry, name)
        for i, entry in enumerate(read_back) if isinstance(entry, str)
    ]
