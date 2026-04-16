"""Mixin for AI tool handlers that read and update project-level data."""

from typing import Any


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
            from server.utils.logger import get_logger
            log = get_logger(__name__)
            log.warning("Failed to list plugins for AI status", exc_info=True)

        # Active theme
        active_theme = getattr(p.ui, "theme", None)
        if active_theme:
            result["active_theme"] = active_theme

        return result

    async def _get_project_state(self, input: dict) -> Any:
        return self._agent.state.snapshot()

    async def _get_state_value(self, input: dict) -> Any:
        key = input.get("key", "")
        value = self._agent.state.get(key)
        return {"key": key, "value": value}

    async def _get_state_history(self, input: dict) -> Any:
        count = input.get("count", 50)
        return self._agent.state.get_history(count)

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
