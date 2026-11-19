"""The driver model: THE reading of what each part of a driver file means.

``read_driver(definition)`` reads a driver file once into a ``DriverModel``,
a reading of the whole file that a reader asks instead of reading the file
for itself. Each rule larger than a few lines lives in a leaf module of its
own beside this one, and the model composes them; a reader that wants one
rule imports its leaf.

Who asks it: the checker, for what the author's doors refuse
(``avcdriver_semantic``, ``model_problems``). The runtime and the simulator
read one query entry at a time through the leaf, ``query_entry``.

What it holds so far:

* ``wire.queries``: every query entry a YAML driver sends on its own, where it
  sits (``QueryLine``): the poll's (``polling.queries``), the start-up's
  (``on_connect``) and each response rule's ``then_query``, in that order,
  each read by ``query_entry.read_query_entry``.

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

Pure: the standard library, ``spec`` and the leaves it composes, with
absolute ``openavc.`` imports (``tests/test_avcdriver_semantic_purity.py``).
Vendored with the driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from openavc.drivers.query_entry import QueryEntry, read_query_entry

YAML = "yaml"
PYTHON = "python"

# Where a query line sits: the poll, the start-up, a response rule's follow-up.
POLL = "poll"
START_UP = "start_up"
THEN_QUERY = "then_query"


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
    definition (``polling.queries[0]``) and the entry as read."""

    origin: str
    place: str
    entry: QueryEntry


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
    )
    return DriverModel(YAML, definition, WireModel(queries), problems)


def _query_lines(definition: Mapping[str, Any]) -> list[QueryLine]:
    polling = definition.get("polling")
    lines = _listed(POLL, "polling.queries", polling.get("queries") if isinstance(polling, Mapping) else None)
    lines += _listed(START_UP, "on_connect", definition.get("on_connect"), osc_form=True)
    responses = definition.get("responses")
    for i, rule in enumerate(responses if isinstance(responses, list) else ()):
        if isinstance(rule, Mapping):
            lines += _listed(THEN_QUERY, f"responses[{i}].then_query", rule.get("then_query"))
    return lines


def _listed(origin: str, place: str, entries: Any, *, osc_form: bool = False) -> list[QueryLine]:
    """Each entry of one list (none when it is not a list: the checker says so)."""
    if not isinstance(entries, list):
        return []
    return [
        QueryLine(origin, f"{place}[{i}]", read_query_entry(entry, osc_form=osc_form))
        for i, entry in enumerate(entries)
    ]
