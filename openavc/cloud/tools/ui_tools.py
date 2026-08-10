"""Mixin for AI tool handlers that manage UI pages and elements."""

from typing import Any

from openavc.cloud.tools import ToolEditError, apply_tool_edit
from openavc.core.ui_events import resolve_press
from openavc.ui.page_geometry import (
    PAGE_BOX,
    absolute_placements,
    layout_chain,
    primary_layout,
    resolved_placements,
)
from openavc.ui.page_references import reference_findings
from openavc.ui.page_review import review_master_element, review_page
from openavc.utils.logger import get_logger

log = get_logger(__name__)

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


def _find_ui_element(engine: Any, element_id: str) -> Any:
    """The element an interaction would reach, page elements then masters.

    Same order as ``ui_events._find_element``, so what this reports and what
    that dispatches are always the same element.
    """
    project = getattr(engine, "project", None)
    ui = getattr(project, "ui", None)
    if ui is None:
        return None
    for page in getattr(ui, "pages", None) or []:
        for candidate in page.elements:
            if candidate.id == element_id:
                return candidate
    for master in getattr(ui, "master_elements", None) or []:
        if master.id == element_id:
            return master
    return None


def _nothing_ran_note(engine: Any, element_id: str, action: str, dispatched: list) -> dict:
    """Say why an interaction did nothing, when it did nothing.

    ``{"success": true, "state_changes": []}`` is what a working device command
    looks like AND what a button with no binding at all looks like, which is how
    a simulation came back reading like a failure for a control that fired. Now
    that the dispatched list separates those two, the remaining silence is worth
    a sentence rather than an empty array to interpret.
    """
    if dispatched:
        return {}
    # First match wins, the way ui_events._find_element resolves it -- so this
    # describes the element the event actually reached.
    element = _find_ui_element(engine, element_id)
    if element is None:
        return {
            "note": f"No element '{element_id}' is on any page or in the master "
                    f"elements, so nothing ran."
        }
    bindings = element.bindings if isinstance(element.bindings, dict) else {}
    do_map = bindings.get("do") if isinstance(bindings.get("do"), dict) else {}
    if not do_map.get(action):
        slots = sorted(slot for slot, actions in do_map.items() if actions)
        has = f" It has: {', '.join(slots)}." if slots else " It has no do bindings at all."
        return {"note": f"'{element_id}' has nothing bound to {action}.{has}"}
    return {"note": f"'{element_id}' has a do.{action} binding, but no action in it ran."}


#: Device properties the PLATFORM sets, which no driver declares.
#:
#: `device.<id>.online` is the single commonest binding on any panel and it
#: appears in no DRIVER_INFO anywhere, so a property check that did not know
#: these would fire on the most correct page in the project. Set by
#: device_manager and connection_fault, not by the device.
_PLATFORM_DEVICE_PROPERTIES = frozenset({
    "connected", "enabled", "host", "name", "offline_detail", "offline_reason",
    "orphan_reason", "orphaned", "paused", "reconnect_attempt", "reconnect_failed",
    "online", "status", "last_seen", "error",
})


