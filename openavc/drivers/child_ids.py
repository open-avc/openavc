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
an id is in the declared RANGE: see ``child_id_range_error``, and which ids the
type declares at all: see ``declared_child_ids``.

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


def declared_child_ids(
    type_def: dict[str, Any] | None, config: dict[str, Any] | None,
) -> list[Any] | None:
    """The ids this child type DECLARES for this device, in order, or ``None``.

    The declared roster, never the live one. A page is usually authored against
    a device that is not connected — the whole point of commissioning ahead of
    the install — and an unconnected device has no children at all. Believing an
    empty live roster is how a 4x4 on the bench offers a convincing sixteen
    outputs, and how a warning that fires on every offline device becomes noise.

    ``None`` whenever the declaration cannot settle it, which is a real answer
    and the common one:

    * ``count_from_state`` — the device resizes the roster once it answers, so
      anything said here is a prediction rather than a fact.
    * ``count_from`` / ``ids_from`` naming a config field this device has not
      filled in. The FIELD is what the caller should name in that case; it is
      right there in ``instances``.
    * no ``instances`` block at all, which is every Python driver that registers
      its children in code.

    Values come back as the declaration spells them — ``1..N`` as integers for a
    count, the literal entries for ``ids``, the split parts for ``ids_from``.
    Coercing them to the type's id kind is the caller's job (``ids`` on a
    string-keyed type is names), and so is what to do with the ``None``.
    """
    if not isinstance(type_def, dict):
        return None
    instances = type_def.get("instances")
    if not isinstance(instances, dict):
        return None
    # A device-reported count can exceed anything declared here.
    if instances.get("count_from_state"):
        return None
    cfg = config if isinstance(config, dict) else {}

    ids = instances.get("ids")
    if isinstance(ids, list) and ids:
        return list(ids)

    count = instances.get("count")
    if isinstance(count, bool):  # bool is an int; not a roster
        return None
    if isinstance(count, int) and count >= 1:
        return list(range(1, count + 1))

    if field := instances.get("count_from"):
        try:
            resolved = int(cfg.get(field))
        except (TypeError, ValueError):
            return None
        return list(range(1, resolved + 1)) if resolved >= 1 else None

    if field := instances.get("ids_from"):
        raw = cfg.get(field)
        if not isinstance(raw, str) or not raw.strip():
            return None
        return [part.strip() for part in raw.split(",") if part.strip()]

    return None


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


def child_display_name(
    project_label: Any,
    child_state: dict[str, Any] | None,
    type_def: dict[str, Any] | None,
) -> str:
    """What a child is CALLED — the one place that settles the precedence.

    A child entity can get its name from three independent places, and they
    are not interchangeable:

      * the **project label**, typed by the integrator and stored in the
        project file,
      * the **device-reported name**, live in the child's own state under the
        state variable the type names in ``label_field`` (a MENTOR endpoint
        name, a DSP component's name, a light's name on its controller), and
      * the **roster label**, seeded from an ``instances.label`` template
        ("Extension {id}") into the child's own reserved ``label`` key — what
        the driver author calls the slot when nobody else has named it.

    The order is: the integrator's own words win, then whatever the device
    calls itself, then what the driver's roster calls the position, then
    nothing. Returning ``""`` for "nothing" is deliberate — the caller owns the
    fallback, because the useful fallback differs by surface (a param picker
    wants ``"decoder 3"``, a matrix picker wants ``"Decoder 3"``, and a report
    may want to say nothing at all). This function answers *what it is called*,
    never *how to word its absence*.

    It lives here, with the other child rules, because more than one door asks
    it and the answers must agree: the child-entity REST responses (which is
    what every param picker in the IDE renders) and the matrix-proposal picker.
    They disagreed until 2026-08-16 — the matrix picker read only the project
    label, so a device-enumerated roster whose names live on the device offered
    "Encoder 1 / Encoder 2" while the param picker beside it showed the real
    endpoint names. Retyping names the device already knows is precisely what
    matrix inference exists to prevent.

    The roster label was the same omission one step further down: a mixer's
    seven AT-LINK extension slots seed "Extension 1".."Extension 7" and report
    no device name until something is chained to them, so every row read
    "(no label)" while the driver had named all seven.

    Pure stdlib, no server imports, like everything else in this module.
    """
    text = "" if project_label is None else str(project_label).strip()
    if text:
        return text

    state = child_state or {}
    field = (type_def or {}).get("label_field")
    if isinstance(field, str) and field.strip():
        reported = state.get(field.strip())
        # A bool is never a name; it means the driver pointed label_field at
        # a flag, which is an authoring mistake rather than an empty name.
        if reported is not None and not isinstance(reported, bool):
            name = str(reported).strip()
            if name:
                return name

    seeded = state.get("label")
    if seeded is None or isinstance(seeded, bool):
        return ""
    return str(seeded).strip()
