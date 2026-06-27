"""
OpenAVC project format migration system.

Applies versioned transforms when loading older project files.
Each migration is a pure function: dict -> dict.
"""

from server.utils.logger import get_logger

log = get_logger(__name__)

CURRENT_VERSION = "0.7.0"

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
    # resolver (engine.resolved_device_config) reads these to rewrite the
    # downstream's effective transport to the bridge's pass-through endpoint.
    "bridge", "bridge_port",
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
    # Collect group assignments from devices
    groups_map: dict[str, list[str]] = {}
    for device in data.get("devices", []):
        group_name = device.pop("group", None)
        if group_name:
            groups_map.setdefault(group_name, []).append(device.get("id", ""))

    # Only create device_groups if there were actual group assignments
    existing = data.get("device_groups")
    if not existing:
        data["device_groups"] = [
            {
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "device_ids": ids,
            }
            for name, ids in groups_map.items()
        ]
    else:
        data.setdefault("device_groups", [])

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


# Ordered list of migrations: (source_version, target_version, transform_fn)
MIGRATIONS = [
    ("0.1.0", "0.2.0", migrate_0_1_to_0_2),
    ("0.2.0", "0.3.0", migrate_0_2_to_0_3),
    ("0.3.0", "0.4.0", migrate_0_3_to_0_4),
    ("0.4.0", "0.5.0", migrate_0_4_to_0_5),
    ("0.5.0", "0.6.0", migrate_0_5_to_0_6),
    ("0.6.0", "0.7.0", migrate_0_6_to_0_7),
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