def _undeclared_state_property(devices: Any, device_ids: list[str], key: str) -> set[str] | None:
    """The properties a driver DOES declare, when the bound one is not among them.

    None means no opinion, and that is the answer far more often than a set is:
    no driver loaded, a driver that declares no state at all, a child type it
    has never heard of, a child id that will not coerce. Only a driver that says
    "these are my state variables" can say a name is not one of them.

    The device id itself is checked elsewhere; this is the property after it,
    which is the half that used to pass silently while a typo'd device, command,
    macro and page were all caught.

    Never raises. Nothing advisory may cost a UI write.
    """
    if devices is None or not isinstance(key, str) or not key.startswith("device."):
        return None
    try:
        for device_id in device_ids:
            prefix = f"device.{device_id}."
            if not key.startswith(prefix):
                continue
            driver = devices.get_driver(device_id)
            if driver is None:
                return None
            info = getattr(driver, "DRIVER_INFO", None) or {}
            rest = key[len(prefix):]
            if rest in _PLATFORM_DEVICE_PROPERTIES:
                return None
            declared = info.get("state_variables") or {}
            parts = rest.split(".")
            if len(parts) < 3:
                # A plain property. Only answerable if the driver declares any.
                if not declared or rest in declared:
                    return None
                return set(declared)
            child_type, raw_id, prop = parts[0], parts[1], ".".join(parts[2:])
            type_def = (info.get("child_entity_types") or {}).get(child_type)
            if not isinstance(type_def, dict):
                # Not a child type it knows. Could still be a plain property
                # containing dots, so only speak if the flat set rules it out.
                if not declared or rest in declared:
                    return None
                return set(declared)
            from openavc.drivers.child_ids import coerce_child_local_id

            schema: dict = {}
            local_id = coerce_child_local_id(type_def, raw_id)
            if local_id is not None and hasattr(driver, "get_child_schema"):
                schema = driver.get_child_schema(child_type, local_id) or {}
            if not schema:
                schema = type_def.get("state_variables") or {}
            if not schema or prop in schema:
                return None
            return set(schema)
        return None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve declared properties for '%s'", key, exc_info=True)
        return None


def _declared_panel_elements(engine: Any, plugin_id: str) -> set[str] | None:
    """The panel-element types a loaded plugin declares, or None for no opinion.

    None means the plugin is not loaded here -- stopped, uninstalled, or simply
    not commissioned yet. That is a deployment fact, not an authoring mistake,
    and warning about it would fire on every page written before the plugin is
    installed.

    Never raises. Nothing advisory may cost a UI write.
    """
    if not plugin_id:
        return None
    try:
        loader = getattr(engine, "plugins", None)
        if loader is None or not hasattr(loader, "get_all_extensions"):
            return None
        declared = {
            str(ext.get("plugin_id")): ext
            for ext in loader.get_all_extensions().get("panel_elements", [])
            if isinstance(ext, dict)
        }
        if plugin_id not in declared:
            return None
        return {
            str(ext.get("type"))
            for ext in loader.get_all_extensions().get("panel_elements", [])
            if isinstance(ext, dict) and str(ext.get("plugin_id")) == plugin_id
            and ext.get("type")
        } or None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve panel elements for '%s'", plugin_id, exc_info=True)
        return None


def _declared_commands(devices: Any, device_id: str) -> set[str] | None:
    """The command names a device's driver declares, or None for no opinion.

    None is the answer far more often than an empty set is, and the difference
    matters: a device that is disabled, not connected yet, or backed by a driver
    that enumerates nothing must produce no warning at all. Only a driver that
    says "these are my commands" can say a name is not one of them.

    Never raises. Nothing advisory may cost a UI write.
    """
    if devices is None or not device_id:
        return None
    try:
        driver = devices.get_driver(device_id)
        if driver is None:
            return None
        commands = (getattr(driver, "DRIVER_INFO", None) or {}).get("commands")
        if not isinstance(commands, dict) or not commands:
            return None
        return set(commands)
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve declared commands for '%s'", device_id, exc_info=True)
        return None


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

    from openavc.core.project_loader import Placement

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


def _resolve_layout(page: Any, layout_id: Any) -> Any:
    """The named arrangement, or the primary when none is named."""
    if not layout_id:
        return primary_layout(page)
    for layout in page.layouts:
        if layout.id == layout_id:
            return layout
    raise ToolEditError({
        "error": f"Layout '{layout_id}' not found on page '{page.id}'. "
                 f"Available: {', '.join(lay.id for lay in page.layouts)}"
    })


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

    primary_id = primary_layout(page).id
    rewritten: list[tuple[Any, dict]] = []
    for layout in page.layouts:
        absolute = absolute_placements(page, layout.id)
        base = absolute.get(new_parent_id) if new_parent_id else dict(PAGE_BOX)
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


def _problem(exc: Exception, el_id: str) -> str:
    """One rejected element, said the way the single-element path says it."""
    if isinstance(exc, ToolEditError):
        return str(exc.result.get("error", exc))
    return f"Element '{el_id}': {exc}"


