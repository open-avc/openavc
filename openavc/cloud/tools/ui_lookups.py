"""What this system tells the AI's UI checks: the lookups they are handed.

The checks themselves (``ui/page_review.py``, ``ui/page_references.py``) import
nothing from the engine. They are handed functions, and this module is those
functions, answered from the loaded drivers, the plugin loader and the
project's ``ui/`` folder for the two callers in ``ui_tools``: a UI write and the
whole-project review. :func:`reference_lookups` is every lookup the reference
check takes, by its keyword, so a new one is added here once and reaches both.

Every answer is advisory. ``None`` is "no opinion" and never warns, and nothing
here may raise into a write: each lookup catches what its driver or plugin
throws and says nothing.

A bound ``device.<id>.…`` key is read once, by :func:`bound_key`: which
device's driver it names (the longest project id that prefixes it, by
``page_references``' own two parts), then, when
the rest is ``<type>.<id>.<property>`` on a child type that driver declares,
which child. The three questions asked of a key differ only in what they
answer from there.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openavc.core.command_params import file_command_check, missing_params_check
from openavc.drivers.child_ids import coerce_child_local_id, declared_child_ids
from openavc.ui.page_references import first_device_of, ids_longest_first
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: Device properties the PLATFORM sets, which no driver declares.
#:
#: `device.<id>.online` is the single commonest binding on any panel and it
#: appears in no DRIVER_INFO anywhere, so a property check that did not know
#: these would fire on the most correct page in the project. Set by
#: device_manager and connection_fault, not by the device.
_PLATFORM_DEVICE_PROPERTIES = frozenset({
    "connected", "enabled", "host", "name", "offline_detail", "offline_reason",
    "orphan_reason", "orphaned", "paused", "paused_for", "reconnect_attempt",
    "reconnect_failed",
    "restarting", "asleep", "wake_command", "online", "status", "last_seen", "error",
})


@dataclass(frozen=True)
class ChildKey:
    """The ``<type>.<local id>.<property>`` part of a key, on a declared type."""

    type_name: str
    raw_id: str
    prop: str
    type_def: dict

    def instance_schema(self, driver: Any) -> Any:
        """The driver's own schema for this one child, or ``{}``.

        A dynamic child type carries its schema per instance, so the driver is
        asked first; it is asked only when the id coerces to the type's kind.
        """
        local_id = coerce_child_local_id(self.type_def, self.raw_id)
        if local_id is None or not hasattr(driver, "get_child_schema"):
            return {}
        return driver.get_child_schema(self.type_name, local_id) or {}

    @property
    def type_schema(self) -> Any:
        """What the type declares for every child of it."""
        return self.type_def.get("state_variables") or {}


@dataclass(frozen=True)
class BoundKey:
    """A ``device.*`` key, read against the driver of the device it names."""

    driver: Any
    rest: str

    @property
    def info(self) -> Any:
        return getattr(self.driver, "DRIVER_INFO", None) or {}

    @property
    def declared(self) -> Any:
        """The driver's flat state variables."""
        return self.info.get("state_variables") or {}

    def child(self) -> ChildKey | None:
        """The child this key names, or None for a flat (or dotted flat) one."""
        parts = self.rest.split(".")
        if len(parts) < 3:
            return None
        type_def = (self.info.get("child_entity_types") or {}).get(parts[0])
        if not isinstance(type_def, dict):
            return None
        return ChildKey(parts[0], parts[1], ".".join(parts[2:]), type_def)


def longest_first(project: Any) -> list[str]:
    """The project's device ids, longest first: the order :func:`bound_key` takes them in."""
    return ids_longest_first(d.id for d in project.devices)


def _is_device_key(key: Any) -> bool:
    return isinstance(key, str) and key.startswith("device.")


