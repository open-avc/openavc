"""
OpenAVC Cloud — AI tool call handler.

Handles AI_TOOL_CALL messages from the cloud platform. Each tool call
is dispatched to the appropriate local function — reading project state,
controlling devices, modifying configurations, installing drivers, etc.
Results are sent back as AI_TOOL_RESULT messages.
"""

from __future__ import annotations

import asyncio
import errno
import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from openavc.cloud.protocol import (
    AI_TOOL_RESULT, build_ai_tool_result_payload, extract_payload,
)
from openavc.core.macro_validation import CANONICAL_CONDITION_OPS
from openavc.core.ui_events import DISPATCHED_ACTIONS
from openavc.ui.page_references import ACTION_SLOTS
from openavc.utils.logger import get_logger

if TYPE_CHECKING:
    from openavc.cloud.agent import CloudAgent
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus

log = get_logger(__name__)

# Standard config format for control surface plugins (Stream Deck, X-Keys, etc.).
# The Surface Configurator writes this format; the platform documents it once so
# the AI can configure any surface plugin without plugin-specific hardcoding.
SURFACE_BUTTONS_FORMAT = """\
Control surface buttons are stored in a top-level "buttons" array (NOT under "pages").
Each entry in the array represents one physical button:

{
  "index": 0,            // Button position (0-based, left-to-right then top-to-bottom)
  "page": 0,             // Page number (0-based, for multi-page surfaces)
  "label": "Power",      // Text shown on the button (optional)
  "icon": "power",       // Lucide icon name, e.g. "power", "volume-2", "play" (optional)
  "bg_color": "#1a1a2e", // Per-button default background color, hex (optional)
  "text_color": "#e0e0e0", // Per-button default text color, hex (optional)
  "label_source": "var.x",   // State key whose live value replaces the label (optional)
  "value_source": "var.lvl", // State key shown live under the label (optional)
  "unit": "dB",              // Appended to the live value (optional)
  "meter": { "min": 0, "max": 100, "color": "#8ab493",   // Level bar along the
    "thresholds": [{ "above": 90, "color": "#e05341" }] }, // bottom edge (optional)
  "bindings": {          // Action and feedback configuration
    "press": [{            // MUST be an array of action objects
      "action": "macro",           // Action type: "macro", "device.command", "state.set", "navigate"
      "macro": "macro_name"        // For macro action
      // OR for device.command:
      // "action": "device.command", "device": "device_id", "command": "cmd", "params": {}
      // OR for state.set:
      // "action": "state.set", "key": "var.my_var", "value": "new_value"
      // OR for deck-page navigation:
      // "action": "navigate", "page": "__next_page__" (next), "__prev_page__" (previous),
      //   or a 0-based page index, e.g. "page": 2

      // Optional mode (default is "tap"):
      // "mode": "toggle",  — requires toggle_key, toggle_value, off_action
      // "mode": "hold_repeat", — requires hold_repeat_ms (default 200)
      // "mode": "tap_hold", — requires hold_action, hold_threshold_ms (default 500)
    }],
    "feedback": {         // Visual feedback based on state (optional)
      "source": "state",
      "key": "device.my_device.power",   // State key to watch
      "condition": { "equals": true },   // When this matches, button shows active style
      "style_active": { "bg_color": "#ff0606", "text_color": "#ffffff" },
      "style_inactive": { "bg_color": "#56aa02", "text_color": "#ffffff" },
      "label_active": "OFF",             // Condition matches (device IS on) — show what pressing WILL DO
      "label_inactive": "ON"            // Condition doesn't match (device is off) — show what pressing WILL DO
    },
    "visible_when": {     // Hide the button unless this state condition holds (optional)
      "key": "device.projector_1.power",
      "operator": "eq",   // eq, ne, gt, lt, gte, lte, truthy, falsy
      "value": "on"       // omit value for truthy/falsy; use "any":[...] for OR logic
    }
  }
}

Color priority: feedback style > per-button defaults > global plugin config defaults.
A hidden button (visible_when false) renders as a blank black key and ignores presses.
Only include fields you need. Unassigned buttons can be omitted from the array.

Locked buttons (optional): entries that must stay identical on every page (page
switchers, mute-all, help) go in a top-level "global_buttons" array. Same entry shape
as "buttons" but with NO "page" field. A global_buttons entry at an index wins over
any per-page button at that index, on every page. A button navigating to a specific
page index is automatically highlighted while that page is showing.

Pages exist by being used (there is no page-count setting): placing a button, paging
rule, page name, or numeric navigate target on page N creates pages 0 through N.

Automatic paging (optional): add a top-level "auto_page" array alongside "buttons" to
switch pages automatically when state changes:

"auto_page": [
  { "page": 1, "when": { "key": "device.projector_1.power", "operator": "eq", "value": "on" } },
  { "page": 0, "when": { "key": "device.projector_1.power", "operator": "ne", "value": "on" } }
]

Each "when" uses the same operator schema (and supports "any":[...] for OR). Rules are
evaluated in order; the first match wins, so list more specific conditions first.

Dials (optional): surfaces with rotary encoders report a dial_count in their plugin
state. Configure them with a top-level "dials" array (dials are not paged):

"dials": [
  {
    "index": 0,            // Dial position (0-based)
    "label": "Volume",     // Shown on the surface's display if it has one
    "icon": "volume-2",    // Optional icon on the dial's strip readout
    "unit": "%",           // Optional unit appended to the live value
    "meter": {...},        // Optional level-bar override (automatic when the
                           // adjust has min+max; false disables)
    "adjust": {            // Optional: turn increments a numeric state value
      "key": "var.volume", // var.* variable or the plugin's own plugin.<id>.* state
      "step": 2,           // Added per detent turned (negative when turned back)
      "min": 0, "max": 100 // Optional clamp
    },
    "cw": [{...}],         // Optional actions, fired once per detent turned
    "ccw": [{...}],        // (capped at 8 per event for fast spins)
    "press": [{...}],      // Optional actions on dial push
    "long_press": [{...}], // Optional: hold then release fires these instead
    "hold_threshold_ms": 500,
    "pressed_adjust": {...}, // Optional: turning WHILE pushed adjusts this
                             // (fine trim); pressed_cw/pressed_ccw also exist.
                             // A push that turned never fires press/long_press.
    "touch": [{...}],        // Optional: tapping the dial's strip readout
                             // (default: tap presses the dial)
    "long_touch": [{...}],   // Optional long-tap override
    "fader": true            // Optional: taps/swipes on the readout jump the
                             // adjust value to the touched position
  }
]

Action objects use the same format as button "press" actions. A macro or trigger
watching the adjust key turns the dial into a device control (volume, gain, pan).

Touchscreen strip (optional): surfaces reporting has_touchscreen render one zone per
dial by default — the dial's label, icon, live adjust value, and an automatic level
bar when the adjust has bounds; tapping a dial's zone presses the dial. With nothing
configured at all the strip shows a clock ({"touchscreen": {"idle": "blank"}} opts
out). Take over the strip with a top-level "touchscreen" object:

"touchscreen": {
  "zones": [
    {
      "label": "Mics",                 // Static label (label_source state key overrides)
      "value_source": "var.mic_gain",  // State key whose live value is displayed
      "unit": "dB",                    // Optional unit appended to the value
      "icon": "mic",                   // Optional Lucide icon
      "meter": { "min": 0, "max": 100, "thresholds": [{...}] },  // Level bar
      "feedback": {...},               // Optional state-driven colors (same
                                       // schema as button feedback)
      "touch": [{...}],                // Optional actions when the zone is tapped
      "long_touch": [{...}],           // Optional actions on long-press (falls back to touch)
      "drag_adjust": {                 // Optional: a horizontal swipe steps a value
        "key": "var.mic_gain", "step": 1, "min": 0, "max": 100,
        "fader": true                  // Taps/swipes jump to the touched position
      },
      "bg_color": "#1a1a2e", "text_color": "#e0e0e0"  // Optional colors
    }
  ]
}

Zones split the strip evenly; explicit "x"/"w" pixel bounds override (strip is
800x100). Default per-dial zones wire drag_adjust to the dial's adjust
automatically, so swiping under a dial does what turning it does. Every touch
briefly flashes the touched zone. Custom zones replace ALL default per-dial
zones — carry over any readouts that should stay.

Touch keys (optional): surfaces reporting touch_key_count in plugin state have
color-only keys at the indices after the LCD keys (key_count .. key_count +
touch_key_count - 1). Configure them as ordinary "buttons" entries at those
indices: presses and feedback work normally, but the key shows only its
bg_color (or feedback bg colors) as an RGB glow — label and icon are ignored.

Info screen (optional): surfaces reporting has_info_screen accept a top-level
"info_strip" object that renders their small secondary screen:

"info_strip": { "source": "state", "key": "var.room_temp", "label": "Temp" }
// or static text:
"info_strip": { "source": "text", "text": "Room A", "label": "" }
// or two elements side by side:
"info_strip": { "items": [
  { "label": "Temp", "key": "var.room_temp", "unit": "F" },
  { "source": "text", "text": "Room A" }
] }

A "state" source shows the key's live value and refreshes when it changes.
Info elements accept the same display fields as zones (icon, unit, meter,
feedback). With no info_strip configured the screen shows a clock;
{"source": "clock"} asks for it explicitly, {"source": "blank"} turns it off.
"""


