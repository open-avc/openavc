"""Child local-id coercion — the one place that reads ``id_format.type``.

A child entity's id arrives from the outside world untyped: a URL path
component, a command parameter, a regex capture off the wire. Every one of
those has to become the kind the driver declared in
``child_entity_types.<type>.id_format`` before it can be looked up, and the
rule is identical wherever it happens:

  * ``integer`` (the default when undeclared) — parse to ``int``. A bool is
    refused outright, because ``int(True)`` is 1 and would silently land on
    child 1.
  * ``string`` — the text, stripped. Valid string ids are restricted to
    ``[A-Za-z0-9_-]`` (see ``BaseDriver._format_child_id``), so surrounding
    whitespace is never part of a real id and stripping it can only help.

Coercion says nothing about whether the id *exists* — that belongs to the
driver's child registry, and ``_format_child_id`` is the validating inverse of
this (a typed id back to its padded string form). It does now answer whether
an id is in the declared RANGE: see ``child_id_range_error``.

Callers own the failure. Every function here returns ``None`` rather than
raising, because the same bad input is a 404 to a REST route, a rejected
parameter to the command dispatcher, and a skipped roster entry to a driver
building its children. Pure stdlib, no server imports, so the simulator can
share it too.
"""

from __future__ import annotations

from typing import Any

# Kept in step with spec.CHILD_ID_TYPES, imported here so this module stays a
# leaf the simulator can use without pulling in the contract registry.
DEFAULT_CHILD_ID_KIND = "integer"


def child_id_kind(type_def: dict[str, Any] | None) -> str:
    """The id kind a child type declares, defaulting to ``integer``.

    Tolerates a missing type definition and a missing or malformed
    ``id_format`` — an undeclared format means the default, not an error.
    """
    id_format = (type_def or {}).get("id_format")
    if not isinstance(id_format, dict):
        return DEFAULT_CHILD_ID_KIND
    kind = id_format.get("type", DEFAULT_CHILD_ID_KIND)
    return kind if isinstance(kind, str) else DEFAULT_CHILD_ID_KIND


def coerce_child_local_id(
    type_def: dict[str, Any] | None, raw: Any,
) -> int | str | None:
    """Coerce ``raw`` to the kind ``type_def`` declares, or ``None``.

    ``None`` means "this can't be that kind of id" and is the caller's cue to
    raise whatever its surface raises. An id that is already the right kind is
    returned untouched.
    """
    if child_id_kind(type_def) == DEFAULT_CHILD_ID_KIND:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return None
    text = str(raw).strip()
    return text or None


def child_id_range_error(
    type_def: dict[str, Any] | None, value: int | str,
) -> str | None:
    """Why ``value`` is outside the type's declared ``id_format`` range.

    Returns ``None`` when the id is in range, or when the type declares no
    bounds to check it against — an undeclared bound means "unbounded", not
    "invalid". String-id types are never range-checked: their ids are names,
    and ``min``/``max`` on them describe nothing orderable.

    Callers own the failure, as everywhere else in this module. The message is
    a reason fragment, not a sentence, so each surface can frame it its own
    way — a rejected command parameter reads differently from a 404.

    Why this exists: a ``child_id`` argument used to be coerced and sent, and
    nothing compared it to the range the driver declared. A zone id of 99 on a
    type declaring ``min: 1, max: 32`` reached the wire, the device answered
    with its own error, and the command still reported success — so the one
    fact the driver had written down was the one thing nobody checked.
    """
    if child_id_kind(type_def) != DEFAULT_CHILD_ID_KIND:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    id_format = (type_def or {}).get("id_format")
    if not isinstance(id_format, dict):
        return None

    def _bound(key: str) -> int | None:
        raw = id_format.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return raw

    low, high = _bound("min"), _bound("max")
    if low is not None and value < low:
        return (
            f"must be at least {low}"
            if high is None
            else f"must be between {low} and {high}"
        )
    if high is not None and value > high:
        return (
            f"must be at most {high}"
            if low is None
            else f"must be between {low} and {high}"
        )
    return None
