"""What the AI's UI tools refuse in a touch-panel element's bindings.

Every UI tool that writes an element's or a master element's bindings first
wraps a single action in a list (``normalize_bindings``), then asks
``validate_bindings``: one sentence naming every malformed shape, or None. It
checks shape only. A macro or device the project lacks is logged, never
refused, and what the bindings point at is reviewed with the write by
``openavc.ui.page_references``.

The names it accepts come from their homes, never a copy kept here: the
interactions are page_references' ``ACTION_SLOTS``, the actions
``ui_events.DISPATCHED_ACTIONS``, and the visible_when operators the canonical
condition operators, ``macro_validation.CANONICAL_CONDITION_OPS`` (without the
aliases the panel also evaluates). The panel evaluates a visible_when in
``_evalConditionOp`` in ``panel.js``, a hand mirror of those operators, so a new
operator goes there too. The sentences listing the operators and the press
modes are written out, and a test holds each to its set. The sentences go back
to the cloud and into the chat.
"""

from __future__ import annotations

from typing import Any

from openavc.core.macro_validation import CANONICAL_CONDITION_OPS
from openavc.core.ui_events import DISPATCHED_ACTIONS
from openavc.ui.page_references import ACTION_SLOTS
from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Touch-panel element bindings use the show/do model: `show` is what the
# element reflects from state (value / items / look / visible_when), `do` is the
# action lists keyed by interaction kind. (Control-surface buttons keep their
# own flat format — see SURFACE_BUTTONS_FORMAT in plugin_tools.)
_DO_INTERACTIONS = frozenset(ACTION_SLOTS)

# v0.6 flat slot names. If any appear at the top level of an element's bindings
# the AI is using the retired model; we reject and point it at show/do.
_LEGACY_FLAT_KEYS = frozenset((
    "press", "release", "hold", "change", "submit", "select", "route",
    "audio_route", "mute_route", "audio_mute_route",
    "value", "variable", "text", "feedback", "color", "items",
    "selected", "visible_when", "meter",
))
_LEGACY_REFUSAL = (
    "Binding validation failed: bindings use the show/do model. Put "
    "interaction actions under 'do' (do.press, do.change, do.route, ...) "
    "and state reads under 'show' (show.value, show.look, show.visible_when, "
    "show.items). Remove top-level: "
)


def normalize_bindings(bindings: dict) -> dict:
    """Normalize a touch-panel element's show/do bindings to canonical format.

    Each ``do.<interaction>`` must be an array of action objects; the AI
    sometimes sends a single action as a plain object — wrap it in an array.
    """
    do = bindings.get("do")
    if isinstance(do, dict):
        for interaction in _DO_INTERACTIONS:
            val = do.get(interaction)
            if isinstance(val, dict):
                do[interaction] = [val]
    return bindings


_VALID_VISIBLE_WHEN_OPS = CANONICAL_CONDITION_OPS
_VISIBLE_WHEN_OPS_TEXT = "eq, ne, gt, lt, gte, lte, truthy, falsy"
# Panel UI element `do` bindings: the runtime's own set, never a copy, since
# refusing an action the runtime dispatches is a write door lying about the
# platform. The page move is "ui.navigate" here, matching the macro step and
# the WS frame — one spelling for one concept. A control surface's deck-page
# action is a separate thing (SURFACE_BUTTONS_FORMAT) and is still
# "navigate": it moves the deck's own pages, not a panel page.
_VALID_ACTION_TYPES = DISPATCHED_ACTIONS
_VALID_MODES = frozenset(("tap", "toggle", "hold_repeat", "tap_hold"))
_MODES_TEXT = "tap, toggle, hold_repeat, tap_hold"

# Required fields per action type (beyond "action" itself)
_ACTION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "macro": ("macro",),
    "device.command": ("device", "command"),
    "state.set": ("key",),
    "ui.navigate": ("page",),
    "script.call": ("function",),
    "event.emit": ("event",),
    "value_map": ("map",),
}


def _validate_action(action: dict, path: str) -> str | None:
    """Validate a single action object. Returns error string or None."""
    action_type = action.get("action", "")
    if not action_type:
        return f"{path}: missing 'action' field"
    if not isinstance(action_type, str):
        return _not_an_action_name(action_type, path)
    if action_type not in _VALID_ACTION_TYPES:
        return (
            f"{path}: action type '{action_type}' is not valid. "
            f"Use: {', '.join(sorted(_VALID_ACTION_TYPES))}"
        )
    for field in _ACTION_REQUIRED_FIELDS.get(action_type, ()):
        if _is_blank(action.get(field)):
            return f"{path}: {action_type} action requires '{field}'"
    check = _ACTION_TYPE_CHECKS.get(action_type)
    return check(action, path) if check else None


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _not_an_action_name(action_type: Any, path: str) -> str:
    """The refusal of an ``action`` field that holds something other than a name.

    Holding another action object is a documented anti-pattern, and the
    nesting is invisible in a diff, so the nested action's name is said.
    """
    nested = action_type.get("action") if isinstance(action_type, dict) else None
    inner = f" You nested a '{nested}' action inside it." if isinstance(nested, str) else ""
    return (
        f"{path}: 'action' must be the action NAME, got "
        f"{type(action_type).__name__}.{inner} Write the fields flat on this "
        f"object -- {{\"action\": \"device.command\", \"device\": ..., "
        f"\"command\": ...}} -- not an action wrapped in another action."
    )


