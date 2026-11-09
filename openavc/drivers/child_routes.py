"""How a response rule's ``child_set:`` and ``child_list:`` compile, in each wire form.

A rule routes what a reply carries into child-entity state. Each wire form says
in its own way where the child id and each value come from:

- a regex rule (``compile_child_set``): capture groups, written ``$N`` or
  ``{group: N}``; the id may also be a literal;
- an OSC rule (``compile_osc_child_set``): no captures, so the id is an address
  segment (``{segment: N}``) or a literal, and a value is a positional argument
  (``{arg: N}``) or a literal;
- a JSON rule (``compile_json_child_set``): nothing to route on, so the id is a
  literal and a value is a JSON path into the body;

and a regex rule's ``child_list:`` (``compile_child_list``) spreads one captured
list across the children of a type, its items read with ``child_set``'s value
forms.

What every form shares has one home here: the child type an entry names (one the
driver does not declare is skipped with a warning), the variables a value is
typed by (the type's own over the reserved ones the platform adds to every
child, so a reported ``online`` is a boolean and not the truthy string
``"false"``), an id's translation ``map``, and a value's long form
``{<source>, value, type, map}``. What differs, each form's id rule and value
source, is one small reader per form, so a change to one form is made in its
reader and a change to all of them is made once.

The compiled shapes are what the runtime's reply rules read (``text_replies``,
``osc_wire``, ``json_replies``, ``reply_values``) and what
``compiled_protocol._attach_conversions`` adds a ``convert`` to: an entry is
``{type, id, id_map, props}`` (a JSON entry carries no ``id_map``), a prop
``{prop, type, group | arg | key | value, map?, contains?}``.

A malformed entry is skipped, most with a warning; the loader reports the same
shapes up front, so a catalog driver never gets here wrong. Pure for the same
reason as ``compiled_protocol``, which calls it: the simulator and the validator
import that module directly, so this one takes nothing beyond the standard
library, ``spec`` and the logger (``tests/test_compiled_protocol_purity.py``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, NamedTuple

from openavc.drivers.spec import CHILD_RESERVED_PROP_SCHEMA
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: A form's id reader: an entry's ``id`` to the compiled entry's id fields, or
#: None to skip the entry (having said why).
IdReader = Callable[[Any, str], "dict[str, Any] | None"]
#: A form's value reader: one ``state:`` item (prop, its expression, the type
#: the child type declares for it, device id) to a compiled prop, or None.
PropReader = Callable[[str, Any, str, str], "dict[str, Any] | None"]


class _Form(NamedTuple):
    """One wire form's two readers: where its child id and its values come from."""

    read_id: IdReader
    read_prop: PropReader


# ── What every form shares ──


def _declared_type(var_def: Any) -> str:
    """The type a declared variable gives its value: its ``type``, else string."""
    return var_def.get("type", "string") if isinstance(var_def, dict) else "string"


def _child_vars(
    child_types: dict[str, Any], ctype: Any, device_id: str
) -> dict[str, Any] | None:
    """The variables an entry's values are typed by, or None (warned) when the
    driver declares no such child type. The reserved props come first so a
    type that declares one of its own keeps its own."""
    tdef = child_types.get(ctype)
    if not isinstance(tdef, dict):
        log.warning(
            f"[{device_id}] child_set: unknown child type "
            f"{ctype!r}; skipping entry"
        )
        return None
    return {
        **CHILD_RESERVED_PROP_SCHEMA,
        **(tdef.get("state_variables") or {}),
    }


def _id_map(cid: dict[str, Any]) -> dict[str, Any] | None:
    """An id's ``map: {wire: local_id}``, keyed by the wire id's text, or None.

    A 0-based protocol or a letter code names its children its own way; a wire
    id the map does not cover skips the entry when a reply is applied."""
    raw_map = cid.get("map")
    if isinstance(raw_map, dict) and raw_map:
        return {str(k): v for k, v in raw_map.items()}
    return None


