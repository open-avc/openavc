"""Mixin for AI tool handlers that manage plugin lifecycle and configuration."""

from typing import Any


class PluginToolsMixin:
    """Plugin listing, install/uninstall, enable/disable, and config tools."""

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
        if engine.plugin_loader.is_running(plugin_id):
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
        engine.plugin_loader.clear_missing(plugin_id)

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

        from server.cloud.ai_tool_handler import SURFACE_BUTTONS_FORMAT

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

        # Validate config against plugin's CONFIG_SCHEMA if available
        from server.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

        from server.cloud.ai_tool_handler import _validate_plugin_config

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
        if plugin_class:
            schema = getattr(plugin_class, "CONFIG_SCHEMA", None)
            if schema and isinstance(schema, dict):
                err = _validate_plugin_config(new_config, schema)
                if err:
                    return {"error": f"Plugin '{plugin_id}': {err}"}

        from server.core.project_loader import save_project

        engine.project.plugins[plugin_id].config = new_config
        save_project(engine.project_path, engine.project)

        # Restart if running
        if engine.plugin_loader.is_running(plugin_id):
            await engine.plugin_loader.stop_plugin(plugin_id)
            await engine.plugin_loader.start_plugin(plugin_id, new_config)

        return {"status": "updated", "plugin_id": plugin_id}
