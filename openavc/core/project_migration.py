"""
OpenAVC project format migration: the chain that brings a project file written
by an older format version up to the one this platform writes.

Each step takes the project dict as one version left it, rewrites it in place
for the next version, stamps that version and returns the same dict.
`MIGRATIONS` is the chain in order, and `migrate_project` runs every step from
the file's own version on. A new format version is one `migrate_X_to_Y` and one
row: `CURRENT_VERSION` is where the chain ends, so the two cannot disagree.

The rule worth knowing before changing a step: a step says what it did to the
projects of its day, so the steps that rewrite UI elements keep their own
numbers and type lists rather than reading today's renderer or page review
(each says so where it does), and reach elements through the walk they were
written with: two walks, one per rule, so editing either changes old steps.
The 0.8.0 grid-to-percent layout conversion is ``core/layout_migration.py``.
"""

from typing import NamedTuple

from openavc.core.layout_migration import convert_grid_layout

# Connection-related config fields that belong in the connections table. The
# list is part of the driver contract (a connection field cannot be
# learned_from a state variable), so it is kept in openavc/drivers/spec.py.
# `port` is a string for serial and an int for TCP/UDP/OSC/HTTP. The serial
# line params live with the connection alongside `baudrate` so a template
# deployment swaps the whole serial config per site. `bridge` / `bridge_port`
# name the bridge device and the port it advertises (e.g. "serial:1"), which
# core.device_config rewrites into the bridge's pass-through endpoint;
# `usb_serial` is the adapter's USB serial number, which it turns into the
# adapter's live OS port path. Older `com_port`/`baud_rate` are translated by
# migrate_0_1_to_0_2.
from openavc.drivers.spec import CONNECTION_FIELDS
from openavc.utils.logger import get_logger

log = get_logger(__name__)


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
    for device in data.get("devices", []):
        device_id = device.get("id", "")
        config = device.get("config", {})
        # Renamed BEFORE the move, so the moved fields carry the new names.
        _rename_serial_fields_0_2(config)
        conn_overrides = {
            key: config.pop(key) for key in list(config.keys()) if key in CONNECTION_FIELDS
        }
        if conn_overrides:
            connections[device_id] = conn_overrides

    data["connections"] = connections
    data.setdefault("driver_dependencies", [])
    data["openavc_version"] = "0.2.0"
    return data


_SERIAL_RENAMES_0_2 = (("com_port", "port"), ("baud_rate", "baudrate"))


def _rename_serial_fields_0_2(config: dict) -> None:
    """The legacy serial field names, renamed in place. If both a legacy name
    and its new one are present (e.g. mixed manual edits), the new name wins."""
    for old_name, new_name in _SERIAL_RENAMES_0_2:
        if old_name in config and new_name not in config:
            config[new_name] = config.pop(old_name)
        elif old_name in config:
            config.pop(old_name)


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

    groups_map = _groups_named_by_devices_0_4(data.get("devices", []))

    # Merge the per-device assignments INTO any device_groups already present
    # (a hand-edited or partially-migrated file can carry both). Dropping
    # either side would silently lose group memberships. Existing groups keep
    # their own ids; groups synthesized from a device's group name get a
    # sanitized id and merge with an existing group of the same id.
    groups = list(data.get("device_groups") or [])
    by_id = {g.get("id"): g for g in groups}
    for name, ids in groups_map.items():
        gid = sanitize_id(name)
        if by_id.get(gid) is None:
            by_id[gid] = {"id": gid, "name": name, "device_ids": []}
            groups.append(by_id[gid])
        _add_group_members_0_4(by_id[gid], ids)
    data["device_groups"] = groups

    data["openavc_version"] = "0.4.0"
    return data