# --- Binding normalization and validation ---

# Touch-panel element bindings use the show/do model: `show` is what the
# element reflects from state (value / items / look / visible_when), `do` is the
# action lists keyed by interaction kind. (Control-surface buttons keep their
# own flat format — see SURFACE_BUTTONS_FORMAT.)
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


def _normalize_bindings(bindings: dict) -> dict:
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
# action is a separate thing (see SURFACE_BUTTONS_FORMAT) and is still
# "navigate": it moves the deck's own pages, not a panel page.
_VALID_ACTION_TYPES = DISPATCHED_ACTIONS
_VALID_MODES = frozenset(("tap", "toggle", "hold_repeat", "tap_hold"))

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


def _validate_bindings(bindings: dict, project: Any = None) -> str | None:
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
            f"do.press[0]: mode '{mode}' is not valid. "
            f"Use: tap, toggle, hold_repeat, tap_hold"
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


# --- Variable validation ---

_VALID_VARIABLE_TYPES = frozenset(("string", "number", "boolean"))


def _validate_variable(var_type: str, default: Any = None) -> str | None:
    """Validate variable type and default value. Returns error string or None."""
    if var_type not in _VALID_VARIABLE_TYPES:
        return (
            f"Variable type '{var_type}' is not valid. "
            f"Use: string, number, boolean"
        )
    if default is not None:
        if var_type == "number" and not isinstance(default, (int, float)):
            return f"Variable type is 'number' but default '{default}' is {type(default).__name__}"
        if var_type == "boolean" and not isinstance(default, bool):
            return f"Variable type is 'boolean' but default '{default}' is {type(default).__name__}"
        if var_type == "string" and not isinstance(default, str):
            return f"Variable type is 'string' but default '{default}' is {type(default).__name__}"
    return None


