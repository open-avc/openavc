"""Mixin for AI tool handlers that manage UI pages and elements."""

from typing import Any

from server.cloud.tools import ToolEditError, apply_tool_edit

# State-change sources a simulated UI action can never itself produce. They're
# excluded from _simulate_ui_action's captured effects so concurrent event-loop
# activity (system metrics/heartbeat, other AI tools, cloud pushes, ISC peers,
# discovery) isn't misattributed to the action. Device polling shares the
# device.<id> source with real command effects, so it can't be filtered here
# without also hiding the action's genuine device changes.
_SIMULATE_IGNORED_SOURCES = frozenset({
    "heartbeat", "system", "cloud", "ai", "isc", "discovered",
})


# Geometry the model is not allowed to speak any more. 0.8.0 replaced whole-cell
# grid coordinates with percentages, and a stale prompt talking to a current
# instance would otherwise have its `grid_area` quietly parked in the
# forward-compat extras -- the element would draw full-page and nothing would
# say why. Named keys, named replacement, said out loud.
_RETIRED_GEOMETRY = {
    "grid_area": "placement {x, y, w, h} (percentages of the parent box)",
    "grid": "layouts[] with placements, plus the authoring-only snap increment",
    "grid_gap": "nothing -- pages no longer have a gap",
}

# The whole page, as a box. What a page-level element's percentages are of.
_PAGE_BOX = {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}


def _rounded_placement(box: Any) -> Any:
    """A Placement quantized to 4 decimal places, exactly as the Builder stores.

    Percentages are stored to 4dp at every write path (GEOMETRY_PRECISION in
    uiBuilderHelpers.ts); an unrounded value is the float jitter that dirties
    the save-reconcile diff and arms phantom autosaves. The Builder quantizes
    with JS Math.round, which rounds a half toward +infinity, while Python's
    round() is banker's -- so mirror the JS arithmetic, or the same reparent
    stores different bytes depending on who performed it.
    """
    import math

    from server.core.project_loader import Placement

    data = box.model_dump() if hasattr(box, "model_dump") else dict(box)
    for key in ("x", "y", "w", "h"):
        v = data.get(key)
        if not isinstance(v, bool) and isinstance(v, (int, float)):
            data[key] = math.floor(v * 10_000 + 0.5) / 10_000
    return Placement(**data)


def _scrub_navigate_actions(el: Any, page_id: str) -> None:
    """Drop navigate actions targeting a deleted page from every do-slot.

    Mirrors the Builder's scrub on page delete: every interaction slot, array
    or legacy single-object shape alike. One spelling to check -- the binding
    action is `ui.navigate`, same as the macro step and the WS frame.
    """
    bindings = el.bindings if isinstance(el.bindings, dict) else None
    do_map = bindings.get("do") if bindings else None
    if not isinstance(do_map, dict):
        return

    def dead(action: Any) -> bool:
        return (
            isinstance(action, dict)
            and action.get("action") == "ui.navigate"
            and action.get("page") == page_id
        )

    for slot in list(do_map.keys()):
        raw = do_map[slot]
        if isinstance(raw, list):
            kept = [a for a in raw if not dead(a)]
            if len(kept) != len(raw):
                if kept:
                    do_map[slot] = kept
                else:
                    del do_map[slot]
        elif dead(raw):
            del do_map[slot]


def _validate_parent(page: Any, element_id: str, new_parent_id: str) -> None:
    """The rules for who may hold whom, shared by every door that sets `parent`.

    They mirror the Builder's canReparent: the container must exist, must BE a
    container, and must not be the element itself or anything inside it. A
    parent the Builder refuses to create is also one its container dropdown
    cannot express or repair, so letting it in here strands the project.
    """
    by_id = {el.id: el for el in page.elements}
    parent = by_id.get(new_parent_id)
    if parent is None:
        raise ToolEditError({
            "error": f"Container '{new_parent_id}' is not an element on page '{page.id}'"
        })
    if parent.type != "group":
        raise ToolEditError({
            "error": f"'{new_parent_id}' is a {parent.type}, not a container -- "
                     f"only a group element can hold children"
        })
    if new_parent_id == element_id or _is_descendant(by_id, new_parent_id, element_id):
        raise ToolEditError({
            "error": f"Element '{element_id}' cannot be placed inside itself or its own contents"
        })


