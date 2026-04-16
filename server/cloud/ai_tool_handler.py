"""
OpenAVC Cloud — AI tool call handler.

Handles AI_TOOL_CALL messages from the cloud platform. Each tool call
is dispatched to the appropriate local function — reading project state,
controlling devices, modifying configurations, installing drivers, etc.
Results are sent back as AI_TOOL_RESULT messages.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from server.cloud.protocol import AI_TOOL_RESULT, extract_payload
from server.utils.logger import get_logger

if TYPE_CHECKING:
    from server.cloud.agent import CloudAgent
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus

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
  "bindings": {          // Action and feedback configuration
    "press": [{            // MUST be an array of action objects
      "action": "macro",           // Action type: "macro", "device.command", "state.set", "navigate"
      "macro": "macro_name"        // For macro action
      // OR for device.command:
      // "action": "device.command", "device": "device_id", "command": "cmd", "params": {}
      // OR for state.set:
      // "action": "state.set", "key": "var.my_var", "value": "new_value"
      // OR for page navigation:
      // "action": "navigate", "page": "__next_page__"  (or "__prev_page__")

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
    }
  }
}

Color priority: feedback style > per-button defaults > global plugin config defaults.
Only include fields you need. Unassigned buttons can be omitted from the array.
"""


# --- Binding normalization and validation ---

_ACTION_SLOTS = frozenset(("press", "release", "hold", "change", "submit", "route", "select"))


def _normalize_bindings(bindings: dict) -> dict:
    """Normalize UI element bindings to canonical format.

    Action slots (press, release, hold) must be arrays of action objects.
    The AI sometimes sends them as plain objects — wrap in an array.
    """
    for slot in _ACTION_SLOTS:
        val = bindings.get(slot)
        if isinstance(val, dict):
            bindings[slot] = [val]
    return bindings
_NON_ACTION_SLOTS = frozenset((
    "feedback", "text", "color", "variable", "value",
    "visible_when", "selected", "items", "meter",
))
_VALID_VISIBLE_WHEN_OPS = frozenset((
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
))
_VALID_TEXT_SOURCES = frozenset(("state", "macro_progress", "conditional"))
_VALID_FEEDBACK_STATE_PROPS = frozenset((
    "label", "bg_color", "text_color", "icon", "icon_color",
    "button_image", "opacity",
))
_VALID_ACTION_TYPES = frozenset((
    "macro", "device.command", "state.set", "navigate", "page",
    "script.call", "value_map",
))
_VALID_MODES = frozenset(("tap", "toggle", "hold_repeat", "tap_hold"))

# Required fields per action type (beyond "action" itself)
_ACTION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "macro": ("macro",),
    "device.command": ("device", "command"),
    "state.set": ("key",),
    "navigate": ("page",),
    "page": ("page",),
    "script.call": ("function",),
    "value_map": ("map",),
}


def _validate_action(action: dict, path: str) -> str | None:
    """Validate a single action object. Returns error string or None."""
    action_type = action.get("action", "")
    if not action_type:
        return f"{path}: missing 'action' field"
    if action_type not in _VALID_ACTION_TYPES:
        return (
            f"{path}: action type '{action_type}' is not valid. "
            f"Use: macro, device.command, state.set, navigate, script.call, value_map"
        )
    required = _ACTION_REQUIRED_FIELDS.get(action_type, ())
    for field in required:
        if field not in action or action[field] is None or action[field] == "":
            return f"{path}: {action_type} action requires '{field}'"
    if action_type == "value_map" and not isinstance(action.get("map"), dict):
        return f"{path}: value_map action requires 'map' to be an object"
    return None


