"""Reading a driver's response rules into a generated simulator's reply tables.

The tables are ``state_replies``' (how a value goes back on the wire); this
module is what fills them: a flat variable's ``StateResponse`` from each
rule's ``set:`` (or the ``mappings:`` the Driver Builder writes), a child
variable's ``ChildStateResponse`` from each ``child_set:``, and a
``ChildListAnswer`` from each ``child_list:``. A rule is read backwards: its
pattern becomes the reply, with the value's capture as the ``{value}`` slot
(and the child id's as ``{child_id}``), a mapped capture as one reply per
value, and a fixed value as a whole reply.

Which rule wins when several write one variable is part of the reply, and
differs by table, as the pins hold it:

- flat: the templates in rule order (the first is the template, the rest are
  alternates the reply falls back on); a fixed-text reply is the LAST rule's,
  a map reversal's the FIRST's.
- child: rules whose ``child_set`` names one variable bind before broader
  ones, then rule order; a value's reply and the id map are the first's.
- list: the first rule for a variable.

A table is created when a rule names its variable, before the rule's value is
judged, so a variable whose rules give nothing usable has an empty table.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openavc.drivers.compiled_protocol import conversion_of
from openavc.simulator.reply_templates import (
    emit_literal,
    emit_template,
    emit_template_multi,
    fill_template,
)
from openavc.simulator.state_replies import (
    ChildListAnswer,
    ChildStateResponse,
    StateResponse,
    map_key,
    rule_pattern,
)

ChildVars = Callable[[str], dict]


def reversed_ids(raw_map: Any) -> dict[str, str] | None:
    """An id ``map:`` turned round, as text both ways: a child's local id ->
    the device's id (a reply), or the device's -> the local one (a command
    reading its capture). None without a map."""
    if isinstance(raw_map, dict) and raw_map:
        return {str(v): str(k) for k, v in raw_map.items()}
    return None


def ref_number(ref: Any) -> int | None:
    """``"$2"`` or ``2`` as 2; None for anything else."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return ref if ref >= 1 else None
    if isinstance(ref, str) and re.fullmatch(r"\$\d+", ref):
        return int(ref[1:]) or None
    return None


def mappings_to_set(mappings: list[dict]) -> dict[str, Any]:
    """Convert canonical response mappings to the simulator's ``set`` shorthand.

    The flat reader understands ``match`` + ``set`` (state → a literal value
    or a ``$N`` capture), so an inline response (which normalizes to ``match``
    + ``mappings``) is remapped here: a fixed ``value`` becomes the literal, a
    ``group`` becomes ``$<n>``.
    """
    out: dict[str, Any] = {}
    for m in mappings:
        if not isinstance(m, dict):
            continue
        state = m.get("state")
        if not state:
            continue
        out[str(state)] = m["value"] if "value" in m else f"${m.get('group', 1)}"
    return out


def _number(text: Any) -> int | None:
    """``int(text)``, None when it is no whole number."""
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# ── flat variables ──


def flat_replies(
    responses: Any, var_def_for: Callable[[str], dict]
) -> dict[str, StateResponse]:
    """Each flat variable's StateResponse, from every rule that sets it."""
    tables: dict[str, StateResponse] = {}
    for resp_def in responses:
        # A JSON-body rule fills state from a parsed object, not a regex
        # template, so there is no literal to rebuild a reply from.
        if resp_def.get("json"):
            continue
        _FlatRule(resp_def).read_into(tables, var_def_for)
    return tables


def _mappings_as_set(resp_def: dict, set_dict: Any, pattern: Any) -> list | None:
    """A rule's ``mappings:`` when they stand in for its set: no ``set:``, a
    pattern, and no address (an address rule is OSC's, read elsewhere)."""
    if set_dict or not pattern:
        return None
    mappings = resp_def.get("mappings")
    if resp_def.get("address") or not isinstance(mappings, list):
        return None
    return mappings


def _reversible(mapping: Any) -> bool:
    """A mapping whose ``map:`` turns a capture into the value it stores."""
    if not isinstance(mapping, dict) or "value" in mapping:
        return False
    return bool(mapping.get("state")) and isinstance(mapping.get("map"), dict)


