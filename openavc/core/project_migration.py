"""
OpenAVC project format migration system.

Applies versioned transforms when loading older project files.
Each migration is a pure function: dict -> dict.
"""

from openavc.utils.logger import get_logger

log = get_logger(__name__)

CURRENT_VERSION = "0.8.0"

# --- 0.8.0 layout-engine constants -------------------------------------------
# The reference screen the old grid was implicitly designed against. Converting
# against it makes a migrated panel pixel-identical here, and proportionally
# identical on any screen of the same shape -- which the grid never was.
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 800

# Old base font size. The 0.8.0 root scale (1.75vmin) resolves to exactly this
# at the reference size, so dividing a px value by it preserves appearance.
REM_BASE_PX = 14

# .panel-page's padding and default cell gap, both driven by --panel-grid-gap.
DEFAULT_GRID_GAP = 8

# Style keys whose stored value was a raw px number.
_STYLE_PX_KEYS = (
    "font_size", "border_radius", "border_width",
    "padding", "padding_vertical", "padding_horizontal",
    "margin", "margin_vertical", "margin_horizontal",
    "letter_spacing", "cell_size", "icon_size", "thumb_size",
)

# Same, but stored on the element rather than inside its style dict.
_ELEMENT_PX_KEYS = ("icon_size", "item_height", "thumb_size")

# Overlay/sidebar box defaults, in px, as panel.js hardcoded them.
_OVERLAY_DEFAULT_W = 400
_OVERLAY_DEFAULT_H = 300
_SIDEBAR_DEFAULT_W = 320

# Connection-related config fields that belong in the connections table.
# Names match what BaseDriver reads at runtime (server/drivers/base.py):
# `port` (string for serial, int for TCP/UDP/OSC/HTTP) and `baudrate` for serial.
# Older `com_port`/`baud_rate` are translated by migrate_0_1_to_0_2.
CONNECTION_FIELDS = {
    "host", "port", "baudrate", "username", "password",
    "base_url", "ssl",
    # Serial line params (match BaseDriver._coerce_serial_params): they live
    # with the connection alongside `baudrate` so a template deployment swaps
    # the whole serial config per site instead of leaving some in device.config.
    "bytesize", "parity", "stopbits", "flow_control",
    # Bridge binding (v0.6.0): a downstream device routes its bytes through
    # another device's typed port. `bridge` is the bridge device id,
    # `bridge_port` the port id it advertises (e.g. "serial:1"). The bridge
    # resolver (core.device_config) reads these to rewrite the
    # downstream's effective transport to the bridge's pass-through endpoint.
    "bridge", "bridge_port",
    # Local USB-to-serial stable identity: the adapter's USB serial number.
    # The USB resolver (core.device_config) turns it into the
    # adapter's live OS port path so a direct serial device survives its port
    # name moving across reboot / replug.
    "usb_serial",
}


def migrate_0_1_to_0_2(data: dict) -> dict:
    """
    Migrate from 0.1.0 to 0.2.0:
    - Rename serial fields com_port -> port, baud_rate -> baudrate so they
      match what BaseDriver reads after resolved_device_config merges the
      connections table back into device.config
    - Move connection fields from device.config to connections table
    - Add empty driver_dependencies (populated on save)
    - Bump version
    """
    connections: dict[str, dict] = {}
    serial_renames = (("com_port", "port"), ("baud_rate", "baudrate"))

    for device in data.get("devices", []):
        device_id = device.get("id", "")
        config = device.get("config", {})

        # Rename legacy serial field names BEFORE moving to connections table.
        # If both legacy and new are present (e.g. mixed manual edits), the
        # new name wins.
        for old_name, new_name in serial_renames:
            if old_name in config and new_name not in config:
                config[new_name] = config.pop(old_name)
            elif old_name in config:
                config.pop(old_name)

        conn_overrides: dict = {}
        for key in list(config.keys()):
            if key in CONNECTION_FIELDS:
                conn_overrides[key] = config.pop(key)

        if conn_overrides:
            connections[device_id] = conn_overrides

    data["connections"] = connections
    data.setdefault("driver_dependencies", [])
    data["openavc_version"] = "0.2.0"
    return data


