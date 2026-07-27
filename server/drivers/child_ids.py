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

Coercion says nothing about whether the id *exists* or is in range — that
belongs to the driver's child registry, and ``_format_child_id`` is the
validating inverse of this (a typed id back to its padded string form).

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