def _primary_layout(page: Any) -> Any:
    """The layout a geometry edit lands in when the caller names no other.

    The loader guarantees a page has exactly one primary, so this always finds
    something; the fallback only exists for a page built by hand in a test.
    """
    for layout in page.layouts:
        if layout.primary:
            return layout
    return page.layouts[0]


def _resolve_layout(page: Any, layout_id: Any) -> Any:
    """The named arrangement, or the primary when none is named."""
    if not layout_id:
        return _primary_layout(page)
    for layout in page.layouts:
        if layout.id == layout_id:
            return layout
    raise ToolEditError({
        "error": f"Layout '{layout_id}' not found on page '{page.id}'. "
                 f"Available: {', '.join(lay.id for lay in page.layouts)}"
    })


def _layout_chain(page: Any, layout_id: str) -> list:
    """The layouts feeding a chosen one, base first.

    A variant stores only what moved, so reading its geometry means folding in
    whatever it inherits. The seen-set is a cycle guard: a hand-edited project
    can point two layouts at each other and every reader still has to answer.
    Mirrors the panel runtime's `_selectLayout` and the builder's `layoutChain`.
    """
    by_id = {lay.id: lay for lay in page.layouts}
    chain: list = []
    seen: set[str] = set()
    cursor = by_id.get(layout_id)
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        chain.insert(0, cursor)
        cursor = by_id.get(cursor.inherits) if cursor.inherits else None
    return chain


def _resolved_placements(page: Any, layout_id: str) -> dict:
    """One arrangement's boxes with its inherits chain folded down."""
    placements: dict = {}
    for layout in _layout_chain(page, layout_id):
        placements.update(layout.placements)
    return placements


def _absolute_placements(page: Any, layout_id: str) -> dict:
    """Every element's box in PAGE percentages, container nesting flattened.

    A child's stored percentages are of its container, so boxes under different
    parents cannot be compared -- 20% wide means two different widths on screen.
    Anything that has to reason about where things actually sit works here and
    converts back on the way out.
    """
    placements = _resolved_placements(page, layout_id)
    by_id = {el.id: el for el in page.elements}
    out: dict = {}

    def resolve(el_id: str, seen: frozenset) -> dict | None:
        if el_id in out:
            return out[el_id]
        place = placements.get(el_id)
        if place is None:
            return None
        named = getattr(by_id.get(el_id), "parent", None)
        # A parent that is missing, self-referential or already on the chain is
        # treated as no parent -- a hand-edited project still has to draw.
        parent_id = named if (named and named != el_id and named in by_id and named not in seen) else None
        if not parent_id:
            out[el_id] = {"x": place.x, "y": place.y, "w": place.w, "h": place.h}
            return out[el_id]
        base = resolve(parent_id, seen | {parent_id})
        out[el_id] = {
            "x": base["x"] + (place.x / 100) * base["w"],
            "y": base["y"] + (place.y / 100) * base["h"],
            "w": (place.w / 100) * base["w"],
            "h": (place.h / 100) * base["h"],
        } if base else {"x": place.x, "y": place.y, "w": place.w, "h": place.h}
        return out[el_id]

    for el in page.elements:
        resolve(el.id, frozenset([el.id]))
    return out


