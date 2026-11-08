"""Mixin for AI tool handlers that manage plugin lifecycle and configuration.

Every answer here is kept in the cloud chat history, so a plugin's own error
text reaches it only when the plugin marked it for the reader by raising
``PluginRefusal``; anything else is the platform's sentence pointing at the
Programmer's Log (``PluginLoader.error_for_cloud``). The Programmer's plugin
card still shows the plugin's text as it stands.

For the same reason a plugin's passwords and keys never go into an answer: a
config comes back with them as ``***`` (``mask_secrets_by_name``, by field
name, at any depth), and a config sent back with ``***`` still in a field keeps
the saved value there. Nor is one taken from the conversation:
``update_plugin_config`` refuses a login typed into the config (a field the
plugin declares as one, a field named like one, a password in an address), as
the device tools do, and sends the person to the plugin's settings. ``***`` or
the saved-login marker sent back keeps the saved one.
"""

import re
from typing import Any

from openavc.cloud.tools import ToolEditError, apply_tool_edit
from openavc.utils.log_redaction import (
    keep_masked_secrets,
    mask_secrets_by_name,
    typed_secrets,
)

# Said with a config that had something masked, so the next write sends it back.
MASKED_NOTE = (
    "Passwords and keys show as ***. To keep a saved one, send *** back "
    "unchanged; to change it, send the new value."
)


def _masked(config: Any) -> dict[str, Any]:
    """The config as an answer may carry it, and the note when it hid something."""
    shown = mask_secrets_by_name(config)
    return {"config": shown, **({"masked": MASKED_NOTE} if shown != config else {})}


def _typed_logins(plugin_id: str, incoming: Any, stored: Any) -> list[str]:
    """The settings in ``incoming`` that carry a login ``stored`` does not:
    each one the plugin declares (``core/plugin_secrets``) and each one named
    like a credential or holding an address's password, as the person reads
    its name."""
    from openavc.core import plugin_secrets
    from openavc.core.device_logins import SAVED_LOGIN
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
    from openavc.drivers.param_labels import param_label

    schema = getattr(_PLUGIN_CLASS_REGISTRY.get(plugin_id), "CONFIG_SCHEMA", None) or {}

    def label(path: str) -> str:
        node: Any = {"fields": schema}
        for part in path.split("."):
            fields = node.get("fields") if isinstance(node, dict) else None
            node = fields.get(part) if isinstance(fields, dict) else None
            if node is None:
                return path
        return param_label(path.rsplit(".", 1)[-1], node)

    names: list[str] = []
    declared = plugin_secrets.declared_for(plugin_id)
    for item, field, _generated in plugin_secrets.places(incoming, declared):
        new = plugin_secrets.get_field(plugin_secrets.holder_of(incoming, item), field)
        old = plugin_secrets.get_field(plugin_secrets.holder_of(stored, item), field)
        if (
            isinstance(new, (str, int, float)) and not isinstance(new, bool)
            and new not in ("", "***", SAVED_LOGIN) and new != old
        ):
            names.append(label(field) if not item else plugin_secrets.display_path(item, field))
    id_keys = {decl.key: decl.id_key for decl in declared.lists}

    def place(path: str) -> str:
        """``streams[1].password`` as ``streams[lobby].password`` when the entry
        has an id, the name the declared half uses for the same place."""
        match = re.match(r"^(?P<list>[^.\[]+)\[(?P<index>\d+)\](?P<rest>.*)$", path)
        if match is None:
            return label(path)
        entries = incoming.get(match["list"]) if isinstance(incoming, dict) else None
        index = int(match["index"])
        entry = entries[index] if isinstance(entries, list) and index < len(entries) else None
        if isinstance(entry, dict):
            keys = [id_keys.get(match["list"]), "id"] + [k for k in entry if str(k).endswith("_id")]
            for key in keys:
                value = entry.get(key) if key else None
                if isinstance(value, (str, int)) and not isinstance(value, bool) and value != "":
                    return f"{match['list']}[{value}]{match['rest']}"
        return path

    for path in typed_secrets(incoming, stored):
        name = place(path)
        if name not in names:
            names.append(name)
    return names


def _login_refusal(names: list[str]) -> str:
    """The answer to a login sent to a plugin tool."""
    one = len(names) == 1
    what = names[0] if one else f"{', '.join(names[:-1])} and {names[-1]}"
    return (
        f"{what} {'is a login' if one else 'are logins'}. Enter "
        f"{'it' if one else 'them'} in this plugin's settings in the Programmer. "
        "Nothing was changed."
    )


def _as_sentence(text: str) -> str:
    """``text`` ending in a full stop, so a sentence can follow it."""
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