def migrate_0_2_to_0_3(data: dict) -> dict:
    """
    Migrate from 0.2.0 to 0.3.0:
    - Add empty plugins dict
    - Add empty plugin_dependencies list
    - Bump version
    """
    data.setdefault("plugins", {})
    data.setdefault("plugin_dependencies", [])
    data["openavc_version"] = "0.3.0"
    return data


def migrate_0_3_to_0_4(data: dict) -> dict:
    """
    Migrate from 0.3.0 to 0.4.0:
    - Convert per-device group field into device_groups entries
    - Bump version
    """
    # sanitize_id strips dots and other characters DeviceGroup's id validator
    # rejects; a raw name.lower().replace(" ", "_") would let a group named
    # e.g. "Row.1" migrate to the id "row.1", which fails validation and makes
    # the whole project unloadable. Imported lazily to keep this module light
    # and avoid an import cycle with project_library.
    from openavc.core.project_library import sanitize_id

    # Collect group assignments from devices
    groups_map: dict[str, list[str]] = {}
    for device in data.get("devices", []):
        group_name = device.pop("group", None)
        if group_name:
            groups_map.setdefault(group_name, []).append(device.get("id", ""))

    # Merge the per-device assignments INTO any device_groups already present
    # (a hand-edited or partially-migrated file can carry both). Dropping
    # either side would silently lose group memberships. Existing groups keep
    # their own ids; groups synthesized from a device's group name get a
    # sanitized id and merge with an existing group of the same id.
    groups = list(data.get("device_groups") or [])
    by_id = {g.get("id"): g for g in groups}
    for name, ids in groups_map.items():
        gid = sanitize_id(name)
        target = by_id.get(gid)
        if target is None:
            target = {"id": gid, "name": name, "device_ids": []}
            by_id[gid] = target
            groups.append(target)
        device_ids = target.setdefault("device_ids", [])
        for device_id in ids:
            if device_id and device_id not in device_ids:
                device_ids.append(device_id)
    data["device_groups"] = groups

    data["openavc_version"] = "0.4.0"
    return data


def migrate_0_4_to_0_5(data: dict) -> dict:
    """
    Migrate from 0.4.0 to 0.5.0:
    - Inject empty child_entities dict on every device so the new
      DeviceConfig.child_entities field has a concrete value on disk
      after the first save. The Pydantic field default would supply
      the same value when loading a v0.4.0 file directly, but writing
      it explicitly keeps the on-disk schema self-describing and lets
      future tooling rely on the key being present.
    - Bump version.
    """
    for device in data.get("devices", []):
        device.setdefault("child_entities", {})
    data["openavc_version"] = "0.5.0"
    return data


def migrate_0_5_to_0_6(data: dict) -> dict:
    """
    Migrate from 0.5.0 to 0.6.0:
    - Introduces the device-bridge connection model: a downstream device can
      route its bytes through another device's typed port (serial / IR / relay)
      via ``bridge`` + ``bridge_port`` keys in its ``connections[<id>]`` entry.
      The connections table is already a free-form ``dict[str, dict]``, so
      existing files need no structural change — this is a version-stamp
      migration that records the new capability and keeps the chain explicit.
    - Bump version.
    """
    data["openavc_version"] = "0.6.0"
    return data


# UI element binding slots that held ordered action lists in v0.6.0; these
# move under ``do``. Matrix sends one ui.route event that ws.py demuxes into
# route/audio_route/mute_route/audio_mute_route by flags; all four are
# author-time slots and migrate the same way.
_ACTION_SLOTS_0_7 = (
    "press", "release", "hold", "change", "submit", "select",
    "route", "audio_route", "mute_route", "audio_mute_route",
)


