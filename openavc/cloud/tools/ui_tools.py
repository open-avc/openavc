"""Mixin for AI tool handlers that manage UI pages and elements."""

from typing import Any

from pydantic import ValidationError

from openavc.cloud.tools import ToolEditError, apply_tool_edit, merge_forward_compat
from openavc.cloud.tools.ui_bindings import normalize_bindings, validate_bindings
from openavc.cloud.tools.ui_layouts import (
    apply_layouts,
    container_problem,
    master_boxes,
    merge_master_boxes,
    place_in_primary,
    remove_elements,
    reparent_element,
    resolve_layout,
    restate_box,
    set_hidden,
)
from openavc.cloud.tools.ui_rewrites import scrub_page_references
from openavc.cloud.tools.ui_simulate import simulate_ui_action
from openavc.cloud.tools.ui_review import UIReview, WriteNotes, master_findings
from openavc.ui.matrix_model import matrix_config_problems


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


def _page_named(project: Any, page_id: str) -> Any:
    """The page with this id, or refused by name."""
    page = next((p for p in project.ui.pages if p.id == page_id), None)
    if page is None:
        raise ToolEditError({"error": f"UI page '{page_id}' not found"})
    return page


def _reject_retired_geometry(data: dict, what: str) -> None:
    """Refuse pre-0.8.0 geometry loudly instead of storing it as an extra."""
    for key, replacement in _RETIRED_GEOMETRY.items():
        if key in data:
            raise ToolEditError({
                "error": f"{what}: '{key}' was removed in project format 0.8.0. Use {replacement}."
            })


def _reject_bad_matrix_config(data: dict, what: str) -> None:
    """Refuse a matrix_config the renderer would answer by drawing less than it says.

    The other matrix checks are the page review's and only warn, because they
    are judgement ("did you mean four destinations?"). These are structure: a
    destination with no ``value`` is not a debatable destination, it is one that
    silently will not be there, and the caller cannot see that in the reply it
    gets back. So this one refuses -- the round trip it costs is cheaper than a
    panel that draws six rows where eight were written.
    """
    if "matrix_config" not in data:
        return
    problems = matrix_config_problems(data["matrix_config"])
    if problems:
        raise ToolEditError({
            "error": f"{what}: " + " ".join(problems),
            "errors": problems,
        })


def _refuse_bad_definition(data: dict, what: str) -> None:
    """What an element's definition is refused for at every door that takes one:
    geometry the format retired, and a ``matrix_config`` that draws less than
    it says."""
    _reject_retired_geometry(data, what)
    _reject_bad_matrix_config(data, what)


def _refuse_bad_master_definition(data: dict, what: str) -> None:
    """An element's refusals, and a box given the way a page element's is.

    A master is valid on every page it appears on, so its box is a percentage
    of the VIEWPORT keyed by orientation -- it borrows no page's layout, which
    is exactly what used to make the same master land somewhere different on
    each page.
    """
    _refuse_bad_definition(data, what)
    if "placement" in data:
        raise ToolEditError({
            "error": f"{what}: use 'placements' keyed by orientation, "
                     f"e.g. {{\"landscape\": {{\"x\": 2, \"y\": 2, \"w\": 20, \"h\": 8}}}}. "
                     f"A master is not part of any page's layout."
        })


def _page_element(project: Any, element_id: str) -> tuple[Any, Any]:
    """The page and element an element update names.

    The first page holding the id, the order the event runtime resolves it in.
    A master is an element too, and looking only at pages made every cross-page
    control unreachable from the element door with a "not found", which reads
    like a typo and invites a retry of the same call; a master's id is pointed
    at the door that can change it.
    """
    for page in project.ui.pages:
        for el in page.elements:
            if el.id == element_id:
                return page, el
    if any(m.id == element_id for m in project.ui.master_elements):
        raise ToolEditError({
            "error": f"'{element_id}' is a master element, not a page element. "
                     f"Use update_master_element to change it."
        })
    raise ToolEditError({"error": f"UI element '{element_id}' not found"})


def _master_named(project: Any, element_id: str) -> Any:
    """The master a master update names; a page element's id is pointed at the
    element door, the other half of :func:`_page_element`."""
    target = next((m for m in project.ui.master_elements if m.id == element_id), None)
    if target is not None:
        return target
    on_page = next(
        (p.id for p in project.ui.pages for el in p.elements if el.id == element_id),
        None,
    )
    if on_page:
        raise ToolEditError({
            "error": f"'{element_id}' is an element on page '{on_page}', not a "
                     f"master. Use update_ui_element to change it."
        })
    raise ToolEditError({"error": f"Master element '{element_id}' not found"})