def _groups_named_by_devices_0_4(devices: list) -> dict[str, list[str]]:
    """Each group name a device carried, with the ids of the devices that
    named it, in the order first named. The field leaves every device."""
    groups_map: dict[str, list[str]] = {}
    for device in devices:
        group_name = device.pop("group", None)
        if group_name:
            groups_map.setdefault(group_name, []).append(device.get("id", ""))
    return groups_map


def _add_group_members_0_4(group: dict, ids: list) -> None:
    """The ids not already listed, added in order; an empty id is not one."""
    device_ids = group.setdefault("device_ids", [])
    for device_id in ids:
        if device_id and device_id not in device_ids:
            device_ids.append(device_id)


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


# The steps that rewrite UI elements reach every page element and then every
# master element, in two ways that differ on a list they cannot read. Each step
# keeps the one it was written with: a step says what it did to the projects of
# its day.

def _elements_as_written(ui: dict):
    """Every page element, then every master element, as the 0.7.0 and 0.8.0
    steps read them: each list as written, so a null one stops the step with a
    TypeError (a PUT answers it with a 422), and one element at a time, so the
    elements before it are already rewritten when it stops."""
    for page in ui.get("pages", []):
        if isinstance(page, dict):
            yield from (el for el in page.get("elements", []) if isinstance(el, dict))
    yield from (mel for mel in ui.get("master_elements", []) if isinstance(mel, dict))


def _elements(data: dict) -> list:
    """Every page element, then every master element, as the steps from 0.10.0
    on read them: no ui, a missing or null list and anything that is not a dict
    all read as nothing. The list is made before the step changes anything, so
    one that cannot be read stops the step with nothing changed."""
    ui = data.get("ui")
    if not isinstance(ui, dict):
        return []
    elements = [
        el
        for page in ui.get("pages", []) or []
        if isinstance(page, dict)
        for el in page.get("elements", []) or []
    ]
    elements += list(ui.get("master_elements", []) or [])
    return [el for el in elements if isinstance(el, dict)]


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

    show = _show_0_7(bindings)
    do = _do_0_7(bindings)
    out: dict = {}
    if show:
        out["show"] = show
    if do:
        out["do"] = do
    # The matrix preset bar (`presets`) is relocated to the element's
    # matrix_config by the element-level caller, so it is intentionally not
    # carried on `bindings` here. The orphan `meter` slot (never wired to an
    # editor or runtime) is dropped; every other v0.6.0 binding key is mapped
    # by the two halves (the show sources, the action slots).
    return out


def _show_0_7(bindings: dict) -> dict:
    """``show``: what the element reflects from state, as value, items, look
    and visible_when, in that order."""
    show: dict = {}
    value = _show_value_0_7(bindings)
    if value is not None:
        show["value"] = value
    # show.items -- list row population.
    if isinstance(bindings.get("items"), dict):
        show["items"] = bindings["items"]
    # show.look -- state-driven appearance (feedback / LED color map / select
    # per-option style). Inner shape is carried verbatim; runtime branches on it.
    look = _last_object(bindings, ("feedback", "color"))
    if look is not None:
        show["look"] = look
    # show.visible_when -- unchanged condition shape.
    if bindings.get("visible_when") is not None:
        show["visible_when"] = bindings["visible_when"]
    return show


def _show_value_0_7(bindings: dict):
    """show.value -- the thing the control IS, or None.

    The later source wins: a `variable` (two-way read source) overrides a plain
    `value`, mirroring the panel's `bindings.variable || bindings.value`;
    `text` is the label's value; and a list's value is its current selection
    (was the `selected` two-way slot), which overrides them all.
    """
    if isinstance(bindings.get("selected"), dict):
        sel_key = bindings["selected"].get("key", "")
        return {"source": "state", "key": sel_key, "write_back": True}
    if isinstance(bindings.get("variable"), dict):
        key = bindings["variable"].get("key", "")
        value: dict = {"source": "state", "key": key}
        if isinstance(key, str) and key.startswith("var."):
            value["write_back"] = True
        return value
    return _last_object(bindings, ("value", "text"))


