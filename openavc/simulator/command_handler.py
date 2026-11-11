"""What a generated simulator does with a line one of the driver's writes
matched: the handler a command or a device setting is read into.

A reader builds it (yaml_auto's ``_CommandRead`` for a command,
setting_handlers for a device setting's write) and yaml_auto's dispatch runs
it. It holds the pattern the line matched, which capture writes which
variable and how each capture is read back, the child the line addresses,
and the variable its reply reports.

The rule worth knowing before changing anything here: a capture is read in
one order, the setting's own word first (ON back to true, which no base or
conversion applies to), then a non-decimal base (a numeric variable only: a
text one keeps the code itself, as hex-code protocols declare it), then the
variable's scale and offset. A new way a value is written on the wire is a
table here, read in ``read_capture``, and filled by the reader that knows it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from openavc.drivers.compiled_protocol import reading_from_wire, value_for_map_word

# The variable types a capture's non-decimal base decodes into a number for.
_NUMERIC_TYPES = ("integer", "number", "float")


@dataclass(eq=False)
class CommandHandler:
    """A driver command's (or device setting's) handler. Compared by
    identity: two handlers built alike are still two handlers. A table left
    as None is empty."""

    name: str
    pattern: re.Pattern
    state_changes: dict
    response_var: str | None
    #: Capture groups whose wire form is non-decimal ({level:02X}), mapped to
    #: the int base that decodes them.
    group_bases: dict[int, int] | None = None
    #: A child-addressed command (exactly one child_id param): its child
    #: type, the capture group holding the child id, the wire -> local id
    #: translation from the param's map:, and which state_changes keys are
    #: the child's own variables (routed to its dotted state) rather than
    #: flat fallbacks.
    child_type: str | None = None
    child_id_group: int = 0
    #: Capture groups carrying a param (or setting) with scale / offset: the
    #: device's number, converted back to the real value it stores.
    group_conversions: dict[int, dict] | None = None
    child_wire_map: dict[str, str] | None = None
    child_state_keys: set[str] | None = None
    response_is_child: bool = False
    #: Capture groups carrying a device setting with a map: the setting
    #: value -> device word table, read in reverse.
    group_value_maps: dict[int, dict] | None = None

    def __post_init__(self) -> None:
        self.group_bases = self.group_bases or {}
        self.group_conversions = self.group_conversions or {}
        self.group_value_maps = self.group_value_maps or {}
        self.child_state_keys = self.child_state_keys or set()

    def target_key(self, state_key: str, child_id: str | None) -> str:
        """Where a state change lands: the addressed child's own dotted
        state for a variable its type declares, else the flat variable."""
        if child_id is not None and state_key in self.child_state_keys:
            return f"{self.child_type}.{child_id}.{state_key}"
        return state_key

    def read_capture(self, group: int, raw: Any, var_def: dict) -> Any:
        """The value a capture group carries, before the variable's type, in
        the order the module docstring gives."""
        found, setting_value = value_for_map_word(raw, self.group_value_maps.get(group))
        if found:
            return setting_value
        var_type = var_def.get("type", "string")
        return self._converted(group, self._from_base(group, raw, var_type), var_type)

    def _from_base(self, group: int, value: Any, var_type: str) -> Any:
        base = self.group_bases.get(group)
        if not base or var_type not in _NUMERIC_TYPES:
            return value
        try:
            return int(value, base)
        except ValueError:
            return value

    def _converted(self, group: int, value: Any, var_type: str) -> Any:
        conversion = self.group_conversions.get(group)
        if not conversion:
            return value
        handled, real = reading_from_wire(value, conversion, var_type)
        return real if handled else value