def _take_placement(el_data: dict, what: str) -> dict | None:
    """Lift an element's box out of its definition, where the box no longer lives.

    Geometry moved off the element and onto the page's layouts in 0.8.0 -- the
    same control can sit in two different places in the landscape and portrait
    arrangements without being duplicated. Callers still describe an element and
    its box in one breath, so the tools split them back apart here.
    """
    _refuse_bad_definition(el_data, what)
    place = el_data.pop("placement", None)
    if place is None:
        return None
    if not isinstance(place, dict):
        raise ToolEditError({
            "error": f"{what}: 'placement' must be an object {{x, y, w, h}}, got {type(place).__name__}"
        })
    return place


def _checked_bindings(bindings: dict, project: Any, what: str) -> dict:
    """Bindings in the form the runtime dispatches, or refused in the door's words.

    Every door that takes bindings runs this before anything lands. One action
    written as an object becomes a list of one -- in the caller's own dict too,
    since the normalisation is in place -- and anything the runtime would not
    dispatch is refused, prefixed with whose bindings they were.
    """
    bindings = normalize_bindings(bindings)
    err = validate_bindings(bindings, project)
    if err:
        raise ToolEditError({"error": f"{what}: {err}"})
    return bindings


def _check_created_bindings(el_data: dict, project: Any, what: str) -> None:
    """A create door's bindings, checked in place in the definition it builds from.

    Bindings that are not an object are left to the model, which refuses them
    with the rest of the definition in its own words.
    """
    if isinstance(el_data.get("bindings"), dict):
        el_data["bindings"] = _checked_bindings(el_data["bindings"], project, what)


def _updated_bindings(input: dict, project: Any, what: str) -> dict:
    """An update's bindings, refused here unless they are an object.

    An update assigns fields to a model that does not validate on assignment
    (``UIElement`` has no validate_assignment), so a non-object would persist
    a structurally invalid element rather than be refused by it.
    """
    bindings = input["bindings"]
    if not isinstance(bindings, dict):
        raise ToolEditError({
            "error": f"{what}: 'bindings' must be an object, got {type(bindings).__name__}"
        })
    return _checked_bindings(bindings, project, what)


def _validated_grant(value: Any, what: str) -> Any:
    """Coerce a grant, or say what is wrong with it.

    The one field checked rather than assigned raw, and the reason is the panel:
    a grant is the whole reach model for author markup, the panel enforces it
    and nothing else can (a WS frame carries no element identity), and the model
    does not validate on assignment. So a string where a list belongs would land
    intact -- and the panel asks ``grant.devices.includes(id)``, which a
    **string** answers too, matching every device id that is a substring of it.

    Shared by the element door and the page door, which take the same grant.
    """
    if value is None:
        return None
    from openavc.core.project_loader import ElementGrant
    try:
        return ElementGrant(**value) if isinstance(value, dict) else ElementGrant(value)
    except (ValidationError, TypeError) as exc:
        raise ToolEditError({
            "error": f"{what}: 'grant' is not shaped right -- devices and variables "
                     f"are lists of ids, macros and navigate are true/false. ({exc})"
        }) from exc


def _assign_plain_fields(target: Any, input: dict, skip: set[str], what: str) -> list[str]:
    """Assign every field the caller named that the model declares; the names
    assigned come back, in the caller's order.

    The alternative -- an if-block per settable field -- is what left
    ``update_ui_element`` able to change eight properties of an existing
    element and silently drop the rest. A caller could re-range a fader, fix a
    matrix, or point a custom control at a different file, get
    ``{"status": "updated"}`` back, and have none of it land: ``min`` and
    ``max`` were even read to decide whether to run the range check, and then
    thrown away. The master-element door already worked this way for the same
    reason ("limiting it twice is what left a master image with no way to be
    given a src"); this is that rule, shared, so the two cannot drift again.

    ``skip`` names the fields whose caller handles them itself, because they
    do not live on the element: a box and a hide belong to a layout, a parent
    needs the percentage conversion, bindings need validating first.

    One field is checked rather than assigned raw. ``grant`` is the whole reach
    model for a custom control -- the panel enforces it and nothing else can
    (a WS frame carries no element identity) -- and the model does not validate
    on assignment, so a string where a list belongs would land intact. The
    panel then asks ``grant.devices.includes(id)``, and a **string** answers
    that too: a grant of ``"projector1"`` matches every device id that is a
    substring of it. Coerce it, and say what was wrong.
    """
    assigned: list[str] = []
    for field, value in input.items():
        if field in skip:
            continue
        if field == "grant":
            value = _validated_grant(value, what)
        if field == "id":
            raise ToolEditError({
                "error": f"{what}: an id cannot be changed here -- delete it and add "
                         f"it again under the new id."
            })
        if not hasattr(target, field) and field not in type(target).model_fields:
            # extra='allow' means an unknown field would be stored and never
            # read. Say so rather than accept it silently.
            raise ToolEditError({
                "error": f"{what}: '{field}' is not a field on a UI element."
            })
        setattr(target, field, value)
        assigned.append(field)
    return assigned