def _validate_bindings(bindings: dict, project: Any = None) -> str | None:
    """Validate UI element bindings after normalization.

    Returns an error message string on failure, or None if valid.
    The project parameter enables soft reference checks (warn-level).
    """
    errors: list[str] = []
    warnings: list[str] = []

    for slot in _ACTION_SLOTS:
        val = bindings.get(slot)
        if val is None:
            continue
        if not isinstance(val, list):
            errors.append(f"'{slot}' must be an array of action objects, got {type(val).__name__}")
            continue
        for i, item in enumerate(val):
            if not isinstance(item, dict):
                errors.append(f"{slot}[{i}]: expected an action object, got {type(item).__name__}")
                continue
            err = _validate_action(item, f"{slot}[{i}]")
            if err:
                errors.append(err)

    # Mode-specific validation on the first press action
    press = bindings.get("press")
    if isinstance(press, list) and press and isinstance(press[0], dict):
        first = press[0]
        mode = first.get("mode", "tap")
        if mode and mode not in _VALID_MODES:
            errors.append(
                f"press[0]: mode '{mode}' is not valid. Use: tap, toggle, hold_repeat, tap_hold"
            )
        elif mode == "toggle":
            if "toggle_key" not in first or not first["toggle_key"]:
                errors.append("press[0]: toggle mode requires 'toggle_key'")
            if "off_action" not in first or not isinstance(first.get("off_action"), dict):
                errors.append("press[0]: toggle mode requires 'off_action' (an action object)")
            elif first.get("off_action"):
                err = _validate_action(first["off_action"], "press[0].off_action")
                if err:
                    errors.append(err)
        elif mode == "tap_hold":
            if "hold_action" not in first or not isinstance(first.get("hold_action"), dict):
                errors.append("press[0]: tap_hold mode requires 'hold_action' (an action object)")
            elif first.get("hold_action"):
                err = _validate_action(first["hold_action"], "press[0].hold_action")
                if err:
                    errors.append(err)

    # Non-action slots: type check + content validation
    for slot in _NON_ACTION_SLOTS:
        val = bindings.get(slot)
        if val is None:
            continue
        if isinstance(val, list):
            errors.append(f"'{slot}' must be an object, not an array")
            continue
        if not isinstance(val, dict):
            errors.append(f"'{slot}' must be an object, got {type(val).__name__}")
            continue

    # feedback: must have key; validate structure
    fb = bindings.get("feedback")
    if isinstance(fb, dict):
        if not fb.get("key"):
            errors.append("feedback: missing 'key' (state key to watch)")
        if "states" in fb:
            # Multi-state feedback
            if not isinstance(fb["states"], dict):
                errors.append("feedback.states must be an object mapping state values to style props")
            else:
                # Anti-pattern #8: properties nested in style object
                for state_name, state_val in fb["states"].items():
                    if isinstance(state_val, dict) and "style" in state_val:
                        errors.append(
                            f"feedback.states.{state_name}: properties (label, bg_color, etc.) "
                            f"must be flat, not nested inside 'style'"
                        )
                        break
        elif "condition" in fb:
            # Binary feedback
            if not isinstance(fb["condition"], dict):
                errors.append("feedback.condition must be an object (e.g. {\"equals\": \"on\"})")

    # text: must have source; validate per source type
    txt = bindings.get("text")
    if isinstance(txt, dict):
        source = txt.get("source", "")
        if not source:
            errors.append("text: missing 'source' (state, macro_progress, or conditional)")
        elif source not in _VALID_TEXT_SOURCES:
            errors.append(
                f"text: source '{source}' is not valid. "
                f"Use: state, macro_progress, conditional"
            )
        elif source == "state" and not txt.get("key"):
            errors.append("text: state source requires 'key'")
        elif source == "macro_progress" and not txt.get("macro"):
            errors.append("text: macro_progress source requires 'macro'")
        elif source == "conditional":
            for field in ("condition", "text_true", "text_false"):
                if field not in txt:
                    errors.append(f"text: conditional source requires '{field}'")

    # color: must have key and map
    clr = bindings.get("color")
    if isinstance(clr, dict):
        if not clr.get("key"):
            errors.append("color: missing 'key' (state key to watch)")
        if "map" not in clr or not isinstance(clr.get("map"), dict):
            errors.append("color: missing 'map' (object mapping values to colors)")

    # variable: must have key
    var = bindings.get("variable")
    if isinstance(var, dict) and not var.get("key"):
        errors.append("variable: missing 'key' (state key for two-way binding)")

    # value: must have key
    val_binding = bindings.get("value")
    if isinstance(val_binding, dict) and not val_binding.get("key"):
        errors.append("value: missing 'key' (state key to display)")

    # visible_when: must have key; validate operator
    vw = bindings.get("visible_when")
    if isinstance(vw, dict):
        if not vw.get("key"):
            errors.append("visible_when: missing 'key' (state key to evaluate)")
        op = vw.get("operator")
        if op and op not in _VALID_VISIBLE_WHEN_OPS:
            errors.append(
                f"visible_when: operator '{op}' is not valid. "
                f"Use: eq, ne, gt, lt, gte, lte, truthy, falsy"
            )

    # selected: must have key
    sel = bindings.get("selected")
    if isinstance(sel, dict) and not sel.get("key"):
        errors.append("selected: missing 'key' (state key for selection tracking)")

    # items: must have key
    itm = bindings.get("items")
    if isinstance(itm, dict) and not itm.get("key"):
        errors.append("items: missing 'key' (state key providing item data)")

    # Soft reference checks — collect warnings but don't block
    if project and not errors:
        macro_ids = {m.id for m in project.macros} if hasattr(project, "macros") else set()
        device_ids = {d.id for d in project.devices} if hasattr(project, "devices") else set()

        for slot in _ACTION_SLOTS:
            val = bindings.get(slot)
            if not isinstance(val, list):
                continue
            for i, item in enumerate(val):
                if not isinstance(item, dict):
                    continue
                if item.get("action") == "macro" and item.get("macro"):
                    if item["macro"] not in macro_ids:
                        warnings.append(f"{slot}[{i}]: macro '{item['macro']}' not found in project")
                if item.get("action") == "device.command" and item.get("device"):
                    if item["device"] not in device_ids:
                        warnings.append(f"{slot}[{i}]: device '{item['device']}' not found in project")

    if errors:
        return "Binding validation failed: " + "; ".join(errors)

    if warnings:
        log.warning("Binding reference warnings: %s", "; ".join(warnings))

    return None