def _long_form(
    prop: str, expr: dict[str, Any], var_type: str, source: str
) -> dict[str, Any] | None:
    """A value written ``{<source>: N | value: V, type?, map?}``, the long form
    the regex (``group``) and OSC (``arg``) forms share. None when it names
    neither, or its source is not a number."""
    pm: dict[str, Any] = {"prop": prop, "type": expr.get("type", var_type)}
    if source in expr:
        try:
            pm[source] = int(expr[source])
        except (TypeError, ValueError):
            return None
    elif "value" in expr:
        pm["value"] = expr["value"]
    else:
        return None
    if isinstance(expr.get("map"), dict):
        pm["map"] = expr["map"]
    return pm


def _names_a_capture(expr: Any) -> bool:
    """``$N`` text: a reference to a regex capture group."""
    return isinstance(expr, str) and expr.startswith("$")


def _static(prop: str, expr: Any, var_type: str) -> dict[str, Any]:
    """A literal value, typed by its variable like a flat ``set:`` literal."""
    return {"prop": prop, "value": expr, "type": var_type}


def _compile_entry(
    entry: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str,
    form: _Form,
) -> dict[str, Any] | None:
    """One ``child_set`` entry: its type, then its id, then each value; None
    when any of them leaves nothing to write."""
    ctype = entry.get("type")
    cvars = _child_vars(child_types, ctype, device_id)
    if cvars is None:
        return None
    route = form.read_id(entry.get("id"), device_id)
    if route is None:
        return None
    state_map = entry.get("state")
    if not isinstance(state_map, dict):
        return None
    props = []
    for prop, expr in state_map.items():
        pm = form.read_prop(prop, expr, _declared_type(cvars.get(prop, {})), device_id)
        if pm is not None:
            props.append(pm)
    if not props:
        return None
    return {"type": ctype, **route, "props": props}


def _compile_entries(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str,
    form: _Form,
) -> list[dict[str, Any]]:
    """A rule's ``child_set:`` list in one form, in order; an item that is not
    an object is passed over without a word."""
    raw = resp.get("child_set")
    if not isinstance(raw, list):
        return []
    compiled = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        routed = _compile_entry(entry, child_types, device_id, form)
        if routed is not None:
            compiled.append(routed)
    return compiled


# ── Regex rules: capture groups ──


def _group_ref(raw: Any) -> int | None:
    """A long-form id's ``group``: ``N`` or ``"$N"`` as its number, else None."""
    if isinstance(raw, str) and raw.startswith("$"):
        raw = raw[1:]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _regex_long_id(cid: dict[str, Any], device_id: str) -> dict[str, Any] | None:
    """``{group: N | $N, map}``: the capture holding the wire id, translated."""
    group = _group_ref(cid.get("group"))
    if group is None:
        log.warning(
            f"[{device_id}] child_set: id group "
            f"{cid.get('group')!r} is not a capture ref; "
            f"skipping entry"
        )
        return None
    return {"id": ("group", group), "id_map": _id_map(cid)}


def _regex_ref_id(cid: str, device_id: str) -> dict[str, Any] | None:
    """``$N``: the capture holding the child id."""
    try:
        return {"id": ("group", int(cid[1:])), "id_map": None}
    except ValueError:
        log.warning(
            f"[{device_id}] child_set: id {cid!r} is not a "
            f"numeric capture ref ($1, $2, ...); skipping entry"
        )
        return None


def _regex_id(cid: Any, device_id: str) -> dict[str, Any] | None:
    """``$N``, ``{group: N | $N, map}``, or a literal id."""
    if isinstance(cid, dict):
        return _regex_long_id(cid, device_id)
    if _names_a_capture(cid):
        return _regex_ref_id(cid, device_id)
    if cid is not None:
        return {"id": ("literal", cid), "id_map": None}
    log.warning(f"[{device_id}] child_set: missing 'id'; skipping entry")
    return None


def _regex_prop(
    prop: str, expr: Any, var_type: str, device_id: str
) -> dict[str, Any] | None:
    """``$N``, the ``{group | value, type, map}`` long form, or a literal."""
    if isinstance(expr, dict):
        return _long_form(prop, expr, var_type, "group")
    if _names_a_capture(expr):
        try:
            group = int(expr[1:])
        except ValueError:
            log.warning(
                f"[{device_id}] child_set: state '{prop}' ref "
                f"{expr!r} is not a numeric capture ref; skipping"
            )
            return None
        return {"prop": prop, "group": group, "type": var_type}
    return _static(prop, expr, var_type)