def _refuse_if_locked(target: Any, input: dict, what: str) -> None:
    """A locked element cannot be moved or re-homed, exactly as on the canvas.

    ``locked`` is authoring-time protection: the Builder refuses to drag,
    resize, nudge or delete one (``lockedIdsFor`` + the guards in
    UIBuilderView), and it has no runtime meaning. Nothing here asked, so
    somebody could pin the background art and have it moved out from under
    them on the next request -- the one failure the flag exists to prevent,
    one door over. Property edits stay allowed, the way the Properties panel
    allows them, which is also how the flag gets turned back off.
    """
    if not getattr(target, "locked", False):
        return
    blocked = [f for f in ("placement", "parent") if f in input]
    if blocked:
        raise ToolEditError({
            "error": f"{what} is locked, so its {' and '.join(blocked)} cannot be "
                     f"changed. Send locked: false first if you meant to move it."
        })


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


class _NewElements:
    """One create call's new controls, every one checked before any lands.

    The write is atomic either way; the only question is whether the caller
    learns about one mistake or all of them, so each refusal is kept and they
    come back together. Every definition goes through the same three checks,
    in this order: what every door refuses (retired geometry, a matrix that
    draws less than it says), its box lifted out of it, and its bindings
    checked in place. A door that builds each control itself hands ``build``,
    whose refusal is kept the same way.
    """

    def __init__(self, project: Any) -> None:
        self.project = project
        self.problems: list[str] = []

    def refuse(self, problem: str) -> None:
        self.problems.append(problem)

    def prepare(self, el_data: dict, build: Any = None) -> tuple[Any, Any] | None:
        """(its box, the control ``build`` made) for one definition, or None
        when it was refused, the refusal kept."""
        el_id = el_data.get("id", "?")
        what = f"Element '{el_id}'"
        try:
            place = _take_placement(el_data, what)
            _check_created_bindings(el_data, self.project, what)
            return place, (build(el_data) if build else None)
        except (ToolEditError, ValueError) as exc:
            self.problems.append(_problem(exc, el_id))
            return None

    def check(self) -> None:
        """Refuse the whole batch, naming every offender, when any was refused."""
        if self.problems:
            raise _batch_error(self.problems, "added")


def _id_taken(el_id: Any, existing_ids: set, seen: set, page_id: str) -> str | None:
    """Why a new control cannot have this id on this page, or None when it can."""
    if el_id in existing_ids:
        return f"Element '{el_id}' already exists on page '{page_id}'"
    if el_id in seen:
        # Two elements with one id: the second silently replaced the first at
        # render time, and nothing said so.
        return f"Element '{el_id}' appears twice in this batch"
    return None


def _set_layouts(page: Any, value: Any, what: str) -> None:
    """Every setter takes ``what`` (whose field it is), the table's one shape;
    the layouts write names each arrangement itself."""
    apply_layouts(page, value)


def _set_snap(page: Any, value: Any, what: str) -> None:
    """Partial merge: omitted fields keep their values (the increment is not
    reset to the defaults) and forward-compat keys survive."""
    from openavc.core.project_loader import SnapConfig
    page.snap = merge_forward_compat(page.snap, SnapConfig, value)


def _set_overlay(page: Any, value: Any, what: str) -> None:
    from openavc.core.project_loader import OverlayConfig
    page.overlay = OverlayConfig(**value) if value else None


def _set_background(page: Any, value: Any, what: str) -> None:
    from openavc.core.project_loader import PageBackground
    page.background = PageBackground(**value) if value else None