# --- Macro step and trigger validation ---

_VALID_STEP_ACTIONS = frozenset((
    "device.command", "group.command", "delay", "state.set",
    "macro", "event.emit", "conditional",
))
_STEP_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "device.command": ("device", "command"),
    "group.command": ("group", "command"),
    "delay": ("seconds",),
    "state.set": ("key",),
    "macro": ("macro",),
    "event.emit": ("event",),
    "conditional": ("condition",),
}
_VALID_TRIGGER_TYPES = frozenset(("schedule", "state_change", "event", "startup"))
_VALID_CONDITION_OPS = frozenset((
    "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
))
_VALID_STATE_TRIGGER_OPS = frozenset((
    "any", "eq", "ne", "gt", "lt", "gte", "lte", "truthy", "falsy",
))
_VALID_OVERLAP_MODES = frozenset(("skip", "queue", "allow"))


def _validate_macro_step(step: dict, path: str) -> list[str]:
    """Validate a single macro step. Returns list of error strings."""
    errors: list[str] = []
    action = step.get("action", "")
    if not action:
        errors.append(f"{path}: missing 'action' field")
        return errors
    if action not in _VALID_STEP_ACTIONS:
        errors.append(
            f"{path}: step action '{action}' is not valid. "
            f"Use: device.command, group.command, delay, state.set, macro, event.emit, conditional"
        )
        return errors

    required = _STEP_REQUIRED_FIELDS.get(action, ())
    for field in required:
        val = step.get(field)
        if val is None or val == "":
            errors.append(f"{path}: {action} step requires '{field}'")

    if action == "delay":
        seconds = step.get("seconds")
        if seconds is not None and (not isinstance(seconds, (int, float)) or seconds < 0):
            errors.append(f"{path}: delay 'seconds' must be a non-negative number")

    if action == "conditional":
        cond = step.get("condition")
        if isinstance(cond, dict):
            if not cond.get("key"):
                errors.append(f"{path}: conditional condition requires 'key'")
            op = cond.get("operator", "eq")
            if op not in _VALID_CONDITION_OPS:
                errors.append(f"{path}: condition operator '{op}' is not valid")
        # Recursively validate then/else steps
        for branch in ("then_steps", "else_steps"):
            branch_steps = step.get(branch)
            if isinstance(branch_steps, list):
                for i, sub in enumerate(branch_steps):
                    if isinstance(sub, dict):
                        errors.extend(_validate_macro_step(sub, f"{path}.{branch}[{i}]"))

    # Validate skip_if guard
    skip_if = step.get("skip_if")
    if isinstance(skip_if, dict):
        if not skip_if.get("key"):
            errors.append(f"{path}: skip_if requires 'key'")
        op = skip_if.get("operator", "eq")
        if op not in _VALID_CONDITION_OPS:
            errors.append(f"{path}: skip_if operator '{op}' is not valid")

    return errors


