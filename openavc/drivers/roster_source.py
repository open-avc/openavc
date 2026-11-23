"""Which source a child type's ``instances:`` block names, and the ids that
source fixes for a config: THE roster-source rule.

A block names one source: ``ids`` (a literal list), ``count`` (1..N),
``count_from`` (an integer config field) or ``ids_from`` (a comma-separated
config field). ``count_from_state`` is no source of its own: it names the
state variable a ``count_from`` roster follows once the device reports a
count, and beside ``count``, ``ids`` or ``ids_from``, which fix the roster, it
is refused where a driver is written (``child_type_checks``) and followed by
no reader. ``roster_source`` is that reading, asked by the runtime roster
(``declared_roster``), the authoring prediction
(``child_ids.declared_child_ids``), the simulator (``simulator/child_roster``)
and the poll-length warning; ``config_child_ids`` is the ids a source fixes
from config alone. What a present source's unusable value gives is each
reader's own answer.

Pure stdlib, no server imports: the checker's vendored closure and the
simulator import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The order the sources are read in. A block the checker passes names one, so
# the order decides only a block naming several, which every door but a Python
# driver's load refuses.
_SOURCE_ORDER = ("ids", "count", "count_from", "ids_from")

#: The one source ``count_from_state`` goes with: a roster sized from a config
#: field follows the count the device reports once it reports one. ``count``,
#: ``ids`` and ``ids_from`` fix the roster, so beside them it is refused where a
#: driver is written and followed by no reader.
FOLLOWED_SOURCE = "count_from"


@dataclass(frozen=True)
class RosterSource:
    """Which source an ``instances:`` block names, and what it follows.

    ``kind`` is ``ids``, ``count``, ``count_from`` or ``ids_from``, or ``None``
    when the block names none (or is not a mapping). ``follows`` is the state
    variable the roster follows once the device reports a count, or ``None``.
    A source present with a value that cannot be used is still the source;
    what that value gives is each reader's own answer.
    """

    kind: str | None
    follows: str | None


def roster_source(instances: Any) -> RosterSource:
    """Which source an ``instances:`` block names, and what it follows."""
    if not isinstance(instances, dict):
        return RosterSource(None, None)
    kind = next((key for key in _SOURCE_ORDER if key in instances), None)
    follows = _followed(instances) if kind == FOLLOWED_SOURCE else None
    return RosterSource(kind, follows)


def _followed(instances: dict[str, Any]) -> str | None:
    """The state variable ``count_from_state`` names, when it names one."""
    followed = instances.get("count_from_state")
    return (followed or None) if isinstance(followed, str) else None


def config_child_ids(
    type_def: dict[str, Any] | None, config: dict[str, Any] | None,
) -> list[Any] | None:
    """The ids this child type's source fixes from ``config`` alone, or ``None``.

    ``declared_child_ids``, except that a roster that follows the device's count
    is read at its ``count_from`` field: what the runtime builds before the
    device's first report, and what a matrix of that size reports anyway.
    """
    instances = _instances(type_def)
    kind = roster_source(instances).kind
    if kind is None:
        return None
    return _SOURCE_IDS[kind](instances[kind], config if isinstance(config, dict) else {})


def _instances(type_def: Any) -> Any:
    return type_def.get("instances") if isinstance(type_def, dict) else None


def _field_value(field: Any, config: dict[str, Any]) -> Any:
    """The config value a source's field names; None for no field, or a
    field name that cannot key a mapping."""
    try:
        return config.get(field) if field else None
    except TypeError:
        return None


def _listed(ids: Any, _config: dict[str, Any]) -> list[Any] | None:
    return list(ids) if isinstance(ids, list) and ids else None


def _counted(count: Any, _config: dict[str, Any]) -> list[int] | None:
    if type(count) is not int:  # a bool is an int, and not a roster
        return None
    return list(range(1, count + 1)) if count >= 1 else None


def _counted_from(field: Any, config: dict[str, Any]) -> list[int] | None:
    try:
        resolved = int(_field_value(field, config))
    except (TypeError, ValueError):
        return None
    return list(range(1, resolved + 1)) if resolved >= 1 else None


def _listed_from(field: Any, config: dict[str, Any]) -> list[str] | None:
    raw = _field_value(field, config)
    parts = [part.strip() for part in raw.split(",")] if isinstance(raw, str) else []
    return [part for part in parts if part] or None


_SOURCE_IDS = {
    "ids": _listed,
    "count": _counted,
    "count_from": _counted_from,
    "ids_from": _listed_from,
}
