"""Whether the Simulator UI can draw a driver's declared controls
(``simulator.controls``).

A declared list replaces the controls the simulator builds from the state
variables, so a misspelt type or a missing field leaves a blank or broken card
on the device's page, with no other message. What each type needs is the
Simulator UI's: the ``ControlDef`` types in ``web/simulator/src/store/api.ts``,
which ``DynamicControls`` switches on. ``_TYPES`` is this validator's copy of
them, one row per type: the fields the card cannot be drawn without (an error),
whether a missing ``label`` shows as "undefined" (a warning), whether ``key``
names a state key, and the type's own rules. A new control type is a row here
and, when it has rules of its own, one function.

A key is reported as unknown only when the device has state keys to compare it
with: with none (``known_keys`` empty), nothing is said about it.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from openavc.simulator.validation_result import ValidationResult


@dataclasses.dataclass(frozen=True)
class _Control:
    """One declared control of a known type: what it says, where it sits,
    and the result its findings go to, each worded "<where> (<type>): ..."."""

    result: ValidationResult
    fields: dict
    where: str
    ctype: str
    known_keys: set[str]

    def error(self, text: str) -> None:
        self.result.error("controls", f"{self.where} ({self.ctype}): {text}")

    def warning(self, text: str) -> None:
        self.result.warning("controls", f"{self.where} ({self.ctype}): {text}")

    def warn_unknown_key(self, key: Any, field: str) -> None:
        """A state key the device does not have, named by ``field``."""
        if not isinstance(key, str) or not self.known_keys:
            return
        if key not in self.known_keys:
            self.warning(
                f"{field} '{key}' is not a state variable or initial_state key — the "
                f"control shows nothing and writes to a key nothing reads"
            )


@dataclasses.dataclass(frozen=True)
class _ControlType:
    """What the Simulator UI needs from one type of control."""

    #: Fields the card cannot be drawn without, beyond ``type``.
    required: tuple[str, ...]
    #: Whether a missing ``label`` shows as "undefined".
    label_required: bool = False
    #: Whether ``key`` names the state key the control shows and writes.
    has_key: bool = True
    #: The type's own rules, after the required fields, label and key.
    own_rules: Callable[[_Control], None] | None = None
    #: Whether its ``controls`` are controls of its own, each checked after it.
    holds_controls: bool = False

    def children(self, fields: dict) -> list:
        """The controls this one holds, when it holds a list of them."""
        children = fields.get("controls") if self.holds_controls else None
        return children if isinstance(children, list) else []


def _positive_integer(control: _Control, field: str) -> None:
    """A size (inputs, outputs, channels) the card draws that many of."""
    value = control.fields.get(field)
    if value is not None and not _is_positive_int(value):
        control.error(f"'{field}' must be a positive integer, got: {value!r}")


def _is_positive_int(value: Any) -> bool:
    """An int above zero (``True`` is one, as Python counts it)."""
    return isinstance(value, int) and value > 0


def _per_item_pattern(control: _Control, field: str, token: str, item: str) -> None:
    """A state key pattern the card fills once per ``item`` (an output, a
    channel) through ``{token}``; its first item must name a known key."""
    pattern = control.fields.get(field)
    if not isinstance(pattern, str):
        return
    placeholder = "{" + token + "}"
    if placeholder not in pattern:
        control.warning(
            f"{field} {pattern!r} has no {placeholder} placeholder — every "
            f"{item} binds the same key"
        )
    else:
        control.warn_unknown_key(pattern.replace(placeholder, "1"), field)


def _select_options(control: _Control) -> None:
    options = control.fields.get("options")
    if options is not None and not isinstance(options, list):
        control.error("'options' must be a list")
    elif isinstance(options, list) and not options:
        control.warning("'options' is empty")


def _slider_bounds(control: _Control) -> None:
    for field in ("min", "max"):
        value = control.fields.get(field)
        if value is not None and not isinstance(value, (int, float)):
            control.error(f"'{field}' must be a number, got: {value!r}")


def _matrix(control: _Control) -> None:
    for field in ("inputs", "outputs"):
        _positive_integer(control, field)
    _per_item_pattern(control, "state_pattern", "output", "output")


def _meters(control: _Control) -> None:
    _positive_integer(control, "channels")
    for field in ("key_pattern", "mute_pattern"):
        _per_item_pattern(control, field, "ch", "channel")


def _presets(control: _Control) -> None:
    if "count" not in control.fields and "names" not in control.fields:
        control.warning("neither 'count' nor 'names' given — the panel renders no preset buttons")
    count = control.fields.get("count")
    if count is not None and not isinstance(count, int):
        control.error("'count' must be an integer")
    names = control.fields.get("names")
    if names is not None and not isinstance(names, list):
        control.error("'names' must be a list")


def _group_controls(control: _Control) -> None:
    """A group's controls are a list (each is checked after the group)."""
    children = control.fields.get("controls")
    if children is not None and not isinstance(children, list):
        control.error("'controls' must be a list")