def bound_key(devices: Any, device_ids: list[str], key: Any) -> BoundKey | None:
    """Which device's driver ``key`` names, or None.

    The first id in ``device_ids`` whose ``device.<id>.`` prefixes the key is
    the device (``page_references.first_device_of``, the reference check's own
    rule), never a split on dots, which cannot tell a child key from a nested
    property. None for a key that is not a ``device.*`` string, one no id
    prefixes, and a device with no driver loaded.
    """
    if devices is None or not _is_device_key(key):
        return None
    device_id = first_device_of(key, device_ids)
    if device_id is None:
        return None
    driver = devices.get_driver(device_id)
    if driver is None:
        return None
    return BoundKey(driver, key[len(f"device.{device_id}."):])


def _not_among(name: str, declared: Any) -> set[str] | None:
    """The declared names, when ``name`` is not one; None with none to judge by."""
    if not declared or name in declared:
        return None
    return set(declared)


def undeclared_state_property(devices: Any, device_ids: list[str], key: Any) -> set[str] | None:
    """The properties a driver DOES declare, when the bound one is not among them.

    None means no opinion, and that is the answer far more often than a set is:
    no driver loaded, a driver that declares no state at all, a child type it
    has never heard of, a child id that will not coerce. Only a driver that says
    "these are my state variables" can say a name is not one of them. A
    child's own schema, when the driver gives one, is the whole answer here;
    the type's is read only when it gives none.

    The device id itself is checked elsewhere; this is the property after it,
    which is the half that used to pass silently while a typo'd device, command,
    macro and page were all caught.
    """
    try:
        bound = bound_key(devices, device_ids, key)
        if bound is None or bound.rest in _PLATFORM_DEVICE_PROPERTIES:
            return None
        child = bound.child()
        if child is None:
            # A plain property, or one with dots that names no child type.
            return _not_among(bound.rest, bound.declared)
        return _not_among(child.prop, child.instance_schema(bound.driver) or child.type_schema)
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve declared properties for '%s'", key, exc_info=True)
        return None


def declared_child_roster(type_def: dict, config: Any) -> set[str] | None:
    """The child IDs this type declares, or None when it will not say.

    The rule itself is ``drivers.child_ids.declared_child_ids`` -- the same one
    the matrix picker builds its destination list from, so a page checked
    against an eight-output frame and a matrix drawn on one cannot disagree
    about how many outputs it has. Here they are only ever compared with the id
    written in a state key, so they arrive as text and lose their order.
    """
    ids = declared_child_ids(type_def, config)
    return {str(v) for v in ids} if ids else None


def unknown_child_id(devices: Any, device_ids: list[str], key: Any) -> set[str] | None:
    """The child IDs a driver declares, when the bound one is not among them.

    ``device.matrix.output.7.signal`` on a four-output frame renders perfectly
    and binds to nothing: the element draws, the key never resolves, and the
    control sits there blank forever. The property after it is already checked;
    this is the number in the middle, which a page repeating one block eight
    times gets wrong exactly once and silently.

    None means no opinion, which is the answer whenever the roster is dynamic,
    config-driven with nothing configured, or registered in Python code.
    """
    try:
        bound = bound_key(devices, device_ids, key)
        child = bound.child() if bound is not None else None
        if child is None:
            return None
        roster = declared_child_roster(child.type_def, getattr(bound.driver, "config", None) or {})
        if not roster or child.raw_id in roster:
            return None
        return roster
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve the child roster for '%s'", key, exc_info=True)
        return None


def declared_state_variable(devices: Any, device_ids: list[str], key: Any) -> dict | None:
    """What the driver declares about the state variable behind a bound key.

    The Builder answers this from the device schema it has already fetched, and
    offers a human a "Match driver range" button. Nothing answered it on this
    side, which is why a dB fader shipped with no upper bound: the driver states
    ``max: 0`` and the write path never looked.

    A flat name is looked up whole first, dots and all; then a child's own
    schema, falling back to its type's when the child's lacks the property.
    The entry is the driver's own object, never a copy.
    """
    try:
        bound = bound_key(devices, device_ids, key)
        if bound is None:
            return None
        entry = bound.declared.get(bound.rest)
        if isinstance(entry, dict):
            return entry
        child = bound.child()
        if child is None:
            return None
        schema = child.instance_schema(bound.driver)
        if child.prop not in schema:
            schema = child.type_schema
        entry = schema.get(child.prop)
        return entry if isinstance(entry, dict) else None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve a declared range for '%s'", key, exc_info=True)
    return None


