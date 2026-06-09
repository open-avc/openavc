"""Mixin for AI tool handlers that manage UI pages and elements."""

from typing import Any

# State-change sources a simulated UI action can never itself produce. They're
# excluded from _simulate_ui_action's captured effects so concurrent event-loop
# activity (system metrics/heartbeat, other AI tools, cloud pushes, ISC peers,
# discovery) isn't misattributed to the action. Device polling shares the
# device.<id> source with real command effects, so it can't be filtered here
# without also hiding the action's genuine device changes.
_SIMULATE_IGNORED_SOURCES = frozenset({
    "heartbeat", "system", "cloud", "ai", "isc", "discovered",
})


def _merge_forward_compat(existing: Any, model_cls: type, partial: dict) -> Any:
    """Apply a partial update to a forward-compat (extra='allow') sub-model.

    Dumps the existing model, overlays the partial input, then re-validates —
    so omitted fields keep their current values (not the model defaults) and
    any unknown forward-compat keys a newer platform stored survive the
    round-trip, instead of being reset by ``model_cls(**partial)``.
    """
    merged = {**existing.model_dump(), **partial}
    return model_cls(**merged)


class UIToolsMixin:
    """UI page CRUD, element management, master elements, and action simulation."""

    async def _get_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        page_id = input.get("page_id", "")
        for p in engine.project.ui.pages:
            if p.id == page_id:
                return p.model_dump(mode="json")
        return {"error": f"UI page '{page_id}' not found"}

    async def _add_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("id", "")
        if not page_id:
            return {"error": "Page ID is required"}
        if any(p.id == page_id for p in engine.project.ui.pages):
            return {"error": f"UI page '{page_id}' already exists"}

        # Normalize + validate inline-element bindings the same way
        # _add_ui_elements does — otherwise a page-with-elements created in one
        # call yields bindings that were never validated (buttons silently do
        # nothing), while the identical elements added via add_ui_elements work.
        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import UIPage, save_project
        elements = input.get("elements", [])
        for el_data in elements:
            if isinstance(el_data, dict) and isinstance(el_data.get("bindings"), dict):
                el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                err = _validate_bindings(el_data["bindings"], engine.project)
                if err:
                    return {"error": f"Element '{el_data.get('id', '?')}': {err}"}

        new_page = UIPage(
            id=page_id,
            name=input.get("name", page_id),
            grid=input.get("grid", {}),
            elements=elements,
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
            # Partial merge: keep omitted fields (don't reset rows/columns to
            # defaults) and preserve any forward-compat keys.
            page.grid = _merge_forward_compat(page.grid, GridConfig, input["grid"])
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

        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import UIElement, save_project
        for el_data in elements:
            if "bindings" in el_data and isinstance(el_data["bindings"], dict):
                el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                err = _validate_bindings(el_data["bindings"], engine.project)
                if err:
                    return {"error": f"Element '{el_data.get('id', '?')}': {err}"}
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

        # Validate bindings BEFORE mutating any fields (avoid partial updates).
        # A non-dict bindings value would bypass the validator AND Pydantic
        # (UIElement has no validate_assignment), persisting a structurally
        # invalid element — reject it instead of assigning it raw.
        if "bindings" in input:
            from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
            bindings = input["bindings"]
            if not isinstance(bindings, dict):
                return {
                    "error": f"Element '{element_id}': 'bindings' must be an object, "
                             f"got {type(bindings).__name__}"
                }
            bindings = _normalize_bindings(bindings)
            err = _validate_bindings(bindings, engine.project)
            if err:
                return {"error": f"Element '{element_id}': {err}"}

        if "label" in input:
            target_el.label = input["label"]
        if "text" in input:
            target_el.text = input["text"]
        if "grid_area" in input:
            from server.core.project_loader import GridArea
            # Partial merge: keep omitted fields (don't snap col/row back to 1)
            # and preserve any forward-compat keys.
            target_el.grid_area = _merge_forward_compat(target_el.grid_area, GridArea, input["grid_area"])
        if "style" in input:
            target_el.style = input["style"]
        if "bindings" in input:
            target_el.bindings = bindings

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

        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import MasterElement, save_project
        el_data = {k: v for k, v in input.items() if k != "id"}
        el_data["id"] = element_id
        if "bindings" in el_data and isinstance(el_data["bindings"], dict):
            el_data["bindings"] = _normalize_bindings(el_data["bindings"])
            err = _validate_bindings(el_data["bindings"], engine.project)
            if err:
                return {"error": f"Master element '{element_id}': {err}"}
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
            # Mirror the real navigation path (engine.handle_ui_event): emit the
            # page event AND broadcast ui.navigate — panels switch page only on
            # the WS broadcast, so the emit alone would report success while no
            # panel actually moves.
            await engine.events.emit(f"ui.page.{page_id}")
            await engine.broadcast_ws({"type": "ui.navigate", "page_id": page_id})
            return {"success": True, "action": "navigate", "page_id": page_id, "state_changes": []}

        if not element_id:
            return {"error": "element_id is required for this action"}

        # Capture state changes during action execution. The '*' subscription
        # sees all event-loop-wide activity during the await, so drop changes
        # from sources the action can't have caused (system metrics, other
        # tools, ISC peers, discovery) — otherwise they're misattributed to it.
        state_changes = []
        def on_change(key, old_val, new_val, source):
            if source in _SIMULATE_IGNORED_SOURCES:
                return
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