_TYPES: dict[str, _ControlType] = {
    "power": _ControlType(("key",)),
    "select": _ControlType(("key", "options"), own_rules=_select_options),
    "slider": _ControlType(("key", "min", "max"), own_rules=_slider_bounds),
    "toggle": _ControlType(("key",), label_required=True),
    "matrix": _ControlType(("inputs", "outputs", "state_pattern"), has_key=False, own_rules=_matrix),
    "meters": _ControlType(("channels", "key_pattern"), has_key=False, own_rules=_meters),
    "presets": _ControlType(("key",), own_rules=_presets),
    "group": _ControlType(
        ("controls",), label_required=True, has_key=False, own_rules=_group_controls, holds_controls=True,
    ),
    "indicator": _ControlType(("key",), label_required=True),
}


def check_controls(result: ValidationResult, sim: dict, known_keys: set[str]) -> None:
    """Each declared control, in order; nothing when none are declared."""
    controls = sim.get("controls")
    if controls is None:
        return
    if not isinstance(controls, list):
        result.error("controls", "simulator.controls must be a list")
        return
    for i, control in enumerate(controls):
        _check_control(result, control, f"controls[{i}]", known_keys)


def _check_control(result: ValidationResult, fields: Any, where: str, known_keys: set[str]) -> None:
    """One control: a mapping of a known type, its row's rules in order, then
    the controls it holds. Each level of a group is one call of this function
    on the stack, so how deep a group can go before Python's recursion limit
    stops the check is a property of this function alone."""
    kind = _control_type(result, fields, where)
    if kind is None:
        return
    _check_fields(_Control(result, fields, where, fields["type"], known_keys), kind)
    for i, child in enumerate(kind.children(fields)):
        _check_control(result, child, f"{where}.controls[{i}]", known_keys)


def _control_type(result: ValidationResult, fields: Any, where: str) -> _ControlType | None:
    """The row for a control's type, or None (said) when it is not a
    mapping or its type is not one the Simulator UI draws."""
    if not isinstance(fields, dict):
        result.error("controls", f"{where}: must be a mapping, got: {fields!r}")
        return None
    ctype = fields.get("type")
    if ctype not in _TYPES:
        valid = ", ".join(sorted(_TYPES))
        result.error(
            "controls",
            f"{where}: unknown control type {ctype!r} — the panel renders "
            f"nothing for it (valid types: {valid})"
        )
        return None
    return _TYPES[ctype]


def _check_fields(control: _Control, kind: _ControlType) -> None:
    """The required fields, the label, the key, then the type's own rules."""
    fields = control.fields
    for field in kind.required:
        if field not in fields:
            control.error(f"missing required field '{field}'")
    if kind.label_required and "label" not in fields:
        control.warning("missing 'label' — the panel shows 'undefined'")
    if kind.has_key:
        control.warn_unknown_key(fields.get("key"), "key")
    if kind.own_rules is not None:
        kind.own_rules(control)