def _last_object(bindings: dict, slots: tuple):
    """The last of these binding slots that holds an object, or None."""
    found = None
    for slot in slots:
        if isinstance(bindings.get(slot), dict):
            found = bindings[slot]
    return found


def _do_0_7(bindings: dict) -> dict:
    """``do``: an action list per interaction, in _ACTION_SLOTS_0_7's order;
    a slot with no action in it is left out."""
    do: dict = {}
    for slot in _ACTION_SLOTS_0_7:
        actions = _actions_0_7(bindings[slot]) if slot in bindings else []
        if actions:
            do[slot] = actions
    return do


def _actions_0_7(raw) -> list:
    """One v0.6.0 action slot as a list: a single action object becomes a
    one-item list, and anything that is not an action is dropped."""
    if isinstance(raw, list):
        return [a for a in raw if isinstance(a, dict)]
    if isinstance(raw, dict) and raw:
        return [raw]
    return []


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
        for el in _elements_as_written(ui):
            _migrate_element_bindings_0_6_to_0_7(el)

    data["openavc_version"] = "0.7.0"
    return data


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
    if isinstance(ui, dict):
        for el in _elements_as_written(ui):
            _migrate_element_navigate_0_7_to_0_8(el)
        convert_grid_layout(ui)

    data["openavc_version"] = "0.8.0"
    return data


def migrate_0_8_to_0_9(data: dict) -> dict:
    """
    Migrate from 0.8.0 to 0.9.0 -- custom controls.

    Nothing in an existing project changes, and that is the whole story: 0.9.0
    adds a ``custom`` element type carrying ``custom_file`` (a path inside the
    project's ``ui/`` tree) and ``custom_config``, and every element that
    existed before it still means exactly what it meant. The step exists so the
    stamped version says whether a project may contain one -- an older platform
    opening a 0.9.0 project has no renderer for that type and logs the version
    mismatch, instead of drawing a page with a silent hole in it.
    """
    data["openavc_version"] = "0.9.0"
    return data


#: What a pattern-form matrix drew when it stated no count. Kept here rather
#: than read from the renderer because a migration's job is to preserve what the
#: panel already drew, and the renderer stops defaulting in 0.10.0.
_MATRIX_LEGACY_DEFAULT_COUNT = 4


class _LegacyMatrixAxis(NamedTuple):
    """One axis as the pattern form described it."""

    name: str
    count_key: str
    labels_key: str
    # Each pattern key this axis took, and the generator key it becomes.
    patterns: tuple


#: The two axes, and which of the pattern form's keys became which generator key.
_MATRIX_AXES_0_10 = (
    _LegacyMatrixAxis("sources", "input_count", "input_labels", (
        ("input_key_pattern", "label_key"),
    )),
    _LegacyMatrixAxis("destinations", "output_count", "output_labels", (
        ("output_key_pattern", "label_key"),
        ("route_key_pattern", "route_key"),
        ("audio_route_key_pattern", "audio_route_key"),
    )),
)

#: The eight keys the pattern form used. Dropped from the config afterwards --
#: left in place they would be keys the renderer no longer reads, and every
#: migrated project would open with a warning about its own history.
_MATRIX_LEGACY_KEYS = frozenset(
    key
    for axis in _MATRIX_AXES_0_10
    for key in (axis.count_key, axis.labels_key, *(legacy for legacy, _ in axis.patterns))
)


