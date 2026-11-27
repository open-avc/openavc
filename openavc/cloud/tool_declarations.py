"""THE AI tool declarations: every tool the cloud may call on this instance,
how it runs here, and what it takes.

Each row of :data:`TOOLS` holds the facts of one tool that its handler already
holds in code:

- **how it runs here** (``READ``, ``UNLOCKED`` or ``LOCKED``, below);
- **its inputs**: each input's name, JSON type, default, allowed values and
  bounds, in a closed subset of JSON Schema (:data:`KEYWORDS`), and which
  inputs it cannot run without;
- **what its success changes** that the project summary reads
  (``changes``): the project itself, the devices' records, or the plugin list;
- **how long it may take to answer** (``answer_within_seconds``), for a tool
  that waits, with every wait at its maximum.

No row holds a description: the words that tell the model what a tool does
are the cloud's. ``cloud/ai_tool_handler.py`` runs each tool as its row says,
``cloud/tools/input_shapes.py`` checks each call against its row before the
tool runs, and the hello sends :func:`declarations` as ``ai_tools``.
``python -m openavc.cloud.contract_gen`` writes the same into
``ai_tool_declarations.json``, which a test holds to this module byte for
byte. ``tests/test_ai_tool_declarations.py`` holds every row to its handler's
source both ways: each input a handler reads is declared, each declared input
is read, and the defaults, the waits and what changes match the code.

Pure data and stdlib, and the page rules' words (``ui/page_rules.py``, which
imports nothing), so the hello and the generator read it without loading a
tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openavc.ui.page_rules import PAGE_TYPES

#: The declarations' own shape; bumps only when that shape changes.
FORMAT = 1

#: The JSON Schema keywords an input may use, and no others.
KEYWORDS = frozenset({
    "type", "enum", "default", "minimum", "maximum",
    "items", "properties", "required", "additionalProperties",
})

# How a tool runs. LOCKED is the default: the tool runs under the project lock,
# so two tools never interleave on engine.project, and the conversation's one
# pre-AI backup is made under it before the first LOCKED tool. Tools that reach
# a device (send to it, test its connection, start a scan) or write state are
# LOCKED too, and so are review_ui and review_custom_ui, which only read: one
# needless backup was preferred to two reviews that run differently. READ is a
# tool that only reads; it runs beside any other, without the lock. UNLOCKED is
# execute_macro alone: it waits on a macro run for tens of seconds and never
# changes engine.project, so holding the lock would block project edits. READ
# and UNLOCKED run the same way; the handler's _READ_ONLY_TOOLS keeps them
# apart. A name missing from TOOLS is answered "Unknown tool".
READ, UNLOCKED, LOCKED = "read", "unlocked", "locked"

# What a tool's success changes that get_project_summary reads: the project
# (through the project's edit door), the device manager's record of a device
# (its status and address), or the plugin list.
PROJECT, DEVICES, PLUGINS = "project", "devices", "plugins"


# --- Input shapes ---

STRING: dict[str, Any] = {"type": "string"}
NUMBER: dict[str, Any] = {"type": "number"}
INTEGER: dict[str, Any] = {"type": "integer"}
BOOLEAN: dict[str, Any] = {"type": "boolean"}
OBJECT: dict[str, Any] = {"type": "object"}
#: Any JSON value at all.
ANYTHING: dict[str, Any] = {}


def nullable(shape: dict[str, Any]) -> dict[str, Any]:
    """``shape``, or null."""
    return {**shape, "type": [shape["type"], "null"]}


def array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def keyed(values: dict[str, Any]) -> dict[str, Any]:
    """An object whose keys are free and whose every value is ``values``."""
    return {"type": "object", "additionalProperties": values}


def one_of(*words: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(words)}


def default(shape: dict[str, Any], value: Any) -> dict[str, Any]:
    return {**shape, "default": value}


STRINGS = array(STRING)
OBJECTS = array(OBJECT)

#: A control's box: percentages of its parent's box.
BOX: dict[str, Any] = {
    "type": "object",
    "properties": {"x": NUMBER, "y": NUMBER, "w": NUMBER, "h": NUMBER},
}

#: One arrangement of a page: its boxes keyed by element id.
LAYOUT: dict[str, Any] = {"type": "object", "properties": {"placements": keyed(BOX)}}

#: The fields a page element's model declares, each a plain field on the
#: element (``core/project_loader.UIElement``; a test holds the two equal).
ELEMENT_FIELDS: dict[str, dict[str, Any]] = {
    "id": STRING, "type": STRING,
    "label": nullable(STRING), "text": nullable(STRING),
    "min": nullable(NUMBER), "max": nullable(NUMBER), "step": nullable(NUMBER),
    "output_min": nullable(NUMBER), "output_max": nullable(NUMBER),
    "scale_to_full": nullable(BOOLEAN), "response": nullable(STRING),
    "response_db_range": nullable(NUMBER), "send_on_release": nullable(BOOLEAN),
    "send_throttle_ms": nullable(INTEGER), "display_decimals": nullable(INTEGER),
    "target_page": nullable(STRING), "highlight_current": nullable(BOOLEAN),
    "options": nullable(OBJECTS), "placeholder": nullable(STRING),
    "src": nullable(STRING), "object_fit": nullable(STRING),
    "preset_number": nullable(INTEGER),
    "icon": nullable(STRING), "icon_position": nullable(STRING),
    "icon_size": nullable(NUMBER), "icon_color": nullable(STRING),
    "display_mode": nullable(STRING), "button_image": nullable(STRING),
    "image_fit": nullable(STRING), "image_blend_mode": nullable(STRING),
    "image_opacity": nullable(NUMBER), "frameless": nullable(BOOLEAN),
    "unit": nullable(STRING), "arc_angle": nullable(NUMBER),
    "zones": nullable(OBJECTS), "orientation": nullable(STRING),
    "clock_mode": nullable(STRING), "format": nullable(STRING),
    "timezone": nullable(STRING), "target_time": nullable(STRING),
    "start_key": nullable(STRING), "duration_minutes": nullable(INTEGER),
    "digits": nullable(INTEGER), "auto_send": nullable(BOOLEAN),
    "auto_send_delay_ms": nullable(INTEGER), "keypad_style": nullable(STRING),
    "show_display": nullable(BOOLEAN), "mask_display": nullable(BOOLEAN),
    "label_position": nullable(STRING), "list_style": nullable(STRING),
    "item_height": nullable(NUMBER), "thumb_size": nullable(NUMBER),
    "items": nullable(OBJECTS), "matrix_config": nullable(OBJECT),
    "matrix_style": nullable(STRING),
    "plugin_type": nullable(STRING), "plugin_id": nullable(STRING),
    "plugin_config": OBJECT,
    "custom_file": nullable(STRING), "custom_config": OBJECT,
    "grant": nullable(OBJECT), "parent": nullable(STRING),
    "aspect_lock": nullable(NUMBER), "css_class": nullable(STRING),
    "tag": nullable(STRING), "locked": BOOLEAN,
    "style": OBJECT, "bindings": OBJECT,
}

#: A master element's fields: a page element's, plus the pages it shows on,
#: whether it shows, and its box keyed by orientation
#: (``core/project_loader.MasterElement``).
MASTER_FIELDS: dict[str, dict[str, Any]] = {
    **ELEMENT_FIELDS,
    "pages": {"type": ["string", "array"], "items": STRING},
    "hidden": BOOLEAN,
    "placements": keyed(BOX),
}


def _without(fields: dict[str, dict[str, Any]], *names: str) -> dict[str, dict[str, Any]]:
    return {name: shape for name, shape in fields.items() if name not in names}


# --- The rows ---

@dataclass(frozen=True)
class Tool:
    """One tool: how it runs here, what it takes, and what it changes."""

    run: str
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The inputs the tool refuses to run without.
    required: tuple[str, ...] = ()
    #: What its success can change that the project summary reads:
    #: PROJECT, DEVICES or PLUGINS; None when nothing.
    changes: str | None = None
    #: The longest it takes to answer once started, every wait at its
    #: maximum; None for a tool that does not wait.
    answer_within_seconds: int | None = None

    @property
    def changes_project(self) -> bool:
        return self.changes is not None


#: Every tool the cloud may call, in the order the hello sends them. Tool
#: ``x`` is the handler's method ``_x``, defined in one of the mixins under
#: ``cloud/tools/``.
TOOLS: dict[str, Tool] = {
    # Reading / Searching
    "get_project_summary": Tool(READ),
    "get_project_state": Tool(READ),
    "get_state_value": Tool(READ, {"key": STRING}, ("key",)),
    "get_state_history": Tool(READ, {"count": default(INTEGER, 50)}),
    "list_devices": Tool(READ),
    "get_device_info": Tool(READ, {"device_id": STRING}, ("device_id",)),
    "list_drivers": Tool(READ),
    "search_community_drivers": Tool(READ, {
        "query": STRING, "category": STRING, "manufacturer": STRING,
        "transport": STRING, "limit": default(INTEGER, 25),
    }),
    "get_community_driver_detail": Tool(READ, {"driver_id": STRING}, ("driver_id",)),
    "find_driver_for_device": Tool(
        READ, {"manufacturer": STRING, "model": STRING}, ("manufacturer", "model"),
    ),
    "get_installed_drivers": Tool(READ),
    "get_driver_definition": Tool(READ, {"driver_id": STRING}, ("driver_id",)),
    "get_script_source": Tool(READ, {"script_id": STRING}, ("script_id",)),
    "get_script_health": Tool(READ, {"script_id": STRING}),
    "get_logs": Tool(READ, {
        "count": default(INTEGER, 100), "category": default(STRING, ""),
        "level": STRING, "search": STRING, "since_seconds": NUMBER,
    }),
    "list_triggers": Tool(READ),
    "get_macro": Tool(READ, {"macro_id": STRING}, ("macro_id",)),
    "get_ui_page": Tool(READ, {"page_id": STRING}, ("page_id",)),
    # Writing / Creating
    "update_project_metadata": Tool(
        LOCKED, {"name": STRING, "description": STRING}, changes=PROJECT,
    ),
    "update_device": Tool(LOCKED, {
        "device_id": STRING, "name": STRING, "driver": STRING,
        "config": OBJECT, "enabled": BOOLEAN,
    }, ("device_id",), changes=PROJECT),
    "delete_device": Tool(LOCKED, {"device_id": STRING}, ("device_id",), changes=PROJECT),
    "add_device": Tool(LOCKED, {
        "id": STRING, "driver": STRING, "name": STRING,
        "config": OBJECT, "enabled": default(BOOLEAN, True),
    }, ("id", "driver"), changes=PROJECT),
    "add_device_group": Tool(
        LOCKED, {"id": STRING, "name": STRING, "device_ids": STRINGS}, ("id",), changes=PROJECT,
    ),
    "update_device_group": Tool(
        LOCKED, {"id": STRING, "name": STRING, "device_ids": STRINGS}, ("id",), changes=PROJECT,
    ),
    "delete_device_group": Tool(LOCKED, {"id": STRING}, ("id",), changes=PROJECT),
    "add_variable": Tool(LOCKED, {
        "id": STRING, "type": default(STRING, "string"), "default": ANYTHING,
        "label": default(STRING, ""), "dashboard": default(BOOLEAN, False),
        "persist": default(BOOLEAN, False),
    }, ("id",), changes=PROJECT),
    "update_variable": Tool(LOCKED, {
        "id": STRING, "type": STRING, "default": ANYTHING,
        "label": STRING, "dashboard": BOOLEAN, "persist": BOOLEAN,
    }, ("id",), changes=PROJECT),
    "delete_variable": Tool(LOCKED, {"id": STRING}, ("id",), changes=PROJECT),
    "add_macro": Tool(LOCKED, {
        "id": STRING, "name": STRING, "steps": OBJECTS, "triggers": OBJECTS,
        "stop_on_error": default(BOOLEAN, False), "cancel_group": STRING,
    }, ("id",), changes=PROJECT),
    "update_macro": Tool(LOCKED, {
        "macro_id": STRING, "name": STRING, "steps": OBJECTS, "triggers": OBJECTS,
        "stop_on_error": BOOLEAN, "cancel_group": nullable(STRING),
    }, ("macro_id",), changes=PROJECT),
    "delete_macro": Tool(LOCKED, {"macro_id": STRING}, ("macro_id",), changes=PROJECT),
    "add_ui_page": Tool(LOCKED, {
        "id": STRING, "name": STRING, "elements": OBJECTS,
        "layouts": array(LAYOUT), "snap": OBJECT, "page_type": one_of(*PAGE_TYPES),
        "overlay": OBJECT, "background": OBJECT,
        "render_mode": one_of("elements", "custom"), "custom_file": STRING,
        "custom_config": OBJECT, "grant": OBJECT,
    }, ("id",), changes=PROJECT),
    "update_ui_page": Tool(LOCKED, {
        "page_id": STRING, "name": STRING, "layouts": array(LAYOUT), "snap": OBJECT,
        "page_type": one_of(*PAGE_TYPES), "overlay": OBJECT, "background": OBJECT,
        "render_mode": one_of("elements", "custom"), "custom_file": nullable(STRING),
        "custom_config": OBJECT, "grant": nullable(OBJECT),
    }, ("page_id",), changes=PROJECT),
    "delete_ui_page": Tool(LOCKED, {"page_id": STRING}, ("page_id",), changes=PROJECT),
    "add_ui_elements": Tool(
        LOCKED, {"page_id": STRING, "elements": OBJECTS},
        ("page_id", "elements"), changes=PROJECT,
    ),
    "update_ui_element": Tool(LOCKED, {
        "element_id": STRING, "layout_id": STRING, "parent": nullable(STRING),
        "placement": BOX, "hidden": BOOLEAN,
        **_without(ELEMENT_FIELDS, "id", "parent"),
    }, ("element_id",), changes=PROJECT),
    "delete_ui_elements": Tool(
        LOCKED, {"element_ids": STRINGS}, ("element_ids",), changes=PROJECT,
    ),
    "add_master_element": Tool(LOCKED, MASTER_FIELDS, ("id", "type"), changes=PROJECT),
    "update_master_element": Tool(LOCKED, {
        "element_id": STRING, **_without(MASTER_FIELDS, "id"),
    }, ("element_id",), changes=PROJECT),
    "delete_master_element": Tool(
        LOCKED, {"element_id": STRING}, ("element_id",), changes=PROJECT,
    ),
    "install_community_driver": Tool(LOCKED, {
        "driver_id": STRING, "file_url": STRING, "min_platform_version": STRING,
    }, ("driver_id", "file_url"), changes=DEVICES),
    "create_driver_definition": Tool(
        LOCKED, {"definition": OBJECT}, ("definition",), changes=DEVICES,
    ),
    "update_driver_definition": Tool(
        LOCKED, {"driver_id": STRING, "definition": OBJECT}, ("driver_id", "definition"),
        changes=DEVICES,
    ),
    "create_script": Tool(LOCKED, {
        "id": STRING, "file": STRING, "source": default(STRING, ""),
        "enabled": default(BOOLEAN, True), "description": default(STRING, ""),
    }, ("id",), changes=PROJECT),
    "update_script_source": Tool(
        LOCKED, {"script_id": STRING, "source": STRING}, ("script_id", "source"),
    ),
    "delete_script": Tool(LOCKED, {"script_id": STRING}, ("script_id",), changes=PROJECT),
    # Plugins
    "list_plugins": Tool(READ),
    "browse_community_plugins": Tool(READ),
    "install_plugin": Tool(
        LOCKED, {"plugin_id": STRING, "file_url": STRING}, ("plugin_id",), changes=PLUGINS,
    ),
    "uninstall_plugin": Tool(LOCKED, {"plugin_id": STRING}, ("plugin_id",), changes=PROJECT),
    "enable_plugin": Tool(LOCKED, {"plugin_id": STRING}, ("plugin_id",), changes=PROJECT),
    "disable_plugin": Tool(LOCKED, {"plugin_id": STRING}, ("plugin_id",), changes=PROJECT),
    "get_plugin_config": Tool(READ, {"plugin_id": STRING}, ("plugin_id",)),
    "update_plugin_config": Tool(
        LOCKED, {"plugin_id": STRING, "config": OBJECT}, ("plugin_id", "config"),
        changes=PROJECT,
    ),
    # Discovery
    "start_discovery_scan": Tool(LOCKED, {
        "subnets": STRINGS, "snmp_enabled": default(BOOLEAN, True),
        "timeout": default(NUMBER, 120),
    }),
    "get_discovery_results": Tool(READ, {
        "wait": default(BOOLEAN, False), "category": STRING,
        "timeout": default(NUMBER, 120),
    }, answer_within_seconds=180),
    # Themes
    "list_themes": Tool(READ),
    "get_theme": Tool(READ, {"theme_id": STRING}, ("theme_id",)),
    "apply_theme": Tool(LOCKED, {"theme_id": STRING}, ("theme_id",), changes=PROJECT),
    # Assets
    "list_assets": Tool(READ),
    "delete_asset": Tool(LOCKED, {"filename": STRING}, ("filename",)),
    # ISC
    "get_isc_status": Tool(READ),
    "list_isc_peers": Tool(READ),
    "send_isc_command": Tool(LOCKED, {
        "instance_id": STRING, "device_id": STRING, "command": STRING,
        "params": default(OBJECT, {}),
    }, ("instance_id", "device_id", "command")),
    # Device settings
    "get_device_settings": Tool(READ, {"device_id": STRING}, ("device_id",)),
    "set_device_setting": Tool(LOCKED, {
        "device_id": STRING, "setting_key": STRING, "value": ANYTHING,
    }, ("device_id", "setting_key", "value")),
    # Custom UI: the project's own ui/ folder, and the stylesheet that
    # restyles the built-in controls. The three writers are LOCKED, so they
    # ride the one-time pre-AI backup that already covers ui/.
    "list_ui_files": Tool(READ),
    "read_ui_file": Tool(READ, {"path": STRING}, ("path",)),
    "write_ui_file": Tool(LOCKED, {"path": STRING, "content": STRING}, ("path", "content")),
    "delete_ui_file": Tool(LOCKED, {"path": STRING}, ("path",)),
    "get_project_stylesheet": Tool(READ),
    "set_project_stylesheet": Tool(LOCKED, {"css": STRING}, ("css",), changes=PROJECT),
    "review_custom_ui": Tool(LOCKED, {"path": STRING}),
    # UI simulation
    "simulate_ui_action": Tool(LOCKED, {
        "action": one_of("press", "release", "hold", "change", "submit", "navigate"),
        "element_id": STRING, "value": ANYTHING, "page_id": STRING,
        "dry_run": BOOLEAN,
    }, ("action",)),
    "review_ui": Tool(LOCKED, {"page_id": STRING}),
    # Impact checking
    "check_references": Tool(READ, {
        "type": one_of("macro", "device", "variable", "script"), "id": STRING,
    }, ("type", "id")),
    # Async / Waiting
    "wait": Tool(READ, {
        "seconds": default(NUMBER, 30), "reason": STRING,
    }, answer_within_seconds=120),
    # Actions
    "send_device_command": Tool(LOCKED, {
        "device_id": STRING, "command": STRING, "params": default(OBJECT, {}),
    }, ("device_id", "command")),
    "test_device_connection": Tool(LOCKED, {"device_id": STRING}, ("device_id",)),
    "test_driver_command": Tool(LOCKED, {
        "host": STRING, "port": default({"type": ["integer", "string"]}, 23),
        "command_string": STRING, "delimiter": default(STRING, "\\r"),
        "timeout": default(NUMBER, 5.0),
    }, ("host", "command_string"), answer_within_seconds=60),
    "execute_macro": Tool(UNLOCKED, {"macro_id": STRING}, ("macro_id",), answer_within_seconds=30),
    "cancel_macro": Tool(LOCKED, {"macro_id": STRING}, ("macro_id",)),
    "set_state_value": Tool(LOCKED, {"key": STRING, "value": ANYTHING}, ("key", "value")),
    "test_trigger": Tool(LOCKED, {"trigger_id": STRING}, ("trigger_id",), answer_within_seconds=30),
}


def input_schema(tool: Tool) -> dict[str, Any]:
    """The tool's inputs as the one object schema the cloud is sent."""
    schema: dict[str, Any] = {"type": "object", "properties": dict(tool.inputs)}
    if tool.required:
        schema["required"] = list(tool.required)
    return schema


def declaration(name: str, tool: Tool) -> dict[str, Any]:
    """One tool as the hello sends it: no run mode, which the cloud never reads."""
    entry: dict[str, Any] = {
        "name": name,
        "changes_project": tool.changes_project,
        "input": input_schema(tool),
    }
    if tool.answer_within_seconds is not None:
        entry["answer_within_seconds"] = tool.answer_within_seconds
    return entry


def declarations() -> dict[str, Any]:
    """Every tool, in :data:`TOOLS` order: the hello's ``ai_tools``."""
    return {
        "format": FORMAT,
        "tools": [declaration(name, tool) for name, tool in TOOLS.items()],
    }
