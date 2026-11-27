"""Which parts of a macro's steps and triggers read a state key, for the AI's
``check_references`` and ``delete_variable`` (``macro_tools``).

A condition reads its own key and the key its value names (``$var.wanted``,
``value_resolver.referenced_key``); a trigger reads the key it watches, the
key a State Change trigger's own value names, and each guard's.
"""

from __future__ import annotations

from typing import Any

from openavc.core.value_resolver import referenced_key


def condition_reads(condition: Any, key: str) -> bool:
    """A condition that tests ``key``, or compares with it (a value that names
    it, ``$key``; ``value_resolver.referenced_key``)."""
    if condition is None:
        return False
    return getattr(condition, "key", None) == key or referenced_key(getattr(condition, "value", None)) == key


def _trigger_fields_reading(trigger: Any, key: str) -> list[str]:
    """The fields of one trigger that read ``key``: it watches it
    (``state_key``), its own value compares with it (``state_value``), and
    each guard that tests it (``condition``) or compares with it
    (``condition_value``)."""
    fields = ["state_key"] if trigger.state_key == key else []
    if trigger.type == "state_change" and referenced_key(trigger.state_value) == key:
        fields.append("state_value")
    for condition in trigger.conditions:
        if condition.key == key:
            fields.append("condition")
        elif condition_reads(condition, key):
            fields.append("condition_value")
    return fields


def triggers_reading(macros: Any, key: str) -> list[dict[str, Any]]:
    """Each trigger field across the project's macros that reads ``key``."""
    return [
        {"macro_id": m.id, "trigger_id": t.id, "field": field}
        for m in macros
        for t in m.triggers
        for field in _trigger_fields_reading(t, key)
    ]