_REGEX = _Form(_regex_id, _regex_prop)


def compile_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile a regex response entry's ``child_set:`` list: route captures
    into child-entity state. Each entry is ``{type, id: $N | literal |
    {group, map}, state: {prop: $N | literal | {group/value/map/type}}}``."""
    return _compile_entries(resp, child_types, device_id, _REGEX)


# ── OSC rules: address segments and arguments ──


def _osc_id(cid: Any, device_id: str) -> dict[str, Any] | None:
    """``{segment: N, map}`` (0-based over the /-split address: in
    ``/ch/07/mix/fader`` segment 1 is ``"07"``) or a literal id."""
    if isinstance(cid, dict):
        try:
            segment = int(cid.get("segment"))
        except (TypeError, ValueError):
            log.warning(
                f"[{device_id}] child_set: OSC id needs a "
                f"'segment' index (got {cid!r}); skipping entry"
            )
            return None
        return {"id": ("segment", segment), "id_map": _id_map(cid)}
    if cid is None or _names_a_capture(cid):
        log.warning(
            f"[{device_id}] child_set: OSC rules have no capture "
            f"groups — id must be {{segment: N}} or a literal "
            f"(got {cid!r}); skipping entry"
        )
        return None
    return {"id": ("literal", cid), "id_map": None}


def _osc_prop(
    prop: str, expr: Any, var_type: str, device_id: str
) -> dict[str, Any] | None:
    """The ``{arg | value, type, map}`` long form or a literal; a ``$N`` has no
    capture to name, so it is refused."""
    if isinstance(expr, dict):
        return _long_form(prop, expr, var_type, "arg")
    if _names_a_capture(expr):
        log.warning(
            f"[{device_id}] child_set: state '{prop}' — OSC "
            f"rules have no capture groups; use {{arg: N}}. Skipping"
        )
        return None
    return _static(prop, expr, var_type)


_OSC = _Form(_osc_id, _osc_prop)


def compile_osc_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile an OSC response entry's ``child_set:`` list: route an
    address-matched message into child-entity state, the id from an address
    segment or a literal, each value from a positional argument or a literal."""
    return _compile_entries(resp, child_types, device_id, _OSC)


# ── JSON rules: literal ids, JSON paths ──


def _json_id(cid: Any, device_id: str) -> dict[str, Any] | None:
    """A literal child id; a body has no capture for ``$N`` to name."""
    if cid is None or _names_a_capture(cid):
        log.warning(
            f"[{device_id}] child_set: a json rule has no captures — "
            f"id must be a literal child id (got {cid!r}); skipping entry"
        )
        return None
    return {"id": ("literal", cid)}


def _json_prop(
    prop: str, expr: Any, var_type: str, device_id: str
) -> dict[str, Any] | None:
    """A JSON path, plain or as ``{key | path, type, map, contains}``, the
    strings a json ``set:`` takes; ``key`` wins over ``path``."""
    if not isinstance(expr, dict):
        return {"prop": prop, "key": str(expr), "type": var_type}
    key = expr.get("key", expr.get("path"))
    if key is None:
        return None
    pm: dict[str, Any] = {
        "prop": prop,
        "key": str(key),
        "type": expr.get("type", var_type),
    }
    if isinstance(expr.get("map"), dict):
        pm["map"] = expr["map"]
    if "contains" in expr:
        pm["contains"] = expr["contains"]
    return pm


_JSON = _Form(_json_id, _json_prop)