class _FlatRule:
    """One response rule read for the flat variables it sets: its ``set:``,
    or its ``mappings:`` read as one, with the mappings whose ``map:`` must
    be reversed."""

    def __init__(self, resp_def: dict) -> None:
        self.pattern = resp_def.get("match", "")
        self.set_dict = resp_def.get("set", {})
        self.map_reversals: list[dict] = []
        mappings = _mappings_as_set(resp_def, self.set_dict, self.pattern)
        if mappings is not None:
            self.set_dict = mappings_to_set(mappings)
            self.map_reversals = [m for m in mappings if _reversible(m)]

    def read_into(
        self, tables: dict[str, StateResponse], var_def_for: Callable[[str], dict]
    ) -> None:
        for state_key, set_value in self.set_dict.items():
            if state_key not in tables:
                tables[state_key] = StateResponse(
                    state_key, conversion_of(var_def_for(state_key))
                )
            self._read_value(tables[state_key], map_key(set_value))
        for mapping in self.map_reversals:
            self._reverse_map(tables, mapping)

    def _read_value(self, sr: StateResponse, set_value: str) -> None:
        """A ``$N`` on a pattern with captures is the template with that
        capture as the value (a reference that is no number gives none); any
        other value is the pattern's literal text, filed under the value."""
        if bool(re.search(r"\(.*\)", self.pattern)) and set_value.startswith("$"):
            group = _number(set_value[1:]) or 0
            template = emit_template(self.pattern, group) if group > 0 else None
            if template:
                sr.add_template(template, rule_pattern(self.pattern, group))
            return
        literal = emit_literal(self.pattern)
        if literal:
            sr.value_map[set_value] = literal

    def _reverse_map(self, tables: dict[str, StateResponse], mapping: dict) -> None:
        """A mapping's ``map:`` stores the friendly value in state (the wire
        token -> the friendly word), so the template would put the friendly
        word on the wire: file each friendly value under the reply with its
        wire token in the mapping's capture instead."""
        group = _number(mapping.get("group", 1))
        if group is None:
            return
        template = emit_template(self.pattern, group) if group > 0 else None
        sr = tables.get(str(mapping["state"])) if template else None
        if sr is None:
            return
        for raw, friendly in mapping["map"].items():
            sr.value_map.setdefault(
                map_key(friendly), fill_template(template, {"value": raw})
            )


# ── child variables ──


def child_replies(
    responses: Any, child_types: dict, child_vars: ChildVars
) -> dict[tuple[str, str], ChildStateResponse]:
    """Each (child type, variable)'s ChildStateResponse, from every
    ``child_set:`` entry that names it with a capture for the child id; a
    literal id serves one hardwired child and has no reply of its own."""
    tables: dict[tuple[str, str], ChildStateResponse] = {}
    if not child_types or not isinstance(responses, list):
        return tables
    entries = list(_child_set_entries(responses, child_types))
    for exact_only in (True, False):
        for pattern, entry in entries:
            if exact_only == (len(entry["state"]) == 1):
                _read_child_entry(tables, pattern, entry, child_vars)
    return tables


def _child_set_entries(responses: list, child_types: dict):
    """Each ``child_set`` entry naming a known child type's state, with its
    rule's pattern."""
    for resp in responses:
        rule = _child_set_rule(resp)
        if rule is not None:
            pattern, raw = rule
            yield from ((pattern, e) for e in raw if _names_child_state(e, child_types))


def _child_set_rule(resp: Any) -> tuple[str, list] | None:
    if not isinstance(resp, dict):
        return None
    raw = resp.get("child_set")
    pattern = resp.get("match", "")
    if not isinstance(raw, list) or not pattern:
        return None
    return pattern, raw


def _names_child_state(entry: Any, child_types: dict) -> bool:
    if not isinstance(entry, dict):
        return False
    return entry.get("type") in child_types and isinstance(entry.get("state"), dict)


def _read_child_entry(
    tables: dict[tuple[str, str], ChildStateResponse],
    pattern: str, entry: dict, child_vars: ChildVars,
) -> None:
    id_group, wire_id_map = _child_set_id_group(entry)
    if id_group is None:
        return
    ctype = entry["type"]
    reader = _ChildValue(pattern, id_group, wire_id_map)
    for prop, expr in entry["state"].items():
        key = (ctype, prop)
        if key not in tables:
            tables[key] = ChildStateResponse(
                ctype, prop, conversion_of(child_vars(ctype).get(prop))
            )
        reader.read_into(tables[key], expr)