def _panel_elements(engine: Any) -> list[dict]:
    """Every panel element the loaded plugins declare; none without a loader."""
    loader = getattr(engine, "plugins", None)
    if loader is None or not hasattr(loader, "get_all_extensions"):
        return []
    return [
        ext for ext in loader.get_all_extensions().get("panel_elements", [])
        if isinstance(ext, dict)
    ]


def declared_panel_elements(engine: Any, plugin_id: str) -> set[str] | None:
    """The panel-element types a loaded plugin declares, or None for no opinion.

    None means the plugin is not loaded here -- stopped, uninstalled, or simply
    not commissioned yet. That is a deployment fact, not an authoring mistake,
    and warning about it would fire on every page written before the plugin is
    installed.
    """
    if not plugin_id:
        return None
    try:
        ours = [ext for ext in _panel_elements(engine) if str(ext.get("plugin_id")) == plugin_id]
        if not ours:
            return None
        return {str(ext.get("type")) for ext in ours if ext.get("type")} or None
    except Exception:  # pragma: no cover - defensive; advisory path only
        log.debug("Could not resolve panel elements for '%s'", plugin_id, exc_info=True)
        return None


def declared_commands(devices: Any, device_id: str) -> set[str] | None:
    """The command names a device's driver declares, or None for no opinion.

    None is the answer far more often than an empty set is, and the difference
    matters: a device that is disabled, not connected yet, or backed by a driver
    that enumerates nothing must produce no warning at all. Only a driver that
    says "these are my commands" can say a name is not one of them.
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


def project_ui_files(engine: Any) -> set[str] | None:
    """Every path in the project's ``ui/`` folder, or None when it cannot say.

    None is "no opinion" and never warns, which is the only safe answer when
    there is no project path to look under -- a caller that guessed an empty set
    there would report every custom control in the project as missing its file.
    """
    try:
        from openavc.core.custom_ui import UI_DIR_NAME, iter_files
        project_path = getattr(engine, "project_path", None)
        if not project_path:
            return None
        ui_dir = Path(project_path).parent / UI_DIR_NAME
        if not ui_dir.is_dir():
            return set()
        return {f.relative_to(ui_dir).as_posix() for f in iter_files(ui_dir)}
    except Exception:  # pragma: no cover - advisory path only
        log.debug("Could not list the project's ui/ folder", exc_info=True)
        return None


def reference_lookups(
    engine: Any, devices: Any, project: Any, device_ids: list[str],
) -> dict[str, Any]:
    """Every lookup ``reference_findings`` takes, by its keyword, for this project.

    Built once per write and once per whole-project review, never per page, so
    the ``ui/`` folder is listed once and a review's pages share one answer.
    ``device_ids`` is :func:`longest_first` of the project, which the caller's
    range lookup reads too.
    """
    return {
        "page_ids": {p.id for p in project.ui.pages},
        "device_ids": set(device_ids),
        "macro_ids": {m.id for m in project.macros},
        "device_commands": lambda device_id: declared_commands(devices, device_id),
        "plugin_elements": lambda plugin_id: declared_panel_elements(engine, plugin_id),
        "undeclared_property": lambda key: undeclared_state_property(devices, device_ids, key),
        "unknown_child_id": lambda key: unknown_child_id(devices, device_ids, key),
        "missing_params": missing_params_check(devices, project),
        "ui_files": project_ui_files(engine),
        "file_command": file_command_check(devices, project),
    }
