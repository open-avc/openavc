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
from pathlib import Path
from typing import Any, TYPE_CHECKING

import httpx

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
    "press": {
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
    },
    "feedback": {         // Visual feedback based on state (optional)
      "source": "state",
      "key": "device.my_device.power",   // State key to watch
      "condition": { "equals": true },   // When this matches, button shows active style
      "style_active": { "bg_color": "#2e7d32", "text_color": "#ffffff", "icon": "power" },
      "style_inactive": { "bg_color": "#c62828", "text_color": "#ffffff", "icon": "power-off" },
      "label_active": "ON",              // Override label when active (optional)
      "label_inactive": "OFF"            // Override label when inactive (optional)
    }
  }
}

Color priority: feedback style > per-button defaults > global plugin config defaults.
Only include fields you need. Unassigned buttons can be omitted from the array.
"""


class AIToolHandler:
    """
    Handles AI tool calls from the cloud and dispatches to local subsystems.

    Every tool maps to an existing local REST API operation or engine call,
    ensuring the AI has the same capabilities as the Programmer IDE.
    """

    def __init__(
        self,
        agent: CloudAgent,
        devices: DeviceManager,
        events: EventBus,
        reload_fn=None,
    ):
        self._agent = agent
        self._devices = devices
        self._events = events
        self._reload_fn = reload_fn

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
            result = await handler(tool_input)
            await self._send_result(request_id, True, result=result)
        except Exception as e:
            log.exception(f"AI tool handler: error executing {tool_name}")
            await self._send_result(request_id, False, error=str(e))

    # ===== READING / SEARCHING =====

    async def _get_project_state(self, input: dict) -> Any:
        return self._agent.state.snapshot()

    async def _get_state_value(self, input: dict) -> Any:
        key = input.get("key", "")
        value = self._agent.state.get(key)
        return {"key": key, "value": value}

    async def _get_state_history(self, input: dict) -> Any:
        count = input.get("count", 50)
        return self._agent.state.get_history(count)

    async def _list_devices(self, input: dict) -> Any:
        return self._devices.list_devices()

    async def _get_device_info(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        return self._devices.get_device_info(device_id)

    async def _list_drivers(self, input: dict) -> Any:
        from server.core.device_manager import get_driver_registry
        return get_driver_registry()

    async def _search_community_drivers(self, input: dict) -> Any:
        base_url = "https://raw.githubusercontent.com/open-avc/openavc-drivers/main"
        index_url = f"{base_url}/index.json"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(index_url)
                resp.raise_for_status()
                data = resp.json()
                drivers = data.get("drivers", []) if isinstance(data, dict) else data
                # Add ready-to-use download URL so the AI doesn't have to construct it
                for drv in drivers:
                    if "file" in drv:
                        drv["download_url"] = f"{base_url}/{drv['file']}"
                return {"drivers": drivers, "error": None}
        except (httpx.HTTPError, OSError) as e:
            return {"drivers": [], "error": str(e)}

    async def _get_installed_drivers(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"drivers": []}
        driver_repo = self._get_driver_repo_dir()
        if not driver_repo.exists():
            return {"drivers": []}

        installed = []
        for filepath in sorted(driver_repo.glob("*.avcdriver")):
            try:
                import yaml
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    installed.append({
                        "id": data.get("id", filepath.stem),
                        "name": data.get("name", filepath.stem),
                        "format": "avcdriver",
                        "filename": filepath.name,
                    })
            except (OSError, yaml.YAMLError, ValueError):
                installed.append({"id": filepath.stem, "name": filepath.stem, "format": "avcdriver", "filename": filepath.name})

        for filepath in sorted(driver_repo.glob("*.py")):
            if filepath.name.startswith("_"):
                continue
            installed.append({"id": filepath.stem, "name": filepath.stem.replace("_", " ").title(), "format": "python", "filename": filepath.name})

        return {"drivers": installed}

    async def _get_driver_definition(self, input: dict) -> Any:
        from server.drivers.driver_loader import list_driver_definitions
        driver_id = input.get("driver_id", "")
        dirs = self._get_driver_dirs()
        for d in list_driver_definitions(dirs):
            if d.get("id") == driver_id:
                d.pop("_source_file", None)
                return d
        return {"error": f"Driver definition '{driver_id}' not found"}

    async def _get_script_source(self, input: dict) -> Any:
        script_id = input.get("script_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        for s in engine.project.scripts:
            if s.id == script_id:
                scripts_dir = engine.project_path.parent / "scripts"
                path = scripts_dir / s.file
                if path.exists():
                    source = path.read_text(encoding="utf-8")
                    return {"id": script_id, "file": s.file, "source": source}
                return {"error": f"Script file not found: {s.file}"}
        return {"error": f"Script '{script_id}' not found"}

    async def _get_logs(self, input: dict) -> Any:
        import time
        from server.utils.log_buffer import get_log_buffer

        count = input.get("count", 100)
        category = input.get("category", "")
        level = input.get("level", "")
        search = input.get("search", "")
        since_seconds = input.get("since_seconds")

        entries = get_log_buffer().get_recent(count)
        if category:
            entries = [e for e in entries if e.get("category") == category]
        if level:
            level_upper = level.upper()
            entries = [e for e in entries if e.get("level") == level_upper]
        if search:
            search_lower = search.lower()
            entries = [e for e in entries if search_lower in (e.get("message", "")).lower()]
        if since_seconds is not None:
            cutoff = time.time() - since_seconds
            entries = [e for e in entries if e.get("timestamp", 0) >= cutoff]
        return entries

    async def _list_triggers(self, input: dict) -> Any:
        engine = self._get_engine()
        if engine and engine.triggers:
            return engine.triggers.list_triggers()
        return []

    async def _get_project_summary(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        p = engine.project
        result = {
            "project": {"id": p.project.id, "name": p.project.name},
            "devices": [
                {"id": d.id, "name": d.name, "driver": d.driver}
                for d in p.devices
            ],
            "device_groups": [
                {"id": g.id, "name": g.name, "device_ids": g.device_ids}
                for g in p.device_groups
            ],
            "variables": [v.model_dump(mode="json") for v in p.variables],
            "macros": [
                {"id": m.id, "name": m.name, "step_count": len(m.steps), "trigger_count": len(m.triggers)}
                for m in p.macros
            ],
            "pages": [
                {
                    "id": pg.id, "name": pg.name,
                    "grid": pg.grid.model_dump(mode="json"),
                    "element_ids": [el.id for el in pg.elements],
                }
                for pg in p.ui.pages
            ],
            "scripts": [
                {"id": s.id, "file": s.file, "enabled": s.enabled, "description": s.description}
                for s in p.scripts
            ],
        }

        # Plugin status
        try:
            plugins = engine.plugin_loader.list_plugins()
            if isinstance(plugins, list):
                result["plugins"] = plugins
        except Exception:
            pass

        # Active theme
        active_theme = getattr(p.ui, "theme", None)
        if active_theme:
            result["active_theme"] = active_theme

        return result

    async def _get_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        macro_id = input.get("macro_id", "")
        for m in engine.project.macros:
            if m.id == macro_id:
                return m.model_dump(mode="json")
        return {"error": f"Macro '{macro_id}' not found"}

    async def _get_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        page_id = input.get("page_id", "")
        for p in engine.project.ui.pages:
            if p.id == page_id:
                return p.model_dump(mode="json")
        return {"error": f"UI page '{page_id}' not found"}

    # ===== WRITING / CREATING =====

    async def _update_project_metadata(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        changed = []
        if "name" in input:
            engine.project.project.name = input["name"]
            changed.append("name")
        if "description" in input:
            engine.project.project.description = input["description"]
            changed.append("description")

        if not changed:
            return {"error": "No fields to update. Provide 'name' and/or 'description'."}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        await self._notify_project_changed()

        return {"status": "updated", "changed": changed}

    async def _update_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        from server.core.project_loader import DeviceConfig, save_project
        device_idx = None
        for i, d in enumerate(engine.project.devices):
            if d.id == device_id:
                device_idx = i
                break
        if device_idx is None:
            return {"error": f"Device '{device_id}' not found"}
        existing = engine.project.devices[device_idx]
        updated = DeviceConfig(
            id=device_id,
            driver=input.get("driver", existing.driver),
            name=input.get("name", existing.name),
            config=input.get("config", existing.config),
            enabled=existing.enabled,
        )
        engine.project.devices[device_idx] = updated
        save_project(engine.project_path, engine.project)
        await engine.devices.update_device(device_id, updated.model_dump())
        return {"status": "updated", "device_id": device_id}

    async def _delete_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        device_id = input.get("device_id", "")
        original_count = len(engine.project.devices)
        engine.project.devices = [d for d in engine.project.devices if d.id != device_id]
        if len(engine.project.devices) == original_count:
            return {"error": f"Device '{device_id}' not found"}
        # Collect impact before saving
        impact = self._find_references("device", device_id)
        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        await engine.devices.remove_device(device_id)
        result: dict = {"status": "deleted", "device_id": device_id}
        if impact:
            result["impact"] = impact
        return result

    async def _add_device(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        device_id = input.get("id", "")
        if not device_id:
            return {"error": "Device ID is required"}
        if any(d.id == device_id for d in engine.project.devices):
            return {"error": f"Device '{device_id}' already exists"}

        from server.core.project_loader import DeviceConfig, save_project
        from server.core.project_migration import CONNECTION_FIELDS

        # Split config into connection fields and protocol fields
        raw_config = input.get("config", {})
        protocol_config = {}
        conn_overrides = {}
        for key, value in raw_config.items():
            if key in CONNECTION_FIELDS:
                conn_overrides[key] = value
            else:
                protocol_config[key] = value

        new_device = DeviceConfig(
            id=device_id,
            driver=input.get("driver", ""),
            name=input.get("name", device_id),
            config=protocol_config,
            enabled=input.get("enabled", True),
        )
        engine.project.devices.append(new_device)
        if conn_overrides:
            engine.project.connections[device_id] = conn_overrides
        save_project(engine.project_path, engine.project)

        # Hot-add via device manager with merged config
        await engine.devices.add_device(engine._resolved_device_config(new_device))
        await self._notify_project_changed()

        return {"status": "created", "id": device_id}

    async def _add_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        if not group_id:
            return {"error": "Group ID is required"}
        if any(g.id == group_id for g in engine.project.device_groups):
            return {"error": f"Group '{group_id}' already exists"}
        from server.core.project_loader import DeviceGroup, save_project
        new_group = DeviceGroup(
            id=group_id,
            name=input.get("name", group_id),
            device_ids=input.get("device_ids", []),
        )
        engine.project.device_groups.append(new_group)
        save_project(engine.project_path, engine.project)
        # Reload groups in macro engine
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
        return {"status": "created", "id": group_id}

    async def _update_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        group_idx = None
        for i, g in enumerate(engine.project.device_groups):
            if g.id == group_id:
                group_idx = i
                break
        if group_idx is None:
            return {"error": f"Group '{group_id}' not found"}
        from server.core.project_loader import save_project
        existing = engine.project.device_groups[group_idx]
        if "name" in input:
            existing.name = input["name"]
        if "device_ids" in input:
            existing.device_ids = input["device_ids"]
        save_project(engine.project_path, engine.project)
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
        return {"status": "updated", "id": group_id}

    async def _delete_device_group(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        group_id = input.get("id", "")
        original_count = len(engine.project.device_groups)
        engine.project.device_groups = [g for g in engine.project.device_groups if g.id != group_id]
        if len(engine.project.device_groups) == original_count:
            return {"error": f"Group '{group_id}' not found"}
        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        engine.macros.load_groups([g.model_dump() for g in engine.project.device_groups])
        await self._notify_project_changed()
        return {"status": "deleted", "id": group_id}

    async def _add_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        if not var_id:
            return {"error": "Variable ID is required"}
        if any(v.id == var_id for v in engine.project.variables):
            return {"error": f"Variable '{var_id}' already exists"}

        from server.core.project_loader import VariableConfig, save_project
        new_var = VariableConfig(
            id=var_id,
            type=input.get("type", "string"),
            default=input.get("default"),
            label=input.get("label", ""),
            dashboard=input.get("dashboard", False),
            persist=input.get("persist", False),
        )
        engine.project.variables.append(new_var)
        save_project(engine.project_path, engine.project)

        # Set initial state directly (no reload needed)
        if new_var.default is not None:
            self._agent.state.set(f"var.{var_id}", new_var.default, source="config")
        await self._notify_project_changed()

        return {"status": "created", "id": var_id}

    async def _update_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        var_idx = None
        for i, v in enumerate(engine.project.variables):
            if v.id == var_id:
                var_idx = i
                break
        if var_idx is None:
            return {"error": f"Variable '{var_id}' not found"}

        from server.core.project_loader import save_project
        existing = engine.project.variables[var_idx]
        if "type" in input:
            existing.type = input["type"]
        if "default" in input:
            existing.default = input["default"]
        if "label" in input:
            existing.label = input["label"]
        if "dashboard" in input:
            existing.dashboard = input["dashboard"]
        if "persist" in input:
            existing.persist = input["persist"]

        save_project(engine.project_path, engine.project)
        await self._notify_project_changed()

        return {"status": "updated", "id": var_id}

    async def _delete_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        # Collect impact before deleting
        impact = self._find_references("variable", var_id)

        original_count = len(engine.project.variables)
        engine.project.variables = [v for v in engine.project.variables if v.id != var_id]
        if len(engine.project.variables) == original_count:
            return {"error": f"Variable '{var_id}' not found"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)
        await self._notify_project_changed()

        result: dict = {"status": "deleted", "id": var_id}
        if impact:
            result["impact"] = impact
        return result

    async def _add_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        macro_id = input.get("id", "")
        if not macro_id:
            return {"error": "Macro ID is required"}
        if any(m.id == macro_id for m in engine.project.macros):
            return {"error": f"Macro '{macro_id}' already exists"}

        from server.core.project_loader import MacroConfig, save_project
        new_macro = MacroConfig(
            id=macro_id,
            name=input.get("name", macro_id),
            steps=input.get("steps", []),
            triggers=input.get("triggers", []),
            stop_on_error=input.get("stop_on_error", False),
        )
        engine.project.macros.append(new_macro)
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "created", "id": macro_id}

    async def _update_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        macro_id = input.get("macro_id", "")
        macro_idx = None
        for i, m in enumerate(engine.project.macros):
            if m.id == macro_id:
                macro_idx = i
                break
        if macro_idx is None:
            return {"error": f"Macro '{macro_id}' not found"}

        from server.core.project_loader import MacroConfig, save_project
        existing = engine.project.macros[macro_idx]
        updated = MacroConfig(
            id=macro_id,
            name=input.get("name", existing.name),
            steps=input["steps"] if "steps" in input else existing.steps,
            triggers=input["triggers"] if "triggers" in input else existing.triggers,
            stop_on_error=input.get("stop_on_error", existing.stop_on_error),
        )
        engine.project.macros[macro_idx] = updated
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "updated", "id": macro_id}

    async def _delete_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        macro_id = input.get("macro_id", "")
        # Collect impact before deleting
        impact = self._find_references("macro", macro_id)

        original_count = len(engine.project.macros)
        engine.project.macros = [m for m in engine.project.macros if m.id != macro_id]
        if len(engine.project.macros) == original_count:
            return {"error": f"Macro '{macro_id}' not found"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        result: dict = {"status": "deleted", "id": macro_id}
        if impact:
            result["impact"] = impact
        return result

    async def _add_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("id", "")
        if not page_id:
            return {"error": "Page ID is required"}
        if any(p.id == page_id for p in engine.project.ui.pages):
            return {"error": f"UI page '{page_id}' already exists"}

        from server.core.project_loader import UIPage, save_project
        new_page = UIPage(
            id=page_id,
            name=input.get("name", page_id),
            grid=input.get("grid", {}),
            elements=input.get("elements", []),
        )
        engine.project.ui.pages.append(new_page)
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "created", "id": page_id}

    async def _update_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        page = None
        for p in engine.project.ui.pages:
            if p.id == page_id:
                page = p
                break
        if page is None:
            return {"error": f"UI page '{page_id}' not found"}

        changed = []
        if "name" in input:
            page.name = input["name"]
            changed.append("name")
        if "grid" in input:
            from server.core.project_loader import GridConfig
            page.grid = GridConfig(**input["grid"])
            changed.append("grid")
        if "page_type" in input:
            page.page_type = input["page_type"]
            changed.append("page_type")
        if "overlay" in input:
            from server.core.project_loader import OverlayConfig
            page.overlay = OverlayConfig(**input["overlay"]) if input["overlay"] else None
            changed.append("overlay")
        if "background" in input:
            from server.core.project_loader import PageBackground
            page.background = PageBackground(**input["background"]) if input["background"] else None
            changed.append("background")

        if not changed:
            return {"error": "No fields to update"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "updated", "page_id": page_id, "changed": changed}

    async def _delete_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        # Count elements being removed
        element_count = 0
        for pg in engine.project.ui.pages:
            if pg.id == page_id:
                element_count = len(pg.elements)
                break

        original_count = len(engine.project.ui.pages)
        engine.project.ui.pages = [p for p in engine.project.ui.pages if p.id != page_id]
        if len(engine.project.ui.pages) == original_count:
            return {"error": f"UI page '{page_id}' not found"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        result: dict = {"status": "deleted", "id": page_id}
        if element_count > 0:
            result["impact"] = {"elements_removed": element_count}
        return result

    async def _add_ui_elements(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        page = None
        for p in engine.project.ui.pages:
            if p.id == page_id:
                page = p
                break
        if page is None:
            return {"error": f"UI page '{page_id}' not found"}

        elements = input.get("elements", [])
        if not elements:
            return {"error": "No elements provided"}

        # Check for duplicate IDs
        existing_ids = {el.id for el in page.elements}
        for el in elements:
            el_id = el.get("id", "")
            if el_id in existing_ids:
                return {"error": f"Element '{el_id}' already exists on page '{page_id}'"}

        from server.core.project_loader import UIElement, save_project
        for el_data in elements:
            page.elements.append(UIElement(**el_data))
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        added_ids = [el.get("id", "") for el in elements]
        return {"status": "created", "page_id": page_id, "element_ids": added_ids}

    async def _update_ui_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")

        # Find the element across all pages
        target_el = None
        for page in engine.project.ui.pages:
            for el in page.elements:
                if el.id == element_id:
                    target_el = el
                    break
            if target_el:
                break

        if target_el is None:
            return {"error": f"UI element '{element_id}' not found"}

        if "label" in input:
            target_el.label = input["label"]
        if "text" in input:
            target_el.text = input["text"]
        if "grid_area" in input:
            from server.core.project_loader import GridArea
            target_el.grid_area = GridArea(**input["grid_area"])
        if "style" in input:
            target_el.style = input["style"]
        if "bindings" in input:
            target_el.bindings = input["bindings"]

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "updated", "element_id": element_id}

    async def _delete_ui_elements(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_ids = input.get("element_ids", [])
        if not element_ids:
            return {"error": "No element_ids provided"}

        ids_set = set(element_ids)
        deleted_ids = []
        for page in engine.project.ui.pages:
            before_ids = {el.id for el in page.elements}
            page.elements = [el for el in page.elements if el.id not in ids_set]
            deleted_ids.extend(ids_set & before_ids)

        if not deleted_ids:
            return {"error": "No matching elements found"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "deleted", "element_ids": sorted(deleted_ids)}

    async def _add_master_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("id", "")
        if not element_id:
            return {"error": "Element ID is required"}

        # Check for ID collision with page elements and existing master elements
        for page in engine.project.ui.pages:
            if any(el.id == element_id for el in page.elements):
                return {"error": f"Element '{element_id}' already exists on page '{page.id}'"}
        if any(el.id == element_id for el in engine.project.ui.master_elements):
            return {"error": f"Master element '{element_id}' already exists"}

        from server.core.project_loader import MasterElement, save_project
        el_data = {k: v for k, v in input.items() if k != "id"}
        el_data["id"] = element_id
        new_el = MasterElement(**el_data)
        engine.project.ui.master_elements.append(new_el)
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "created", "id": element_id}

    async def _delete_master_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")
        original_count = len(engine.project.ui.master_elements)
        engine.project.ui.master_elements = [
            el for el in engine.project.ui.master_elements if el.id != element_id
        ]
        if len(engine.project.ui.master_elements) == original_count:
            return {"error": f"Master element '{element_id}' not found"}

        from server.core.project_loader import save_project
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "deleted", "element_id": element_id}

    async def _install_community_driver(self, input: dict) -> Any:
        from server.core.device_manager import register_driver
        from server.drivers.driver_loader import load_driver_file, load_python_driver_file
        from server.drivers.configurable import create_configurable_driver_class

        driver_id = input.get("driver_id", "")
        file_url = input.get("file_url", "")
        if not file_url:
            return {"error": "No file_url provided"}

        driver_repo = self._get_driver_repo_dir()
        driver_repo.mkdir(parents=True, exist_ok=True)

        ext = ".avcdriver" if file_url.endswith(".avcdriver") else ".py" if file_url.endswith(".py") else ""
        if not ext:
            return {"error": "URL must point to a .avcdriver or .py file"}

        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in driver_id)
        filepath = driver_repo / f"{safe_id}{ext}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(file_url)
                resp.raise_for_status()
                filepath.write_text(resp.text, encoding="utf-8")
        except (httpx.HTTPError, OSError) as e:
            return {"error": f"Download failed: {e}"}

        try:
            if ext == ".avcdriver":
                driver_def = load_driver_file(filepath)
                if driver_def is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "Invalid driver definition file"}
                driver_class = create_configurable_driver_class(driver_def)
                register_driver(driver_class)
            else:
                driver_class = load_python_driver_file(filepath)
                if driver_class is None:
                    filepath.unlink(missing_ok=True)
                    return {"error": "No valid driver class found in Python file"}
                register_driver(driver_class)
        except Exception as e:
            # Catch-all: driver loading can fail with YAML, import, or validation errors
            filepath.unlink(missing_ok=True)
            return {"error": f"Failed to load driver: {e}"}

        return {"status": "installed", "driver_id": driver_id, "file": filepath.name}

    async def _create_driver_definition(self, input: dict) -> Any:
        from server.drivers.driver_loader import list_driver_definitions, save_driver_definition, validate_driver_definition
        from server.drivers.configurable import create_configurable_driver_class
        from server.core.device_manager import register_driver

        definition = input.get("definition", {})
        if not definition.get("id"):
            return {"error": "Driver definition must have an 'id' field"}

        dirs = self._get_driver_dirs()
        existing = list_driver_definitions(dirs)
        if any(d.get("id") == definition["id"] for d in existing):
            return {"error": f"Driver definition '{definition['id']}' already exists"}

        errors = validate_driver_definition(definition)
        if errors:
            return {"error": "; ".join(errors)}

        save_dir = dirs[1] if len(dirs) > 1 else dirs[0]
        save_driver_definition(definition, save_dir)
        driver_class = create_configurable_driver_class(definition)
        register_driver(driver_class)
        return {"status": "created", "id": definition["id"]}

    async def _update_driver_definition(self, input: dict) -> Any:
        from server.drivers.driver_loader import delete_driver_definition, list_driver_definitions, save_driver_definition, validate_driver_definition
        from server.drivers.configurable import create_configurable_driver_class
        from server.core.device_manager import register_driver

        driver_id = input.get("driver_id", "")
        definition = input.get("definition", {})
        dirs = self._get_driver_dirs()

        existing = list_driver_definitions(dirs)
        if not any(d.get("id") == driver_id for d in existing):
            return {"error": f"Driver definition '{driver_id}' not found"}

        errors = validate_driver_definition(definition)
        if errors:
            return {"error": "; ".join(errors)}

        delete_driver_definition(driver_id, dirs)
        save_dir = dirs[1] if len(dirs) > 1 else dirs[0]
        save_driver_definition(definition, save_dir)
        driver_class = create_configurable_driver_class(definition)
        register_driver(driver_class)
        return {"status": "updated", "id": definition.get("id", driver_id)}

    async def _create_script(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        script_id = input.get("id", "")
        filename = input.get("file", f"{script_id}.py")
        source = input.get("source", "")
        description = input.get("description", "")
        enabled = input.get("enabled", True)

        for s in engine.project.scripts:
            if s.id == script_id:
                return {"error": f"Script '{script_id}' already exists"}

        scripts_dir = engine.project_path.parent / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        path = scripts_dir / filename
        path.write_text(source, encoding="utf-8")

        from server.core.project_loader import ScriptConfig, save_project
        new_script = ScriptConfig(id=script_id, file=filename, enabled=enabled, description=description)
        engine.project.scripts.append(new_script)
        save_project(engine.project_path, engine.project)
        if self._reload_fn:
            await self._reload_fn()
        return {"status": "created", "id": script_id}

    async def _update_script_source(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        source = input.get("source", "")
        for s in engine.project.scripts:
            if s.id == script_id:
                scripts_dir = engine.project_path.parent / "scripts"
                path = scripts_dir / s.file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
                return {"status": "saved"}
        return {"error": f"Script '{script_id}' not found"}

    async def _delete_script(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        script_id = input.get("script_id", "")
        cfg = None
        for s in engine.project.scripts:
            if s.id == script_id:
                cfg = s
                break
        if not cfg:
            return {"error": f"Script '{script_id}' not found"}

        scripts_dir = engine.project_path.parent / "scripts"
        path = scripts_dir / cfg.file
        if path.exists():
            path.unlink()
        from server.core.project_loader import save_project
        engine.project.scripts = [s for s in engine.project.scripts if s.id != script_id]
        save_project(engine.project_path, engine.project)
        if self._reload_fn:
            await self._reload_fn()
        return {"status": "deleted"}

    # ===== ACTION TOOLS =====

    async def _send_device_command(self, input: dict) -> Any:
        device_id = input.get("device_id", "")
        command = input.get("command", "")
        params = input.get("params", {})
        await self._events.emit("ai.device_command", {
            "device_id": device_id, "command": command, "params": params,
        })
        await self._devices.send_command(device_id, command, params)
        return "OK"

    async def _test_device_connection(self, input: dict) -> Any:
        import asyncio as _asyncio
        import time as _time

        device_id = input.get("device_id", "")
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"success": False, "error": "No project loaded", "latency_ms": None}

        device_cfg = None
        for d in engine.project.devices:
            if d.id == device_id:
                device_cfg = d
                break
        if device_cfg is None:
            return {"success": False, "error": f"Device '{device_id}' not found", "latency_ms": None}

        cfg = device_cfg.config
        host = cfg.get("host", "")
        port = cfg.get("port")
        transport = cfg.get("transport", "tcp")
        start = _time.monotonic()

        if transport == "http":
            url = cfg.get("base_url", cfg.get("url", ""))
            if not url and host:
                scheme = "https" if cfg.get("ssl") else "http"
                url = f"{scheme}://{host}" + (f":{port}" if port else "")
            if not url:
                return {"success": False, "error": "No URL configured", "latency_ms": None}
            try:
                import httpx
                async with httpx.AsyncClient(timeout=5.0, verify=False) as client:
                    await client.head(url)
                latency = round((_time.monotonic() - start) * 1000, 1)
                return {"success": True, "error": None, "latency_ms": latency}
            except (httpx.HTTPError, OSError) as e:
                return {"success": False, "error": str(e), "latency_ms": None}
        else:
            if not host:
                return {"success": False, "error": "No host configured", "latency_ms": None}
            tcp_port = int(port) if port else 23
            try:
                reader, writer = await _asyncio.wait_for(
                    _asyncio.open_connection(host, tcp_port), timeout=5.0
                )
                writer.close()
                await writer.wait_closed()
                latency = round((_time.monotonic() - start) * 1000, 1)
                return {"success": True, "error": None, "latency_ms": latency}
            except _asyncio.TimeoutError:
                return {"success": False, "error": "Connection timed out (5s)", "latency_ms": None}
            except OSError as e:
                return {"success": False, "error": str(e), "latency_ms": None}

    async def _test_driver_command(self, input: dict) -> Any:
        import asyncio
        from server.transport.tcp import TCPTransport

        host = input.get("host", "")
        port = input.get("port", 23)
        command_string = input.get("command_string", "")
        delimiter = input.get("delimiter", "\\r\\n")
        timeout = input.get("timeout", 5)

        if not host or not command_string:
            return {"success": False, "error": "host and command_string are required", "response": None}

        delimiter_bytes = delimiter.encode().decode("unicode_escape").encode()

        try:
            transport = await TCPTransport.create(
                host=host, port=port,
                on_data=lambda d: None, on_disconnect=lambda: None,
                delimiter=delimiter_bytes, timeout=timeout,
            )
        except ConnectionError as e:
            return {"success": False, "error": str(e), "response": None}

        try:
            cmd_data = command_string.encode().decode("unicode_escape").encode()
            response = await transport.send_and_wait(cmd_data, timeout=timeout)
            response_text = response.decode("ascii", errors="replace")
            return {"success": True, "error": None, "response": response_text}
        except asyncio.TimeoutError:
            return {"success": False, "error": "Timeout waiting for response", "response": None}
        except (OSError, ConnectionError) as e:
            return {"success": False, "error": str(e), "response": None}
        finally:
            await transport.close()

    async def _execute_macro(self, input: dict) -> Any:
        macro_id = input.get("macro_id", "")
        engine = self._get_engine()
        if engine and engine.macros:
            await engine.macros.execute(macro_id)
            return {"status": "executed", "macro_id": macro_id}
        return {"error": "Macro engine not available"}

    async def _cancel_macro(self, input: dict) -> Any:
        macro_id = input.get("macro_id", "")
        engine = self._get_engine()
        if engine and engine.macros:
            cancelled = await engine.macros.cancel(macro_id)
            return {"status": "cancelled" if cancelled else "not_running", "macro_id": macro_id}
        return {"error": "Macro engine not available"}

    async def _set_state_value(self, input: dict) -> Any:
        key = input.get("key", "")
        value = input.get("value")
        self._agent.state.set(key, value, source="ai")
        return {"key": key, "value": value}

    async def _test_trigger(self, input: dict) -> Any:
        trigger_id = input.get("trigger_id", "")
        engine = self._get_engine()
        if engine and engine.triggers:
            ok = await engine.triggers.test_trigger(trigger_id)
            if ok:
                return {"status": "fired", "trigger_id": trigger_id}
            return {"error": f"Trigger '{trigger_id}' not found"}
        return {"error": "Trigger engine not available"}

    # ===== HELPERS =====

    async def _notify_project_changed(self) -> None:
        """Broadcast project change to connected IDE clients.

        Called by handlers that modify the project but don't trigger a full
        reload (e.g., add_device, variable tools). Handlers that DO reload
        get this broadcast automatically via engine.reload_project().
        """
        engine = self._get_engine()
        if engine and hasattr(engine, "_broadcast_ws"):
            await engine._broadcast_ws({"type": "project.reloaded"})

    # ===== PLUGINS =====

    async def _list_plugins(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        return engine.plugin_loader.list_plugins()

    async def _browse_community_plugins(self, input: dict) -> Any:
        from server.core.plugin_installer import get_community_plugins

        plugins, error = await get_community_plugins()
        return {"plugins": plugins, "error": error}

    async def _install_plugin(self, input: dict) -> Any:
        from server.core.plugin_installer import (
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
            return result
        except ValueError as e:
            return {"error": str(e)}

    async def _uninstall_plugin(self, input: dict) -> Any:
        from server.core.plugin_installer import uninstall_plugin

        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        # Stop plugin if running
        if plugin_id in engine.plugin_loader._instances:
            await engine.plugin_loader.stop_plugin(plugin_id)

        project_plugins = engine.project.plugins if engine.project else None
        try:
            result = await uninstall_plugin(plugin_id, project_plugins)
        except ValueError as e:
            return {"error": str(e)}

        # Remove from project file
        if engine.project and plugin_id in engine.project.plugins:
            del engine.project.plugins[plugin_id]
            engine.project.plugin_dependencies = [
                d for d in engine.project.plugin_dependencies
                if d.plugin_id != plugin_id
            ]
            from server.core.project_loader import save_project
            save_project(engine.project_path, engine.project)

        # Clear missing plugin state if tracked
        if plugin_id in engine.plugin_loader._missing_plugins:
            del engine.plugin_loader._missing_plugins[plugin_id]

        return result

    async def _enable_plugin(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
        from server.core.project_loader import PluginConfig, build_default_plugin_config, save_project

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class is None:
            return {"error": f"Plugin '{plugin_id}' not installed"}

        if plugin_id not in engine.project.plugins:
            schema = getattr(plugin_class, "CONFIG_SCHEMA", {}) or {}
            default_config = build_default_plugin_config(schema)
            engine.project.plugins[plugin_id] = PluginConfig(
                enabled=True,
                config=default_config,
            )
        else:
            engine.project.plugins[plugin_id].enabled = True

        save_project(engine.project_path, engine.project)
        config = engine.project.plugins[plugin_id].config
        success = await engine.plugin_loader.start_plugin(plugin_id, config)

        return {
            "status": "enabled" if success else "error",
            "plugin_id": plugin_id,
            "config": config,
        }

    async def _disable_plugin(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        if not plugin_id:
            return {"error": "plugin_id is required"}

        if plugin_id not in engine.project.plugins:
            return {"error": f"Plugin '{plugin_id}' not in project"}

        from server.core.project_loader import save_project

        engine.project.plugins[plugin_id].enabled = False
        save_project(engine.project_path, engine.project)
        await engine.plugin_loader.stop_plugin(plugin_id)

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
        from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY
        from server.core.project_loader import get_plugin_setup_fields

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        schema = {}
        setup_fields = []
        result: dict[str, Any] = {
            "plugin_id": plugin_id,
            "config": entry.config,
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
        result["required_fields"] = [f["name"] for f in setup_fields]
        return result

    async def _update_plugin_config(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        plugin_id = input.get("plugin_id", "")
        new_config = input.get("config", {})
        if not plugin_id:
            return {"error": "plugin_id is required"}

        if plugin_id not in engine.project.plugins:
            return {"error": f"Plugin '{plugin_id}' not in project"}

        from server.core.project_loader import save_project

        engine.project.plugins[plugin_id].config = new_config
        save_project(engine.project_path, engine.project)

        # Restart if running
        if plugin_id in engine.plugin_loader._instances:
            await engine.plugin_loader.stop_plugin(plugin_id)
            await engine.plugin_loader.start_plugin(plugin_id, new_config)

        return {"status": "updated", "plugin_id": plugin_id}

    # ===== ASYNC / WAITING =====

    async def _wait(self, input: dict) -> Any:
        """Wait for a specified number of seconds before returning.

        Use this when an async operation (discovery scan, device connection test,
        etc.) needs time to complete. Avoids burning tool rounds on rapid polling.
        """
        seconds = input.get("seconds", 30)
        seconds = max(1, min(seconds, 120))  # Clamp to 1-120
        reason = input.get("reason", "")

        log.info(f"AI wait: {seconds}s (reason: {reason or 'none'})")
        await asyncio.sleep(seconds)

        return {
            "waited_seconds": seconds,
            "message": f"Waited {seconds} seconds. You can now check the results of your operation.",
        }

    # ===== DISCOVERY =====

    async def _start_discovery_scan(self, input: dict) -> Any:
        from server.api.discovery import _engine as discovery_engine
        if discovery_engine is None:
            return {"error": "Discovery engine not available"}

        subnets = input.get("subnets")
        snmp_enabled = input.get("snmp_enabled", True)
        timeout = input.get("timeout", 120.0)

        discovery_engine.config["snmp_enabled"] = snmp_enabled

        try:
            scan_id = await discovery_engine.start_scan(
                subnets=subnets,
                timeout=timeout,
            )
        except RuntimeError:
            return {"error": "A scan is already in progress"}
        except ValueError as e:
            return {"error": str(e)}

        status = discovery_engine.get_status()
        return {
            "scan_id": scan_id,
            "status": status["status"],
            "subnets": status["subnets"],
        }

    async def _get_discovery_results(self, input: dict) -> Any:
        from server.api.discovery import _engine as discovery_engine
        if discovery_engine is None:
            return {"error": "Discovery engine not available"}

        wait = input.get("wait", False)
        wait_timeout = input.get("timeout", 120)
        min_confidence = input.get("min_confidence", 0.0)
        category = input.get("category")

        # If wait=True, poll until scan completes (or timeout)
        if wait:
            wait_timeout = max(5, min(wait_timeout, 180))
            elapsed = 0.0
            poll_interval = 3.0
            while elapsed < wait_timeout:
                status = discovery_engine.get_status()
                if status["status"] not in ("running", "scanning"):
                    break
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

        devices = discovery_engine.get_results()
        if min_confidence > 0:
            devices = [d for d in devices if d["confidence"] >= min_confidence]
        if category:
            devices = [d for d in devices if d.get("category") == category]

        status = discovery_engine.get_status()
        return {
            "devices": devices,
            "total_devices": len(devices),
            "scan_status": status["status"],
            "scan_duration_seconds": status["duration"],
        }

    # ===== THEMES =====

    async def _list_themes(self, input: dict) -> Any:
        from server.api.themes import _list_all_themes

        engine = self._get_engine()
        active_theme = None
        if engine and engine.project:
            active_theme = getattr(engine.project.ui, "theme", None)

        themes = _list_all_themes()
        return [
            {
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description", ""),
                "source": t.get("_source", "custom"),
                "active": t["id"] == active_theme if active_theme else False,
            }
            for t in themes
        ]

    async def _get_theme(self, input: dict) -> Any:
        from server.api.themes import BUILTIN_THEMES_DIR, _custom_themes_dir, _load_theme

        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}

        builtin_path = BUILTIN_THEMES_DIR / f"{theme_id}.json"
        if builtin_path.exists():
            theme = _load_theme(builtin_path)
            theme["_source"] = "builtin"
            return theme

        try:
            custom_path = _custom_themes_dir() / f"{theme_id}.json"
            if custom_path.exists():
                theme = _load_theme(custom_path)
                theme["_source"] = "custom"
                return theme
        except Exception:
            pass

        return {"error": f"Theme '{theme_id}' not found"}

    async def _apply_theme(self, input: dict) -> Any:
        from server.api.themes import BUILTIN_THEMES_DIR, _custom_themes_dir
        from server.core.project_loader import save_project

        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        theme_id = input.get("theme_id", "")
        if not theme_id:
            return {"error": "theme_id is required"}

        # Verify theme exists
        builtin = (BUILTIN_THEMES_DIR / f"{theme_id}.json").exists()
        try:
            custom = (_custom_themes_dir() / f"{theme_id}.json").exists()
        except Exception:
            custom = False

        if not builtin and not custom:
            return {"error": f"Theme '{theme_id}' not found"}

        engine.project.ui.theme = theme_id
        save_project(engine.project_path, engine.project)

        if self._reload_fn:
            await self._reload_fn()

        return {"status": "applied", "theme_id": theme_id}

    # ===== ASSETS =====

    async def _list_assets(self, input: dict) -> Any:
        from server.api.assets import _assets_dir, ALLOWED_EXTENSIONS

        try:
            assets_dir = _assets_dir()
        except Exception:
            return {"assets": [], "total_size": 0}

        assets = []
        for f in sorted(assets_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
                assets.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "type": f.suffix.lower().lstrip("."),
                })
        total_size = sum(a["size"] for a in assets)
        return {"assets": assets, "total_size": total_size}

    async def _delete_asset(self, input: dict) -> Any:
        from server.api.assets import _assets_dir

        filename = input.get("filename", "")
        if not filename:
            return {"error": "filename is required"}

        try:
            assets_dir = _assets_dir()
        except Exception:
            return {"error": "Assets directory not available"}

        # Sanitize: use only the filename part
        safe_name = Path(filename).name
        path = assets_dir / safe_name
        if not path.exists():
            return {"error": f"Asset '{safe_name}' not found"}

        path.unlink()
        return {"status": "deleted", "name": safe_name}

    # ===== ISC =====

    async def _get_isc_status(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return {"enabled": False}
        return engine.isc.get_status()

    async def _list_isc_peers(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return []
        return engine.isc.get_instances()

    async def _send_isc_command(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        if engine.isc is None:
            return {"error": "ISC not enabled"}

        instance_id = input.get("instance_id", "")
        device_id = input.get("device_id", "")
        command = input.get("command", "")
        params = input.get("params", {})

        if not instance_id or not device_id or not command:
            return {"error": "instance_id, device_id, and command are required"}

        try:
            result = await engine.isc.send_command(instance_id, device_id, command, params)
            return {"success": True, "result": result}
        except ConnectionError:
            return {"error": f"ISC peer '{instance_id}' is not connected"}
        except TimeoutError:
            return {"error": f"Command timed out on ISC peer '{instance_id}'"}

    # ===== DEVICE SETTINGS =====

    async def _get_device_settings(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        if not device_id:
            return {"error": "device_id is required"}

        try:
            settings = engine.devices.get_device_settings(device_id)
            return {"device_id": device_id, "settings": settings}
        except ValueError:
            return {"error": f"Device '{device_id}' not found"}

    async def _set_device_setting(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        device_id = input.get("device_id", "")
        setting_key = input.get("setting_key", "")
        value = input.get("value")

        if not device_id or not setting_key:
            return {"error": "device_id and setting_key are required"}

        try:
            await engine.devices.set_device_setting(device_id, setting_key, value)
            return {"success": True, "device_id": device_id, "key": setting_key, "value": value}
        except ValueError:
            return {"error": f"Device '{device_id}' or setting '{setting_key}' not found"}
        except ConnectionError:
            return {"error": f"Device '{device_id}' is not connected"}
        except NotImplementedError:
            return {"error": f"Device '{device_id}' does not support writable settings"}

    # ===== UI SIMULATION =====

    async def _simulate_ui_action(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        action = input.get("action", "")
        element_id = input.get("element_id", "")
        value = input.get("value")
        page_id = input.get("page_id", "")

        if action == "navigate":
            if not page_id:
                return {"error": "page_id is required for navigate action"}
            await engine.events.emit(f"ui.page.{page_id}")
            return {"success": True, "action": "navigate", "page_id": page_id, "state_changes": []}

        if not element_id:
            return {"error": "element_id is required for this action"}

        # Capture state changes during action execution
        state_changes = []
        def on_change(key, old_val, new_val, source):
            state_changes.append({"key": key, "old_value": old_val, "new_value": new_val})

        sub_id = self._agent.state.subscribe("*", on_change)
        try:
            if action in ("press", "release", "hold"):
                await engine.handle_ui_event(action, element_id)
            elif action == "change":
                await engine.handle_ui_event("change", element_id, {"value": value})
            elif action == "submit":
                await engine.handle_ui_event("submit", element_id, {"value": value})
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": f"Action failed: {e}", "state_changes": state_changes}
        finally:
            self._agent.state.unsubscribe(sub_id)

        return {"success": True, "action": action, "element_id": element_id, "state_changes": state_changes}

    # ===== IMPACT CHECKING =====

    def _find_references(self, ref_type: str, ref_id: str) -> dict:
        """Find all references to a macro, device, variable, or script in the project."""
        engine = self._get_engine()
        if not engine or not engine.project:
            return {}

        p = engine.project
        result: dict = {"macros": [], "triggers": [], "bindings": [], "scripts": []}

        if ref_type == "macro":
            # Check triggers on all macros
            for m in p.macros:
                for t in m.triggers:
                    action = t.action if hasattr(t, "action") else ""
                    if action == ref_id or (hasattr(t, "macro") and t.macro == ref_id):
                        result["triggers"].append({"macro_id": m.id, "trigger_id": getattr(t, "id", "")})
            # Check UI bindings
            for page in p.ui.pages:
                for el in page.elements:
                    bindings = el.bindings if hasattr(el, "bindings") and el.bindings else {}
                    if isinstance(bindings, dict):
                        for slot, binding in bindings.items():
                            if isinstance(binding, dict):
                                if binding.get("action") == "macro" and binding.get("macro") == ref_id:
                                    result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
            # Check scripts
            for s in p.scripts:
                try:
                    from server.config import BASE_DIR
                    script_path = BASE_DIR / "projects" / "default" / "scripts" / s.file
                    if script_path.exists():
                        content = script_path.read_text(encoding="utf-8")
                        if ref_id in content:
                            result["scripts"].append({"script_id": s.id, "file": s.file})
                except Exception:
                    pass

        elif ref_type == "device":
            # Check macros for device commands
            for m in p.macros:
                for step in m.steps:
                    step_dict = step.model_dump(mode="json") if hasattr(step, "model_dump") else step
                    if isinstance(step_dict, dict) and step_dict.get("device") == ref_id:
                        result["macros"].append({"macro_id": m.id, "macro_name": m.name})
                        break
            # Check UI bindings
            for page in p.ui.pages:
                for el in page.elements:
                    bindings = el.bindings if hasattr(el, "bindings") and el.bindings else {}
                    if isinstance(bindings, dict):
                        for slot, binding in bindings.items():
                            if isinstance(binding, dict) and binding.get("device") == ref_id:
                                result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
            # Check scripts
            for s in p.scripts:
                try:
                    from server.config import BASE_DIR
                    script_path = BASE_DIR / "projects" / "default" / "scripts" / s.file
                    if script_path.exists():
                        content = script_path.read_text(encoding="utf-8")
                        if ref_id in content:
                            result["scripts"].append({"script_id": s.id, "file": s.file})
                except Exception:
                    pass

        elif ref_type == "variable":
            state_key = f"var.{ref_id}"
            # Check UI bindings
            for page in p.ui.pages:
                for el in page.elements:
                    bindings = el.bindings if hasattr(el, "bindings") and el.bindings else {}
                    if isinstance(bindings, dict):
                        for slot, binding in bindings.items():
                            if isinstance(binding, dict) and binding.get("key") in (ref_id, state_key):
                                result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
            # Check triggers
            for m in p.macros:
                for t in m.triggers:
                    trigger_dict = t.model_dump(mode="json") if hasattr(t, "model_dump") else t
                    if isinstance(trigger_dict, dict) and trigger_dict.get("key") in (ref_id, state_key):
                        result["triggers"].append({"macro_id": m.id})
            # Check scripts
            for s in p.scripts:
                try:
                    from server.config import BASE_DIR
                    script_path = BASE_DIR / "projects" / "default" / "scripts" / s.file
                    if script_path.exists():
                        content = script_path.read_text(encoding="utf-8")
                        if ref_id in content:
                            result["scripts"].append({"script_id": s.id, "file": s.file})
                except Exception:
                    pass

        # Remove empty lists
        return {k: v for k, v in result.items() if v}

    async def _check_references(self, input: dict) -> Any:
        ref_type = input.get("type", "")
        ref_id = input.get("id", "")
        if not ref_type or not ref_id:
            return {"error": "type and id are required"}
        if ref_type not in ("macro", "device", "variable", "script"):
            return {"error": f"Invalid type: {ref_type}. Must be macro, device, variable, or script."}

        refs = self._find_references(ref_type, ref_id)
        return {"type": ref_type, "id": ref_id, "referenced_by": refs}

    # ===== HELPERS =====

    def _get_engine(self):
        try:
            from server.api.rest import _engine
            return _engine
        except ImportError:
            return None

    def _get_driver_repo_dir(self) -> Path:
        from server.config import BASE_DIR
        return BASE_DIR / "driver_repo"

    def _get_driver_dirs(self) -> list[Path]:
        from server.config import BASE_DIR
        return [
            BASE_DIR / "server" / "drivers" / "definitions",
            BASE_DIR / "driver_repo",
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