# --- Plugin config schema validation ---
# Moved to openavc/core/plugin_config.py so the REST config endpoint and the
# cloud AI tool accept/reject identical shapes.


# --- State key validation ---
# The key/value rules live in openavc.core.state_store.check_state_write, shared
# with the REST and WebSocket doors — this layer used to keep its own copy of
# the prefix list and the flat-primitive check, which is how the three doors
# came to disagree about what a valid write was.


# --- Tool result classification ---

def _tool_result_is_error(result: Any) -> bool:
    """Decide whether a tool's return value represents a failure.

    The tool mixins signal failure in two ways: an explicit ``success: False``
    in the returned dict, or (the common convention) a dict whose only failure
    signal is a truthy ``error`` key. Both must be reported back to the model
    with ``success=False`` so a failed mutation isn't delivered as a success
    with the error buried in the result body (the cloud sets ``is_error`` on
    the Anthropic tool_result from this flag).

    An explicit ``success`` key always wins, so a tool that returns
    ``{"success": True, "error": None, ...}`` is never misread as a failure.
    Non-dict returns (lists, scalars) are always treated as success.
    """
    if not isinstance(result, dict):
        return False
    if "success" in result:
        return not result["success"]
    return bool(result.get("error"))


# --- What a tool that raised answers ---