def _validate_trigger(trigger: dict, path: str) -> list[str]:
    """Validate a single trigger definition. Returns list of error strings."""
    errors: list[str] = []
    ttype = trigger.get("type", "")
    if not ttype:
        errors.append(f"{path}: missing 'type' field")
        return errors
    if ttype not in _VALID_TRIGGER_TYPES:
        errors.append(
            f"{path}: trigger type '{ttype}' is not valid. "
            f"Use: schedule, state_change, event, startup"
        )
        return errors

    if ttype == "schedule":
        cron = trigger.get("cron")
        if not cron or not isinstance(cron, str):
            errors.append(f"{path}: schedule trigger requires 'cron' (string)")
        elif cron:
            parts = cron.strip().split()
            if len(parts) not in (5, 6):
                errors.append(f"{path}: cron expression must have 5 or 6 fields, got {len(parts)}")
    elif ttype == "state_change":
        if not trigger.get("state_key"):
            errors.append(f"{path}: state_change trigger requires 'state_key'")
        op = trigger.get("state_operator")
        if op and op not in _VALID_STATE_TRIGGER_OPS:
            errors.append(
                f"{path}: state_operator '{op}' is not valid. "
                f"Use: any, eq, ne, gt, lt, gte, lte, truthy, falsy"
            )
    elif ttype == "event":
        if not trigger.get("event_pattern"):
            errors.append(f"{path}: event trigger requires 'event_pattern'")

    overlap = trigger.get("overlap")
    if overlap and overlap not in _VALID_OVERLAP_MODES:
        errors.append(f"{path}: overlap '{overlap}' is not valid. Use: skip, queue, allow")

    # Validate guard conditions
    conditions = trigger.get("conditions")
    if isinstance(conditions, list):
        for i, c in enumerate(conditions):
            if isinstance(c, dict):
                if not c.get("key"):
                    errors.append(f"{path}.conditions[{i}]: missing 'key'")
                op = c.get("operator", "eq")
                if op not in _VALID_CONDITION_OPS:
                    errors.append(f"{path}.conditions[{i}]: operator '{op}' is not valid")

    return errors


