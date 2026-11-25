"""The handlers a generated simulator answers the driver's reads with.

Every entry the driver sends to read a value -- a poll query, a start-up
entry, a response rule's ``then_query``, a command's ``read_back`` -- reaches
at most one of four kinds of handler, decided by its keys alone
(``query_kind``, over the one reading of an entry,
``drivers/query_entry.read_query_entry``):

* a bare string, or an entry with no ``each_child`` whose ``query_for`` is
  not dotted: a **flat** read of one variable (``QueryHandler``);
* ``query_for: <child type>.<variable>`` with no ``each_child`` (or
  ``each_child: null``): a **list** read, sent once and answered with every
  child (``ListQueryHandler``);
* ``each_child: <type>`` with ``page_size``: one **page** of a list per
  stretch of children (``ListQueryHandler`` again, with its first child);
* ``each_child: <type>`` without it: a read of **each child** on its own
  (``ChildQueryHandler``).

``each_child: null`` with an undotted ``query_for`` reaches none of them. The
builder of each kind then judges the entry's own fields and leaves out what
it cannot answer, so an entry of a kind may still build nothing: a page
smaller than two, a list no ``child_list`` rule fills, a start-up entry that
declares no ``query_for``.

The flat handlers are built once, from the poll, the follow-ups and the
read-backs (a variable inferred from a command sending the same text when the
entry names none), then the start-up entries that declare their variable; a
start-up block mixes reads with arming writes, so nothing is inferred there.
The list, page and per-child handlers are built from ``every_query_entry``
whenever the roster changes, lists first: a page names its first child the way
a per-child query does, each line filled as the runtime fills it (THE child
line fill, ``query_entry.fill_child_line``, given the roster's id through the
type's ``id_format``). The order is the order a line is offered to them, and
the first that matches answers.

What a list read covers is ``ListQueryHandler.covers``. The answers themselves
(the reply tables, the state) stay with the simulator.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from openavc.drivers.child_ids import page_starts
from openavc.drivers.driver_model import POLL, START_UP, read_driver
from openavc.drivers.query_entry import (
    EACH_CHILD,
    OSC_MESSAGE,
    QueryEntry,
    fill_child_line,
    read_query_entry,
)
from openavc.simulator.child_roster import wire_id
from openavc.simulator.command_names import infer_state_var
from openavc.simulator.reread_queries import follow_up_queries, read_back_queries

#: The four reads an entry can be (``query_kind``).
FLAT, LIST, PAGE, CHILD = "flat", "list", "page", "child"

#: The stripped line a query template arrives as (the simulator's own
#: ``_query_wire_line``: config filled in, escapes decoded, ends trimmed).
WireLine = Callable[[Any], str]


@dataclass(eq=False)
class QueryHandler:
    """A flat read: the line, and the variable whose value answers it."""

    pattern: re.Pattern
    response_var: str


@dataclass(eq=False)
class ChildQueryHandler:
    """One child's each_child poll query."""

    pattern: re.Pattern
    child_type: str
    child_id: str          # unpadded id, as the wire carries it
    response_var: str      # a child state variable


