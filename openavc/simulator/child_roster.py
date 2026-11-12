"""Which children a generated simulator holds: the three places its child
roster comes from, each read as a list of ids.

A child type's children are, in the order yaml_auto adds them: the ids its
``instances:`` block declares, read against this instance's config
(``declared_ids``); the children the ``simulator:`` section's dotted state
seeds name (``output.2.input: 5`` names output 2; ``seeded_children``); and
the project's children, which the manager delivers after construction
(``project_ids``). yaml_auto holds the roster and seeds each new child's
variables with their defaults; this module only reads. The rules worth
knowing before changing anything here:

* **The simulator's own reader of ``instances:``, on purpose.** There are
  three: drivers/declared_roster.py is the runtime roster, and
  child_ids.declared_child_ids the authoring prediction. This one seeds the
  simulated device from config alone, so a ``count_from_state`` count is
  never read (the simulator IS the device; there is no reported count yet),
  a count that does not read as a whole number is no children, and an
  ``ids`` that is not a list is passed over for the forms after it. A new
  ``instances:`` form is added to all three.
* **Ids are the wire's text.** The roster holds each id as unpadded text,
  the form a regex capture or an expanded ``{child_id}`` query carries; a
  project child's padded id is read back through the type's ``id_format``,
  and one it cannot read is kept as it is.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from openavc.drivers.child_ids import coerce_child_local_id
from openavc.drivers.declared_roster import COUNT_KEYS


def declared_ids(type_def: Any, config: dict) -> list:
    """The ids a child type's ``instances:`` block declares for this
    instance's config (the driver's defaults under its own), in order; empty
    when it declares none. One reader per form, the first that applies: a
    literal list, a count, a comma list from a config field."""
    inst = type_def.get("instances") if isinstance(type_def, dict) else None
    if not isinstance(inst, dict):
        return []
    if isinstance(inst.get("ids"), list):
        return _listed_ids(inst["ids"])
    if any(key in inst for key in COUNT_KEYS):
        return _counted_ids(inst, config)
    if isinstance(inst.get("ids_from"), str):
        return _ids_from_field(config.get(inst["ids_from"], ""))
    return []


def _listed_ids(ids: list) -> list:
    """``ids:``: each string or integer, as written; anything else skipped."""
    return [i for i in ids if isinstance(i, (str, int)) and not isinstance(i, bool)]


def _counted_ids(inst: dict, config: dict) -> list[int]:
    """``count``, else the config field ``count_from`` names, as 1..N."""
    raw = inst.get("count")
    if raw is None and inst.get("count_from"):
        raw = config.get(inst.get("count_from"), "")
    return list(range(1, _whole_number(raw) + 1))


def _whole_number(raw: Any) -> int:
    """A count as written or typed into a config field; 0 for one that is
    blank or does not read as a whole number."""
    try:
        text = str(raw).strip()
        return int(text) if text != "" else 0
    except (TypeError, ValueError):
        return 0


def _ids_from_field(raw: Any) -> list[str]:
    """``ids_from``: the config field's comma list, each part trimmed, the
    empty ones dropped."""
    return [part.strip() for part in str(raw or "").split(",") if part.strip()]


def seeded_children(
    state_keys: list, child_types: dict, child_vars: Callable[[str], dict],
) -> Iterator[tuple[str, str]]:
    """(type, id) for each dotted state key ``<type>.<id>.<variable>`` that
    names a declared variable of a declared type, in key order. With no
    child types nothing is read."""
    if not child_types:
        return
    for key in state_keys:
        parts = key.split(".")
        if len(parts) != 3:
            continue
        ctype, cid, prop = parts
        if ctype in child_types and prop in child_vars(ctype):
            yield ctype, cid


def project_ids(type_def: Any, entries: dict) -> list:
    """The project's children of one type, each padded id as the id the wire
    carries, or as its text when the type's id_format cannot read it (an id
    the driver could not route still names a child)."""
    return [_wire_id(type_def, padded) for padded in entries]


def _wire_id(type_def: Any, padded: Any) -> Any:
    # `is not None` and not `or`: local id 0 is a real id and falsy.
    local = coerce_child_local_id(type_def, padded)
    return local if local is not None else str(padded)
