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


def _coerce_numeric(value: Any) -> float | int | None:
    """Try to coerce a value to a number for comparison."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
    return None


def _coerce_bool(value: Any) -> bool | None:
    """Normalize boolean-like values for comparison."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return None


def eval_operator(op: str, actual: Any, target: Any) -> bool:
    """Evaluate a comparison operator with alias normalization and type coercion."""
    op = _OPERATOR_ALIASES.get(op, op)

    if op in ("eq", "ne"):
        # Try boolean coercion if either side is a bool
        if isinstance(actual, bool) or isinstance(target, bool):
            a_bool = _coerce_bool(actual)
            t_bool = _coerce_bool(target)
            if a_bool is not None and t_bool is not None:
                return (a_bool == t_bool) if op == "eq" else (a_bool != t_bool)
        # Try numeric coercion if types differ
        if type(actual) is not type(target):
            a_num = _coerce_numeric(actual)
            t_num = _coerce_numeric(target)
            if a_num is not None and t_num is not None:
                return (a_num == t_num) if op == "eq" else (a_num != t_num)
        return (actual == target) if op == "eq" else (actual != target)

    if op in ("gt", "lt", "gte", "lte"):
        if actual is None or target is None:
            return False
        a_num = _coerce_numeric(actual)
        t_num = _coerce_numeric(target)
        if a_num is not None and t_num is not None:
            if op == "gt":
                return a_num > t_num
            if op == "lt":
                return a_num < t_num
            if op == "gte":
                return a_num >= t_num
            return a_num <= t_num
        try:
            if op == "gt":
                return actual > target
            if op == "lt":
                return actual < target
            if op == "gte":
                return actual >= target
            return actual <= target
        except TypeError:
            return False

    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    raise ValueError(
        f"Unknown condition operator: '{op}'. "
        f"Valid operators: eq, ne, gt, lt, gte, lte, truthy, falsy"
    )