def _migrate_bindings_0_6_to_0_7(bindings: dict) -> dict:
    """Rewrite one element's v0.6.0 binding slots into the v0.7.0
    ``show`` / ``do`` shape.

    ``show`` = what the element reflects from state (value / items / look /
    visible_when); ``do`` = action lists keyed by interaction. The two-way
    shortcut becomes ``show.value.write_back`` and is kept **only** for writable
    ``var.*`` keys: a v0.6.0 ``variable`` bound to a ``device.*`` key never
    reached the device (it wrote the state mirror, overwritten on the next
    poll), so it degrades to a read-only value (an interactive control reading a
    device key needs a command to drive it, which validation surfaces).
    """
    if not isinstance(bindings, dict):
        return bindings

    show: dict = {}
    do: dict = {}

    # show.value — the thing the control IS. Order matters: a `variable`
    # (two-way read source) overrides a plain `value`, mirroring the panel's
    # `bindings.variable || bindings.value`. `text` is the label's value.
    if isinstance(bindings.get("value"), dict):
        show["value"] = bindings["value"]
    if isinstance(bindings.get("text"), dict):
        show["value"] = bindings["text"]
    if isinstance(bindings.get("variable"), dict):
        key = bindings["variable"].get("key", "")
        value: dict = {"source": "state", "key": key}
        if isinstance(key, str) and key.startswith("var."):
            value["write_back"] = True
        show["value"] = value
    # A list's value is its current selection (was the `selected` two-way slot).
    if isinstance(bindings.get("selected"), dict):
        sel_key = bindings["selected"].get("key", "")
        show["value"] = {"source": "state", "key": sel_key, "write_back": True}

    # show.items — list row population.
    if isinstance(bindings.get("items"), dict):
        show["items"] = bindings["items"]

    # show.look — state-driven appearance (feedback / LED color map / select
    # per-option style). Inner shape is carried verbatim; runtime branches on it.
    if isinstance(bindings.get("feedback"), dict):
        show["look"] = bindings["feedback"]
    if isinstance(bindings.get("color"), dict):
        show["look"] = bindings["color"]

    # show.visible_when — unchanged condition shape.
    if bindings.get("visible_when") is not None:
        show["visible_when"] = bindings["visible_when"]

    # do.<interaction> — normalize a single action object to a one-item list.
    for slot in _ACTION_SLOTS_0_7:
        if slot not in bindings:
            continue
        raw = bindings[slot]
        if isinstance(raw, list):
            actions = [a for a in raw if isinstance(a, dict)]
        elif isinstance(raw, dict) and raw:
            actions = [raw]
        else:
            actions = []
        if actions:
            do[slot] = actions

    out: dict = {}
    if show:
        out["show"] = show
    if do:
        out["do"] = do
    # The matrix preset bar (`presets`) is relocated to the element's
    # matrix_config by the element-level caller, so it is intentionally not
    # carried on `bindings` here. The orphan `meter` slot (never wired to an
    # editor or runtime) is dropped; every other v0.6.0 binding key is mapped
    # above (action slots, the show sources).
    return out


def _migrate_element_bindings_0_6_to_0_7(el: dict) -> None:
    """Rewrite one element's bindings to the v0.7.0 ``show`` / ``do`` shape in
    place and relocate any matrix preset bar to ``matrix_config.presets``.

    The preset bar (`bindings.presets`, a list of ``{name, macro}``) is matrix
    element configuration rather than a show/do binding, so its principled home
    is ``matrix_config`` beside ``input_count`` / ``route_key_pattern``. Moving
    it leaves ``bindings`` as exactly ``{show, do}``.
    """
    bindings = el.get("bindings")
    if not isinstance(bindings, dict):
        return
    presets = bindings.get("presets")
    el["bindings"] = _migrate_bindings_0_6_to_0_7(bindings)
    if isinstance(presets, list) and presets:
        cfg = el.get("matrix_config")
        if not isinstance(cfg, dict):
            cfg = {}
            el["matrix_config"] = cfg
        cfg["presets"] = presets


def migrate_0_6_to_0_7(data: dict) -> dict:
    """
    Migrate from 0.6.0 to 0.7.0:
    - Rewrite UI element bindings from the ad-hoc per-control slot set into the
      unified ``show`` / ``do`` model. Applies to page elements and
      master_elements alike. Two-way collapses to ``show.value.write_back``
      (writable ``var.*`` keys only); the orphan ``meter`` slot is dropped; the
      matrix preset bar moves to ``matrix_config.presets``.
    - Bump version.
    """
    ui = data.get("ui")
    if isinstance(ui, dict):
        for page in ui.get("pages", []):
            if not isinstance(page, dict):
                continue
            for el in page.get("elements", []):
                if isinstance(el, dict):
                    _migrate_element_bindings_0_6_to_0_7(el)
        for mel in ui.get("master_elements", []):
            if isinstance(mel, dict):
                _migrate_element_bindings_0_6_to_0_7(mel)

    data["openavc_version"] = "0.7.0"
    return data