@dataclass(eq=False)
class ListQueryHandler:
    """A list read: a query sent once whose ``query_for`` names ``<child
    type>.<variable>``, or one page of a paged per-child query. Answered with
    every child the reply covers, in the shape of the driver's ``child_list``
    rule."""

    pattern: re.Pattern
    child_type: str
    response_var: str
    first: str | None = None    # the page's first child (unpadded); None = the whole list
    count: int | None = None    # children on a page; None = the whole list

    def covers(self, answer: Any, roster: list[str]) -> tuple[list[str], int | str]:
        """The children this read lists and where its list starts. By place
        (``answer.by_place``), the list runs without a gap from its first
        child to the last rostered one the page reaches; by id, it is the
        rostered children the page's range holds. No children: nothing to
        answer."""
        if answer.by_place:
            return self._by_place(answer.start, roster)
        return self._by_id(roster), self.first or ""

    def _by_place(self, default_start: int, roster: list[str]) -> tuple[list[str], int | str]:
        numbers = sorted(int(c) for c in roster if c.isdigit())
        first = self._first_place(default_start)
        if first is None or not numbers:
            return [], ""
        last = numbers[-1] if self.count is None else min(numbers[-1], first + self.count - 1)
        return [str(n) for n in range(first, last + 1)], first

    def _first_place(self, default_start: int) -> int | None:
        """The page's first place: its first child's number, the rule's own
        start for the whole list, None for a first child with no number."""
        numbered = self._numbered_first()
        if numbered is not None:
            return numbered
        return default_start if self.first is None else None

    def _by_id(self, roster: list[str]) -> list[str]:
        if self.count is None:
            return list(roster)
        low = self._numbered_first()
        if low is None:
            return list(roster)
        return [c for c in roster if c.isdigit() and low <= int(c) < low + self.count]

    def _numbered_first(self) -> int | None:
        """The page's first child as a number, when it is one."""
        return int(self.first) if self.first and self.first.isdigit() else None


def query_kind(entry: Any) -> str | None:
    """Which of the four reads an entry is (FLAT, LIST, PAGE, CHILD), or None
    for none of them, by its keys alone."""
    read = read_query_entry(entry)
    if read.form != EACH_CHILD:
        return LIST if _dotted(read) else FLAT
    if read.each_child is None:
        return LIST if _dotted(read) else None
    return PAGE if read.page_size is not None else CHILD


def _dotted(read: QueryEntry) -> bool:
    return isinstance(read.query_for, str) and "." in read.query_for


def _filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _line_pattern(wire: str) -> re.Pattern:
    return re.compile(f"^{re.escape(wire)}$")


# ── flat reads ──


def flat_handlers(definition: dict, wire_line: WireLine) -> list[QueryHandler]:
    """The flat reads: each poll query, follow-up and read-back that names its
    variable or sends a command's text, then each start-up entry that names
    its variable. The poll's and the start-up's entries are the driver
    model's (``drivers/driver_model``); the follow-ups and read-backs are
    those no earlier entry already sends (``reread_queries``)."""
    written = read_driver(definition).wire.queries
    queries = [line.written for line in written if line.origin == POLL]
    queries += follow_up_queries(definition)
    queries += read_back_queries(definition)
    state_vars = set(definition.get("state_variables", {}).keys())
    commands = definition.get("commands", {})

    handlers = [
        handler for query in queries
        if (handler := _flat_handler(query, commands, state_vars, wire_line))
    ]
    handlers += [
        handler for line in written if line.origin == START_UP
        if (handler := _start_up_handler(line.written, wire_line))
    ]
    return handlers


def _flat_handler(
    query: Any, commands: dict, state_vars: set[str], wire_line: WireLine,
) -> QueryHandler | None:
    if query_kind(query) != FLAT:
        return None
    text, declared = _flat_read(query)
    if not text:
        return None
    response_var = declared or _command_target(text, commands, state_vars)
    return QueryHandler(_line_pattern(wire_line(text)), response_var) if response_var else None


def _flat_read(query: Any) -> tuple[Any, str | None]:
    """A flat entry's query text and the variable it declares (None when it
    declares none)."""
    if not isinstance(query, dict):
        return str(query), None
    read = read_query_entry(query)
    return read.send, read.query_for if _filled(read.query_for) else None


def _command_target(text: Any, commands: dict, state_vars: set[str]) -> str | None:
    """The variable a query reads when a parameterless command sends the same
    text: the command's own ``query_for``, else what its name says (the first
    such command that says one)."""
    for cmd_name, cmd_def in commands.items():
        if cmd_def.get("send") != text or cmd_def.get("params"):
            continue
        cmd_qf = cmd_def.get("query_for")
        target = cmd_qf if _filled(cmd_qf) else infer_state_var(cmd_name, state_vars)
        if target:
            return target
    return None


