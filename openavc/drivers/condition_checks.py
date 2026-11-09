"""The shape of a state-condition block, checked when a driver loads.

Two driver fields carry this shape: an action's ``visible_when`` and a response
rule's ``only_when``. Either is one ``{key, operator, value}`` condition or an
``{any: [...]}`` / ``{all: [...]}`` group of them. The check is light-touch:
unknown extra keys are tolerated and only the recognised shapes are checked,
so both fields' messages come from here and read alike.

Purity contract, as for ``avcdriver_semantic``: standard library and the
sibling ``spec`` table only. The module is vendored with the rest of the
driver contract (openavc-drivers' catalog CI, openavc-sdk).
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.spec import VISIBLE_WHEN_OPERATORS

# A block naming either of these is a group of conditions, not one condition.
_GROUP_KEYS = ("any", "all")


def condition_block_errors(
    where: str, block: Any, field: str = "visible_when"
) -> list[str]:
    """What is wrong with one condition block; empty when it is absent.

    ``field`` names the block in the message (``visible_when`` or
    ``only_when``). A group's ``any`` is checked before its ``all``.
    """
    if block is None:
        return []
    if not isinstance(block, dict):
        return [f"{where}: '{field}' must be a mapping"]
    if not any(key in block for key in _GROUP_KEYS):
        return _condition_errors(f"{where}: {field}", block)
    errors: list[str] = []
    for key in _GROUP_KEYS:
        if key in block:
            errors += _group_errors(f"{where}: {field}.{key}", block[key])
    return errors


def _group_errors(where: str, group: Any) -> list[str]:
    if not isinstance(group, list) or not group:
        return [f"{where} must be a non-empty list"]
    errors: list[str] = []
    for j, condition in enumerate(group):
        errors += _condition_errors(f"{where}[{j}]", condition)
    return errors


def _condition_errors(where: str, condition: Any) -> list[str]:
    if not isinstance(condition, dict):
        return [f"{where}: condition must be a mapping"]
    errors: list[str] = []
    key = condition.get("key")
    if not isinstance(key, str) or not key:
        errors.append(f"{where}: condition missing 'key' (state key string)")
    operator = condition.get("operator", "eq")
    if operator not in VISIBLE_WHEN_OPERATORS:
        errors.append(f"{where}: unknown operator '{operator}'")
    return errors
