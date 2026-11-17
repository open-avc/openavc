"""THE answer to which child a reply's value belongs to, for every reply form.

A YAML reply rule that writes a child (``child_set:``, ``child_list:``) first
finds the child its reply names. The regex, OSC and JSON rules all ask
``ReplyValues._place_child``, and the answer is one ``Placement``:

* ``PLACED``: a registered child; its values are written.
* ``UNREGISTERED``: an id the roster lacks; nothing is written. Why decides
  whether anyone is told:

  - ``NAMED``: the device named it (an id capture, an address segment, a
    literal). A device legitimately answers for ports beyond the roster a
    person configured, so this is skipped, and told to a contract observer
    (``child_unregistered``).
  - ``GAP``: a place a by-place list covers that the roster skips (a page of
    16 from channel 1 over a roster of 1, 2, 17 and 18). The page covers it
    on purpose, so nobody is told.
  - ``UNMAPPED``: a wire id the entry's ``id_map`` does not list. A rule may
    split one reply's ids across several entries, each mapping its own, so
    on every reply one entry finds its id unlisted; nobody is told.

* ``UNPLACEABLE``, with a reason: the reply carries the child's id, or the
  start of its page, in a form that cannot be read. Nothing is written, and a
  contract observer is told (``child_unplaced``):

  - ``NO_START``: a by-place page's start is not a whole number (or its
    capture did not take part), so no item on the page can be placed.
  - ``BAD_ID``: the id does not read as the child type's ``id_format``.

A reply that does not carry an entry's id at all is not a placement: an
optional capture that did not take part, an OSC address shorter than the
segment the entry reads, a list item with no id of its own, a list capture
that did not take part, or (a json rule) a body with nothing at the entry's
paths. One rule may serve replies of several widths, its later pairs
optional, so every reply leaves some entries without an id; the validator
already refuses a capture the pattern does not have.

What a reply's placements mean for the poll loop's unread-replies count is
``reply_read``: a reply whose rule tried to place a child and found none it
could place, and read nothing else, was not read. Placed, unregistered and
gap children are read, as a rule that raised is (``reply_guard``).

``UNPLACED_TEXT`` and ``placement_example`` are the words a device audit and
the Driver Builder's live test both use (``audit/observe.contract_example``).

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PLACED = "placed"
UNREGISTERED = "unregistered"
UNPLACEABLE = "unplaceable"

# Why an id the roster lacks was not written (``Placement.why``).
NAMED = "named"
GAP = "gap"
UNMAPPED = "unmapped"

# Why a reply's child could not be placed (``Placement.why``).
NO_START = "no_start"
BAD_ID = "bad_id"

# Each unplaceable reason, as the audit and the live test say it. ``{child}``
# is the child type's name.
UNPLACED_TEXT: dict[str, str] = {
    NO_START: "the reply does not say which {child} its list starts at",
    BAD_ID: "the reply's {child} id is not in a form this driver reads",
}


@dataclass(frozen=True)
class Placement:
    """Where one child entry (or one list item) of a reply went."""

    outcome: str
    child_type: str
    local_id: Any = None
    why: str = ""
    # The wire id as the reply carried it, when the reply carried one and it
    # is what could not be read (BAD_ID, NO_START).
    raw_id: Any = None


def placed(child_type: str, local_id: Any) -> Placement:
    return Placement(PLACED, child_type, local_id)


def unregistered(child_type: str, local_id: Any, why: str = NAMED) -> Placement:
    return Placement(UNREGISTERED, child_type, local_id, why)


def unplaceable(child_type: str, why: str, raw_id: Any = None) -> Placement:
    return Placement(UNPLACEABLE, child_type, why=why, raw_id=raw_id)


@dataclass
class ChildReads:
    """What a rule's child entries found in one reply: the writes for the
    children it placed and a value was read for, and every placement."""

    writes: list[tuple[str, Any, dict[str, Any]]] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)

    def add(self, placement: Placement, updates: dict[str, Any] | None = None) -> None:
        self.placements.append(placement)
        if placement.outcome == PLACED and updates:
            self.writes.append((placement.child_type, placement.local_id, updates))

    def extend(self, other: ChildReads) -> None:
        self.writes.extend(other.writes)
        self.placements.extend(other.placements)


def reply_read(placements: list[Placement], read_flat: bool) -> bool:
    """Whether a rule that matched a reply read it: False only when it tried
    to place a child, could place none (every placement unplaceable), and
    read no flat value."""
    if read_flat or not placements:
        return True
    return any(p.outcome != UNPLACEABLE for p in placements)


def placement_detail(placement: Placement) -> dict[str, Any] | None:
    """What a contract observer is told about one placement, or None when it
    is not told (placed, a gap, an id another entry maps)."""
    if placement.outcome == UNREGISTERED and placement.why == NAMED:
        return {"child_type": placement.child_type, "local_id": placement.local_id}
    if placement.outcome == UNPLACEABLE:
        detail: dict[str, Any] = {"child_type": placement.child_type, "reason": placement.why}
        if placement.raw_id is not None:
            detail["id"] = placement.raw_id
        return detail
    return None


def placement_example(detail: dict[str, Any]) -> str:
    """One placement event in a line: the child it named (``output 9``), or
    the child type and why the reply could not be placed."""
    child = str(detail.get("child_type") or "channel")
    reason = detail.get("reason")
    if reason is None:
        return f"{child} {detail.get('local_id')}"
    words = UNPLACED_TEXT.get(str(reason), str(reason)).format(child=child)
    raw = detail.get("id")
    return f'{child}: {words}' + (f' ("{raw}")' if raw is not None else "")
