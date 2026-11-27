"""What the AI's UI tools refuse in a touch-panel element's bindings.

Every UI tool that writes an element's or a master element's bindings first
wraps a single action in a list (``normalize_bindings``), then asks
``validate_bindings``: one sentence naming every malformed shape, or None.
What a binding may hold is ``openavc.ui.page_structure``'s
(``binding_issues``), the rule every door and report asks; this is the AI's
half of it: the wrap, the sentence the refusal opens with, and what is logged
rather than refused. A macro or device the project lacks is logged, never
refused, and what the bindings point at is reviewed with the write by
``openavc.ui.page_references``. The sentences go back to the cloud and into
the chat.
"""

from __future__ import annotations

from typing import Any

from openavc.ui.page_structure import binding_issues, interactions, refusal
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Touch-panel element bindings use the show/do model: `show` is what the
# element reflects from state (value / items / look / visible_when), `do` is the
# action lists keyed by interaction kind. (Control-surface buttons keep their
# own flat format — see SURFACE_BUTTONS_FORMAT in plugin_tools.)


def normalize_bindings(bindings: dict) -> dict:
    """Normalize a touch-panel element's show/do bindings to canonical format.

    Each ``do.<interaction>`` must be an array of action objects; the AI
    sometimes sends a single action as a plain object — wrap it in an array.
    """
    do = bindings.get("do")
    if isinstance(do, dict):
        for interaction in interactions():
            val = do.get(interaction)
            if isinstance(val, dict):
                do[interaction] = [val]
    return bindings


def validate_bindings(bindings: dict, project: Any = None) -> str | None:
    """Validate UI element bindings after normalization.

    Returns an error message string on failure, or None if valid. Every
    problem found is in the one message, the ``do`` half's before the
    ``show`` half's. With ``project``, an action naming a macro or device the
    project lacks is only logged as a warning, and only when nothing was
    refused.
    """
    issues = binding_issues(bindings)
    if issues:
        return "Binding validation failed: " + refusal(issues)
    do = bindings.get("do")
    _log_binding_warnings(bindings, do if isinstance(do, dict) else None, project)
    return None


def _log_binding_warnings(bindings: dict, do: dict | None, project: Any) -> None:
    """What is logged rather than refused: a top-level key other than show and
    do, and (with a project) an action naming a macro or device it lacks."""
    warnings = [
        f"unknown top-level binding key '{key}' (expected 'show' or 'do')"
        for key in bindings if key not in ("show", "do")
    ]
    if project and do is not None:
        warnings.extend(_missing_references(do, project))
    if warnings:
        log.warning("Binding reference warnings: %s", "; ".join(warnings))


#: The actions that name something in the project: the field naming it, and
#: the project list it should be in.
_PROJECT_REFERENCES = {"macro": ("macro", "macros"), "device.command": ("device", "devices")}


def _missing_references(do: dict, project: Any) -> list[str]:
    """Each top-level action naming a macro or device the project lacks.

    Only asked once the ``do`` half passed, so every interaction holds a list
    of action objects with a valid action name.
    """
    known = {
        attr: {entry.id for entry in getattr(project, attr, ())}
        for _, attr in _PROJECT_REFERENCES.values()
    }
    warnings = []
    for interaction, actions in do.items():
        for i, item in enumerate(actions):
            missing = _missing_reference(item, known)
            if missing:
                warnings.append(f"do.{interaction}[{i}]: {missing} not found in project")
    return warnings


def _missing_reference(item: dict, known: dict[str, set]) -> str | None:
    reference = _PROJECT_REFERENCES.get(item.get("action"))
    if reference is None:
        return None
    field, attr = reference
    value = item.get(field)
    if value and value not in known[attr]:
        return f"{field} '{value}'"
    return None