def _migrate_matrix_config_0_9_to_0_10(config: dict) -> dict:
    """One matrix's counts and glob patterns, as the generator that says the same.

    Nothing about what draws changes. ``input_count: 8`` plus
    ``route_key_pattern: "device.mx.output.*.input"`` said "eight destinations,
    numbered from one, each watching its number substituted into that key" --
    which is exactly what the generator says, in the shape that can now also say
    the things the pattern form could not.

    An axis whose count was never stated migrates to a stated 4, because 4 is
    what it was drawing. Recording the number it drew is the honest reading of a
    migration; the review is where "did you mean 4?" belongs.

    **The lock column is the one thing that does change, deliberately.** A
    matrix that never said ``show_lock`` used to draw a lock button per
    destination and now does not, so an older panel opens a column narrower
    than it was. Nothing here restores it, and nothing should: that button sent
    nothing, reached no other panel, and was forgotten the next time the
    element redrew. Writing ``show_lock: true`` into every migrated matrix
    would preserve the picture by preserving a control that does not work,
    and the lock that replaced it is per destination and asked for by name
    (``lock_key``), so there is no old value to carry into it. A project that
    wants the button back turns it on and says which variable backs it.
    """
    out = {k: v for k, v in config.items() if k not in _MATRIX_LEGACY_KEYS}
    for axis in _MATRIX_AXES_0_10:
        # A file that already speaks 0.10.0 keeps what it says. Nothing writes
        # one yet, but a hand-edited project should not have its lists replaced
        # by an expansion of keys it also happens to carry.
        if axis.name not in config:
            out[axis.name] = {"from": _axis_generator_0_10(config, axis)}
    return out


def _axis_generator_0_10(config: dict, axis: _LegacyMatrixAxis) -> dict:
    """One axis's pattern-form keys as its generator: the count, then the
    labels, then each pattern key that was set."""
    generator: dict = {"count": _legacy_count_0_10(config.get(axis.count_key))}
    labels = config.get(axis.labels_key)
    if isinstance(labels, list) and labels:
        generator["labels"] = labels
    for legacy, key in axis.patterns:
        if config.get(legacy):
            generator[key] = config[legacy]
    return generator


def _legacy_count_0_10(value) -> int:
    """The count an axis drew: an unstated or unreadable one drew 4, and
    nothing drew fewer than none."""
    try:
        count = int(value or _MATRIX_LEGACY_DEFAULT_COUNT)
    except (TypeError, ValueError):
        count = _MATRIX_LEGACY_DEFAULT_COUNT
    return max(count, 0)


def migrate_0_9_to_0_10(data: dict) -> dict:
    """
    Migrate from 0.9.0 to 0.10.0 -- the matrix becomes two lists.

    A matrix used to be two counts, two label arrays and four glob patterns, so
    it could only describe a rectangular frame with contiguous ports numbered
    from one on a single device reporting plain integers. Now it is a list of
    sources and a list of destinations, each destination carrying its own route
    key and an opaque value, which is what it takes to express a decoder with six
    routing planes, a frame with two ports patched, string ids, or a source that
    is an IP address.

    Every existing matrix migrates to the GENERATOR form -- the same shorthand,
    stated as a generator rather than as loose keys -- so no project needs
    re-authoring and no matrix changes shape.
    """
    for el in _elements(data):
        if el.get("type") != "matrix":
            continue
        config = el.get("matrix_config")
        # No config at all stays that way: the element is unconfigured, and
        # inventing a generator for it would hide that behind a shape.
        if isinstance(config, dict) and config:
            el["matrix_config"] = _migrate_matrix_config_0_9_to_0_10(config)

    data["openavc_version"] = "0.10.0"
    return data


