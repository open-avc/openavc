"""
Shared condition evaluation for macros and triggers.

Evaluates comparison operators against state values.
Used by MacroEngine (skip_if, conditional steps) and TriggerEngine (guard conditions).
"""

from __future__ import annotations

from typing import Any

# Aliases for operator names (user-friendly → canonical)
_OPERATOR_ALIASES: dict[str, str] = {
    "equals": "eq",
    "not_equals": "ne",
    "==": "eq",
    "!=": "ne",
    ">": "gt",
    "<": "lt",
    ">=": "gte",
    "<=": "lte",
    "equal": "eq",
    "not_equal": "ne",
    "greater_than": "gt",
    "less_than": "lt",
    "greater_or_equal": "gte",
    "less_or_equal": "lte",
}


def eval_operator(op: str, actual: Any, target: Any) -> bool:
    """Evaluate a comparison operator with alias normalization."""
    op = _OPERATOR_ALIASES.get(op, op)
    if op == "eq":
        return actual == target
    if op == "ne":
        return actual != target
    if op == "gt":
        return actual is not None and target is not None and actual > target
    if op == "lt":
        return actual is not None and target is not None and actual < target
    if op == "gte":
        return actual is not None and target is not None and actual >= target
    if op == "lte":
        return actual is not None and target is not None and actual <= target
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    raise ValueError(
        f"Unknown condition operator: '{op}'. "
        f"Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy"
    )