def _round4(value: float) -> float:
    """Percentages are stored to 4 decimal places, everywhere, always.

    Without one canonical rounding, a px->%->px round-trip drifts by a
    millionth, the save-reconcile diff sees a change that isn't one, and the
    panel arms a phantom autosave every time somebody opens a page.
    """
    return round(value + 0.0, 4)


def _num(value, default=None):
    """Return value as a float if it is a real number, else default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _px_to_rem(value):
    """Convert one stored px number to rem. Non-numbers pass through."""
    px = _num(value)
    if px is None:
        return value
    return _round4(px / REM_BASE_PX)


def _cell_rect_to_percent(area, columns, rows, box_w, box_h, pad, gap):
    """Convert one grid_area into a percentage rect of its box.

    This reproduces what CSS Grid actually laid out, rather than the naive
    col/columns fraction: `.panel-page` insets its content by one gap on every
    side and puts another gap between cells, so a naive conversion is off by
    half a gap in position and a whole gap in width at wide spans.
    """
    columns = max(1, int(_num(columns, 12) or 12))
    rows = max(1, int(_num(rows, 8) or 8))
    box_w = _num(box_w) or REFERENCE_WIDTH
    box_h = _num(box_h) or REFERENCE_HEIGHT

    cell_w = (box_w - 2 * pad - (columns - 1) * gap) / columns
    cell_h = (box_h - 2 * pad - (rows - 1) * gap) / rows

    col = max(1, int(_num(area.get("col"), 1) or 1))
    row = max(1, int(_num(area.get("row"), 1) or 1))
    col_span = max(1, int(_num(area.get("col_span"), 1) or 1))
    row_span = max(1, int(_num(area.get("row_span"), 1) or 1))

    return {
        "x": _round4((pad + (col - 1) * (cell_w + gap)) / box_w * 100),
        "y": _round4((pad + (row - 1) * (cell_h + gap)) / box_h * 100),
        "w": _round4((col_span * cell_w + (col_span - 1) * gap) / box_w * 100),
        "h": _round4((row_span * cell_h + (row_span - 1) * gap) / box_h * 100),
    }


def _migrate_style_units_0_7_to_0_8(el: dict) -> None:
    """Rewrite one element's px measurements as rem, in place.

    rem (not em) is what makes this exact: em would resolve against the
    element's own font size, so dividing by 14 would only be right for elements
    whose font size happens to be 14.
    """
    style = el.get("style")
    if isinstance(style, dict):
        for key in _STYLE_PX_KEYS:
            if key in style:
                style[key] = _px_to_rem(style[key])
    for key in _ELEMENT_PX_KEYS:
        if key in el:
            el[key] = _px_to_rem(el[key])


def _page_box_0_7_to_0_8(page: dict) -> tuple[float, float]:
    """The px box a page's grid was laid out inside, at the reference size.

    A full page is the viewport. An overlay or sidebar is its own smaller box,
    which is the second, separate source of rigidity 0.8.0 removes.
    """
    page_type = page.get("page_type", "page")
    overlay = page.get("overlay") if isinstance(page.get("overlay"), dict) else {}
    if page_type == "sidebar":
        return (
            _num(overlay.get("width"), _SIDEBAR_DEFAULT_W) or _SIDEBAR_DEFAULT_W,
            REFERENCE_HEIGHT,
        )
    if page_type == "overlay":
        return (
            _num(overlay.get("width"), _OVERLAY_DEFAULT_W) or _OVERLAY_DEFAULT_W,
            _num(overlay.get("height"), _OVERLAY_DEFAULT_H) or _OVERLAY_DEFAULT_H,
        )
    return REFERENCE_WIDTH, REFERENCE_HEIGHT


def _cell_area(el: dict) -> tuple[int, int, int, int]:
    """(col, row, col_span, row_span) with the loader's own defaults applied."""
    area = el.get("grid_area")
    if not isinstance(area, dict):
        area = {}
    return (
        max(1, int(_num(area.get("col"), 1) or 1)),
        max(1, int(_num(area.get("row"), 1) or 1)),
        max(1, int(_num(area.get("col_span"), 1) or 1)),
        max(1, int(_num(area.get("row_span"), 1) or 1)),
    )


