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

        from server.core.project_loader import VariableConfig
        new_var = VariableConfig(
            id=var_id,
            type=var_type,
            default=default,
            label=input.get("label", ""),
            dashboard=input.get("dashboard", False),
            persist=input.get("persist", False),
        )
        # Build the change on a copy — apply_project diffs it against the
        # live project, so an in-place edit would reconcile nothing. The
        # variables reconcile seeds var.<id> from the default and registers
        # persistence/bindings/validation.
        project = engine.project.model_copy(deep=True)
        project.variables.append(new_var)
        await engine.apply_project(project)

        return {"status": "created", "id": var_id}

    async def _update_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        from server.cloud.ai_tool_handler import _validate_variable

        # Mutate a copy — the variables reconcile then applies the change
        # (persister keys for a persist flip, seeding for a new default,
        # rebinding/validation), which the old direct save never did.
        project = engine.project.model_copy(deep=True)
        existing = next(
            (v for v in project.variables if v.id == var_id), None
        )
        if existing is None:
            return {"error": f"Variable '{var_id}' not found"}

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

        await engine.apply_project(project)

        return {"status": "updated", "id": var_id}

    async def _delete_variable(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        var_id = input.get("id", "")
        # Collect impact before deleting
        impact = self._find_references("variable", var_id)

        # Mutate a copy — the variables reconcile sweeps the orphaned
        # var.<id> state key and drops it from the persister, which the old
        # direct save left behind.
        project = engine.project.model_copy(deep=True)
        original_count = len(project.variables)
        project.variables = [v for v in project.variables if v.id != var_id]
        if len(project.variables) == original_count:
            return {"error": f"Variable '{var_id}' not found"}

        await engine.apply_project(project)

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

        from server.core.project_loader import MacroConfig
        new_macro = MacroConfig(
            id=macro_id,
            name=input.get("name", macro_id),
            steps=steps,
            triggers=triggers,
            stop_on_error=input.get("stop_on_error", False),
            cancel_group=input.get("cancel_group"),
        )
        # EDIT-origin apply: the macros reconcile reloads macro/trigger
        # definitions without re-firing startup triggers (the old full
        # reload re-fired them on every AI macro edit).
        project = engine.project.model_copy(deep=True)
        project.macros.append(new_macro)
        await engine.apply_project(project)

        return {"status": "created", "id": macro_id}

    async def _update_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        macro_id = input.get("macro_id", "")
        project = engine.project.model_copy(deep=True)
        macro_idx = None
        for i, m in enumerate(project.macros):
            if m.id == macro_id:
                macro_idx = i
                break
        if macro_idx is None:
            return {"error": f"Macro '{macro_id}' not found"}

        from server.core.project_loader import MacroConfig
        from server.cloud.ai_tool_handler import _validate_macro
        from server.cloud.tools.ui_tools import _merge_forward_compat
        existing = project.macros[macro_idx]
        # Only validate fields that are being changed
        if "steps" in input or "triggers" in input:
            err = _validate_macro(
                input.get("steps", []) if "steps" in input else [],
                input.get("triggers", []) if "triggers" in input else [],
                engine.project,
            )
            if err:
                return {"error": f"Macro '{macro_id}': {err}"}

        # Merge onto the existing macro rather than rebuilding from declared
        # fields, so forward-compat (extra='allow') fields a newer platform
        # stored survive the edit.
        patch = {
            k: input[k]
            for k in ("name", "steps", "triggers", "stop_on_error", "cancel_group")
            if k in input
        }
        updated = _merge_forward_compat(existing, MacroConfig, patch)
        project.macros[macro_idx] = updated
        await engine.apply_project(project)

        return {"status": "updated", "id": macro_id}

    async def _delete_macro(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        macro_id = input.get("macro_id", "")
        # Collect impact before deleting
        impact = self._find_references("macro", macro_id)

        project = engine.project.model_copy(deep=True)
        original_count = len(project.macros)
        project.macros = [m for m in project.macros if m.id != macro_id]
        if len(project.macros) == original_count:
            return {"error": f"Macro '{macro_id}' not found"}

        await engine.apply_project(project)

        result: dict = {"status": "deleted", "id": macro_id}
        if impact:
            result["impact"] = impact
        return result

    async def _execute_macro(self, input: dict) -> Any:
        macro_id = input.get("macro_id", "")
        from server.api._engine import _test_call_retry_after
        if _test_call_retry_after(f"macro_execute:{macro_id}") > 0:
            return {"error": "Too many requests — wait a moment before running the same macro again."}
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
        from server.cloud.ai_tool_handler import _validate_state_key, _validate_state_value
        err = _validate_state_key(key)
        if err:
            return {"error": err}
        value = input.get("value")
        # The store enforces the flat-primitive contract itself (and drops
        # offenders), but silently — reject here so the AI gets a clear error
        # instead of a success report for a write that never happened.
        err = _validate_state_value(value)
        if err:
            return {"error": err}
        self._agent.state.set(key, value, source="ai")
        return {"key": key, "value": value}

    async def _test_trigger(self, input: dict) -> Any:
        trigger_id = input.get("trigger_id", "")
        from server.api._engine import _test_call_retry_after
        if _test_call_retry_after(f"test_trigger:{trigger_id}") > 0:
            return {"error": "Too many requests — wait a moment before testing the same trigger again."}
        engine = self._get_engine()
        if engine and engine.triggers:
            ok = await engine.triggers.test_trigger(trigger_id)
            if ok:
                return {"status": "fired", "trigger_id": trigger_id}
            return {"error": f"Trigger '{trigger_id}' not found"}
        return {"error": "Trigger engine not available"}

    async def _check_references(self, input: dict) -> Any:
        ref_type = input.get("type", "")
        ref_id = input.get("id", "")
        if not ref_type or not ref_id:
            return {"error": "type and id are required"}
        if ref_type not in ("macro", "device", "variable", "script"):
            return {"error": f"Invalid type: {ref_type}. Must be macro, device, variable, or script."}

        refs = self._find_references(ref_type, ref_id)
        return {"type": ref_type, "id": ref_id, "referenced_by": refs}

    def _scan_scripts_for_ref(self, engine: Any, ref_id: str) -> list[dict]:
        """Grep the project's scripts for a reference to ``ref_id``.

        The scripts directory lives next to the loaded project file
        (engine.project_path), NOT under a fixed projects/default path —
        every non-dev deployment sets OPENAVC_PROJECT elsewhere, and a wrong
        base dir silently under-reports references before destructive
        deletes. Paths are containment-checked like the scripts API route.
        """
        from server.utils.paths import safe_path_within

        hits: list[dict] = []
        scripts_dir = engine.project_path.parent / "scripts"
        for s in engine.project.scripts:
            try:
                script_path = safe_path_within(scripts_dir, s.file)
                if script_path is None or not script_path.exists():
                    continue
                content = script_path.read_text(encoding="utf-8")
                if ref_id in content:
                    hits.append({"script_id": s.id, "file": s.file})
            except (OSError, UnicodeDecodeError):
                log.debug("Failed to read script '%s' for reference check", s.file)
        return hits

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
            result["scripts"] = self._scan_scripts_for_ref(engine, ref_id)

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
            result["scripts"] = self._scan_scripts_for_ref(engine, ref_id)

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
            result["scripts"] = self._scan_scripts_for_ref(engine, ref_id)

        # Remove empty lists
        return {k: v for k, v in result.items() if v}