def _state_set_problem(action: dict, path: str) -> str | None:
    """Device state is read-only: never write a device.* key with state.set.
    To drive a device, use a device.command action with $value. This makes
    the device-two-way footgun unrepresentable."""
    key = action.get("key", "")
    if isinstance(key, str) and key.startswith("device."):
        return (
            f"{path}: state.set cannot target a device key ('{key}'). Device "
            f"state is read-only — drive the device with a device.command "
            f"action using $value instead."
        )
    return None


def _value_map_problem(action: dict, path: str) -> str | None:
    """Each option maps to an action (or a list of actions), each checked as
    one, so the device-key rule and field checks apply to them too. The first
    problem is the answer."""
    vmap = action.get("map")
    if not isinstance(vmap, dict):
        return f"{path}: value_map action requires 'map' to be an object"
    for opt, mapped in vmap.items():
        for sub_path, sub in _mapped_actions(mapped, f"{path}.map[{opt}]"):
            err = _validate_action(sub, sub_path)
            if err:
                return err
    return None


def _mapped_actions(mapped: Any, path: str) -> list[tuple[str, dict]]:
    """The action objects one value_map option maps to, each with its path;
    anything else is skipped."""
    if isinstance(mapped, list):
        return [(f"{path}[{j}]", sub) for j, sub in enumerate(mapped) if isinstance(sub, dict)]
    return [(path, mapped)] if isinstance(mapped, dict) else []


#: What one action type is checked for beyond its required fields.
_ACTION_TYPE_CHECKS = {"state.set": _state_set_problem, "value_map": _value_map_problem}


def validate_bindings(bindings: dict, project: Any = None) -> str | None:
    """Validate UI element bindings after normalization.

    Returns an error message string on failure, or None if valid. Every
    problem found is in the one message, the ``do`` half's before the
    ``show`` half's. With ``project``, an action naming a macro or device the
    project lacks is only logged as a warning, and only when nothing was
    refused.
    """
    # Steer the AI off the retired flat model: every interaction action belongs
    # under `do`, every state read under `show`.
    legacy = sorted(k for k in bindings if k in _LEGACY_FLAT_KEYS)
    if legacy:
        return _LEGACY_REFUSAL + ", ".join(legacy)

    errors: list[str] = []
    do = _binding_half(bindings, "do", errors)
    if do is not None:
        _check_do(do, errors)
    show = _binding_half(bindings, "show", errors)
    if show is not None:
        _validate_show(show, errors)
    if errors:
        return "Binding validation failed: " + "; ".join(errors)
    _log_binding_warnings(bindings, do, project)
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


def _binding_half(bindings: dict, name: str, errors: list[str]) -> dict | None:
    """``bindings[name]`` when it is an object; None when it is absent, or
    (with the problem added to ``errors``) when it is anything else."""
    half = bindings.get(name)
    if half is None or isinstance(half, dict):
        return half
    errors.append(f"'{name}' must be an object, got {type(half).__name__}")
    return None


def _check_do(do: dict, errors: list[str]) -> None:
    """The ``do`` half: every interaction's actions, then the first press
    action's mode."""
    for interaction, actions in do.items():
        errors.extend(_interaction_problems(interaction, actions))
    first = _first_press_action(do.get("press"))
    if first is not None:
        _check_press_mode(first, errors)


def _interaction_problems(interaction: str, actions: Any) -> list[str]:
    if interaction not in _DO_INTERACTIONS:
        return [
            f"do.{interaction}: not a valid interaction. Use: "
            + ", ".join(sorted(_DO_INTERACTIONS))
        ]
    if not isinstance(actions, list):
        return [
            f"do.{interaction} must be an array of action objects, "
            f"got {type(actions).__name__}"
        ]
    problems = []
    for i, item in enumerate(actions):
        problem = _action_item_problem(item, f"do.{interaction}[{i}]")
        if problem:
            problems.append(problem)
    return problems


def _action_item_problem(item: Any, path: str) -> str | None:
    if not isinstance(item, dict):
        return f"{path}: expected an action object, got {type(item).__name__}"
    return _validate_action(item, path)


def _first_press_action(press: Any) -> dict | None:
    """The action a button's mode is read from: the first under ``do.press``."""
    if not isinstance(press, list) or not press:
        return None
    first = press[0]
    return first if isinstance(first, dict) else None


def _check_press_mode(first: dict, errors: list[str]) -> None:
    """What the button's mode needs: a toggle its key and off action, a
    tap-and-hold its hold action."""
    mode = first.get("mode", "tap")
    if mode and mode not in _VALID_MODES:
        errors.append(
            f"do.press[0]: mode '{mode}' is not valid. Use: {_MODES_TEXT}"
        )
    elif mode == "toggle":
        if not first.get("toggle_key"):
            errors.append("do.press[0]: toggle mode requires 'toggle_key'")
        _check_mode_action(first, mode, "off_action", errors)
    elif mode == "tap_hold":
        _check_mode_action(first, mode, "hold_action", errors)


