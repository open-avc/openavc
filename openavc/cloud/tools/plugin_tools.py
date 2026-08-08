"""Mixin for AI tool handlers that manage plugin lifecycle and configuration."""

from typing import Any

from openavc.cloud.tools import ToolEditError, apply_tool_edit


class PluginToolsMixin:
    """Plugin listing, install/uninstall, enable/disable, and config tools."""

    async def _list_plugins(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        return engine.plugin_loader.list_plugins()

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
            return result
        except ValueError as e:
            return {"error": str(e)}

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
            health = await engine.plugin_loader.get_health(plugin_id)
            return {
                "status": "error",
                "error": (
                    f"Plugin '{plugin_id}' failed to start: "
                    f"{health.get('message', 'unknown error')}. "
                    f"The enable was rolled back; its config is preserved."
                ),
                "plugin_id": plugin_id,
                "config": config,
            }

        return {
            "status": "enabled",
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

        from openavc.cloud.ai_tool_handler import SURFACE_BUTTONS_FORMAT

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

        # Validate config against plugin's CONFIG_SCHEMA if available.
        # Wrong types are rejected; required fields that aren't set yet
        # only warn, mirroring the REST path — the config may legitimately
        # be built up across several calls.
        from openavc.core.plugin_config import (
            missing_required_for_plugin,
            validate_config_for_plugin,
        )

        err = validate_config_for_plugin(plugin_id, new_config)
        if err:
            return {"error": f"Plugin '{plugin_id}': {err}"}
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
        if missing:
            result["missing_required"] = sorted(missing)
            result["warning"] = (
                f"Config saved, but required field(s) {', '.join(sorted(missing))} "
                f"are not set yet. Plugin '{plugin_id}' can't run until they are."
            )
        elif outcome == "start_failed":
            result["warning"] = (
                f"Config saved, but plugin '{plugin_id}' failed to restart with "
                f"it and is stopped. Check the config values and plugin logs."
            )
        return result
