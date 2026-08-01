"""Mixin for AI tool handlers that read and update project-level data."""

from typing import Any

from server.cloud.state_relay import is_cloud_excluded_key
from server.cloud.tools import ToolEditError, apply_tool_edit


class ProjectToolsMixin:
    """Project reading, state inspection, and metadata update tools."""

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
                {
                    "id": m.id, "name": m.name, "step_count": len(m.steps),
                    "trigger_count": len(m.triggers),
                    "triggers": [
                        {k: v for k, v in t.model_dump(mode="json").items() if v is not None}
                        for t in m.triggers
                    ],
                }
                for m in p.macros
            ],
            "pages": [
                {
                    "id": pg.id, "name": pg.name,
                    "layouts": [
                        {"id": lay.id, "orientation": lay.orientation, "primary": lay.primary}
                        for lay in pg.layouts
                    ],
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
            from server.utils.logger import get_logger
            log = get_logger(__name__)
            log.warning("Failed to list plugins for AI status", exc_info=True)

        # Active theme
        settings = getattr(p.ui, "settings", None)
        active_theme = getattr(settings, "theme_id", None) if settings else None
        if active_theme:
            result["active_theme"] = active_theme

        return result

    # Results from the state read tools ship to the cloud and persist in AI
    # conversation history, so they apply the same exclusion the state relay
    # does: cloud-internal (system.cloud.*) and ISC peer (isc.*) state never
    # leaves the box.

    async def _get_project_state(self, input: dict) -> Any:
        return {
            k: v for k, v in self._agent.state.snapshot().items()
            if not is_cloud_excluded_key(k)
        }

    async def _get_state_value(self, input: dict) -> Any:
        key = input.get("key", "")
        if is_cloud_excluded_key(key):
            return {
                "error": f"State key '{key}' is internal (system.cloud.* and "
                         f"isc.* state is never sent to the cloud)"
            }
        value = self._agent.state.get(key)
        return {"key": key, "value": value}

    async def _get_state_history(self, input: dict) -> Any:
        try:
            count = int(input.get("count", 50))
        except (TypeError, ValueError):
            return {"error": "count must be an integer"}
        return [
            entry for entry in self._agent.state.get_history(count)
            if not is_cloud_excluded_key(entry["key"])
        ]

    async def _update_project_metadata(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        # The metadata reconcile updates system.project_name and the mDNS
        # advertised name, which the old direct save left stale.
        changed = []

        def mutate(project):
            if "name" in input:
                project.project.name = input["name"]
                changed.append("name")
            if "description" in input:
                project.project.description = input["description"]
                changed.append("description")

            if not changed:
                raise ToolEditError(
                    {"error": "No fields to update. Provide 'name' and/or 'description'."}
                )

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "updated", "changed": changed}