def _validate_macro(steps: list, triggers: list, project: Any = None) -> str | None:
    """Validate macro steps and triggers. Returns error string or None."""
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(steps, list):
        for i, step in enumerate(steps):
            if isinstance(step, dict):
                errors.extend(_validate_macro_step(step, f"steps[{i}]"))
            else:
                errors.append(f"steps[{i}]: expected an object, got {type(step).__name__}")

    if isinstance(triggers, list):
        for i, trigger in enumerate(triggers):
            if isinstance(trigger, dict):
                errors.extend(_validate_trigger(trigger, f"triggers[{i}]"))
            else:
                errors.append(f"triggers[{i}]: expected an object, got {type(trigger).__name__}")

    # Soft reference checks
    if project and not errors:
        macro_ids = {m.id for m in project.macros} if hasattr(project, "macros") else set()
        device_ids = {d.id for d in project.devices} if hasattr(project, "devices") else set()
        group_ids = {g.id for g in project.device_groups} if hasattr(project, "device_groups") else set()

        def _check_step_refs(step: dict, path: str) -> None:
            action = step.get("action", "")
            if action == "device.command" and step.get("device"):
                if step["device"] not in device_ids:
                    warnings.append(f"{path}: device '{step['device']}' not found in project")
            if action == "group.command" and step.get("group"):
                if step["group"] not in group_ids:
                    warnings.append(f"{path}: device group '{step['group']}' not found in project")
            if action == "macro" and step.get("macro"):
                if step["macro"] not in macro_ids:
                    warnings.append(f"{path}: macro '{step['macro']}' not found in project")
            for branch in ("then_steps", "else_steps"):
                for i, sub in enumerate(step.get(branch) or []):
                    if isinstance(sub, dict):
                        _check_step_refs(sub, f"{path}.{branch}[{i}]")

        if isinstance(steps, list):
            for i, step in enumerate(steps):
                if isinstance(step, dict):
                    _check_step_refs(step, f"steps[{i}]")

    if errors:
        return "Macro validation failed: " + "; ".join(errors)
    if warnings:
        log.warning("Macro reference warnings: %s", "; ".join(warnings))
    return None


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


# --- Script syntax validation ---

def _validate_script_syntax(source: str, filename: str = "<script>") -> str | None:
    """Validate Python script syntax. Returns error string or None."""
    try:
        compile(source, filename, "exec")
    except SyntaxError as e:
        line_info = f" (line {e.lineno})" if e.lineno else ""
        return f"Python syntax error{line_info}: {e.msg}"
    return None


# --- Plugin config schema validation ---