def _set_render_mode(page: Any, value: Any, what: str) -> None:
    """Checked rather than assigned: the model declares a Literal and does not
    validate on assignment, so "Custom" or a typo would land and the page would
    quietly go back to drawing its elements."""
    if value not in ("elements", "custom"):
        raise ToolEditError({
            "error": f"{what}: render_mode is 'elements' (draw the page's own "
                     f"controls) or 'custom' (give the screen to a page in the "
                     f"project's ui/ folder), not {value!r}."
        })
    page.render_mode = value


def _set_grant(page: Any, value: Any, what: str) -> None:
    page.grant = _validated_grant(value, what)


def _set_plain(field: str) -> Any:
    def assign(page: Any, value: Any, what: str) -> None:
        setattr(page, field, value)
    return assign


#: Every page field the AI may set, in the order an update applies them and
#: names them in its reply, each with how an update assigns it. The create door
#: hands the same fields to the model, which validates as it constructs; an
#: update assigns, which validates nothing, so the fields a wrong value would
#: break are converted or checked here. What the page DRAWS (its own controls,
#: or one page out of the project's ui/ folder given the whole screen) is
#: settable at both doors: a page the AI can read as ``render_mode: "custom"``
#: and cannot set back is the element door's old defect one level up.
_PAGE_FIELDS = {
    "name": _set_plain("name"),
    "layouts": _set_layouts,
    "snap": _set_snap,
    "page_type": _set_plain("page_type"),
    "overlay": _set_overlay,
    "background": _set_background,
    "render_mode": _set_render_mode,
    "custom_file": _set_plain("custom_file"),
    "custom_config": _set_plain("custom_config"),
    "grant": _set_grant,
}


def _page_update_touches(input: dict) -> set[str] | None:
    """The elements a page update answers for, or None when it has nothing to review.

    Only a layouts edit moves anything, and it answers for the boxes it named
    -- a rename or a background swap has no geometry to measure. A page that
    has just been handed to markup -- or granted, or pointed at another file --
    is reviewed even though nothing moved: those three are exactly the write
    whose mistake is invisible.
    """
    moved = {
        el_id
        for entry in (input.get("layouts") or [])
        if isinstance(entry, dict)
        for el_id in (entry.get("placements") or {})
    } if "layouts" in input else set()
    hands_over = any(field in input for field in ("render_mode", "custom_file", "grant"))
    return moved if moved or hands_over else None


