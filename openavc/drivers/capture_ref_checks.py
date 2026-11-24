"""The capture references a text rule writes, judged at the author's doors.

A text (regex) response rule names the capture each value and each child id is
read from. The runtime reads every one through ``capture_ref`` and skips, with
a warning, one that names no capture or a capture its ``match:`` does not
have, so the value is never written. This check names each such reference
where a driver is written, and a door loading the driver names it and loads it
(``avcdriver_semantic.check_driver_definition``):

* a flat ``set:`` value written ``$N``, and a ``mappings:`` entry's ``group``
  (``$0`` is the whole match, which both may read);
* a ``child_set:`` id or value written ``$...`` that names no capture, and a
  value's long form ``{group}`` (a child value reads a capture from 1 up).

What a ``child_set:`` reference written ``$N`` or ``{group: N}`` as an id may
be beyond that, and a whole ``child_list:``, are ``response_rule_checks``',
which refuses at every door as it always has. A ``mappings:`` group the
contract does not type as a number is the shape walk's to name.

Purity contract, as ``spec``: standard library and the sibling contract
modules only. Vendored with the rest of the driver contract.
"""

from __future__ import annotations

import re
from typing import Any

from openavc.drivers.capture_ref import capture_group, names_a_capture


def capture_count(pattern: Any) -> int | None:
    """The captures a ``match:`` pattern has, or None when it does not compile
    as written (a ``{config}`` placeholder filled in at runtime, say), in
    which case no reference is held to a count."""
    if not isinstance(pattern, str) or not pattern:
        return None
    try:
        return re.compile(pattern).groups
    except re.error:
        return None


def not_a_ref(where: str, ref: Any) -> str:
    """What is said of a reference that names no capture."""
    return f"{where}: {ref!r} is not a numeric capture ref ($1, $2, ...)"


def ref_range_errors(where: str, group: int, groups: int | None, floor: int = 1) -> list[str]:
    """A capture number below the place's floor, or past the pattern's count."""
    if group < floor:
        return [f"{where}: capture ref must be ${floor} or higher"]
    if groups is not None and group > groups:
        return [f"{where}: capture ref ${group} exceeds the pattern's {groups} group(s)"]
    return []


def capture_ref_errors(index: int, rule: Any) -> list[str]:
    """Each reference one text rule writes that the runtime would skip."""
    if not isinstance(rule, dict):
        return []
    if "address" in rule or rule.get("json"):
        return []
    refs = _TextRefs(f"Response {index}", capture_count(rule.get("match")))
    refs.flat(rule)
    for k, entry in _listed(rule.get("child_set")):
        refs.child(f"{refs.where}: child_set[{k}]", entry)
    return refs.found


def _items(value: Any) -> Any:
    """A mapping's items, none for anything else."""
    return value.items() if isinstance(value, dict) else ()


def _listed(value: Any) -> Any:
    """A list's items with their places, none for anything else."""
    return enumerate(value) if isinstance(value, list) else ()


def _number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class _TextRefs:
    """One text rule's references, held to its pattern's capture count."""

    def __init__(self, where: str, groups: int | None) -> None:
        self.where = where
        self.groups = groups
        self.found: list[str] = []

    def flat(self, rule: dict[str, Any]) -> None:
        """A ``set:`` value written ``$N``, and a ``mappings:`` group written
        as a number (one the contract does not type as one is the shape
        walk's); both may read the whole match."""
        for state, value in _items(rule.get("set")):
            self._set_value(f"{self.where}: set '{state}'", value)
        for j, mapping in _listed(rule.get("mappings")):
            self._mapping(f"{self.where}: mappings[{j}] group", mapping)

    def _set_value(self, where: str, value: Any) -> None:
        if names_a_capture(value):
            self._ref(where, value, floor=0)

    def _mapping(self, where: str, mapping: Any) -> None:
        if not isinstance(mapping, dict) or "value" in mapping:
            return
        if _number(mapping.get("group")):
            self._ref(where, mapping["group"], floor=0)

    def child(self, where: str, entry: Any) -> None:
        """A ``child_set:`` entry's id and values."""
        if not isinstance(entry, dict):
            return
        cid = entry.get("id")
        self._named(f"{where}: id", cid)
        if isinstance(cid, dict):
            self._named(f"{where}: id group", cid.get("group"))
        for prop, value in _items(entry.get("state")):
            self._child_value(f"{where}: state '{prop}'", value)

    def _child_value(self, where: str, value: Any) -> None:
        if isinstance(value, dict) and value.get("group") is not None:
            self._ref(f"{where} group", value["group"], floor=1)
        else:
            self._named(where, value)

    def _named(self, where: str, value: Any) -> None:
        """A ``$...`` that names no capture; what one that does may name is
        ``response_rule_checks``'."""
        if names_a_capture(value) and capture_group(value) is None:
            self.found.append(not_a_ref(where, value))

    def _ref(self, where: str, ref: Any, floor: int) -> None:
        group = capture_group(ref, default=None)
        if group is None:
            self.found.append(not_a_ref(where, ref))
        else:
            self.found += ref_range_errors(where, group, self.groups, floor)