def migrate_0_10_to_0_11(data: dict) -> dict:
    """
    Migrate from 0.10.0 to 0.11.0 -- the project says which readings matter.

    Almost nothing in an existing project moves, and that is the point: 0.11.0
    adds a top-level ``monitors`` list, and a project that declares none reads
    exactly as it did. The stamped version exists so an older platform opening
    the file knows there may be a declaration it has no idea what to do with,
    rather than quietly ignoring the thing the room was set up to watch.

    The one mechanical change is the old ``dashboard: true`` flag on a
    variable. It said "show this on the local Dashboard", which is precisely a
    monitored reading with NO limits, so it becomes one -- carrying only the
    label and type the variable already declared, which is all the tile ever
    had. The flag itself is dropped rather than left beside its replacement:
    two fields meaning the same thing is how the Dashboard and the card end up
    disagreeing about what is on them.

    The variable's ``validation`` block is deliberately NOT carried across as
    limits. It says what may be STORED in the variable, which is a different
    claim from what is healthy in the room -- and a migration that turned every
    existing validation rule into an alert would light up rooms whose owners
    never asked to be told anything.
    """
    existing = data.get("monitors")
    monitors = list(existing) if isinstance(existing, list) else []
    already = _monitored_keys_0_11(monitors)

    for var in data.get("variables", []) or []:
        key = _dashboard_key_0_11(var)
        if key is None or key in already:
            continue
        already.add(key)
        monitors.append({
            "key": key,
            "label": var.get("label") or "",
            "type": var.get("type") or "string",
        })

    if monitors:
        data["monitors"] = monitors

    data["openavc_version"] = "0.11.0"
    return data


def _monitored_keys_0_11(monitors: list) -> set[str]:
    """The keys the project's monitors already name."""
    return {
        m.get("key") for m in monitors
        if isinstance(m, dict) and isinstance(m.get("key"), str)
    }


def _dashboard_key_0_11(var) -> str | None:
    """The var.<id> key a variable's dashboard flag asked to show, or None.
    The flag leaves every variable either way."""
    if not isinstance(var, dict):
        return None
    tracked = var.pop("dashboard", None)
    var_id = var.get("id")
    if not tracked:
        return None
    return f"var.{var_id}" if isinstance(var_id, str) and var_id else None


def migrate_0_11_to_0_12(data: dict) -> dict:
    """
    Migrate from 0.11.0 to 0.12.0 -- a control can call a function and emit an
    event.

    Nothing in an existing project moves, and the stamp is the whole point. A
    UI binding gains `event.emit` (the macro step, written directly on a
    control) and `script.call` gains `params`, which changes what an existing
    `script.call` MEANS: on 0.11.0 it emitted `script.call.<function>` and only
    a script subscribing to that name by hand ever heard it, while from 0.12.0
    it calls the function itself. An older platform opening a 0.12.0 project
    would emit where the author expected a call, and would reach no branch at
    all for `event.emit` -- so the version has to say which meaning the project
    was written against, exactly as 0.9.0 does for a `custom` element.
    """
    data["openavc_version"] = "0.12.0"
    return data


def migrate_0_12_to_0_13(data: dict) -> dict:
    """
    Migrate from 0.12.0 to 0.13.0 -- the project carries the settings a whole
    site shares.

    Nothing in an existing project moves and nothing is written: the block is
    left ABSENT, so every value comes from the model's own defaults. That is
    deliberate. An absent block and a block full of defaults mean the same
    thing today, but only the absent one keeps meaning "nobody chose" if a
    default ever changes -- writing today's numbers into every project on
    migration would freeze them there forever and make a future default change
    invisible to every project that had already been opened once.

    The visible consequence is that the idle dim arrives ON, at five minutes
    and twenty percent, on panels that never asked for it. That is the point:
    it exists for panels showing one page around the clock, and off-by-default
    meant every unit needed a visit to switch it on. It only applies where the
    instance drives its own screen, and any touch clears it.
    """
    data["openavc_version"] = "0.13.0"
    return data


def migrate_0_13_to_0_14(data: dict) -> dict:
    """
    Migrate from 0.13.0 to 0.14.0 -- a control can name itself to the macro it
    runs.

    A version stamp: nothing in an existing project moves, and no element gains
    a `tag`. What the stamp records is that a press now hands the macro a
    context (`$trigger.tag`, `$trigger.element`, and the event tokens), which it
    did not before -- a macro run from a control used to learn nothing at all
    about which control ran it.

    That is a real difference to an older platform rather than a cosmetic one,
    the same shape as 0.12.0's binding actions: open a 0.14.0 project on a
    platform that passes no context and every `$trigger.tag` in it resolves to
    nothing, so a row of buttons sharing one macro silently all do the same
    thing. The version is what says which meaning the project was written
    against.
    """
    data["openavc_version"] = "0.14.0"
    return data