class PluginToolsMixin:
    """Plugin listing, install/uninstall, enable/disable, and config tools."""

    async def _list_plugins(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        return engine.plugin_loader.list_plugins(for_cloud=True)

    async def _browse_community_plugins(self, input: dict) -> Any:
        from openavc.core.plugin_installer import get_community_plugins

        plugins, error = await get_community_plugins()
        return {"plugins": plugins, "error": error}

    async def _install_plugin(self, input: dict) -> Any:
        from openavc.core.plugin_installer import (
            COMMUNITY_REPO_URL,
            get_community_plugins,
            install_plugin,
        )

        plugin_id = input.get("plugin_id", "")
        file_url = input.get("file_url", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        # Auto-resolve URL from community index if not provided
        if not file_url:
            plugins, error = await get_community_plugins()
            if error:
                return {"error": f"Could not fetch community index: {error}"}
            match = next((p for p in plugins if p.get("id") == plugin_id), None)
            if not match or not match.get("file"):
                return {"error": f"Plugin '{plugin_id}' not found in community index"}
            file_url = f"{COMMUNITY_REPO_URL}/{match['file']}"

        try:
            result = await install_plugin(plugin_id, file_url)
        except ValueError as e:
            return {"error": str(e)}
        if result.get("status") == "load_failed":
            # The error is what loading the plugin's code raised: its own
            # text, or Python's. The Installed tab shows it; the Log has it.
            return {
                **result,
                "error": (
                    f"Plugin '{plugin_id}' was installed but couldn't load. "
                    "The Programmer's Log has the details."
                ),
            }
        return result

    async def _uninstall_plugin(self, input: dict) -> Any:
        from openavc.core.plugin_installer import uninstall_plugin

        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        # Stop plugin if running
        if engine.plugin_loader.is_running(plugin_id):
            await engine.plugin_loader.stop_plugin(plugin_id)

        project_plugins = engine.project.plugins if engine.project else None
        try:
            result = await uninstall_plugin(plugin_id, project_plugins)
        except ValueError as e:
            return {"error": str(e)}

        # Remove from project file. apply_project bumps the revision (a stale
        # editor PUT would otherwise silently restore the entry) and its
        # plugin reconcile clears the loader tracking for ids the project no
        # longer references (mirrors the REST uninstall endpoint).
        if engine.project and plugin_id in engine.project.plugins:
            def mutate(project):
                project.plugins.pop(plugin_id, None)
                project.plugin_dependencies = [
                    d for d in project.plugin_dependencies
                    if d.plugin_id != plugin_id
                ]

            await apply_tool_edit(engine, mutate)

        # Clear tracking directly too: the files are gone even when the
        # plugin was never referenced by the project (no entry to diff).
        engine.plugin_loader.remove_plugin_tracking(plugin_id)

        # Clear missing plugin state if tracked
        engine.plugin_loader.clear_missing(plugin_id)

        return result

    async def _enable_plugin(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
        from openavc.core.project_loader import PluginConfig, build_default_plugin_config

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            return {"error": f"Plugin '{plugin_id}' not installed"}

        entry = engine.project.plugins.get(plugin_id)
        if entry is None:
            schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
            config = build_default_plugin_config(schema)
        else:
            config = entry.config

        # Start first; only persist enabled=True if the start succeeded.
        # start_plugins() retries every enabled entry at each startup, so
        # persisting before the start would make a broken plugin retry on
        # every boot (mirrors the REST enable endpoint's rollback). The
        # seam's plugin sync then sees runtime == project and does nothing.
        success = await engine.plugin_loader.start_plugin(plugin_id, config)

        def mutate(project):
            if plugin_id not in project.plugins:
                project.plugins[plugin_id] = PluginConfig(
                    enabled=success,
                    config=config,
                )
            else:
                project.plugins[plugin_id].enabled = success

        await apply_tool_edit(engine, mutate)

        if not success:
            reason = engine.plugin_loader.error_for_cloud(plugin_id)
            if reason:
                said = f"Plugin '{plugin_id}' failed to start: {_as_sentence(reason)}"
            else:
                said = (
                    f"Plugin '{plugin_id}' couldn't start. "
                    "The Programmer's Log has the details."
                )
            return {
                "status": "error",
                "error": f"{said} The enable was rolled back; its config is preserved.",
                "plugin_id": plugin_id,
                **_masked(config),
            }

        return {
            "status": "enabled",
            "plugin_id": plugin_id,
            **_masked(config),
        }

    async def _disable_plugin(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        def mutate(project):
            if plugin_id not in project.plugins:
                raise ToolEditError({"error": f"Plugin '{plugin_id}' not in project"})
            project.plugins[plugin_id].enabled = False

        # The plugins-section reconcile stops the running plugin.
        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "disabled", "plugin_id": plugin_id}

    async def _get_plugin_config(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        entry = engine.project.plugins.get(plugin_id)
        if entry is None:
            return {"error": f"Plugin '{plugin_id}' not in project"}

        # Also get schema/setup fields if the plugin class is available
        from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
        from openavc.core.project_loader import get_plugin_setup_fields

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        schema = {}
        setup_fields = []
        result: dict[str, Any] = {
            "plugin_id": plugin_id,
            **_masked(entry.config),
        }

        if plugin_class:
            schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
            setup_fields = get_plugin_setup_fields(schema)

            # If plugin has a surface layout, include it and the standard
            # surface button config format so the AI knows how to write it.
            surface_layout = getattr(plugin_class, "SURFACE_LAYOUT", None)
            if surface_layout:
                result["surface_layout"] = surface_layout
                result["buttons_format"] = SURFACE_BUTTONS_FORMAT

            # Plugin-specific AI guidance (optional, declared by plugin author)
            ai_guide = getattr(plugin_class, "AI_GUIDE", None)
            if ai_guide:
                result["ai_guide"] = ai_guide

        result["schema"] = schema
        # Keyed by the setting's name.
        result["required_fields"] = list(setup_fields)
        return result

    async def _update_plugin_config(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        # 'config' must be explicit — defaulting an omitted arg to {} would
        # silently wipe the plugin's configuration and restart it broken.
        # (An explicit {} is a legitimate complete config for a plugin whose
        # schema has no required fields; only the missing key is an error.)
        if "config" not in input:
            return {
                "error": "config is required — pass the complete configuration "
                         "object (call get_plugin_config first)"
            }
        new_config = input["config"]
        if not isinstance(new_config, dict):
            return {"error": "config must be an object"}

        if plugin_id not in engine.project.plugins:
            return {"error": f"Plugin '{plugin_id}' not in project"}

        # A login typed into the conversation is not taken: the person enters
        # it in the plugin's settings, where it never passes through the chat.
        typed = _typed_logins(plugin_id, new_config, engine.project.plugins[plugin_id].config)
        if typed:
            return {"error": _login_refusal(typed), "login_settings": typed}

        # The config the AI read had its passwords as ***; one sent back
        # unchanged means keep the saved value, never "the password is ***".
        new_config, unresolved = keep_masked_secrets(
            new_config, engine.project.plugins[plugin_id].config,
        )
        if unresolved:
            return {
                "error": (
                    f"Plugin '{plugin_id}': {', '.join(unresolved)} came back as *** "
                    "but there is no saved value there to keep. Send the real "
                    "value, or ask the user to enter it in the Programmer."
                )
            }

        # Validate config against plugin's CONFIG_SCHEMA if available.
        # Wrong types are rejected; required fields that aren't set yet
        # only warn, mirroring the REST path — the config may legitimately
        # be built up across several calls.
        from openavc.core.plugin_config import (
            config_problems_added,
            missing_required_for_plugin,
            validate_config_for_plugin,
        )

        err = validate_config_for_plugin(plugin_id, new_config)
        if err:
            return {"error": f"Plugin '{plugin_id}': {err}"}
        # The plugin's own judgement, as on the REST path: refuse what this
        # call adds, keep and report what the stored config already carried.
        added, kept = config_problems_added(
            plugin_id, new_config, engine.project.plugins[plugin_id].config,
        )
        if added:
            return {"error": f"Plugin '{plugin_id}': {' '.join(added)}"}
        missing = missing_required_for_plugin(plugin_id, new_config)

        # Hot-apply when the plugin supports it, else restart — before the
        # seam apply, so the reconcile sees the running config already
        # current and doesn't apply it a second time.
        outcome = await engine.plugin_loader.restart_or_apply(plugin_id, new_config)

        def mutate(project):
            if plugin_id not in project.plugins:
                raise ToolEditError({"error": f"Plugin '{plugin_id}' not in project"})
            project.plugins[plugin_id].config = new_config

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        result = {"status": "updated", "plugin_id": plugin_id, "applied": outcome}
        if kept:
            result["config_problems"] = kept
        if missing:
            result["missing_required"] = sorted(missing)
            result["warning"] = (
                f"Config saved, but required field(s) {', '.join(sorted(missing))} "
                f"are not set yet. Plugin '{plugin_id}' can't run until they are."
            )
        elif outcome == "start_failed":
            reason = engine.plugin_loader.error_for_cloud(plugin_id)
            if reason:
                result["warning"] = (
                    f"Config saved, but plugin '{plugin_id}' failed to restart with "
                    f"it and is stopped: {_as_sentence(reason)}"
                )
            else:
                result["warning"] = (
                    f"Config saved, but plugin '{plugin_id}' failed to restart with "
                    f"it and is stopped. Check the config values and plugin logs."
                )
        return result


# Standard config format for control surface plugins (Stream Deck, X-Keys, etc.),
# sent by get_plugin_config with any plugin that declares a SURFACE_LAYOUT. The
# Surface Configurator writes this format; the platform documents it once so
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
