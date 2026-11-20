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
commands a poll runs (``audit/command_catalog``). The runtime reads one query
entry at a time through the leaf, ``query_entry``.

What it holds so far:

* ``wire.queries``: every query entry a YAML driver sends on its own, where it
  sits (``QueryLine``): the poll's (``polling.queries``), the start-up's
  (``on_connect``), each response rule's ``then_query`` and each command's
  ``read_back`` text, in that order (the order the checker reports them),
  each read by ``query_entry.read_query_entry``. A reader that judges each
  place reads these.
* ``query_lines``: the same lines grouped by what they send (``SentLine``):
  each line once, in the order first sent, with every entry that sends it.
  A reader that answers or covers each line reads these, each with its
  filter stated.
* ``problems`` also holds the fields of a push listener's frame parser the
  frame table refuses where push does not refuse them at every door
  (``framing_checks.push_frame_field_issues``).

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
``framing_checks``), with
absolute ``openavc.`` imports (``tests/test_avcdriver_semantic_purity.py``).
Vendored with the driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.framing_checks import push_frame_field_issues
from openavc.drivers.query_entry import OSC_MESSAGE, QueryEntry, read_query_entry

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
    problems = tuple(
        ModelProblem(line.place, line.entry.problem.code, line.entry.problem.words)
        for line in queries if line.entry.problem is not None
    ) + tuple(
        ModelProblem(place, FRAME_FIELD, words)
        for place, words in push_frame_field_issues(definition)
    )
    return DriverModel(YAML, definition, WireModel(queries), problems)


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


def sent_text(line: QueryLine) -> str | None:
    """The line one entry sends, as written: its ``send`` (a line, a long
    line, a per-child template), or an OSC message's ``address``; None for
    an entry that sends no text (no form, or a value that is not text)."""
    sent = line.entry.address if line.entry.form == OSC_MESSAGE else line.entry.send
    return sent if isinstance(sent, str) and sent else None


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
