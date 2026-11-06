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

        from openavc.cloud.ai_tool_handler import SURFACE_BUTTONS_FORMAT

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