def _check_mode_action(first: dict, mode: str, field: str, errors: list[str]) -> None:
    """A mode's second action (``off_action``, ``hold_action``) is an action
    object, checked as an action unless it is empty."""
    action = first.get(field)
    if not isinstance(action, dict):
        errors.append(f"do.press[0]: {mode} mode requires '{field}' (an action object)")
    elif action:
        err = _validate_action(action, f"do.press[0].{field}")
        if err:
            errors.append(err)


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


def _validate_show(show: dict, errors: list[str]) -> None:
    """Validate the ``show`` half of an element's bindings (value / items / look /
    visible_when). Appends any problems to ``errors`` in place."""
    for slot, check in _SHOW_CHECKS:
        value = show.get(slot)
        if value is None:
            continue
        if isinstance(value, dict):
            check(value, errors)
        else:
            errors.append(f"show.{slot} must be an object, got {type(value).__name__}")


def _check_shown_value(val: dict, errors: list[str]) -> None:
    """``show.value``: the thing the control IS (numeric / selection / label text)."""
    source = val.get("source", "state")
    if source == "macro_progress":
        if not val.get("macro"):
            errors.append("show.value: macro_progress source requires 'macro'")
    elif source in ("state", "conditional", ""):
        _check_state_read(val, errors)
    else:
        errors.append(
            f"show.value: source '{source}' is not valid. Use: state, macro_progress"
        )


def _check_state_read(val: dict, errors: list[str]) -> None:
    if not val.get("key"):
        errors.append("show.value: requires 'key' (the state key to read)")
    if "condition" in val:
        errors.extend(
            f"show.value: conditional text requires '{field}'"
            for field in ("text_true", "text_false") if field not in val
        )
    if val.get("write_back"):
        key = val.get("key", "")
        if not (isinstance(key, str) and key.startswith("var.")):
            errors.append(
                "show.value: write_back (two-way) is only valid for var.* keys. "
                f"'{key}' is read-only — to drive a device add a do.change "
                "device.command using $value."
            )


def _check_items(itm: dict, errors: list[str]) -> None:
    """``show.items``: list row population."""
    if not (itm.get("key") or itm.get("key_pattern")):
        errors.append("show.items: requires 'key' or 'key_pattern' (the state providing items)")


#: The forms a look takes, in the order they are looked for. Only the first one
#: present is checked, and it must be an object.
_LOOK_FORMS = {
    "states": "show.look.states must be an object mapping state values to style props",
    "condition": "show.look.condition must be an object (e.g. {\"equals\": \"on\"})",
    "map": "show.look.map must be an object mapping values to colors",
    "style_map": "show.look.style_map must be an object mapping option values to styles",
}


def _check_look(look: dict, errors: list[str]) -> None:
    """``show.look``: state-driven appearance (feedback / LED color map /
    per-option style)."""
    if not look.get("key"):
        errors.append("show.look: missing 'key' (state key to watch)")
    form = next((name for name in _LOOK_FORMS if name in look), None)
    if form is None:
        return
    if not isinstance(look[form], dict):
        errors.append(_LOOK_FORMS[form])
    elif form == "states":
        errors.extend(_nested_style_problems(look["states"]))


def _nested_style_problems(states: dict) -> list[str]:
    """A state's properties are flat; the first state nesting them inside a
    'style' object is named."""
    for state_name, props in states.items():
        if isinstance(props, dict) and "style" in props:
            return [
                f"show.look.states.{state_name}: properties (label, bg_color, etc.) "
                f"must be flat, not nested inside 'style'"
            ]
    return []


def _check_visible_when(vw: dict, errors: list[str]) -> None:
    """``show.visible_when``: a single condition, or any:[] (OR) / all:[] (AND)
    groups of them. A group that is not a list has nothing to check."""
    group = "any" if "any" in vw else ("all" if "all" in vw else None)
    if group is None:
        errors.extend(_condition_problems(vw, "show.visible_when", " (state key to evaluate)"))
        return
    conditions = vw[group] if isinstance(vw[group], list) else []
    for i, cond in enumerate(conditions):
        if isinstance(cond, dict):
            errors.extend(_condition_problems(cond, f"show.visible_when.{group}[{i}]"))


def _condition_problems(cond: dict, path: str, key_note: str = "") -> list[str]:
    problems = []
    if not cond.get("key"):
        problems.append(f"{path}: missing 'key'{key_note}")
    op = cond.get("operator")
    if op and op not in _VALID_VISIBLE_WHEN_OPS:
        problems.append(
            f"{path}: operator '{op}' is not valid. Use: {_VISIBLE_WHEN_OPS_TEXT}"
        )
    return problems


#: The ``show`` slots in the order they are checked, each with its check.
_SHOW_CHECKS = (
    ("value", _check_shown_value),
    ("items", _check_items),
    ("look", _check_look),
    ("visible_when", _check_visible_when),
)
