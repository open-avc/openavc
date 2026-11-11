"""How a generated simulator writes a state value back on the wire.

``yaml_auto`` reads the driver's response rules into one table per variable: a
flat variable's ``StateResponse``, one child variable's ``ChildStateResponse``,
and a ``ChildListAnswer`` for a variable a ``child_list`` rule spreads across
children. This module is what those tables do with a value: the value map
first, keyed by ``map_key`` (the one rule both the table builders and the
lookups use, so a YAML ``true`` and a boolean state value meet), then the
templates of every rule that writes the variable, each rendered in each form
the value can take (``wire_forms``), and the first rendering whose own rule
reads the value back is the one sent (``first_read_back``). A reply carries the
device's own number: the variable's scale / offset / unknown is applied on the
way out.

Several rules can write one variable because a driver puts a rule for the
device's special codes ahead of the general one, and the driver takes the first
rule that matches; rendering from the first rule alone sent values the general
rule rejects. Flat, child and list replies all render through ``render_first``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openavc.drivers.compiled_protocol import value_to_wire
from openavc.simulator.reply_templates import fill_template, slot_text

# A response rule's ``match:`` compiled the way the driver reads it
# (``search``), with the capture group the value is read from; None when the
# pattern does not compile as written.
ReadBack = tuple[re.Pattern, int] | None


def map_key(value: Any) -> str:
    """The key a value map files ``value`` under: its text, a boolean
    lowercase (``true``), so a YAML ``true`` and a boolean state value meet."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def rule_pattern(pattern: str, group: int) -> ReadBack:
    """A response rule's pattern and the group its value is read from."""
    try:
        return re.compile(pattern), group
    except re.error:
        return None


def wire_forms(value: Any, conversion: dict | None) -> list[Any]:
    """The ways a value can go out, the simulator's own rendering first.

    A boolean has three: Python's ``True``, the ``true`` most devices send,
    and ``1``. A rule written for the device's lowercase ``(true|false)``
    never reads back ``True``, so a change pushed from the Simulator UI was
    dropped by the driver. first_read_back takes the first form the rule
    reads back; with none, the first (today's) rendering still goes out."""
    wire = value_to_wire(value, conversion)
    if not isinstance(value, bool):
        return [wire]
    forms = [wire, str(value).lower(), "1" if value else "0"]
    return [form for index, form in enumerate(forms)
            if str(form) not in {str(f) for f in forms[:index]}]


def value_text(template: str, wire: Any) -> str:
    """The text a template's value slot renders, padding included."""
    text = slot_text(template, "value", wire)
    return str(wire) if text is None else text


def first_read_back(lines: list[tuple[str, str, ReadBack]]) -> str:
    """The first rendering whose own rule reads the value back, else the first.

    A rule that matches the line but captures something other than exactly
    the rendered value in the value's group is not enough: it would hand the
    driver a different value from the one the simulator holds. A first
    rendering with no rule is sent as it is; a later one is passed over."""
    for index, (line, text, rule) in enumerate(lines):
        if rule is None:
            if index == 0:
                return line
            continue
        pattern, group = rule
        match = pattern.search(line)
        if match and match.group(group) == text:
            return line
    return lines[0][0]


def render_first(
    templates: list[tuple[str, ReadBack, dict[str, Any]]],
    value: Any,
    conversion: dict | None,
) -> str:
    """``value`` rendered through each ``(template, rule, other slots)`` in
    each of its wire forms, the first rendering its rule reads back."""
    return first_read_back([
        (fill_template(template, {**slots, "value": wire}), value_text(template, wire), rule)
        for template, rule, slots in templates
        for wire in wire_forms(value, conversion)
    ])


def _wire_id(child_id: Any, id_map: dict[str, str] | None) -> Any:
    """A child's id as the device writes it, through a rule's id map."""
    return child_id if id_map is None else id_map.get(str(child_id), str(child_id))


class _RuleTemplates:
    """The templates of every rule that writes one variable: the first is
    ``template`` (with the rule it came from), the rest are alternates, in
    the order the rules were read. A table sets the three attributes in its
    own constructor, in the order it always held them."""

    template: str | None
    template_rule: ReadBack
    alternates: list[tuple]

    def _keep(self, template: str, rule: ReadBack, *more: Any) -> bool:
        """Keep a rule's template, an alternate carrying ``more`` after its
        rule; True when it is the first."""
        if self.template is not None:
            self.alternates.append((template, rule, *more))
            return False
        self.template = template
        self.template_rule = rule
        return True