class UIToolsMixin:
    """UI page CRUD, element management, master elements, and action simulation."""

    async def _get_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        page_id = input.get("page_id", "")
        for p in engine.project.ui.pages:
            if p.id == page_id:
                # Unset fields left out: an element carries some sixty optional
                # ones, and a page sent with every one as null ran to four times
                # its size.
                return p.model_dump(mode="json", exclude_none=True)
        return {"error": f"UI page '{page_id}' not found"}

    async def _add_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("id", "")
        if not page_id:
            return {"error": "Page ID is required"}

        from openavc.core.project_loader import UIPage
        elements = input.get("elements", [])
        notes = WriteNotes()

        def mutate(project):
            if any(p.id == page_id for p in project.ui.pages):
                raise ToolEditError({"error": f"UI page '{page_id}' already exists"})
            _reject_retired_geometry(input, f"Page '{page_id}'")

            # Each inline element goes through what add_ui_elements puts each
            # through -- otherwise a page created with its elements in one call
            # keeps bindings nothing validated (buttons silently do nothing).
            # One that is not an object is left to the model, with the page.
            batch = _NewElements(project)
            boxes: dict[str, dict] = {}
            for el_data in elements:
                made = batch.prepare(el_data) if isinstance(el_data, dict) else None
                if made and made[0] is not None:
                    boxes[el_data.get("id", "?")] = made[0]
            batch.check()

            # Built through the model rather than field by field, so a page can
            # be created as one that draws its own markup instead of having to
            # be created and then converted. Pydantic answers for the Literal
            # and the grant shape here; the update door checks them by hand
            # because assignment does not.
            given = {field: input[field] for field in _PAGE_FIELDS if field in input}
            try:
                new_page = UIPage(
                    id=page_id,
                    elements=elements,
                    **{"name": page_id, "snap": {}, "layouts": [], **given},
                )
            except ValidationError as exc:
                raise ToolEditError({
                    "error": f"Page '{page_id}' is not shaped right: {exc}"
                }) from exc
            place_in_primary(new_page, boxes)
            # A UI-only change just swaps the project and pushes the new
            # ui.definition to connected panels.
            project.ui.pages.append(new_page)
            notes.created(new_page.grant)
            notes.add(*UIReview(engine, self._devices, project).write(
                new_page, {el.id for el in new_page.elements},
            ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({"status": "created", "id": page_id})

    async def _update_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        changed = []
        notes = WriteNotes()

        def mutate(project):
            page = _page_named(project, page_id)
            what = f"Page '{page_id}'"
            _reject_retired_geometry(input, what)
            grant_before = page.grant
            for field, assign in _PAGE_FIELDS.items():
                if field in input:
                    assign(page, input[field], what)
                    changed.append(field)
            if not changed:
                raise ToolEditError({"error": "No fields to update"})
            if "grant" in input:
                notes.changed(grant_before, page.grant)

            # Nothing here expresses a range, so no auto-fill.
            touched = _page_update_touches(input)
            if touched is not None:
                notes.add(*UIReview(engine, self._devices, project).write(
                    page, touched, ranges=False,
                ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({"status": "updated", "page_id": page_id, "changed": changed})

    async def _delete_ui_page(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        page_id = input.get("page_id", "")
        element_count = 0

        def mutate(project):
            nonlocal element_count
            element_count = len(_page_named(project, page_id).elements)
            project.ui.pages = [p for p in project.ui.pages if p.id != page_id]
            scrub_page_references(project, page_id)

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

        from openavc.core.project_loader import UIElement
        notes = WriteNotes()

        def mutate(project):
            page = _page_named(project, page_id)

            batch = _NewElements(project)
            holders = {el.id: el for el in page.elements}
            existing_ids = set(holders)
            seen: set[str] = set()
            prepared: list[tuple[Any, dict | None]] = []

            def build(el_data: dict) -> Any:
                element = UIElement(**el_data)
                problem = element.parent is not None and container_problem(
                    holders, element.parent, page_id,
                )
                if problem:
                    raise ToolEditError({"error": f"Element '{el_data.get('id', '?')}': {problem}"})
                return element

            for el_data in elements:
                el_id = el_data.get("id", "?")
                taken = _id_taken(el_id, existing_ids, seen, page_id)
                if taken:
                    batch.refuse(taken)
                    continue
                seen.add(el_id)
                made = batch.prepare(el_data, build)
                if made is not None:
                    place, element = made
                    prepared.append((element, place))
                    # A container and its children can arrive in one call, so
                    # an element accepted here is a candidate parent for a
                    # later one -- as when each was appended before the next
                    # was checked.
                    holders[element.id] = element
            batch.check()

            for element, place in prepared:
                page.elements.append(element)
                # What each new control may reach, named rather than assumed:
                # a grant arriving on a create is no more visible afterwards
                # for having arrived early.
                notes.created_on(element.id, element.grant)
            place_in_primary(page, {
                element.id: place for element, place in prepared if place is not None
            })

            notes.add(*UIReview(engine, self._devices, project).write(
                page, {el.get("id", "") for el in elements},
            ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({
            "status": "created", "page_id": page_id,
            "element_ids": [el.get("id", "") for el in elements],
        })

    async def _update_ui_element(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}

        element_id = input.get("element_id", "")
        notes = WriteNotes()
        # What actually landed, named in the reply. A caller that asked for a
        # grant change and a range change should not have to re-read the
        # element to find out which of them the door accepted.
        changed: list[str] = []
        # An edit that says nothing about a control's range gets no auto-fill:
        # completing a bound the caller never mentioned would be a surprise
        # mutation on an unrelated update.
        touches_range = any(
            field in input for field in ("bindings", "min", "max", "step", "unit")
        )

        def mutate(project):
            target_page, target_el = _page_element(project, element_id)
            _refuse_bad_definition(input, f"Element '{element_id}'")
            # Geometry and per-layout visibility belong to one arrangement. The
            # primary is where a box lives unless the caller is authoring a
            # variant and says so.
            layout = resolve_layout(target_page, input.get("layout_id"))

            # Bindings are checked before any field moves, so a refused update
            # changes nothing.
            if "bindings" in input:
                bindings = _updated_bindings(input, project, f"Element '{element_id}'")

            _refuse_if_locked(target_el, input, f"Element '{element_id}'")

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
                reparent_element(target_page, element_id, new_parent)
                changed.append("parent")
            if "placement" in input:
                # Runs after any reparent, so the stated box overrides the
                # conversion in the resolved layout and the other arrangements
                # keep the converted one.
                restate_box(target_page, layout, target_el.id, input["placement"])
                changed.append("placement")
            if "hidden" in input:
                set_hidden(target_page, layout, element_id, bool(input["hidden"]))
                changed.append("hidden")
            if "bindings" in input:
                target_el.bindings = bindings
                changed.append("bindings")

            # Everything else is a plain field on the element. The four above
            # are handled by hand because they do not live on it: a box and a
            # hide belong to a layout, a parent needs the percentage
            # conversion, and bindings were validated before anything moved.
            grant_before = target_el.grant
            changed.extend(_assign_plain_fields(
                target_el, input,
                skip={"element_id", "layout_id", "parent", "placement", "hidden", "bindings"},
                what=f"Element '{element_id}'",
            ))
            if "grant" in input:
                notes.changed(grant_before, target_el.grant)

            notes.add(*UIReview(engine, self._devices, project).write(
                target_page, {element_id}, ranges=touches_range,
            ))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({
            "status": "updated", "element_id": element_id, "changed": sorted(changed),
        })

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
            # A locked element is protected from deletion here for the same
            # reason the canvas protects it from the Delete key: it was pinned
            # on purpose. Named individually, because a batch of twenty that
            # contains one pinned logo should say which one.
            pinned = sorted(
                el.id
                for page in project.ui.pages
                for el in page.elements
                if el.id in ids_set and getattr(el, "locked", False)
            )
            if pinned:
                raise ToolEditError({
                    "error": "Locked, so nothing was deleted: "
                             + ", ".join(f"'{i}'" for i in pinned)
                             + ". Unlock with update_ui_element locked: false first."
                })

            for page in project.ui.pages:
                deleted_ids.extend(remove_elements(page, ids_set))

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

        from openavc.core.project_loader import MasterElement
        el_data = {k: v for k, v in input.items() if k != "id"}
        el_data["id"] = element_id
        notes = WriteNotes()

        def mutate(project):
            _refuse_bad_master_definition(el_data, f"Master element '{element_id}'")
            # Check for ID collision with page elements and existing master elements
            for page in project.ui.pages:
                if any(el.id == element_id for el in page.elements):
                    raise ToolEditError({"error": f"Element '{element_id}' already exists on page '{page.id}'"})
            if any(el.id == element_id for el in project.ui.master_elements):
                raise ToolEditError({"error": f"Master element '{element_id}' already exists"})

            _check_created_bindings(el_data, project, f"Master element '{element_id}'")
            if isinstance(el_data.get("placements"), dict):
                el_data["placements"] = master_boxes(el_data["placements"])
            new_el = MasterElement(**el_data)
            project.ui.master_elements.append(new_el)
            notes.created(new_el.grant)
            notes.add(master_findings(project, new_el))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({"status": "created", "id": element_id})

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
        notes = WriteNotes()

        def mutate(project):
            target = _master_named(project, element_id)
            _refuse_bad_master_definition(input, f"Master element '{element_id}'")

            if "bindings" in input:
                target.bindings = _updated_bindings(
                    input, project, f"Master element '{element_id}'",
                )
                changed.append("bindings")

            if "placements" in input:
                merge_master_boxes(target, input["placements"], f"Master element '{element_id}'")
                changed.append("placements")

            # Everything else is a plain field on the element. Anything the
            # model declares is settable -- the schema is what limits the
            # caller, and limiting it twice is what left a master image with no
            # way to be given a src. Shared with update_ui_element, which had
            # exactly that defect until the rule moved into one place.
            grant_before = target.grant
            changed.extend(_assign_plain_fields(
                target, input,
                skip={"element_id", "bindings", "placements", "placement"},
                what=f"Master element '{element_id}'",
            ))
            if "grant" in input:
                # A master can be a custom control too, drawn on every page.
                notes.changed(grant_before, target.grant)

            if not changed:
                raise ToolEditError({"error": "No fields to update"})
            notes.add(master_findings(project, target))

        err = await apply_tool_edit(engine, mutate)
        if err:
            return err

        return notes.reply({"status": "updated", "element_id": element_id, "changed": changed})

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
        """Re-run every UI check across the whole project, or one page, on demand."""
        engine = self._get_engine()
        if not engine or not engine.project:
            return {"error": "No project loaded"}
        return UIReview(engine, self._devices, engine.project).whole(input.get("page_id"))

    async def _simulate_ui_action(self, input: dict) -> Any:
        engine = self._get_engine()
        if not engine:
            return {"error": "Engine not available"}
        return await simulate_ui_action(engine, self._agent.state, input)