def _child_set_id_group(entry: dict) -> tuple[int | None, dict[str, str] | None]:
    """The capture group holding a child_set rule's child id (``$N``, or
    ``{group: N | $N, map}``), plus the local-id -> wire-id map (the reverse
    of the rule's wire -> local ``map:``) a reply must apply to put the
    device's own id form back on the wire."""
    cid = entry.get("id")
    if isinstance(cid, dict):
        gref = cid.get("group")
        if isinstance(gref, str) and gref.startswith("$"):
            gref = gref[1:]
        group = _number(gref)
        return (None, None) if group is None else (group, reversed_ids(cid.get("map")))
    if isinstance(cid, str) and cid.startswith("$"):
        return _number(cid[1:]), None
    return None, None


class _ChildValue:
    """One child_set property expression read into its table: a capture ref
    is the ``{value}`` template, a mapped capture one reply per mapped value,
    a static value (bare or ``{value: ...}``) a whole reply carrying only the
    child id."""

    def __init__(self, pattern: str, id_group: int, wire_id_map: dict[str, str] | None):
        self.pattern = pattern
        self.id_group = id_group
        self.wire_id_map = wire_id_map

    def read_into(self, sr: ChildStateResponse, expr: Any) -> None:
        if isinstance(expr, str) and expr.startswith("$"):
            group = _number(expr[1:])
            if group is not None:
                self._template(sr, group)
        elif isinstance(expr, dict):
            self._read_long_form(sr, expr)
        else:
            self._reply(sr, expr, {self.id_group: "{child_id}"})

    def _read_long_form(self, sr: ChildStateResponse, expr: dict) -> None:
        if "group" in expr:
            group = _number(expr["group"])
            if group is not None:
                self._capture(sr, group, expr.get("map"))
        elif "value" in expr:
            self._reply(sr, expr["value"], {self.id_group: "{child_id}"})

    def _capture(self, sr: ChildStateResponse, group: int, value_map: Any) -> None:
        if not (isinstance(value_map, dict) and value_map):
            self._template(sr, group)
            return
        for wire_tok, value in value_map.items():
            self._reply(sr, value, {self.id_group: "{child_id}", group: str(wire_tok)})

    def _template(self, sr: ChildStateResponse, group: int) -> None:
        template = emit_template_multi(
            self.pattern, {self.id_group: "{child_id}", group: "{value}"}
        )
        if template:
            sr.add_template(template, rule_pattern(self.pattern, group), self.wire_id_map)

    def _reply(self, sr: ChildStateResponse, value: Any, slots: dict[int, str]) -> None:
        """File ``value``'s whole reply, unless an earlier rule did; the id
        map comes with the first reply."""
        vkey = map_key(value)
        if vkey in sr.value_map:
            return
        reply = emit_template_multi(self.pattern, slots)
        if reply:
            sr.value_map[vkey] = reply
            if sr.id_wire_map is None:
                sr.id_wire_map = self.wire_id_map


# ── child lists ──


def list_answers(
    responses: Any, child_types: dict, child_vars: ChildVars
) -> dict[tuple[str, str], ChildListAnswer]:
    """Each ``child_list`` rule read back into how the device writes the
    list, per child variable it fills: the reply with the list (and the
    page's start) as slots, one item with the value (and its child id) as
    slots. A variable a rule fills from a static, or a rule whose patterns
    the emitter cannot model (a lookaround), gets nothing."""
    answers: dict[tuple[str, str], ChildListAnswer] = {}
    for resp in responses or []:
        frame = _read_list_frame(resp, child_types)
        if frame is not None:
            frame.answer_into(answers, child_vars)
    return answers


def _list_rule(resp: Any, child_types: dict) -> tuple[dict, str] | None:
    """A ``child_list`` rule's spec and reply pattern; None for one the
    reader cannot use."""
    if not isinstance(resp, dict):
        return None
    spec = resp.get("child_list")
    pattern = resp.get("match")
    if not isinstance(spec, dict) or not isinstance(pattern, str):
        return None
    if spec.get("type") not in child_types or not isinstance(spec.get("element"), str):
        return None
    return (spec, pattern) if isinstance(spec.get("state"), dict) else None


