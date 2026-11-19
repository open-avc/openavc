"""What one query entry is: THE reading of its form and the keys it carries.

A query entry is one line of a YAML driver's ``polling.queries``, its
``on_connect`` or a text rule's ``then_query``. Its form is decided by its
keys, the first that applies:

* a string: ``LINE``, sent as written (a raw protocol line, or the name of a
  declared command: which of the two is the sender's call, by the commands
  the driver declares);
* a mapping with ``each_child``: ``EACH_CHILD``, its ``send`` once per
  registered child of that type, or once per page of ``page_size`` children;
* a mapping with ``address``, in a list that takes the OSC message
  (``on_connect``): ``OSC_MESSAGE``, ``{address, args}`` (its ``args`` are
  the OSC start-up's to read);
* a mapping whose ``send`` is text that is not empty: ``SEND``, a line written
  as a mapping so it can carry a reason a string cannot
  (``spec.QUERY_ENTRY_REASONS``: ``when``, ``query_for``, ``when_state``);
* anything else: no form (``None``). The checker refuses it at every door
  and the runtime skips it.

A ``SEND`` entry that carries no reason is a string written the long way. The
author's doors refuse it; a door that loads the driver still sends it, so it
reads as ``SEND`` with a problem (``LONG_LINE``) that says what to change.

Defaults: a key left out, or written null, reads as absent: no ``when`` or
``when_state`` gate, no ``query_for`` variable, no ``page_size`` (one line
per child). Which form an entry takes is read from the keys it
has, as the published schema reads it: an ``each_child`` written null is
still a per-child entry naming no type, and a reason written null is still a
reason (the checker refuses the null).

Who asks it: the checker (``query_entry_checks``, and through the driver
model, ``driver_model``, the author's doors' words for a long line), the
runtime's expansion (``query_entries``, the OSC start-up in
``start_up_and_poll``, ``rereads``), and the simulator
(``simulator/query_handlers.query_kind``). The Driver Builder's
``queryEntryHelpers.ts`` still reads an entry for itself.

Pure: the standard library and ``spec`` only. Vendored with the driver
contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openavc.drivers.spec import QUERY_ENTRY_REASONS

LINE = "line"
EACH_CHILD = "each_child"
OSC_MESSAGE = "osc_message"
SEND = "send"

# The problem code of a SEND entry that carries no reason.
LONG_LINE = "long_line"

# The keys every mapping form carries, read with null as absent.
_CARRIED = ("send", "page_size", "when", "when_state", "query_for")


@dataclass(frozen=True)
class QueryProblem:
    """What a reader may still read but the author's doors refuse: a stable
    ``code`` and the ``words`` a person reads, said at the entry's place."""

    code: str
    words: str


@dataclass(frozen=True)
class QueryEntry:
    """One entry as read: its form and what it carries, as written (a value
    the checker refuses stays as it was, for the checker to name).

    ``send`` is the line a ``LINE`` or ``SEND`` entry sends and the template
    an ``EACH_CHILD`` entry fills; ``address`` is an ``OSC_MESSAGE``'s."""

    form: str | None
    send: Any = None
    each_child: Any = None
    page_size: Any = None
    when: Any = None
    when_state: Any = None
    query_for: Any = None
    address: Any = None
    problem: QueryProblem | None = None


def read_query_entry(entry: Any, *, osc_form: bool = False) -> QueryEntry:
    """``entry`` read once, as every reader of it reads it (the module
    docstring has the forms and the defaults). ``osc_form``: the list takes
    an OSC ``{address, args}`` entry (``on_connect``). Never raises."""
    if isinstance(entry, str):
        return QueryEntry(LINE, send=entry)
    if not isinstance(entry, dict):
        return QueryEntry(None)
    return _read_mapping(entry, osc_form)


def _read_mapping(entry: dict[str, Any], osc_form: bool) -> QueryEntry:
    carried = {key: entry.get(key) for key in _CARRIED}
    if "each_child" in entry:
        return QueryEntry(EACH_CHILD, each_child=entry["each_child"], **carried)
    if osc_form and "address" in entry:
        return QueryEntry(OSC_MESSAGE, address=entry["address"], **carried)
    send = carried["send"]
    if not isinstance(send, str) or not send:
        return QueryEntry(None, **carried)
    return QueryEntry(SEND, problem=_reason_missing(entry), **carried)


def _reason_missing(entry: dict[str, Any]) -> QueryProblem | None:
    """A ``{send}`` entry's problem: none when it carries a reason to be a
    mapping, else ``LONG_LINE``."""
    if any(key in entry for key in QUERY_ENTRY_REASONS):
        return None
    return _long_line()


def _long_line() -> QueryProblem:
    reasons = [f"'{key}'" for key in QUERY_ENTRY_REASONS]
    named = ", ".join(reasons[:-1]) + f" or {reasons[-1]}"
    return QueryProblem(
        LONG_LINE,
        f"a mapping with only 'send' is a plain query written the long way: "
        f"write it as a string, or add {named}",
    )