class StateResponse(_RuleTemplates):
    """How the device writes one flat state variable."""

    def __init__(self, state_key: str, conversion: dict | None = None):
        self.state_key = state_key
        # The variable's scale / offset / unknown: the reply carries the
        # device's own number, not the real value the simulator holds.
        self.conversion = conversion
        self.template = None
        self.value_map: dict[str, str] = {}  # e.g., {"true": "Amt1", "false": "Amt0"}
        self.template_rule = None
        self.alternates = []

    def add_template(self, template: str, rule: ReadBack) -> None:
        """Keep a rule's template (e.g. "In{value} All"); alternates are
        (template, rule)."""
        self._keep(template, rule)

    def format(self, value: Any) -> str:
        """The reply text for ``value``: its value map entry, else its
        templates' first rendering read back, else the bare wire value."""
        key = map_key(value)
        if key in self.value_map:
            return self.value_map[key]
        if self.template:
            return render_first([
                (template, rule, {})
                for template, rule in ((self.template, self.template_rule), *self.alternates)
            ], value, self.conversion)
        return str(value_to_wire(value, self.conversion))


class ChildStateResponse(_RuleTemplates):
    """How the device writes one (child type, state variable) pair, from the
    driver's child_set: response rules. The template carries a {child_id}
    slot alongside {value}; value_map entries carry {child_id} inside each
    mapped reply."""

    def __init__(self, child_type: str, var: str, conversion: dict | None = None):
        self.child_type = child_type
        self.var = var
        self.conversion = conversion
        self.template = None
        self.value_map: dict[str, str] = {}  # e.g. {"true": "Vmt{child_id}*1"}
        # local id -> wire id, from the child_set rule's id map: reversed —
        # a device that reports 0-based ids must answer with them too.
        self.id_wire_map: dict[str, str] | None = None
        self.template_rule = None
        self.alternates = []

    def add_template(
        self, template: str, rule: ReadBack, id_wire_map: dict[str, str] | None
    ) -> None:
        """Keep a rule's template (e.g. "Out{child_id} In{value} Vid") with
        its id map; alternates are (template, rule, id map)."""
        if self._keep(template, rule, id_wire_map):
            self.id_wire_map = id_wire_map

    def format(self, child_id: str, value: Any) -> str | None:
        """Reply text for this child/value, or None when nothing derivable
        represents the value (unlike flat replies, a bare value with no id
        slot would be garbage on the wire)."""
        key = map_key(value)
        if key in self.value_map:
            return fill_template(
                self.value_map[key], {"child_id": _wire_id(child_id, self.id_wire_map)}
            )
        if self.template:
            return render_first([
                (template, rule, {"child_id": _wire_id(child_id, id_map)})
                for template, rule, id_map in (
                    (self.template, self.template_rule, self.id_wire_map),
                    *self.alternates,
                )
            ], value, self.conversion)
        return None


@dataclass(eq=False)
class ChildListAnswer:
    """How the device writes one child variable as a list, read back from a
    ``child_list`` response rule: the reply around the list, one item, the
    text between items, and how an item names its child."""

    child_type: str
    var: str
    outer: str                          # e.g. "Vgp0*Out{start} {list}Vid"
    item: str                           # e.g. "{value:02d}" or "{value}>{child_id}"
    item_rule: ReadBack                 # the element, compiled, and the value's group
    separator: str
    by_place: bool                      # items named by place, from start
    start: int                          # the first place when the reply says nothing
    value_words: dict[str, str] | None  # value -> the device's word, from a map
    id_wire_map: dict[str, str] | None  # local id -> wire id
    conversion: dict | None

    def render_item(self, child_id: str, value: Any) -> str:
        wire_id = _wire_id(child_id, self.id_wire_map)
        if self.value_words is not None:
            word = self.value_words.get(map_key(value))
            if word is not None:
                return fill_template(self.item, {"value": word, "child_id": wire_id})
        return render_first(
            [(self.item, self.item_rule, {"child_id": wire_id})], value, self.conversion
        )

    def render(self, start: int | str, items: list[str]) -> str:
        return fill_template(
            self.outer, {"list": self.separator.join(items), "start": start}
        )