#: The answer for an exception the platform did not word. Its type and text
#: are in the local log, which the Programmer's Log view shows.
TOOL_FAILED = "The tool failed with an error on the server. The Programmer's Log has the details."

# The errnos friendly_error answers with a fixed sentence of its own.
_WORDED_ERRNOS = frozenset({
    errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EADDRINUSE, errno.ENOSPC, errno.EROFS,
})


def describe_tool_failure(exc: Exception) -> str:
    """What the cloud is told when a tool raised instead of answering.

    The answer is kept in the cloud chat history, so an exception's text
    reaches it only when the platform wrote that text for a reader: a typed
    refusal, which tells the model what to change in its next call. The
    project's validation errors are reduced to each field and its message.
    Anything else (an operating system or library error that may hold a local
    path or an address, a driver's or a plugin's own text, Python's own
    message for a mistake) gets :data:`TOOL_FAILED`.
    """
    from pydantic import ValidationError

    from openavc.api.error_messages import _WORDED_CONNECTION_ERRORS, friendly_error
    from openavc.core.device_manager import DeviceNotFoundError
    from openavc.core.installed_equipment import InstalledEquipmentError
    from openavc.core.isc import ISCRemoteError
    from openavc.drivers.base import (
        CommandParamError,
        CommandPartialError,
        CommandRefusedError,
        DeviceSettingValueError,
        UnknownCommandError,
        WakeAddressError,
    )
    from openavc.drivers.command_files import FileCommandError
    from openavc.drivers.command_outcome import CommandOutcomeError
    from openavc.reach_devices import AdapterUnavailable
    from openavc.utils.community_integrity import CommunityArtifactError

    if isinstance(exc, ValidationError):
        problems = [
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'value'}: "
            f"{error.get('msg', '')}"
            for error in exc.errors(include_url=False, include_input=False)
        ]
        return "; ".join(problems) or TOOL_FAILED
    if isinstance(exc, WakeAddressError):
        return friendly_error(exc)
    if isinstance(exc, (
        DeviceNotFoundError, UnknownCommandError, CommandParamError,
        DeviceSettingValueError, FileCommandError, CommandOutcomeError,
        CommandRefusedError, CommandPartialError, AdapterUnavailable,
        InstalledEquipmentError, CommunityArtifactError, ISCRemoteError,
    )) and str(exc).strip():
        return str(exc)
    if isinstance(exc, ConnectionError):
        # The device manager's own refusals carry the device's id.
        if getattr(exc, "device_id", None) is not None:
            return str(exc)
        return friendly_error(exc) if isinstance(exc, _WORDED_CONNECTION_ERRORS) else TOOL_FAILED
    if isinstance(exc, PermissionError) or (
        isinstance(exc, OSError) and getattr(exc, "errno", None) in _WORDED_ERRNOS
    ):
        return friendly_error(exc)
    return TOOL_FAILED


from openavc.cloud.tools.catalog_tools import CatalogToolsMixin
from openavc.cloud.tools.custom_ui_tools import CustomUIToolsMixin
from openavc.cloud.tools.device_tools import DeviceToolsMixin
from openavc.cloud.tools.driver_tools import DriverToolsMixin
from openavc.cloud.tools.macro_tools import MacroToolsMixin
from openavc.cloud.tools.plugin_tools import PluginToolsMixin
from openavc.cloud.tools.project_tools import ProjectToolsMixin
from openavc.cloud.tools.script_tools import ScriptToolsMixin
from openavc.cloud.tools.system_tools import SystemToolsMixin
from openavc.cloud.tools.ui_tools import UIToolsMixin


