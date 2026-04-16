"""Mixin for AI tool handlers that manage macros, variables, triggers, and state."""

from typing import Any

from server.utils.logger import get_logger

log = get_logger(__name__)


class MacroToolsMixin:
    """Macro, variable, trigger, and state management tools."""

    async def _get_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        macro_id = input.get("macro_id", "")
        for m in engine.project.macros:
            if m.id == macro_id:
                return m.model_dump(mode="json")
        return {"error": f"Macro '{macro_id}' not found"}

    async def _list_triggers(self, input: dict) -> Any:
        engine = self._get_engine()
        if engine and engine.triggers:
            return engine.triggers.list_triggers()
        return []

    async def _add_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        if not var_id:
            return {"error": "Variable ID is required"}
        if any(v.id == var_id for v in engine.project.variables):
            return {"error": f"Variable '{var_id}' already exists"}

        var_type = input.get("type", "string")
        default = input.get("default")
        from server.cloud.ai_tool_handler import _validate_variable
        err = _validate_variable(var_type, default)
        if err:
            return {"error": f"Variable '{var_id}': {err}"}

        from server.core.project_loader import VariableConfig, save_project
        new_var = VariableConfig(
            id=var_id,
            type=var_type,
            default=default,
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
        from server.cloud.ai_tool_handler import _validate_variable
        existing = engine.project.variables[var_idx]

        # Validate before mutating
        check_type = input.get("type", existing.type)
        check_default = input.get("default", existing.default)
        if "type" in input or "default" in input:
            err = _validate_variable(check_type, check_default)
            if err:
                return {"error": f"Variable '{var_id}': {err}"}

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

        steps = input.get("steps", [])
        triggers = input.get("triggers", [])
        from server.cloud.ai_tool_handler import _validate_macro
        err = _validate_macro(steps, triggers, engine.project)
        if err:
            return {"error": f"Macro '{macro_id}': {err}"}

        from server.core.project_loader import MacroConfig, save_project
        new_macro = MacroConfig(
            id=macro_id,
            name=input.get("name", macro_id),
            steps=steps,
            triggers=triggers,
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
        from server.cloud.ai_tool_handler import _validate_macro
        existing = engine.project.macros[macro_idx]
        steps = input["steps"] if "steps" in input else existing.steps
        triggers = input["triggers"] if "triggers" in input else existing.triggers
        # Only validate fields that are being changed
        if "steps" in input or "triggers" in input:
            err = _validate_macro(
                input.get("steps", []) if "steps" in input else [],
                input.get("triggers", []) if "triggers" in input else [],
                engine.project,
            )
            if err:
                return {"error": f"Macro '{macro_id}': {err}"}

        updated = MacroConfig(
            id=macro_id,
            name=input.get("name", existing.name),
            steps=steps,
            triggers=triggers,
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

    async def _execute_macro(self, input: dict) -> Any:
        macro_id = input.get("macro_id", "")
        engine = self._get_engine()
        if engine and engine.macros:
            try:
                await engine.macros.execute(macro_id)
            except ValueError as e:
                return {"error": str(e)}
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
        from server.cloud.ai_tool_handler import _validate_state_key
        err = _validate_state_key(key)
        if err:
            return {"error": err}
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

    async def _notify_project_changed(self) -> None:
        """Broadcast project change to connected IDE clients.

        Called by handlers that modify the project but don't trigger a full
        reload (e.g., add_device, variable tools). Handlers that DO reload
        get this broadcast automatically via engine.reload_project().
        """
        engine = self._get_engine()
        if engine and hasattr(engine, "broadcast_ws"):
            await engine.broadcast_ws({"type": "project.reloaded"})

    async def _check_references(self, input: dict) -> Any:
        ref_type = input.get("type", "")
        ref_id = input.get("id", "")
        if not ref_type or not ref_id:
            return {"error": "type and id are required"}
        if ref_type not in ("macro", "device", "variable", "script"):
            return {"error": f"Invalid type: {ref_type}. Must be macro, device, variable, or script."}

        refs = self._find_references(ref_type, ref_id)
        return {"type": ref_type, "id": ref_id, "referenced_by": refs}

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
                            actions = binding if isinstance(binding, list) else [binding] if isinstance(binding, dict) else []
                            for act in actions:
                                if isinstance(act, dict) and act.get("action") == "macro" and act.get("macro") == ref_id:
                                    result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
                                    break
            # Check scripts
            for s in p.scripts:
                try:
                    from server.config import BASE_DIR
                    script_path = BASE_DIR / "projects" / "default" / "scripts" / s.file
                    if script_path.exists():
                        content = script_path.read_text(encoding="utf-8")
                        if ref_id in content:
                            result["scripts"].append({"script_id": s.id, "file": s.file})
                except (OSError, UnicodeDecodeError):
                    log.debug("Failed to read script '%s' for reference check", s.file)

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
                            actions = binding if isinstance(binding, list) else [binding] if isinstance(binding, dict) else []
                            for act in actions:
                                if isinstance(act, dict) and act.get("device") == ref_id:
                                    result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
                                    break
            # Check scripts
            for s in p.scripts:
                try:
                    from server.config import BASE_DIR
                    script_path = BASE_DIR / "projects" / "default" / "scripts" / s.file
                    if script_path.exists():
                        content = script_path.read_text(encoding="utf-8")
                        if ref_id in content:
                            result["scripts"].append({"script_id": s.id, "file": s.file})
                except (OSError, UnicodeDecodeError):
                    log.debug("Failed to read script '%s' for reference check", s.file)

        elif ref_type == "variable":
            state_key = f"var.{ref_id}"
            # Check UI bindings
            for page in p.ui.pages:
                for el in page.elements:
                    bindings = el.bindings if hasattr(el, "bindings") and el.bindings else {}
                    if isinstance(bindings, dict):
                        for slot, binding in bindings.items():
                            actions = binding if isinstance(binding, list) else [binding] if isinstance(binding, dict) else []
                            for act in actions:
                                if isinstance(act, dict) and act.get("key") in (ref_id, state_key):
                                    result["bindings"].append({"page_id": page.id, "element_id": el.id, "slot": slot})
                                    break
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
                except (OSError, UnicodeDecodeError):
                    log.debug("Failed to read script '%s' for reference check", s.file)

        # Remove empty lists
        return {k: v for k, v in result.items() if v}