def _batch_error(problems: list[str], verb: str) -> ToolEditError:
    """Every offender in one reply, rather than the first one N times over.

    A batch write is atomic, so a rejected call used to teach the caller about
    exactly one problem while throwing away the other twenty-nine they could
    have fixed in the same turn. Thirty elements with three mistakes in them
    cost three round trips; they should cost one.

    The full list goes in ``error`` as well as ``errors``, because a caller that
    only reads the message would otherwise still be hunting them one at a time.
    """
    count = f"{len(problems)} elements" if len(problems) > 1 else "1 element"
    return ToolEditError({
        "error": f"{count} could not be {verb}: " + "; ".join(problems),
        "errors": problems,
    })


def _apply_layouts(page: Any, layouts_input: Any) -> None:
    """Add or edit a page's arrangements.

    An entry naming an existing layout edits it; anything else is a new
    arrangement. Placements merge (a variant is deltas -- sending one moved
    control must not blank the rest) while `hidden` replaces, because it is a
    set and "hide exactly these" is the only unambiguous reading of a list.
    """
    from openavc.core.project_loader import Layout, normalize_primary_layout

    if not isinstance(layouts_input, list):
        raise ToolEditError({"error": "'layouts' must be an array of layout objects"})

    by_id = {lay.id: lay for lay in page.layouts}
    primary_id = primary_layout(page).id

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


def _declared_state_variable(devices: Any, device_ids: list[str], key: str) -> dict | None:
    """What the driver declares about the state variable behind a bound key.

    The Builder answers this from the device schema it has already fetched, and
    offers a human a "Match driver range" button. Nothing answered it on this
    side, which is why a dB fader shipped with no upper bound: the driver states
    ``max: 0`` and the write path never looked.

    Handles both key shapes -- ``device.<id>.<prop>`` and the child-entity
    ``device.<id>.<type>.<local_id>.<prop>`` -- and resolves the device by
    longest matching id rather than by splitting on dots, because a device id
    may contain them and a child key looks exactly like a nested one.

    Never raises: this is advisory, and no failure to answer it may cost a UI
    write. An unknown key, a disabled device, a driver with no schema all mean
    "no opinion".
    """
    if devices is None or not isinstance(key, str) or not key.startswith("device."):
        return None
    try:
        for device_id in device_ids:
            prefix = f"device.{device_id}."
            if not key.startswith(prefix):
                continue
            driver = devices.get_driver(device_id)
            if driver is None:
                return None
            info = getattr(driver, "DRIVER_INFO", None) or {}
            rest = key[len(prefix):]
            declared = info.get("state_variables") or {}
            entry = declared.get(rest)
            if isinstance(entry, dict):
                return entry
            parts = rest.split(".")
            if len(parts) < 3:
                return None
            child_type, raw_id, prop = parts[0], parts[1], ".".join(parts[2:])
            type_def = (info.get("child_entity_types") or {}).get(child_type)
            if not isinstance(type_def, dict):
                return None
            # A dynamic child type carries its schema per instance, so ask the
            # driver for that child first and fall back to the type-level one.
            from openavc.drivers.child_ids import coerce_child_local_id

            schema: dict = {}
            local_id = coerce_child_local_id(type_def, raw_id)
            if local_id is not None and hasattr(driver, "get_child_schema"):
                schema = driver.get_child_schema(child_type, local_id) or {}
            if prop not in schema:
                schema = type_def.get("state_variables") or {}
            entry = schema.get(prop)
            return entry if isinstance(entry, dict) else None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve a declared range for '%s'", key, exc_info=True)
    return None


def _theme_defaults(project: Any, element_type: str) -> dict | None:
    """The project's own theme overrides for one element type.

    Only one control minimum moves with the theme (a slider's thumb), and only
    ``thumb_size`` in ``element_defaults`` moves it. A custom theme FILE that
    changed it would not be seen here -- reading it would mean disk access on a
    write path -- but every built-in theme ships the 44px default, so the
    override in the project is the case that can actually differ.
    """
    try:
        overrides = project.ui.settings.theme_overrides or {}
        defaults = (overrides.get("element_defaults") or {}).get(element_type)
        return defaults if isinstance(defaults, dict) else None
    except Exception:  # pragma: no cover - defensive
        return None