def _contains(outer: tuple, inner: tuple) -> bool:
    """True when inner's cell rect sits entirely inside outer's."""
    ocol, orow, ocs, ors = outer
    icol, irow, ics, irs = inner
    return (
        icol >= ocol
        and irow >= orow
        and icol + ics <= ocol + ocs
        and irow + irs <= orow + ors
    )


def _adopt_into_containers_0_7_to_0_8(elements: list, page_id: str) -> dict:
    """Decide which elements become children of which group.

    Groups used to be decorative frames painted *behind* their apparent
    children, who were really peers. 0.8.0 makes them real containers, so the
    migration has to guess the hierarchy the author drew. It only adopts on
    full containment -- a merely overlapping element stays a page-level peer --
    and the innermost group wins.
    """
    groups = [
        (idx, el) for idx, el in enumerate(elements)
        if isinstance(el, dict) and el.get("type") == "group"
    ]
    if not groups:
        return {}

    areas = {
        id(el): _cell_area(el)
        for el in elements if isinstance(el, dict)
    }
    # Smallest first, so the innermost containing group is found first.
    ranked = sorted(
        groups,
        key=lambda pair: (areas[id(pair[1])][2] * areas[id(pair[1])][3], pair[0]),
    )

    parent_of: dict[str, str] = {}
    for el in elements:
        if not isinstance(el, dict):
            continue
        el_id = el.get("id")
        if not el_id:
            continue
        for _, group in ranked:
            if group is el:
                continue
            group_id = group.get("id")
            if not group_id:
                continue
            if _contains(areas[id(group)], areas[id(el)]):
                parent_of[el_id] = group_id
                break

    # Identical-rect groups can point at each other. Walk each chain and drop
    # any link that closes a loop.
    for el_id in list(parent_of):
        seen = {el_id}
        cursor = parent_of.get(el_id)
        while cursor is not None:
            if cursor in seen:
                del parent_of[el_id]
                break
            seen.add(cursor)
            cursor = parent_of.get(cursor)

    if parent_of:
        log.info(
            "Project migration 0.8.0: page '%s' adopted %d element(s) into "
            "container(s) %s -- drag them out in the Outline panel if that is "
            "not the grouping you meant",
            page_id, len(parent_of), sorted(set(parent_of.values())),
        )
    return parent_of


def _convert_spacers_0_7_to_0_8(elements: list) -> list:
    """Drop invisible spacers; keep visible ones as empty labels.

    A spacer's only job was occupying grid cells, and there are no cells any
    more. But renderSpacer applies style, so one with a background or border was
    a decorative rectangle somebody drew on purpose -- that keeps its box.
    """
    kept = []
    for el in elements:
        if not isinstance(el, dict) or el.get("type") != "spacer":
            kept.append(el)
            continue
        style = el.get("style") if isinstance(el.get("style"), dict) else {}
        has_visual = any(
            style.get(key)
            for key in ("bg_color", "background_image", "background_gradient", "border_width")
        )
        if not has_visual:
            continue
        el["type"] = "label"
        el["text"] = ""
        el["label"] = None
        kept.append(el)
    return kept


def _relative_to_parent(child: dict, parent: dict) -> dict:
    """Re-express a child's page-percentage rect against its container's box."""
    p_w = parent["w"] or 100.0
    p_h = parent["h"] or 100.0
    return {
        "x": _round4((child["x"] - parent["x"]) / p_w * 100),
        "y": _round4((child["y"] - parent["y"]) / p_h * 100),
        "w": _round4(child["w"] / p_w * 100),
        "h": _round4(child["h"] / p_h * 100),
    }


_RETIRED_NAVIGATE_ACTIONS = ("navigate", "page")


def _rename_navigate_action(action: object) -> None:
    """Rewrite a retired page-move spelling to the canonical ``ui.navigate``.

    Recurses into ``value_map`` branches, which hold real actions.
    """
    if not isinstance(action, dict):
        return
    if action.get("action") in _RETIRED_NAVIGATE_ACTIONS:
        action["action"] = "ui.navigate"
    branches = action.get("map")
    if isinstance(branches, dict):
        for branch in branches.values():
            for sub in branch if isinstance(branch, list) else [branch]:
                _rename_navigate_action(sub)


