"""The fault code a YAML driver writes on a child, read against the codes a
driver may assert.

THE rule: where a response rule's ``child_set:`` or ``child_list:`` writes a
child's ``offline_reason`` from the file itself (a literal, a long form's
``value:``, a ``map:`` target), it writes ``""`` or null (nothing claimed) or
one of the codes a driver may assert (``spec.CHILD_DRIVER_FAULT_CODES``).
``parent_offline`` is the platform's: it marks every child in service when the
device becomes unreachable and puts exactly those back when it returns, so a
driver's claim of it puts the child back in service against what the device
said. Any other code matches no automation and reaches the device page as a
bare word; a value that is not text is stored as its text, so it is one too.
``child_fault_code_errors`` names each, with the three codes.

A value read off the wire (a capture, an OSC argument, a JSON path) is not
judged here: at runtime a driver's ``parent_offline`` is dropped and any other
code written as given (``core.connection_fault.normalize_child_fault_claim``).

Only the author's doors refuse on it (Validate, the Builder's save,
``drivers.check``, the AI's driver tool, catalog CI); a door loading the
driver reports the same sentence as a notice and loads it
(``avcdriver_semantic.check_driver_definition``, through the walk's
``authors_parts``).

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling contract modules. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.capture_ref import names_a_capture
from openavc.drivers.declared_names import did_you_mean
from openavc.drivers.spec import CHILD_DRIVER_FAULT_CODES, CHILD_PARENT_OFFLINE

_PROP = "offline_reason"

# What a refusal asks for: the three codes, then the way to claim nothing.
_FIRST, *_REST = sorted(CHILD_DRIVER_FAULT_CODES)
_WRITE_ONE = (
    f"write {', '.join([_FIRST, *_REST[:-1]])} or {_REST[-1]}, or \"\" for none"
)


def child_fault_code_errors(index: int, rule: Any) -> list[str]:
    """The fault codes one rule writes on a child that a driver may not.
    The author's doors refuse them; a door loading the driver names them and
    loads it (``avcdriver_semantic.check_driver_definition``).

    A rule's form decides which of its values the file holds: a JSON rule's
    plain value is a path into the body, any other form's is a literal, and a
    ``$N`` is a capture. Only an entry and a value that are the shape their
    checks ask for are read; one of another shape is reported there.
    """
    if not isinstance(rule, dict):
        return []
    form = _Form(rule)
    where = f"Response {index}"
    child_set = rule.get("child_set")
    errors: list[str] = []
    for j, entry in enumerate(child_set if isinstance(child_set, list) else ()):
        errors += form.entry_errors(f"{where}: child_set[{j}]", entry)
    child_list = rule.get("child_list")
    if isinstance(child_list, dict):
        errors += form.entry_errors(f"{where}: child_list", child_list)
    return errors


class _Form:
    """How one rule's form writes a value: the key its long form reads the
    wire by, and whether a plain value is a literal or a JSON path."""

    def __init__(self, rule: dict[str, Any]) -> None:
        osc = "address" in rule
        self.json = not osc and bool(rule.get("json"))
        self.source = "arg" if osc else "group"

    def entry_errors(self, where: str, entry: Any) -> list[str]:
        state = entry.get("state") if isinstance(entry, dict) else None
        if not isinstance(state, dict) or _PROP not in state:
            return []
        return [
            _refusal(f"{where}: state '{_PROP}' {verb}", code)
            for verb, code in self._written(state[_PROP])
            if not _may_write(code)
        ]

    def _written(self, expr: Any) -> list[tuple[str, Any]]:
        """Each code the file writes, with how it writes it."""
        if isinstance(expr, dict):
            return self._long_form(expr)
        if self.json or names_a_capture(expr):
            return []
        return [("writes", expr)]

    def _long_form(self, expr: dict[str, Any]) -> list[tuple[str, Any]]:
        """A long form's ``value:`` (read only when it names no wire source,
        and as its map's target when the map covers it) and each map target."""
        value_map = expr.get("map")
        value_map = value_map if isinstance(value_map, dict) else {}
        written: list[tuple[str, Any]] = []
        if self._writes_its_value(expr) and str(expr["value"]) not in value_map:
            written.append(("writes", expr["value"]))
        written += [(f"maps {key!r} to", code) for key, code in value_map.items()]
        return written

    def _writes_its_value(self, expr: dict[str, Any]) -> bool:
        """A long form's ``value:`` is written when it names no wire source;
        a JSON spec has none to write."""
        return "value" in expr and self.source not in expr and not self.json


def _may_write(code: Any) -> bool:
    """Nothing claimed, or a code a driver may assert."""
    return code is None or code == "" or (
        isinstance(code, str) and code in CHILD_DRIVER_FAULT_CODES
    )


def _refusal(said: str, code: Any) -> str:
    if code == CHILD_PARENT_OFFLINE:
        return (
            f"{said} {code!r}, the code the platform writes while the device is "
            f"unreachable; {_WRITE_ONE}"
        )
    nearest = did_you_mean(code, set(CHILD_DRIVER_FAULT_CODES)) if isinstance(code, str) else ""
    return f"{said} {code!r}, which is not a child fault code{nearest}; {_WRITE_ONE}"