# The element types whose panel renderer never draws `label`, as they stood at
# 0.15.0. Written out rather than read from the page review's table: a
# migration says what it did to the projects of its day, and has to keep doing
# exactly that after the table moves on.
_UNDRAWN_LABEL_TYPES_0_15 = frozenset({"clock", "custom", "label", "plugin"})


def migrate_0_14_to_0_15(data: dict) -> dict:
    """
    Migrate from 0.14.0 to 0.15.0 -- a label on a control that draws none is
    dropped.

    The UI Builder used to give every new custom control the label "Custom
    Control" and every plugin control its plugin type as a label, and offered a
    Label field on a clock. None of them draws a label, and neither does a text
    label (it draws `text`), so the page review warns about every one of those
    labels -- and the Builder, which now offers the field only where it is
    drawn, has no way left to clear it. Every page and master element of those
    four types loses its `label`, whoever wrote it, and nothing else moves.

    Nothing on a panel changes: no renderer read the field. What a person can
    see go is the caption the Outline list showed beside such a control's id,
    which was the label. An older platform opening a 0.15.0 project logs the
    version mismatch and loads it, drawing the same thing it always did.
    """
    for el in _elements(data):
        if el.get("type") in _UNDRAWN_LABEL_TYPES_0_15:
            el.pop("label", None)

    data["openavc_version"] = "0.15.0"
    return data


def migrate_0_15_to_0_16(data: dict) -> dict:
    """
    Migrate from 0.15.0 to 0.16.0 -- a fader with no unit is given the `%` it
    has always drawn.

    The panel used to draw `%` beside a fader's value whenever the fader named
    no unit, so a fader bound to a dB gain the driver gave no unit read
    "-41.0 %". It now draws no unit, as the slider and the gauge always have.
    Every fader the UI Builder made stores its unit, but one written by the AI
    or by hand may have none, and those have shown `%` all along. Each page and
    master fader whose `unit` is absent or null is given `%`, so it reads the
    same after the upgrade. An empty string is somebody clearing the Unit field
    and is left alone: that fader now shows no unit, which is what clearing it
    asked for.
    """
    for el in _elements(data):
        if el.get("type") == "fader" and el.get("unit") is None:
            el["unit"] = "%"

    data["openavc_version"] = "0.16.0"
    return data


# Ordered list of migrations: (source_version, target_version, transform_fn)
MIGRATIONS = [
    ("0.1.0", "0.2.0", migrate_0_1_to_0_2),
    ("0.2.0", "0.3.0", migrate_0_2_to_0_3),
    ("0.3.0", "0.4.0", migrate_0_3_to_0_4),
    ("0.4.0", "0.5.0", migrate_0_4_to_0_5),
    ("0.5.0", "0.6.0", migrate_0_5_to_0_6),
    ("0.6.0", "0.7.0", migrate_0_6_to_0_7),
    ("0.7.0", "0.8.0", migrate_0_7_to_0_8),
    ("0.8.0", "0.9.0", migrate_0_8_to_0_9),
    ("0.9.0", "0.10.0", migrate_0_9_to_0_10),
    ("0.10.0", "0.11.0", migrate_0_10_to_0_11),
    ("0.11.0", "0.12.0", migrate_0_11_to_0_12),
    ("0.12.0", "0.13.0", migrate_0_12_to_0_13),
    ("0.13.0", "0.14.0", migrate_0_13_to_0_14),
    ("0.14.0", "0.15.0", migrate_0_14_to_0_15),
    ("0.15.0", "0.16.0", migrate_0_15_to_0_16),
]

# The format this platform writes: where the chain ends.
CURRENT_VERSION = MIGRATIONS[-1][1]


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
