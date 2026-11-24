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

Which source an ``instances:`` block names, and the state variable it follows,
is ``roster_source``'s reading, which ``declared_child_ids`` asks.

Callers own the failure. Every function here returns ``None`` rather than
raising, because the same bad input is a 404 to a REST route, a rejected
parameter to the command dispatcher, and a skipped roster entry to a driver
building its children. Pure stdlib, no server imports, so the simulator can
share it too.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.roster_source import config_child_ids, roster_source

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

    * a roster that follows the device's count (``count_from_state`` beside
      ``count_from``, ``roster_source(...).follows``) — the device resizes it
      once it answers, so anything said here is a prediction rather than a fact.
      ``config_child_ids`` is the roster before it answers.
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
    instances = type_def.get("instances") if isinstance(type_def, dict) else None
    if roster_source(instances).follows:
        return None
    return config_child_ids(type_def, config)


def page_starts(ids: list[Any], page_size: Any) -> list[Any]:
    """The children a per-child query with ``page_size`` is sent for.

    A device's list read answers ``page_size`` consecutive children starting at
    the one asked for (a matrix's view of 16 outputs from output 17), so a
    query goes out for the lowest id and then for the first id at or past the
    end of the page before it: ids 1-32 at 16 give 1 and 17, and a roster with
    gaps still asks for each stretch the pages cover. Ids that are not all
    whole numbers are paged by their place in the roster. No ``page_size``
    (or one below 2) is one query per child, the ids as given.

    One rule for the runtime that sends the queries, the simulator that
    answers them and the validator that counts them.
    """
    if not _pages(page_size):
        return list(ids)
    if not ids or not all(_whole(i) for i in ids):
        return list(ids)[::page_size]
    return _stretch_starts(sorted(ids), page_size)


def _whole(value: Any) -> bool:
    """A whole number, never a boolean."""
    return isinstance(value, int) and not isinstance(value, bool)


def _pages(page_size: Any) -> bool:
    """Whether ``page_size`` pages a query: a whole number of 2 or more."""
    return _whole(page_size) and page_size >= 2


def _stretch_starts(ids: list[int], page_size: int) -> list[int]:
    """The lowest of ``ids`` (sorted), then each first id at or past the end
    of the page before it."""
    starts: list[int] = []
    next_start: int | None = None
    for local_id in ids:
        if next_start is None or local_id >= next_start:
            starts.append(local_id)
            next_start = local_id + page_size
    return starts


def child_id_range_error(
    type_def: dict[str, Any] | None, value: int | str,
) -> str | None:
    """Why ``value`` is outside the type's ``id_format`` range
    (``integer_id_range_error``), or ``None`` when it is in it.

    String-id types are never range-checked: their ids are names, and
    ``min``/``max`` on them describe nothing orderable.

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
    return integer_id_range_error((type_def or {}).get("id_format"), value)


#: The lowest integer child id when a type declares no ``min``.
DEFAULT_MIN_CHILD_ID = 1


def integer_id_range_error(id_format: Any, value: int) -> str | None:
    """THE integer child id range: [``min``, ``max``], ``min`` 1 when the
    type declares none (``DEFAULT_MIN_CHILD_ID``) and ``max`` unbounded. Why
    ``value`` is outside it, as a fragment each door frames (a command
    parameter refused before it is sent, a registration), or ``None``. A
    bound that is not a whole number reads as undeclared."""
    low = _whole_bound(id_format, "min")
    low = DEFAULT_MIN_CHILD_ID if low is None else low
    high = _whole_bound(id_format, "max")
    if high is None:
        return None if value >= low else f"must be at least {low}"
    if low <= value <= high:
        return None
    return f"must be between {low} and {high}"


def _whole_bound(id_format: Any, key: str) -> int | None:
    """An ``id_format`` bound that is a whole number, else None."""
    raw = id_format.get(key) if isinstance(id_format, dict) else None
    return raw if _whole(raw) else None


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
    return _reported_name(state, type_def) or _name_text(state.get("label"))


def _reported_name(state: dict[str, Any], type_def: dict[str, Any] | None) -> str:
    """The name the device reports, in the variable the type's
    ``label_field`` names; "" when it names none or the device has none."""
    field = (type_def or {}).get("label_field")
    if not isinstance(field, str) or not field.strip():
        return ""
    return _name_text(state.get(field.strip()))


def _name_text(value: Any) -> str:
    """A value as a child's name, "" for none. A bool is never a name; it
    means the driver pointed label_field at a flag, which is an authoring
    mistake rather than an empty name."""
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()