def _migrate_element_navigate_0_7_to_0_8(el: dict) -> None:
    """Canonicalise the page-move action in every ``do`` slot of one element."""
    bindings = el.get("bindings")
    do_map = bindings.get("do") if isinstance(bindings, dict) else None
    if not isinstance(do_map, dict):
        return
    for slot in do_map.values():
        for action in slot if isinstance(slot, list) else [slot]:
            _rename_navigate_action(action)


def migrate_0_7_to_0_8(data: dict) -> dict:
    """
    Migrate from 0.7.0 to 0.8.0 -- the layout engine.

    - Grid cells become percentages of the parent box, computed from the exact
      rect CSS Grid laid out at 1280x800 so appearance is preserved.
    - Each page gains a ``layouts`` list holding those placements, and a
      ``snap`` increment that is an authoring aid rather than a container.
    - ``group`` elements become real containers and adopt what they contain.
    - Overlay/sidebar boxes stop being px and become viewport percentages.
    - px style measurements become rem.
    - ``spacer`` is gone; ``ui.settings.orientation`` moves onto the layout.
    - A button's page move is spelled ``ui.navigate``, matching the macro step
      and the WS frame. The older ``navigate``/``page`` spellings are rewritten
      here: the runtime no longer answers them, and an unmigrated one would
      leave a button that does nothing at all without reporting an error.
    """
    ui = data.get("ui")
    if not isinstance(ui, dict):
        data["openavc_version"] = "0.8.0"
        return data

    for page in ui.get("pages", []):
        if not isinstance(page, dict):
            continue
        for el in page.get("elements", []):
            if isinstance(el, dict):
                _migrate_element_navigate_0_7_to_0_8(el)
    for mel in ui.get("master_elements", []):
        if isinstance(mel, dict):
            _migrate_element_navigate_0_7_to_0_8(mel)

    settings = ui.get("settings") if isinstance(ui.get("settings"), dict) else {}
    orientation = settings.get("orientation") or "landscape"
    if orientation not in ("landscape", "portrait"):
        orientation = "landscape"
    settings.pop("orientation", None)

    overrides = settings.get("theme_overrides")
    pad = DEFAULT_GRID_GAP
    if isinstance(overrides, dict):
        pad = _num(overrides.get("grid_gap"), DEFAULT_GRID_GAP) or DEFAULT_GRID_GAP

    pages = [p for p in ui.get("pages", []) if isinstance(p, dict)]
    # Masters are converted against a page's grid, but the page loop removes
    # `grid` on its way past -- so remember each one first.
    page_grids: dict[str, tuple[int, int]] = {}

    for page in pages:
        grid = page.get("grid") if isinstance(page.get("grid"), dict) else {}
        columns = max(1, int(_num(grid.get("columns"), 12) or 12))
        rows = max(1, int(_num(grid.get("rows"), 8) or 8))
        if page.get("id"):
            page_grids[page["id"]] = (columns, rows)
        # page.grid_gap only ever overrode the gap between cells, never the
        # page padding -- that stayed on the CSS variable.
        gap = _num(page.pop("grid_gap", None), pad) or pad
        box_w, box_h = _page_box_0_7_to_0_8(page)

        page["elements"] = _convert_spacers_0_7_to_0_8(page.get("elements", []))
        elements = [el for el in page["elements"] if isinstance(el, dict)]

        page_rects = {}
        for el in elements:
            el_id = el.get("id")
            if not el_id:
                continue
            area = el.get("grid_area") if isinstance(el.get("grid_area"), dict) else {}
            page_rects[el_id] = _cell_rect_to_percent(
                area, columns, rows, box_w, box_h, pad, gap,
            )

        parent_of = _adopt_into_containers_0_7_to_0_8(elements, page.get("id", "?"))

        placements = {}
        for el in elements:
            el_id = el.get("id")
            if not el_id or el_id not in page_rects:
                continue
            el.pop("grid_area", None)
            _migrate_style_units_0_7_to_0_8(el)
            parent_id = parent_of.get(el_id)
            el["parent"] = parent_id
            rect = page_rects[el_id]
            if parent_id and parent_id in page_rects:
                rect = _relative_to_parent(rect, page_rects[parent_id])
            placements[el_id] = rect

        page.pop("grid", None)
        page["snap"] = {"enabled": True, "x": _round4(100.0 / 12), "y": _round4(100.0 / 8)}
        page["layouts"] = [{
            "id": orientation,
            "orientation": orientation,
            "primary": True,
            "inherits": None,
            "placements": placements,
            "hidden": [],
        }]

        # The overlay box itself stops being px.
        overlay = page.get("overlay")
        if isinstance(overlay, dict):
            if _num(overlay.get("width")) is not None:
                overlay["width"] = _round4(
                    _num(overlay["width"]) / REFERENCE_WIDTH * 100
                )
            if _num(overlay.get("height")) is not None:
                overlay["height"] = _round4(
                    _num(overlay["height"]) / REFERENCE_HEIGHT * 100
                )

    _migrate_masters_0_7_to_0_8(ui, pages, page_grids, orientation, pad)

    data["openavc_version"] = "0.8.0"
    return data