def _reparent_element(page: Any, element_id: str, new_parent_id: str | None) -> None:
    """Move an element into a container (or back out) without moving it on screen.

    A child's percentages are of its container, so changing the parent and
    nothing else teleports it -- 20% of a quarter-page box is not 20% of the
    page. The box is converted out to page space against the old parent and back
    in against the new one, in **every** arrangement the page carries, so the
    layout the caller is not looking at does not shift either. This is the same
    conversion the Builder does when you drag a control into a container.
    """

    by_id = {el.id: el for el in page.elements}
    element = by_id.get(element_id)
    if element is None or (element.parent or None) == new_parent_id:
        return
    if new_parent_id is not None:
        _validate_parent(page, element_id, new_parent_id)

    primary_id = _primary_layout(page).id
    rewritten: list[tuple[Any, dict]] = []
    for layout in page.layouts:
        absolute = _absolute_placements(page, layout.id)
        base = absolute.get(new_parent_id) if new_parent_id else dict(_PAGE_BOX)
        if not base or base["w"] <= 0 or base["h"] <= 0:
            continue
        # A variant only gets a delta if it already had one; inventing entries
        # there would pin boxes that were happily inheriting from the primary.
        if layout.id != primary_id and element_id not in layout.placements:
            continue
        box = absolute.get(element_id)
        if box is None:
            continue
        rewritten.append((layout, {
            "x": (box["x"] - base["x"]) / base["w"] * 100,
            "y": (box["y"] - base["y"]) / base["h"] * 100,
            "w": box["w"] / base["w"] * 100,
            "h": box["h"] / base["h"] * 100,
        }))

    element.parent = new_parent_id
    for layout, box in rewritten:
        layout.placements[element_id] = _rounded_placement(box)


def _is_descendant(by_id: dict, candidate_id: str, ancestor_id: str) -> bool:
    """Is `candidate_id` somewhere inside `ancestor_id`? Cycle-safe."""
    seen: set[str] = set()
    cursor = by_id.get(candidate_id)
    while cursor is not None and cursor.id not in seen:
        seen.add(cursor.id)
        if cursor.parent == ancestor_id:
            return True
        cursor = by_id.get(cursor.parent) if cursor.parent else None
    return False


def _reject_retired_geometry(data: dict, what: str) -> None:
    """Refuse pre-0.8.0 geometry loudly instead of storing it as an extra."""
    for key, replacement in _RETIRED_GEOMETRY.items():
        if key in data:
            raise ToolEditError({
                "error": f"{what}: '{key}' was removed in project format 0.8.0. Use {replacement}."
            })


def _take_placement(el_data: dict, what: str) -> dict | None:
    """Lift an element's box out of its definition, where the box no longer lives.

    Geometry moved off the element and onto the page's layouts in 0.8.0 -- the
    same control can sit in two different places in the landscape and portrait
    arrangements without being duplicated. Callers still describe an element and
    its box in one breath, so the tools split them back apart here.
    """
    _reject_retired_geometry(el_data, what)
    place = el_data.pop("placement", None)
    if place is None:
        return None
    if not isinstance(place, dict):
        raise ToolEditError({
            "error": f"{what}: 'placement' must be an object {{x, y, w, h}}, got {type(place).__name__}"
        })
    return place