class AIToolHandler(
    ProjectToolsMixin,
    DeviceToolsMixin,
    DriverToolsMixin,
    CatalogToolsMixin,
    ScriptToolsMixin,
    UIToolsMixin,
    CustomUIToolsMixin,
    PluginToolsMixin,
    MacroToolsMixin,
    SystemToolsMixin,
):
    """
    Handles AI tool calls from the cloud and dispatches to local subsystems.

    Every tool maps to an existing local REST API operation or engine call,
    ensuring the AI has the same capabilities as the Programmer IDE.

    Tool handler methods are organized into domain-specific mixins under
    cloud/tools/ — this class provides the dispatch table and execution
    infrastructure.
    """

    # Tools that only read data and never modify the project
    _READ_ONLY_TOOLS: set[str] = {
        "get_project_summary", "get_project_state", "get_state_value",
        "get_state_history", "list_devices", "get_device_info",
        "list_drivers", "search_community_drivers", "get_community_driver_detail",
        "find_driver_for_device", "get_installed_drivers",
        "get_driver_definition", "get_script_source", "get_script_health",
        "get_logs",
        "list_triggers", "get_macro", "get_ui_page",
        "list_plugins", "browse_community_plugins", "get_plugin_config",
        "list_themes", "get_theme", "list_assets",
        "get_isc_status", "list_isc_peers",
        "get_device_settings", "check_references",
        "get_discovery_results", "wait",
        "list_ui_files", "read_ui_file", "get_project_stylesheet",
    }

    # Tools that may run without the project-serialization lock: every
    # read-only tool, plus execute_macro — it waits on a macro run (bounded
    # now, but still tens of seconds) and never mutates engine.project, so
    # holding the lock would needlessly block project edits. Everything else
    # (all project-mutating tools, and unclassified tools) takes the lock by
    # default, so a new write tool is serialized automatically.
    _CONCURRENT_SAFE_TOOLS = _READ_ONLY_TOOLS | {"execute_macro"}

    # Idle timeout before resetting the AI backup flag (seconds)
    _AI_BACKUP_IDLE_TIMEOUT = 300  # 5 minutes

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        project_path=None,
    ):
        self._agent = agent
        self._devices = devices
        self._events = events
        self._project_path = project_path

        # Backup tracking: create one backup before the first write in a conversation
        self._ai_backup_created: bool = False
        self._ai_last_write_time: float = 0

        # Serialize project-mutating tools so concurrent tool tasks can't
        # interleave on the shared engine.project (lost updates). Hold strong
        # refs to in-flight tasks so they aren't GC'd mid-run and can be
        # cancelled on shutdown.
        self._project_lock = asyncio.Lock()
        self._pending_tasks: set[asyncio.Task] = set()

        # Tool dispatch table
        self._tools: dict[str, Any] = {
            # Reading / Searching
            "get_project_summary": self._get_project_summary,
            "get_project_state": self._get_project_state,
            "get_state_value": self._get_state_value,
            "get_state_history": self._get_state_history,
            "list_devices": self._list_devices,
            "get_device_info": self._get_device_info,
            "list_drivers": self._list_drivers,
            "search_community_drivers": self._search_community_drivers,
            "get_community_driver_detail": self._get_community_driver_detail,
            "find_driver_for_device": self._find_driver_for_device,
            "get_installed_drivers": self._get_installed_drivers,
            "get_driver_definition": self._get_driver_definition,
            "get_script_source": self._get_script_source,
            "get_script_health": self._get_script_health,
            "get_logs": self._get_logs,
            "list_triggers": self._list_triggers,
            "get_macro": self._get_macro,
            "get_ui_page": self._get_ui_page,
            # Writing / Creating
            "update_project_metadata": self._update_project_metadata,
            "update_device": self._update_device,
            "delete_device": self._delete_device,
            "add_device": self._add_device,
            "add_device_group": self._add_device_group,
            "update_device_group": self._update_device_group,
            "delete_device_group": self._delete_device_group,
            "add_variable": self._add_variable,
            "update_variable": self._update_variable,
            "delete_variable": self._delete_variable,
            "add_macro": self._add_macro,
            "update_macro": self._update_macro,
            "delete_macro": self._delete_macro,
            "add_ui_page": self._add_ui_page,
            "update_ui_page": self._update_ui_page,
            "delete_ui_page": self._delete_ui_page,
            "add_ui_elements": self._add_ui_elements,
            "update_ui_element": self._update_ui_element,
            "delete_ui_elements": self._delete_ui_elements,
            "add_master_element": self._add_master_element,
            "update_master_element": self._update_master_element,
            "delete_master_element": self._delete_master_element,
            "install_community_driver": self._install_community_driver,
            "create_driver_definition": self._create_driver_definition,
            "update_driver_definition": self._update_driver_definition,
            "create_script": self._create_script,
            "update_script_source": self._update_script_source,
            "delete_script": self._delete_script,
            # Plugins
            "list_plugins": self._list_plugins,
            "browse_community_plugins": self._browse_community_plugins,
            "install_plugin": self._install_plugin,
            "uninstall_plugin": self._uninstall_plugin,
            "enable_plugin": self._enable_plugin,
            "disable_plugin": self._disable_plugin,
            "get_plugin_config": self._get_plugin_config,
            "update_plugin_config": self._update_plugin_config,
            # Discovery
            "start_discovery_scan": self._start_discovery_scan,
            "get_discovery_results": self._get_discovery_results,
            # Themes
            "list_themes": self._list_themes,
            "get_theme": self._get_theme,
            "apply_theme": self._apply_theme,
            # Assets
            "list_assets": self._list_assets,
            "delete_asset": self._delete_asset,
            # ISC
            "get_isc_status": self._get_isc_status,
            "list_isc_peers": self._list_isc_peers,
            "send_isc_command": self._send_isc_command,
            # Device settings
            "get_device_settings": self._get_device_settings,
            "set_device_setting": self._set_device_setting,
            # Custom UI: the project's own ui/ folder, and the stylesheet that
            # restyles the built-in controls. The three writers are deliberately
            # outside _CONCURRENT_SAFE_TOOLS, so they take the project lock and
            # ride the one-time pre-AI backup that already covers ui/.
            "list_ui_files": self._list_ui_files,
            "read_ui_file": self._read_ui_file,
            "write_ui_file": self._write_ui_file,
            "delete_ui_file": self._delete_ui_file,
            "get_project_stylesheet": self._get_project_stylesheet,
            "set_project_stylesheet": self._set_project_stylesheet,
            "review_custom_ui": self._review_custom_ui,
            # UI simulation
            "simulate_ui_action": self._simulate_ui_action,
            "review_ui": self._review_ui,
            # Impact checking
            "check_references": self._check_references,
            # Async / Waiting
            "wait": self._wait,
            # Actions
            "send_device_command": self._send_device_command,
            "test_device_connection": self._test_device_connection,
            "test_driver_command": self._test_driver_command,
            "execute_macro": self._execute_macro,
            "cancel_macro": self._cancel_macro,
            "set_state_value": self._set_state_value,
            "test_trigger": self._test_trigger,
        }

    async def handle(self, msg: dict[str, Any]) -> None:
        """Route an incoming AI_TOOL_CALL message to the appropriate handler.

        Dispatches tool execution as a background task so long-running tools
        (discovery scans, wait) don't block the agent's receive loop.
        """
        payload = extract_payload(msg)
        request_id = payload.get("request_id", "")
        tool_name = payload.get("tool_name", "")
        tool_input = payload.get("tool_input", {})

        log.info(f"AI tool call: {tool_name} (request_id={request_id})")

        handler = self._tools.get(tool_name)
        if not handler:
            await self._send_result(
                request_id, False,
                error=f"Unknown tool: {tool_name}",
            )
            return

        # Run in background so the receive loop stays responsive to pings/acks.
        # Keep a strong ref (GC-safety) and track it so shutdown() can cancel
        # in-flight tools when the agent stops.
        task = asyncio.create_task(self._execute_tool(request_id, tool_name, handler, tool_input))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _execute_tool(
        self, request_id: str, tool_name: str, handler: Any, tool_input: dict
    ) -> None:
        """Execute a tool handler and send the result back to the cloud."""
        try:
            if tool_name in self._CONCURRENT_SAFE_TOOLS:
                result = await handler(tool_input)
            else:
                # Project-mutating tool: serialize on the project lock so two
                # concurrent tools can't interleave on engine.project, and run
                # the one-time pre-AI backup under the same lock so it happens
                # exactly once even under concurrency.
                async with self._project_lock:
                    await self._maybe_create_pre_ai_backup()
                    result = await handler(tool_input)
            # A tool that returns an {"error": ...} / {"success": False} dict
            # has failed even though it didn't raise — report it as a failure
            # (carrying the body) so the model gets a structured error signal
            # instead of a success with the error hidden in the result.
            if _tool_result_is_error(result):
                err = result.get("error") if isinstance(result, dict) else None
                await self._send_result(
                    request_id, False, result=result,
                    error=str(err) if err else "The tool reported an error.",
                )
            else:
                await self._send_result(request_id, True, result=result)
        except Exception as e:
            # The type and text in the message itself, not only the traceback:
            # the Log view shows a record's message, and the answer below
            # sends the reader there.
            log.exception(f"AI tool handler: {tool_name} failed: {type(e).__name__}: {e}")
            # Worded before it leaves the local trust boundary: the answer is
            # kept in the cloud chat history.
            await self._send_result(request_id, False, error=describe_tool_failure(e))

    async def _maybe_create_pre_ai_backup(self) -> None:
        """Create one backup before the first project-mutating tool in an AI
        conversation. The caller holds ``self._project_lock`` so the flag
        check and the backup are atomic (no double backup under concurrency).
        """
        # Reset the flag if idle for too long (treat as a new conversation).
        if (self._ai_last_write_time
                and time.monotonic() - self._ai_last_write_time > self._AI_BACKUP_IDLE_TIMEOUT):
            self._ai_backup_created = False

        if not self._ai_backup_created and self._project_path:
            try:
                from openavc.core.backup_manager import create_backup
                await asyncio.to_thread(create_backup, Path(self._project_path), "Before AI changes")
            except Exception:
                # Leave the flag unset so the next mutating tool retries the
                # backup. Latching it True on failure would silently disable
                # the safety net for the rest of the AI editing session.
                log.warning(
                    "Could not create pre-AI backup; will retry before the next change",
                    exc_info=True,
                )
            else:
                self._ai_backup_created = True

        self._ai_last_write_time = time.monotonic()

    async def shutdown(self) -> None:
        """Cancel any in-flight tool tasks.

        Called when the cloud agent stops so background tool execution doesn't
        outlive the agent, write to a torn-down engine, or leak tasks.
        """
        tasks = list(self._pending_tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._pending_tasks.clear()

    # Tool handler methods are defined in the domain-specific mixins:
    # - ProjectToolsMixin: project state, metadata
    # - DeviceToolsMixin: devices, device groups, settings, commands
    # - DriverToolsMixin: installed drivers, definitions, the raw command test
    # - CatalogToolsMixin: the community driver catalog
    # - ScriptToolsMixin: scripts
    # - UIToolsMixin: UI pages, elements
    # - PluginToolsMixin: plugin management
    # - MacroToolsMixin: macros, triggers, variables
    # - SystemToolsMixin: logs, discovery, themes, assets, ISC

    # ===== HELPERS =====

    def _get_engine(self):
        try:
            from openavc.api.rest import _engine
            return _engine
        except ImportError:
            return None

    def _get_driver_repo_dir(self) -> Path:
        from openavc.system_config import DRIVER_REPO_DIR
        return DRIVER_REPO_DIR

    def _get_driver_dirs(self) -> list[Path]:
        from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
        return [
            DRIVER_DEFINITIONS_DIR,
            DRIVER_REPO_DIR,
        ]

    async def _send_result(
        self, request_id: str, success: bool,
        result: Any = None, error: str | None = None
    ) -> None:
        if not request_id:
            return
        if result is not None:
            try:
                json.dumps(result)
            except (TypeError, ValueError):
                result = str(result)
        await self._agent.send_message(AI_TOOL_RESULT, build_ai_tool_result_payload(
            request_id, success, result=result, error=error,
        ))