#: What the warnings are for, said once per reply that carries any. It lives
#: here rather than in the standing prompt because a rule is only worth its
#: tokens at the moment it is broken.
_WARNING_NOTE = (
    "The write landed -- these are warnings, not failures. Each one names the element, "
    "what will draw wrong, and the number to change. Fix them before reporting this "
    "page finished."
)


def _with_findings(result: dict, findings: list, adjustments: list) -> dict:
    """Hand the caller its own mistakes back, in the reply it is already reading."""
    if adjustments:
        result["auto_filled"] = [a.message for a in adjustments]
    if findings:
        result["warnings"] = [f.message for f in findings]
        result["warning_note"] = _WARNING_NOTE
    return result


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

    def _review_write(
        self, project: Any, page: Any, touched: set[str], *, ranges: bool = True,
    ) -> tuple[list, list]:
        """Measure what a write just did to a page, and fix what is unambiguous.

        Runs inside the mutate, so the fills land in the project being written
        and the findings describe what actually got stored rather than what was
        asked for. Scoped to ``touched``: a call answers for the elements it
        wrote, not for everything already on the page.

        ``ranges=False`` for an edit that says nothing about a control's range
        -- filling in a bound the caller did not mention would be a surprise on
        an unrelated update, and D2 is that auto-fill happens only where no
        intent was expressed.
        """
        device_ids = sorted((d.id for d in project.devices), key=len, reverse=True)
        cache: dict[str, dict | None] = {}

        def declared_range(key: str) -> dict | None:
            if key not in cache:
                cache[key] = _declared_state_variable(self._devices, device_ids, key)
            return cache[key]

        findings, adjustments = review_page(
            page,
            touched=touched,
            theme=_theme_defaults(project, "slider"),
            declared_range=declared_range if ranges else None,
        )
        findings.extend(reference_findings(
            page,
            touched=touched,
            page_ids={p.id for p in project.ui.pages},
            device_ids=set(device_ids),
            macro_ids={m.id for m in project.macros},
            device_commands=lambda device_id: _declared_commands(self._devices, device_id),
            plugin_elements=lambda plugin_id: _declared_panel_elements(
                self._get_engine(), plugin_id,
            ),
            undeclared_property=lambda key: _undeclared_state_property(
                self._devices, device_ids, key,
            ),
        ))
        if findings:
            log.info(
                "AI UI write on page '%s': %d element(s) reviewed, %d warning(s), %d filled in",
                getattr(page, "id", "?"), len(touched), len(findings), len(adjustments),
            )
        return findings, adjustments

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

        from openavc.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from openavc.core.project_loader import UIPage
        elements = input.get("elements", [])
        findings: list = []
        adjustments: list = []

        def mutate(project):
            if any(p.id == page_id for p in project.ui.pages):
                raise ToolEditError({"error": f"UI page '{page_id}' already exists"})
            _reject_retired_geometry(input, f"Page '{page_id}'")

            # Normalize + validate inline-element bindings the same way
            # _add_ui_elements does — otherwise a page-with-elements created in one
            # call yields bindings that were never validated (buttons silently do
            # nothing), while the identical elements added via add_ui_elements work.
            boxes: dict[str, dict] = {}
            problems: list[str] = []
            for el_data in elements:
                if not isinstance(el_data, dict):
                    continue
                el_id = el_data.get("id", "?")
                try:
                    place = _take_placement(el_data, f"Element '{el_id}'")
                    if place is not None:
                        boxes[el_id] = place
                    if isinstance(el_data.get("bindings"), dict):
                        el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                        err = _validate_bindings(el_data["bindings"], project)
                        if err:
                            raise ToolEditError({"error": f"Element '{el_id}': {err}"})
                except (ToolEditError, ValueError) as exc:
                    problems.append(_problem(exc, el_id))
            if problems:
                raise _batch_error(problems, "added")

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
            primary = primary_layout(new_page)
            for el_id, box in boxes.items():
                primary.placements[el_id] = _rounded_placement(box)
            # A UI-only change just swaps the project and pushes the new
            # ui.definition to connected panels.
            project.ui.pages.append(new_page)
            found, filled = self._review_write(
                project, new_page, {el.id for el in new_page.elements},
            )
            findings.extend(found)
            adjustments.extend(filled)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return _with_findings({"status": "created", "id": page_id}, findings, adjustments)

    async def _update_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        changed = []
        findings: list = []
        adjustments: list = []

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
                from openavc.core.project_loader import SnapConfig
                # Partial merge: keep omitted fields (don't reset the increment
                # to defaults) and preserve any forward-compat keys.
                page.snap = _merge_forward_compat(page.snap, SnapConfig, input["snap"])
                changed.append("snap")
            if "page_type" in input:
                page.page_type = input["page_type"]
                changed.append("page_type")
            if "overlay" in input:
                from openavc.core.project_loader import OverlayConfig
                page.overlay = OverlayConfig(**input["overlay"]) if input["overlay"] else None
                changed.append("overlay")
            if "background" in input:
                from openavc.core.project_loader import PageBackground
                page.background = PageBackground(**input["background"]) if input["background"] else None
                changed.append("background")

            if not changed:
                raise ToolEditError({"error": "No fields to update"})

            # Only a layouts edit moves anything, and it answers for the boxes
            # it named -- a rename or a background swap has no geometry to
            # measure. Nothing here expresses a range, so no auto-fill.
            moved = {
                el_id
                for entry in (input.get("layouts") or [])
                if isinstance(entry, dict)
                for el_id in (entry.get("placements") or {})
            } if "layouts" in input else set()
            if moved:
                found, filled = self._review_write(project, page, moved, ranges=False)
                findings.extend(found)
                adjustments.extend(filled)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return _with_findings(
            {"status": "updated", "page_id": page_id, "changed": changed},
            findings, adjustments,
        )

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

        from openavc.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from openavc.core.project_loader import UIElement
        findings: list = []
        adjustments: list = []

        def mutate(project):
            page = None
            for p in project.ui.pages:
                if p.id == page_id:
                    page = p
                    break
            if page is None:
                raise ToolEditError({"error": f"UI page '{page_id}' not found"})

            # Every element is checked before any of them lands, and every
            # complaint comes back together. The write is atomic either way; the
            # only question is whether the caller learns about one mistake or
            # all of them.
            existing_ids = {el.id for el in page.elements}
            holders = {el.id: el for el in page.elements}
            problems: list[str] = []
            prepared: list[tuple[Any, dict | None]] = []
            seen: set[str] = set()

            for el_data in elements:
                el_id = el_data.get("id", "?")
                if el_id in existing_ids:
                    problems.append(f"Element '{el_id}' already exists on page '{page_id}'")
                    continue
                if el_id in seen:
                    # Two elements with one id: the second silently replaced the
                    # first at render time, and nothing said so.
                    problems.append(f"Element '{el_id}' appears twice in this batch")
                    continue
                seen.add(el_id)
                try:
                    place = _take_placement(el_data, f"Element '{el_id}'")
                    if "bindings" in el_data and isinstance(el_data["bindings"], dict):
                        el_data["bindings"] = _normalize_bindings(el_data["bindings"])
                        err = _validate_bindings(el_data["bindings"], project)
                        if err:
                            raise ToolEditError({"error": f"Element '{el_id}': {err}"})
                    element = UIElement(**el_data)
                    if element.parent is not None:
                        holder = holders.get(element.parent)
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
                except (ToolEditError, ValueError) as exc:
                    problems.append(_problem(exc, el_id))
                    continue
                prepared.append((element, place))
                # A container and its children can arrive in one call, so an
                # element accepted here is a candidate parent for a later one --
                # exactly as when each was appended before the next was checked.
                holders[element.id] = element

            if problems:
                raise _batch_error(problems, "added")

            # A new control exists in every arrangement the moment it exists at
            # all, so its box goes in the primary -- write it into a variant and
            # it would have no box anywhere else.
            primary = primary_layout(page)
            for element, place in prepared:
                page.elements.append(element)
                if place is not None:
                    primary.placements[element.id] = _rounded_placement(place)

            found, filled = self._review_write(
                project, page, {el.get("id", "") for el in elements},
            )
            findings.extend(found)
            adjustments.extend(filled)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        added_ids = [el.get("id", "") for el in elements]
        return _with_findings(
            {"status": "created", "page_id": page_id, "element_ids": added_ids},
            findings, adjustments,
        )

    async def _update_ui_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")
        findings: list = []
        adjustments: list = []
        # An edit that says nothing about a control's range gets no auto-fill:
        # completing a bound the caller never mentioned would be a surprise
        # mutation on an unrelated update.
        touches_range = any(
            field in input for field in ("bindings", "min", "max", "step", "unit")
        )

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
                # A master is an element too, and looking only at pages made
                # every cross-page control unreachable from here -- the reply
                # said "not found", which reads like a typo and invites a retry
                # of the same call. Name the tool that can.
                if any(m.id == element_id for m in project.ui.master_elements):
                    raise ToolEditError({
                        "error": f"'{element_id}' is a master element, not a page element. "
                                 f"Use update_master_element to change it."
                    })
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
                from openavc.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
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
                from openavc.core.project_loader import Placement
                # Partial merge: keep omitted fields (don't snap x/y back to 0)
                # and preserve any forward-compat keys. The placement lives on
                # the page's layout, not the element. Runs after any reparent,
                # so the stated box overrides the conversion in the resolved
                # layout and the other arrangements keep the converted one.
                existing = resolved_placements(target_page, layout.id).get(target_el.id, Placement())
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
                    for ancestor in layout_chain(target_page, layout.id):
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

            found, filled = self._review_write(
                project, target_page, {element_id}, ranges=touches_range,
            )
            findings.extend(found)
            adjustments.extend(filled)

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return _with_findings(
            {"status": "updated", "element_id": element_id}, findings, adjustments,
        )

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

        result = {"status": "deleted", "element_ids": sorted(deleted_ids)}
        # The deletes that DID land are kept -- an id that is already gone is not
        # a reason to put twenty-seven elements back. But it is said, because the
        # caller otherwise has to diff two lists to notice.
        missing = sorted(ids_set - set(deleted_ids))
        if missing:
            result["not_found"] = missing
            result["warnings"] = [
                f"No element with id '{el_id}' on any page, so nothing was deleted for it."
                for el_id in missing
            ]
        return result

    async def _add_master_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("id", "")
        if not element_id:
            return {"error": "Element ID is required"}

        from openavc.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
        from openavc.core.project_loader import MasterElement
        el_data = {k: v for k, v in input.items() if k != "id"}
        el_data["id"] = element_id
        findings: list = []

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
            findings.extend(review_master_element(
                new_el, _theme_defaults(project, "slider"),
            ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return _with_findings({"status": "created", "id": element_id}, findings, [])

    async def _update_master_element(self, input: dict) -> Any:
        """Change a master element in place.

        Its absence was not a small gap. ``update_ui_element`` looks only at
        pages, so the only way to change a master was to delete it and build it
        again -- re-sending its style, placements, bindings and page list from
        scratch every time, with anything forgotten silently gone. A logo whose
        `src` needed setting could not be reached at all.

        Partial, like ``update_ui_element``: fields absent from the call keep
        their current values, and ``placements`` merges per orientation so
        moving the landscape box does not blank the portrait one.
        """
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")
        if not element_id:
            return {"error": "element_id is required"}
        changed: list[str] = []
        findings: list = []

        def mutate(project):
            target = next(
                (m for m in project.ui.master_elements if m.id == element_id), None,
            )
            if target is None:
                on_page = next(
                    (p.id for p in project.ui.pages
                     for el in p.elements if el.id == element_id),
                    None,
                )
                if on_page:
                    raise ToolEditError({
                        "error": f"'{element_id}' is an element on page '{on_page}', not a "
                                 f"master. Use update_ui_element to change it."
                    })
                raise ToolEditError({"error": f"Master element '{element_id}' not found"})

            _reject_retired_geometry(input, f"Master element '{element_id}'")
            if "placement" in input:
                raise ToolEditError({
                    "error": f"Master element '{element_id}': use 'placements' keyed by "
                             f"orientation, e.g. {{\"landscape\": {{\"x\": 2, \"y\": 2, "
                             f"\"w\": 20, \"h\": 8}}}}. A master is not part of any page's layout."
                })

            if "bindings" in input:
                from openavc.cloud.ai_tool_handler import _normalize_bindings, _validate_bindings
                bindings = input["bindings"]
                if not isinstance(bindings, dict):
                    raise ToolEditError({
                        "error": f"Master element '{element_id}': 'bindings' must be an "
                                 f"object, got {type(bindings).__name__}"
                    })
                bindings = _normalize_bindings(bindings)
                err = _validate_bindings(bindings, project)
                if err:
                    raise ToolEditError({"error": f"Master element '{element_id}': {err}"})
                target.bindings = bindings
                changed.append("bindings")

            if "placements" in input:
                # Merge per orientation: a call that moves the landscape box
                # must not blank a portrait one it said nothing about.
                boxes = input["placements"]
                if not isinstance(boxes, dict):
                    raise ToolEditError({
                        "error": f"Master element '{element_id}': 'placements' must be an "
                                 f"object keyed by orientation"
                    })
                merged = dict(target.placements or {})
                for orientation, box in boxes.items():
                    merged[orientation] = (
                        _rounded_placement(box) if isinstance(box, dict) else box
                    )
                target.placements = merged
                changed.append("placements")

            # Everything else is a plain field on the element. Anything the
            # model declares is settable -- the schema is what limits the
            # caller, and limiting it twice is what left a master image with no
            # way to be given a src.
            for field, value in input.items():
                if field in ("element_id", "bindings", "placements", "placement"):
                    continue
                if field == "id":
                    raise ToolEditError({
                        "error": "A master element's id cannot be changed here -- delete it "
                                 "and add it again under the new id."
                    })
                if not hasattr(target, field) and field not in (
                    type(target).model_fields
                ):
                    # extra='allow' means an unknown field would be stored and
                    # never read. Say so rather than accept it silently.
                    raise ToolEditError({
                        "error": f"Master element '{element_id}': '{field}' is not a field "
                                 f"on a UI element."
                    })
                setattr(target, field, value)
                changed.append(field)

            if not changed:
                raise ToolEditError({"error": "No fields to update"})
            findings.extend(review_master_element(
                target, _theme_defaults(project, "slider"),
            ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return _with_findings(
            {"status": "updated", "element_id": element_id, "changed": changed},
            findings, [],
        )

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

    async def _review_ui(self, input: dict) -> Any:
        """Re-run every UI check across the whole project, on demand.

        The checks all existed; there was no way to ASK. Findings rode back only
        on the write that caused them, so a panel built over sixty calls left
        its warnings scattered through a transcript, and a compacted or resumed
        session lost them outright. There was no way to end a build with "and
        now it is clean" -- only to remember, or not.

        Unscoped on purpose: no ``touched`` filter, every page, every master,
        every arrangement. Read-only, so it fills nothing in -- an auto-fill
        needs a write to belong to, and answering a question should not change
        the project.
        """
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        project = engine.project

        only = input.get("page_id")
        pages = [p for p in project.ui.pages if not only or p.id == only]
        if only and not pages:
            return {
                "error": f"UI page '{only}' not found. The pages are: "
                         f"{', '.join(p.id for p in project.ui.pages) or '(none)'}"
            }

        device_ids = sorted((d.id for d in project.devices), key=len, reverse=True)
        page_ids = {p.id for p in project.ui.pages}
        macro_ids = {m.id for m in project.macros}
        by_page: dict[str, list[str]] = {}
        total = 0

        for page in pages:
            findings, _ = review_page(
                page,
                theme=_theme_defaults(project, "slider"),
                # Ranges are read-only here: review_page only reports a range
                # mismatch, and the fills it would make are suppressed by
                # set_field below.
                declared_range=lambda key: _declared_state_variable(
                    self._devices, device_ids, key,
                ),
                set_field=lambda *_: None,
            )
            findings.extend(reference_findings(
                page,
                page_ids=page_ids,
                device_ids=set(device_ids),
                macro_ids=macro_ids,
                device_commands=lambda device_id: _declared_commands(self._devices, device_id),
                plugin_elements=lambda plugin_id: _declared_panel_elements(
                    self._get_engine(), plugin_id,
                ),
                undeclared_property=lambda key: _undeclared_state_property(
                    self._devices, device_ids, key,
                ),
            ))
            if findings:
                by_page[page.id] = [f.message for f in findings]
                total += len(findings)

        master_findings: list[str] = []
        if not only:
            for master in project.ui.master_elements:
                master_findings.extend(
                    f.message
                    for f in review_master_element(
                        master, _theme_defaults(project, "slider"),
                    )
                )
            total += len(master_findings)

        result: dict = {
            "pages_checked": [p.id for p in pages],
            "masters_checked": 0 if only else len(project.ui.master_elements),
            "finding_count": total,
        }
        if by_page:
            result["pages"] = by_page
        if master_findings:
            result["master_elements"] = master_findings
        if not total:
            result["status"] = "clean"
            result["note"] = (
                "Nothing to report: every control has a box it fits in, no two collide, "
                "and every binding points at something that exists."
            )
        else:
            result["status"] = "findings"
            result["note"] = _WARNING_NOTE
        return result

    async def _simulate_ui_action(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}

        action = input.get("action", "")
        element_id = input.get("element_id", "")
        value = input.get("value")
        page_id = input.get("page_id", "")
        dry_run = bool(input.get("dry_run"))

        if action == "navigate":
            if not page_id:
                return {"error": "page_id is required for navigate action"}
            if dry_run:
                return {
                    "success": True, "action": "navigate", "page_id": page_id,
                    "dry_run": True, "state_changes": [],
                    "note": f"Would move every connected panel to '{page_id}'.",
                }
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

        # A real press on a toggle button is resolved BY THE PANEL -- it compares
        # the bound key against toggle_value in the browser and sends press or
        # toggle_off. Firing "press" here regardless reported the on-branch
        # twice in a row for a working toggle, which is indistinguishable from a
        # toggle that only ever fires one branch.
        toggle_note: str | None = None
        effective = action
        if action == "press":
            element = _find_ui_element(engine, element_id)
            if element is not None:
                effective, toggle_note = resolve_press(element, self._agent.state)

        sub_id = self._agent.state.subscribe("*", on_change)
        try:
            if action in ("press", "release", "hold"):
                dispatched = await engine.handle_ui_event(
                    effective, element_id, dry_run=dry_run,
                )
            elif action == "change":
                dispatched = await engine.handle_ui_event(
                    "change", element_id, {"value": value}, dry_run=dry_run,
                )
            elif action == "submit":
                dispatched = await engine.handle_ui_event(
                    "submit", element_id, {"value": value}, dry_run=dry_run,
                )
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": f"Action failed: {e}", "state_changes": state_changes}
        finally:
            self._agent.state.unsubscribe(sub_id)

        return {
            "success": True,
            "action": action,
            **({"dispatched_as": effective} if effective != action else {}),
            **({"toggle": toggle_note} if toggle_note else {}),
            **({"dry_run": True} if dry_run else {}),
            "element_id": element_id,
            # What the interaction actually DID. State changes alone report a
            # working button as "success, nothing happened": a device.command
            # writes no state key directly -- it goes out the wire and comes
            # back on a poll or a push -- so an empty list is the expected
            # result for a command that fired. That is a verification tool
            # that cannot verify the commonest binding there is.
            "dispatched": dispatched or [],
            "state_changes": state_changes,
            **_nothing_ran_note(engine, element_id, action, dispatched),
        }