def compile_json_child_set(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> list[dict[str, Any]]:
    """Compile a ``json: true`` response entry's ``child_set:`` list: route
    fields of one parsed body into child-entity state.

    A body says which child by *where* a value sits, which its path already
    expresses, so a five-zone device is five entries, each with a literal id,
    rather than one rule with a wildcard."""
    return _compile_entries(resp, child_types, device_id, _JSON)


# ── A regex rule's child_list ──


class _Refused(Exception):
    """A ``child_list:`` block that cannot compile; the message says why."""


def _capture_ref(raw: Any) -> int | None:
    """``"$3"`` or ``3`` as the capture number, None for anything else.

    Stricter than the ``child_set`` refs (``_group_ref`` and the ``$N`` forms,
    which take ``$0``, the whole match, and a negative): each is the rule its
    form has always compiled by, so making them one would change what a
    hand-installed driver compiles to."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 1 else None
    if isinstance(raw, str) and raw.startswith("$"):
        try:
            group = int(raw[1:])
        except ValueError:
            return None
        return group if group >= 1 else None
    return None


def _list_element(raw: dict[str, Any]) -> re.Pattern[str]:
    """The item pattern, compiled; refused when it is missing or invalid."""
    try:
        element = re.compile(str(raw.get("element") or ""))
    except re.error as e:
        raise _Refused(f"invalid element pattern: {e}") from None
    if not element.pattern:
        raise _Refused("missing element; skipping")
    return element


def _list_id(raw: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    """The item capture naming each child and its map, ``(None, None)`` when the
    items are children by their place; refused when it names no capture."""
    cid = raw.get("id")
    if cid is None:
        return None, None
    if isinstance(cid, dict):
        id_group, id_map = _capture_ref(cid.get("group")), _id_map(cid)
    else:
        id_group, id_map = _capture_ref(cid), None
    if id_group is None:
        raise _Refused(f"id {cid!r} is not a capture ref of the item; skipping")
    return id_group, id_map


def _list_start(raw: dict[str, Any]) -> tuple[str, int]:
    """Where the numbering starts: a number, or a capture of the reply's
    (``$N``); 1 for anything else that is not text."""
    raw_start = raw.get("start", 1)
    if isinstance(raw_start, str):
        group = _capture_ref(raw_start)
        if group is None:
            raise _Refused(
                f"start {raw_start!r} is not a number or a capture ref; skipping"
            )
        return ("group", group)
    if isinstance(raw_start, int) and not isinstance(raw_start, bool):
        return ("literal", raw_start)
    return ("literal", 1)


def _child_list(
    raw: dict[str, Any], child_types: dict[str, Any], device_id: str
) -> dict[str, Any]:
    """The compiled block, checked in the order the warnings are worded."""
    ctype = raw.get("type")
    if not isinstance(child_types.get(ctype), dict):
        raise _Refused(f"unknown child type {ctype!r}; skipping")
    group = _capture_ref(raw.get("group", 1))
    if group is None:
        raise _Refused(f"group {raw.get('group')!r} is not a capture ref; skipping")
    element = _list_element(raw)
    separator = raw.get("separator")
    separator = separator if isinstance(separator, str) and separator else None
    id_group, id_map = _list_id(raw)
    start = _list_start(raw)
    # The item's values take child_set's regex value forms, read by the same
    # reader, so the two read a value the same way.
    probe = _compile_entry(
        {"type": ctype, "id": 0, "state": raw.get("state")},
        child_types, device_id, _REGEX,
    )
    if probe is None:
        raise _Refused("no usable state; skipping")
    return {
        "type": ctype,
        "list": {
            "group": group,
            "element": element,
            "separator": separator,
            "id_group": id_group,
            "start": start,
        },
        "id_map": id_map,
        "props": probe["props"],
    }


def compile_child_list(
    resp: dict[str, Any],
    child_types: dict[str, Any],
    device_id: str = "",
) -> dict[str, Any] | None:
    """Compile a regex response entry's ``child_list:`` block: one reply
    carrying a list, spread across the children of one type.

    The result rides in the rule's child-mapping list beside any compiled
    ``child_set`` entries (the validator refuses both on one rule), told apart
    by its ``list`` key, so every table that walks child mappings carries it
    unchanged. ``props`` has the child_set shape, with capture numbers that
    count the item's own captures, so value conversion attaches to it the
    same way. None for a malformed block, logged; the loader reports the
    same shape up front.
    """
    raw = resp.get("child_list")
    if not isinstance(raw, dict):
        return None
    try:
        return _child_list(raw, child_types, device_id)
    except _Refused as refused:
        log.warning(f"[{device_id}] child_list: {refused}")
        return None