def _apply_layouts(page: Any, layouts_input: Any) -> None:
    """Add or edit a page's arrangements.

    An entry naming an existing layout edits it; anything else is a new
    arrangement. Placements merge (a variant is deltas -- sending one moved
    control must not blank the rest) while `hidden` replaces, because it is a
    set and "hide exactly these" is the only unambiguous reading of a list.
    """
    from server.core.project_loader import Layout, normalize_primary_layout

    if not isinstance(layouts_input, list):
        raise ToolEditError({"error": "'layouts' must be an array of layout objects"})

    by_id = {lay.id: lay for lay in page.layouts}
    primary_id = _primary_layout(page).id

    def check_inherits(layout_id: str, target: Any) -> None:
        # A dangling inherits does not error anywhere downstream -- the variant
        # silently becomes its own chain root and everything without a delta
        # falls to default boxes. Refuse it at the door, by name.
        if target is None:
            return
        if target == layout_id:
            raise ToolEditError({"error": f"Layout '{layout_id}' cannot inherit from itself"})
        if target not in by_id:
            raise ToolEditError({
                "error": f"Layout '{layout_id}': inherits '{target}', which is not an "
                         f"arrangement on this page. Available: {', '.join(by_id)}"
            })

    def check_orientation(layout_id: str, orientation: Any) -> None:
        # One arrangement per orientation is the Builder's invariant -- the
        # runtime draws the first match, so a second one is unreachable and
        # makes the canvas and the panel disagree.
        taken = next(
            (lay.id for lay in page.layouts
             if lay.orientation == orientation and lay.id != layout_id),
            None,
        )
        if taken is not None:
            raise ToolEditError({
                "error": f"Layout '{layout_id}': there is already a {orientation} "
                         f"arrangement ('{taken}'). Edit that one instead."
            })

    for entry in layouts_input:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ToolEditError({"error": "Each layout needs an 'id'"})
        layout_id = entry["id"]
        placements = entry.get("placements") or {}
        if not isinstance(placements, dict):
            raise ToolEditError({"error": f"Layout '{layout_id}': 'placements' must be an object keyed by element id"})
        boxes = {el_id: _rounded_placement(box) for el_id, box in placements.items()}
        existing = by_id.get(layout_id)
        if existing is None:
            # The primary is the page's fallback for an unmatched screen and the
            # layout every variant inherits from. Handing that role to a new
            # arrangement is a bigger change than "add a portrait version", so
            # a new layout is always a variant.
            fields = {k: v for k, v in entry.items() if k not in ("placements", "hidden", "primary")}
            fields.setdefault("inherits", primary_id)
            check_inherits(layout_id, fields.get("inherits"))
            new_layout = Layout(**fields, placements=boxes, hidden=entry.get("hidden") or [])
            check_orientation(layout_id, new_layout.orientation)
            new_layout.primary = False
            page.layouts.append(new_layout)
            by_id[layout_id] = new_layout
            continue
        if entry.get("primary") is not None and bool(entry["primary"]) != existing.primary:
            raise ToolEditError({
                "error": f"Layout '{layout_id}': which layout is primary cannot be changed here. "
                         f"'{primary_id}' is the arrangement an unmatched screen falls back to."
            })
        if "inherits" in entry:
            check_inherits(layout_id, entry["inherits"])
        if "orientation" in entry:
            check_orientation(layout_id, entry["orientation"])
        for field in ("orientation", "inherits"):
            if field in entry:
                setattr(existing, field, entry[field])
        existing.placements.update(boxes)
        if "hidden" in entry:
            existing.hidden = list(entry["hidden"] or [])

    # Two edits in one call can each be legal and still tie the chains in a
    # loop (A inherits B, then B inherits A). Both renderers cycle-guard, so
    # nothing crashes -- but the layouts stop inheriting and the file is
    # permanently odd. Walk every chain before accepting the batch.
    for lay in page.layouts:
        seen: set[str] = set()
        cursor = lay
        while cursor is not None and cursor.id not in seen:
            seen.add(cursor.id)
            cursor = by_id.get(cursor.inherits) if cursor.inherits else None
        if cursor is not None:
            raise ToolEditError({
                "error": f"Layout inheritance loops: '{lay.id}' eventually inherits from itself"
            })

    page.layouts = normalize_primary_layout(page.layouts)


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

        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import UIPage
        elements = input.get("elements", [])

        def mutate(project):
            if any(p.id == page_id for p in project.ui.pages):
                raise ToolEditError({"error": f"UI page '{page_id}' already exists"})
            _reject_retired_geometry(input, f"Page '{page_id}'")

            # Normalize + validate inline-element bindings the same way
            # _add_ui_elements does — otherwise a page-with-elements created in one
            # call yields bindings that were never validated (buttons silently do
            # nothing), while the identical elements added via add_ui_elements work.
            boxes: dict[str, dict] = {}
            for el_data in elements:
                if not isinstance(el_data, dict):
                    continue
                el_id = el_data.get("id", "?")
                place = _take_placement(el_data, f"Element '{el_id}'")
                if place is not None:
                    boxes[el_id] = place
                if isinstance(el_data.get("bindings"), dict):
                    el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                    err = _validate_bindings(el_data["bindings"], project)
                    if err:
                        raise ToolEditError({"error": f"Element '{el_id}': {err}"})

            new_page = UIPage(
                id=page_id,
                name=input.get("name", page_id),
                snap=input.get("snap", {}),
                elements=elements,
                layouts=input.get("layouts", []),
            )
            # Elements are shared by every arrangement; their boxes are not, so
            # a new control's box belongs in the primary -- the one every other
            # layout inherits from.
            primary = _primary_layout(new_page)
            for el_id, box in boxes.items():
                primary.placements[el_id] = _rounded_placement(box)
            # A UI-only change just swaps the project and pushes the new
            # ui.definition to connected panels.
            project.ui.pages.append(new_page)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "created", "id": page_id}

    async def _update_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        changed = []

        def mutate(project):
            page = None
            for p in project.ui.pages:
                if p.id == page_id:
                    page = p
                    break
            if page is None:
                raise ToolEditError({"error": f"UI page '{page_id}' not found"})

            _reject_retired_geometry(input, f"Page '{page_id}'")
            if "name" in input:
                page.name = input["name"]
                changed.append("name")
            if "layouts" in input:
                _apply_layouts(page, input["layouts"])
                changed.append("layouts")
            if "snap" in input:
                from server.core.project_loader import SnapConfig
                # Partial merge: keep omitted fields (don't reset the increment
                # to defaults) and preserve any forward-compat keys.
                page.snap = _merge_forward_compat(page.snap, SnapConfig, input["snap"])
                changed.append("snap")
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
                raise ToolEditError({"error": "No fields to update"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "updated", "page_id": page_id, "changed": changed}

    async def _delete_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        element_count = 0

        def mutate(project):
            nonlocal element_count
            # Count elements being removed
            for pg in project.ui.pages:
                if pg.id == page_id:
                    element_count = len(pg.elements)
                    break

            original_count = len(project.ui.pages)
            project.ui.pages = [p for p in project.ui.pages if p.id != page_id]
            if len(project.ui.pages) == original_count:
                raise ToolEditError({"error": f"UI page '{page_id}' not found"})

            # Scrub everything that pointed at the page -- the same set the
            # Builder's page delete scrubs, so a page deleted by the AI does
            # not leave masters pinned to a ghost and nav buttons that go
            # nowhere.
            for master in project.ui.master_elements:
                if isinstance(master.pages, list) and page_id in master.pages:
                    remaining = [pid for pid in master.pages if pid != page_id]
                    # A master shown nowhere is invisible with no way to see
                    # why; every-page is the least surprising repair.
                    master.pages = remaining if remaining else "*"
            for pg in project.ui.pages:
                for el in pg.elements:
                    if el.type == "page_nav" and el.target_page == page_id:
                        el.target_page = ""
                    _scrub_navigate_actions(el, page_id)
            for macro in project.macros:
                for trigger in macro.triggers or []:
                    if trigger.conditions:
                        trigger.conditions = [
                            c for c in trigger.conditions
                            if not (getattr(c, "key", None) == "system.current_page"
                                    and getattr(c, "value", None) == page_id)
                        ]
            if project.ui.settings.idle_page == page_id:
                project.ui.settings.idle_page = (
                    project.ui.pages[0].id if project.ui.pages else ""
                )
            for group in project.ui.page_groups:
                if page_id in group.pages:
                    group.pages = [pid for pid in group.pages if pid != page_id]

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        result: dict = {"status": "deleted", "id": page_id}
        if element_count > 0:
            result["impact"] = {"elements_removed": element_count}
        return result

    async def _add_ui_elements(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        elements = input.get("elements", [])
        if not elements:
            return {"error": "No elements provided"}

        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import UIElement

        def mutate(project):
            page = None
            for p in project.ui.pages:
                if p.id == page_id:
                    page = p
                    break
            if page is None:
                raise ToolEditError({"error": f"UI page '{page_id}' not found"})

            # Check for duplicate IDs
            existing_ids = {el.id for el in page.elements}
            for el in elements:
                el_id = el.get("id", "")
                if el_id in existing_ids:
                    raise ToolEditError({"error": f"Element '{el_id}' already exists on page '{page_id}'"})

            # A new control exists in every arrangement the moment it exists at
            # all, so its box goes in the primary -- write it into a variant and
            # it would have no box anywhere else.
            primary = _primary_layout(page)
            for el_data in elements:
                el_id = el_data.get("id", "?")
                place = _take_placement(el_data, f"Element '{el_id}'")
                if "bindings" in el_data and isinstance(el_data["bindings"], dict):
                    el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                    err = _validate_bindings(el_data["bindings"], project)
                    if err:
                        raise ToolEditError({"error": f"Element '{el_id}': {err}"})
                element = UIElement(**el_data)
                if element.parent is not None:
                    holder = next((e for e in page.elements if e.id == element.parent), None)
                    if holder is None:
                        raise ToolEditError({
                            "error": f"Element '{el_id}': container '{element.parent}' "
                                     f"is not an element on page '{page_id}'"
                        })
                    if holder.type != "group":
                        raise ToolEditError({
                            "error": f"Element '{el_id}': '{element.parent}' is a "
                                     f"{holder.type}, not a container -- only a group "
                                     f"element can hold children"
                        })
                page.elements.append(element)
                if place is not None:
                    primary.placements[element.id] = _rounded_placement(place)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        added_ids = [el.get("id", "") for el in elements]
        return {"status": "created", "page_id": page_id, "element_ids": added_ids}

    async def _update_ui_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")

        def mutate(project):
            # Find the element across all pages
            target_el = None
            target_page = None
            for page in project.ui.pages:
                for el in page.elements:
                    if el.id == element_id:
                        target_el = el
                        target_page = page
                        break
                if target_el:
                    break

            if target_el is None:
                raise ToolEditError({"error": f"UI element '{element_id}' not found"})
            _reject_retired_geometry(input, f"Element '{element_id}'")
            # Geometry and per-layout visibility belong to one arrangement. The
            # primary is where a box lives unless the caller is authoring a
            # variant and says so.
            layout = _resolve_layout(target_page, input.get("layout_id"))

            # Validate bindings BEFORE mutating any fields (avoid partial updates).
            # A non-dict bindings value would bypass the validator AND Pydantic
            # (UIElement has no validate_assignment), persisting a structurally
            # invalid element — reject it instead of assigning it raw.
            if "bindings" in input:
                from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
                bindings = input["bindings"]
                if not isinstance(bindings, dict):
                    raise ToolEditError({
                        "error": f"Element '{element_id}': 'bindings' must be an object, "
                                 f"got {type(bindings).__name__}"
                    })
                bindings = _normalize_bindings(bindings)
                err = _validate_bindings(bindings, project)
                if err:
                    raise ToolEditError({"error": f"Element '{element_id}': {err}"})

            if "label" in input:
                target_el.label = input["label"]
            if "text" in input:
                target_el.text = input["text"]
            if "parent" in input:
                # Percentages are of the parent box, so changing only the parent
                # teleports the element. The conversion runs across every
                # arrangement -- including when the caller also states a box,
                # because the stated box lands in ONE layout and a variant with
                # its own delta would otherwise keep a box still expressed in
                # the old parent's space. The same guards apply either way; the
                # old stated-box shortcut skipped them, which let a parent
                # cycle into the project.
                new_parent = input["parent"] or None
                _reparent_element(target_page, element_id, new_parent)
            if "placement" in input:
                from server.core.project_loader import Placement
                # Partial merge: keep omitted fields (don't snap x/y back to 0)
                # and preserve any forward-compat keys. The placement lives on
                # the page's layout, not the element. Runs after any reparent,
                # so the stated box overrides the conversion in the resolved
                # layout and the other arrangements keep the converted one.
                existing = _resolved_placements(target_page, layout.id).get(target_el.id, Placement())
                layout.placements[target_el.id] = _rounded_placement(_merge_forward_compat(
                    existing, Placement, input["placement"],
                ))
            if "hidden" in input:
                # Hiding is per-arrangement: a portrait variant can drop a wide
                # banner that will not fit without touching the landscape panel.
                hide = bool(input["hidden"])
                if hide:
                    if element_id not in layout.hidden:
                        layout.hidden.append(element_id)
                else:
                    # The runtime unions `hidden` down the inherits chain, so a
                    # variant can add a hide but never take back an inherited
                    # one. Say which layout it came from rather than accept an
                    # edit that changes nothing.
                    for ancestor in _layout_chain(target_page, layout.id):
                        if ancestor.id != layout.id and element_id in ancestor.hidden:
                            raise ToolEditError({
                                "error": f"Element '{element_id}' is hidden by layout "
                                         f"'{ancestor.id}', which '{layout.id}' inherits from. "
                                         f"Unhide it there, or the whole chain keeps it hidden."
                            })
                    layout.hidden = [i for i in layout.hidden if i != element_id]
            if "aspect_lock" in input:
                target_el.aspect_lock = input["aspect_lock"]
            if "style" in input:
                target_el.style = input["style"]
            if "bindings" in input:
                target_el.bindings = bindings

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

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

        def mutate(project):
            for page in project.ui.pages:
                before_ids = {el.id for el in page.elements}
                doomed = ids_set & before_ids
                if not doomed:
                    continue
                # Re-home anything left inside a container that is going away,
                # while the tree is still intact enough to measure -- the same
                # conversion a reparent does, so nothing jumps when its
                # container disappears.
                by_id = {el.id: el for el in page.elements}
                for el in page.elements:
                    if el.id in doomed or not el.parent or el.parent not in doomed:
                        continue
                    survivor = el.parent
                    seen: set[str] = set()
                    while survivor in doomed and survivor not in seen:
                        seen.add(survivor)
                        survivor = getattr(by_id.get(survivor), "parent", None)
                    _reparent_element(page, el.id, survivor if survivor not in doomed else None)

                page.elements = [el for el in page.elements if el.id not in ids_set]
                # Geometry lives on the layouts now, so a deleted element leaves
                # entries behind unless they are cleared here.
                for layout in page.layouts:
                    for el_id in doomed:
                        layout.placements.pop(el_id, None)
                    layout.hidden = [i for i in layout.hidden if i not in doomed]
                deleted_ids.extend(doomed)

            if not deleted_ids:
                raise ToolEditError({"error": "No matching elements found"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "deleted", "element_ids": sorted(deleted_ids)}

    async def _add_master_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("id", "")
        if not element_id:
            return {"error": "Element ID is required"}

        from server.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from server.core.project_loader import MasterElement
        el_data = {k: v for k, v in input.items() if k != "id"}
        el_data["id"] = element_id

        def mutate(project):
            # A master is valid on every page it appears on, so its box is a
            # percentage of the VIEWPORT keyed by orientation -- it borrows no
            # page's layout, which is exactly what used to make the same master
            # land somewhere different on each page.
            _reject_retired_geometry(el_data, f"Master element '{element_id}'")
            if "placement" in el_data:
                raise ToolEditError({
                    "error": f"Master element '{element_id}': use 'placements' keyed by orientation, "
                             f"e.g. {{\"landscape\": {{\"x\": 2, \"y\": 2, \"w\": 20, \"h\": 8}}}}. "
                             f"A master is not part of any page's layout."
                })
            # Check for ID collision with page elements and existing master elements
            for page in project.ui.pages:
                if any(el.id == element_id for el in page.elements):
                    raise ToolEditError({"error": f"Element '{element_id}' already exists on page '{page.id}'"})
            if any(el.id == element_id for el in project.ui.master_elements):
                raise ToolEditError({"error": f"Master element '{element_id}' already exists"})

            if "bindings" in el_data and isinstance(el_data["bindings"], dict):
                el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                err = _validate_bindings(el_data["bindings"], project)
                if err:
                    raise ToolEditError({"error": f"Master element '{element_id}': {err}"})
            if isinstance(el_data.get("placements"), dict):
                el_data["placements"] = {
                    key: _rounded_placement(box) if isinstance(box, dict) else box
                    for key, box in el_data["placements"].items()
                }
            new_el = MasterElement(**el_data)
            project.ui.master_elements.append(new_el)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return {"status": "created", "id": element_id}

    async def _delete_master_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")

        def mutate(project):
            original_count = len(project.ui.master_elements)
            project.ui.master_elements = [
                el for el in project.ui.master_elements if el.id != element_id
            ]
            if len(project.ui.master_elements) == original_count:
                raise ToolEditError({"error": f"Master element '{element_id}' not found"})

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

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