def _start_up_handler(entry: Any, wire_line: WireLine) -> QueryHandler | None:
    """A start-up entry's read when it is a flat one that declares
    ``query_for`` (an OSC bring-up message is not one)."""
    read = read_query_entry(entry, osc_form=True)
    if not isinstance(entry, dict) or read.form == OSC_MESSAGE:
        return None
    if query_kind(entry) != FLAT:
        return None
    query_for, send = read.query_for, read.send
    if not (_filled(query_for) and _filled(send)):
        return None
    return QueryHandler(_line_pattern(wire_line(send)), query_for)


# ── reads of the children ──


@dataclass(frozen=True)
class Children:
    """The roster the child reads are built for: each type's ids as the
    roster holds them (unpadded text), and the types they are read against."""

    roster: dict[str, list[str]]
    types: dict[str, Any]

    def ids(self, ctype: str) -> list[str]:
        return self.roster.get(ctype, [])

    def wire_id(self, ctype: str, cid: str) -> Any:
        """A child as a ``{child_id}`` is filled with it: through the type's
        id_format (``child_roster.wire_id``), so an integer type's ``"1"`` is
        1 and a text type's ``"01"`` stays ``"01"``."""
        return wire_id(self.types.get(ctype), cid)


def child_handlers(
    entries: list[Any],
    children: Children,
    list_answers: dict,
    wire_line: WireLine,
) -> Iterator[ListQueryHandler | ChildQueryHandler]:
    """The list and page reads, in entry order, then each child's own reads,
    from ``entries`` (``reread_queries.every_query_entry``) and the roster.
    A list or page needs a ``child_list`` rule that fills its variable
    (``list_answers``)."""
    yield from (
        handler for entry in entries
        for handler in _LIST_READS.get(query_kind(entry), _no_reads)(
            entry, children, list_answers, wire_line,
        )
    )
    yield from (
        handler for entry in entries if query_kind(entry) == CHILD
        for handler in _each_child_reads(entry, children, wire_line)
    )


def _no_reads(*_args: Any) -> Iterator[ListQueryHandler]:
    return iter(())


def _list_read(
    entry: dict, children: Children, list_answers: dict, wire_line: WireLine,
) -> Iterator[ListQueryHandler]:
    read = read_query_entry(entry)
    template = read.send
    if not _filled(template):
        return
    ctype, _, var = read.query_for.partition(".")
    if not var or (ctype, var) not in list_answers:
        return
    wire = wire_line(template)
    if wire:
        yield ListQueryHandler(_line_pattern(wire), ctype, var)


def _page_reads(
    entry: dict, children: Children, list_answers: dict, wire_line: WireLine,
) -> Iterator[ListQueryHandler]:
    read = read_query_entry(entry)
    template, query_for = read.send, read.query_for
    if not (_filled(template) and isinstance(query_for, str)):
        return
    ctype, page = read.each_child, read.page_size
    if not _pages(page) or (ctype, query_for) not in list_answers:
        return
    ids = [children.wire_id(ctype, cid) for cid in children.ids(ctype)]
    for first in page_starts(ids, page):
        wire = wire_line(fill_child_line(template, first))
        if wire:
            yield ListQueryHandler(_line_pattern(wire), ctype, query_for, str(first), page)


def _pages(page: Any) -> bool:
    """A page size that pages: a whole number of two or more."""
    return isinstance(page, int) and not isinstance(page, bool) and page >= 2


def _each_child_reads(
    entry: dict, children: Children, wire_line: WireLine,
) -> Iterator[ChildQueryHandler]:
    read = read_query_entry(entry)
    ctype, template, query_for = read.each_child, read.send, read.query_for
    if not isinstance(ctype, str):
        return
    if not (_filled(template) and _filled(query_for)):
        return
    for cid in children.ids(ctype):
        wire = wire_line(fill_child_line(template, children.wire_id(ctype, cid)))
        if wire:
            yield ChildQueryHandler(_line_pattern(wire), ctype, cid, query_for)


#: The list reads by kind: sent once, or a page at a time.
_LIST_READS = {LIST: _list_read, PAGE: _page_reads}