def _migrate_masters_0_7_to_0_8(
    ui: dict, pages: list, page_grids: dict, orientation: str, pad: float,
) -> None:
    """Convert master elements, which have no grid of their own.

    A master carries one grid_area but renders into whatever grid the current
    page has, so the same master genuinely landed at different pixel rects on
    different pages. There is no correct conversion, only a least-surprising
    one: use the grid of the first page it targets, which is the page the
    author was almost certainly looking at. Every project whose pages share the
    default 12x8 -- which is all of them in practice -- converts identically
    whichever page is picked. Afterwards the ambiguity is gone for good.
    """
    masters = [m for m in ui.get("master_elements", []) if isinstance(m, dict)]
    if not masters:
        return

    first_page_id = pages[0].get("id") if pages else None

    for master in masters:
        targets = master.get("pages", "*")
        reference_id = None
        if targets == "*" or not targets:
            reference_id = first_page_id
        elif isinstance(targets, str):
            reference_id = targets if targets in page_grids else None
        elif isinstance(targets, list):
            for target in targets:
                if target in page_grids:
                    reference_id = target
                    break

        columns, rows = page_grids.get(reference_id, (12, 8))
        log.info(
            "Project migration 0.8.0: master element '%s' converted against "
            "page '%s' (%dx%d grid)",
            master.get("id", "?"), reference_id or "<none, used 12x8>", columns, rows,
        )

        area = master.get("grid_area") if isinstance(master.get("grid_area"), dict) else {}
        rect = _cell_rect_to_percent(
            area, columns, rows, REFERENCE_WIDTH, REFERENCE_HEIGHT, pad, pad,
        )
        master.pop("grid_area", None)
        _migrate_style_units_0_7_to_0_8(master)
        master["parent"] = None
        master["placements"] = {orientation: rect}
        master["hidden"] = False


# Ordered list of migrations: (source_version, target_version, transform_fn)
MIGRATIONS = [
    ("0.1.0", "0.2.0", migrate_0_1_to_0_2),
    ("0.2.0", "0.3.0", migrate_0_2_to_0_3),
    ("0.3.0", "0.4.0", migrate_0_3_to_0_4),
    ("0.4.0", "0.5.0", migrate_0_4_to_0_5),
    ("0.5.0", "0.6.0", migrate_0_5_to_0_6),
    ("0.6.0", "0.7.0", migrate_0_6_to_0_7),
    ("0.7.0", "0.8.0", migrate_0_7_to_0_8),
]


def migrate_project(data: dict) -> tuple[dict, bool]:
    """
    Apply all needed migrations to bring a project to the current version.

    Returns:
        (migrated_data, was_migrated) — the transformed dict and whether
        any migrations were applied.
    """
    current = data.get("openavc_version", "0.1.0")
    migrated = False

    for source_ver, target_ver, migrator in MIGRATIONS:
        if current == source_ver:
            log.info(f"Migrating project from {source_ver} to {target_ver}")
            data = migrator(data)
            current = target_ver
            migrated = True

    if current != CURRENT_VERSION:
        log.warning(
            "Project version %s does not match current platform version %s "
            "— some features may not work correctly",
            current, CURRENT_VERSION,
        )

    return data, migrated