def _list_start(spec: dict) -> tuple[int | None, int]:
    """Where the numbering starts: the reply's capture holding it (``$N``),
    and the number used when the reply says nothing (1 for anything that is
    not a number)."""
    raw_start = spec.get("start", 1)
    start_group = ref_number(raw_start) if isinstance(raw_start, str) else None
    plain = isinstance(raw_start, int) and not isinstance(raw_start, bool)
    return start_group, raw_start if plain else 1


def _list_id(cid: Any) -> tuple[int | None, dict[str, str] | None]:
    """The item capture naming each child (``$N``, or ``{group, map}``) and
    the local-id -> wire-id map; (None, None) when the items are children by
    their place."""
    if isinstance(cid, dict):
        return ref_number(cid.get("group")), reversed_ids(cid.get("map"))
    return ref_number(cid), None


def _names_no_capture(cid: Any, id_group: int | None) -> bool:
    """An id the rule gives that names no capture of the item."""
    return cid is not None and id_group is None


def _read_list_frame(resp: Any, child_types: dict) -> _ListFrame | None:
    """What one ``child_list`` rule says about the whole list; None when its
    list or id names no capture, or its reply cannot be modelled."""
    rule = _list_rule(resp, child_types)
    if rule is None:
        return None
    spec, pattern = rule
    list_group = ref_number(spec.get("group", 1))
    cid = spec.get("id")
    id_group, id_wire_map = _list_id(cid)
    start_group, start = _list_start(spec)
    if list_group is None or _names_no_capture(cid, id_group):
        return None
    outer_slots = {list_group: "{list}"}
    if cid is None and start_group is not None:
        outer_slots[start_group] = "{start}"
    outer = emit_template_multi(pattern, outer_slots)
    if outer is None:
        return None
    return _ListFrame(
        spec["type"], spec["element"], spec["state"], spec.get("separator") or "",
        cid is None, outer, id_group, id_wire_map, start,
    )


def _list_value(expr: Any) -> tuple[int | None, dict[str, str] | None] | None:
    """A list variable's item capture and its device words (value -> word,
    the first word for a value winning); None for a static value."""
    if isinstance(expr, str) and expr.startswith("$"):
        return ref_number(expr), None
    if not (isinstance(expr, dict) and "group" in expr):
        return None
    raw_map = expr.get("map")
    if not (isinstance(raw_map, dict) and raw_map):
        return ref_number(expr.get("group")), None
    words: dict[str, str] = {}
    for word, value in raw_map.items():
        words.setdefault(map_key(value), str(word))
    return ref_number(expr.get("group")), words


@dataclass
class _ListFrame:
    """What one ``child_list`` rule says about the whole list: the reply
    around it, how an item names its child (by its id capture, through the
    id map, or by place from ``start``), the text between items."""

    ctype: str
    element: str
    state_map: dict
    separator: str
    by_place: bool
    outer: str
    id_group: int | None
    id_wire_map: dict[str, str] | None
    start: int

    def answer_into(
        self, answers: dict[tuple[str, str], ChildListAnswer], child_vars: ChildVars
    ) -> None:
        """Each variable this rule fills that no earlier rule answered."""
        for var, expr in self.state_map.items():
            key = (self.ctype, var)
            if key in answers:
                continue
            answer = self._answer(var, expr, child_vars)
            if answer is not None:
                answers[key] = answer

    def _answer(self, var: str, expr: Any, child_vars: ChildVars) -> ChildListAnswer | None:
        read = _list_value(expr)
        if read is None:
            return None
        group, value_words = read
        if group is None or group == self.id_group:
            return None
        item_slots = {group: "{value}"}
        if self.id_group is not None:
            item_slots[self.id_group] = "{child_id}"
        item = emit_template_multi(self.element, item_slots)
        if item is None:
            return None
        return ChildListAnswer(
            self.ctype, var, self.outer, item, rule_pattern(self.element, group),
            self.separator, self.by_place, self.start, value_words, self.id_wire_map,
            conversion_of(child_vars(self.ctype).get(var)),
        )