_SCHEMA_TYPE_VALIDATORS: dict[str, type | tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def _validate_plugin_config(config: dict, schema: dict) -> str | None:
    """Validate plugin config against its CONFIG_SCHEMA.

    Returns error string or None. Only checks required fields and basic types.
    """
    errors: list[str] = []
    for key, field_def in schema.items():
        if not isinstance(field_def, dict):
            continue

        # Group fields — recurse
        if field_def.get("type") == "group":
            sub_schema = field_def.get("fields", {})
            sub_config = config.get(key, {})
            if isinstance(sub_schema, dict) and isinstance(sub_config, dict):
                err = _validate_plugin_config(sub_config, sub_schema)
                if err:
                    errors.append(err)
            continue

        # Required field check
        if field_def.get("required") and key not in config:
            if "default" not in field_def:
                errors.append(f"Missing required config field '{key}'")
                continue

        # Type check for present values
        value = config.get(key)
        if value is not None:
            expected_type = field_def.get("type", "")
            valid_types = _SCHEMA_TYPE_VALIDATORS.get(expected_type)
            if valid_types and not isinstance(value, valid_types):
                errors.append(
                    f"Config field '{key}' should be {expected_type}, "
                    f"got {type(value).__name__}"
                )

    if errors:
        return "Plugin config validation failed: " + "; ".join(errors)
    return None


# --- State key validation ---

_VALID_STATE_PREFIXES = ("device.", "var.", "ui.", "system.", "plugin.")


def _validate_state_key(key: str) -> str | None:
    """Validate state key format. Returns error string or None."""
    if not key:
        return "State key cannot be empty"
    if not any(key.startswith(p) for p in _VALID_STATE_PREFIXES):
        return (
            f"State key '{key}' has an invalid prefix. "
            f"Must start with: device., var., ui., system., plugin."
        )
    return None


from server.cloud.tools.device_tools import DeviceToolsMixin
from server.cloud.tools.macro_tools import MacroToolsMixin
from server.cloud.tools.plugin_tools import PluginToolsMixin
from server.cloud.tools.project_tools import ProjectToolsMixin
from server.cloud.tools.system_tools import SystemToolsMixin
from server.cloud.tools.ui_tools import UIToolsMixin


class AIToolHandler(
    ProjectToolsMixin,
    DeviceToolsMixin,
    UIToolsMixin,
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
        "list_drivers", "search_community_drivers", "get_installed_drivers",
        "get_driver_definition", "get_script_source", "get_logs",
        "list_triggers", "get_macro", "get_ui_page",
        "list_plugins", "browse_community_plugins", "get_plugin_config",
        "list_themes", "get_theme", "list_assets",
        "get_isc_status", "list_isc_peers",
        "get_device_settings", "check_references",
        "get_discovery_results", "wait",
    }

    # Idle timeout before resetting the AI backup flag (seconds)
    _AI_BACKUP_IDLE_TIMEOUT = 300  # 5 minutes

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        reload_fn=None,
        project_path=None,
    ):
        self._agent = agent
        self._devices = devices
        self._events = events
        self._reload_fn = reload_fn
        self._project_path = project_path

        # Backup tracking: create one backup before the first write in a conversation
        self._ai_backup_created: bool = False
        self._ai_last_write_time: float = 0

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
            "get_installed_drivers": self._get_installed_drivers,
            "get_driver_definition": self._get_driver_definition,
            "get_script_source": self._get_script_source,
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
            # UI simulation
            "simulate_ui_action": self._simulate_ui_action,
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

        # Run in background so the receive loop stays responsive to pings/acks
        asyncio.create_task(self._execute_tool(request_id, tool_name, handler, tool_input))

    async def _execute_tool(
        self, request_id: str, tool_name: str, handler: Any, tool_input: dict
    ) -> None:
        """Execute a tool handler and send the result back to the cloud."""
        try:
            # Create a backup before the first write operation in this AI conversation
            if tool_name not in self._READ_ONLY_TOOLS:
                # Reset backup flag if idle for too long (new conversation)
                if (self._ai_last_write_time
                        and time.monotonic() - self._ai_last_write_time > self._AI_BACKUP_IDLE_TIMEOUT):
                    self._ai_backup_created = False

                if not self._ai_backup_created and self._project_path:
                    try:
                        from server.core.backup_manager import create_backup
                        await asyncio.to_thread(create_backup, Path(self._project_path).parent, "Before AI changes")
                    except Exception:
                        log.debug("Could not create pre-AI backup", exc_info=True)
                    self._ai_backup_created = True

                self._ai_last_write_time = time.monotonic()

            result = await handler(tool_input)
            await self._send_result(request_id, True, result=result)
        except Exception as e:
            log.exception(f"AI tool handler: error executing {tool_name}")
            await self._send_result(request_id, False, error=str(e))

    # Tool handler methods are defined in the domain-specific mixins:
    # - ProjectToolsMixin: project state, metadata
    # - DeviceToolsMixin: devices, drivers, scripts
    # - UIToolsMixin: UI pages, elements
    # - PluginToolsMixin: plugin management
    # - MacroToolsMixin: macros, triggers, variables
    # - SystemToolsMixin: logs, discovery, themes, assets, ISC

    # ===== HELPERS =====

    def _get_engine(self):
        try:
            from server.api.rest import _engine
            return _engine
        except ImportError:
            return None

    def _get_driver_repo_dir(self) -> Path:
        from server.system_config import DRIVER_REPO_DIR
        return DRIVER_REPO_DIR

    def _get_driver_dirs(self) -> list[Path]:
        from server.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR
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
        await self._agent.send_message(AI_TOOL_RESULT, {
            "request_id": request_id,
            "success": success,
            "result": result,
            "error": error,
        })
