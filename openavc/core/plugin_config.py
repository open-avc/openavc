"""Plugin CONFIG_SCHEMA validation, shared by every config-write path.

The REST endpoint (openavc/api/plugins.py) and the cloud AI tool
(openavc/cloud/tools/plugin_tools.py) both persist plugin config; they must
accept and reject exactly the same shapes, so the validator lives here and
both import it.

Past the schema's types, a plugin may judge a proposed config itself through
an optional ``validate_config(config)`` class method: the parts it would skip
or misread, one sentence each (a control-surface plugin's key layouts, which no
schema describes). The rules stay in the plugin, the only thing that reads
them. Both doors refuse a save that adds one of those problems and save one
that only carries problems the stored config already had, so a project that
arrived with one can still be edited.
"""

import copy
import inspect
import re
from collections import Counter
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger(__name__)

SCHEMA_TYPE_VALIDATORS: dict[str, type | tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def validate_plugin_config(config: dict, schema: dict) -> str | None:
    """Validate plugin config value types against its CONFIG_SCHEMA.

    Returns error string or None. Checks only the types of values that are
    present. Required fields that are absent are NOT an error here — a
    config form is saved incrementally during first-time setup, so a
    partially-filled config must persist. Missing required fields are a
    start-time concern, reported separately via missing_required_fields()
    so the write paths can attach a warning instead of rejecting the save.
    """
    errors: list[str] = []
    for key, field_def in schema.items():
        if not isinstance(field_def, dict):
            continue

        # Group fields — recurse
        if field_def.get("type") == "group":
            sub_schema = field_def.get("fields", {})
            sub_config = config.get(key, {})
            if isinstance(sub_schema, dict) and isinstance(sub_config, dict):
                err = validate_plugin_config(sub_config, sub_schema)
                if err:
                    errors.append(err)
            continue

        # Type check for present values
        value = config.get(key)
        if value is not None:
            expected_type = field_def.get("type", "")
            valid_types = SCHEMA_TYPE_VALIDATORS.get(expected_type)
            if valid_types and not isinstance(value, valid_types):
                errors.append(
                    f"Config field '{key}' should be {expected_type}, "
                    f"got {type(value).__name__}"
                )

    if errors:
        return "Plugin config validation failed: " + "; ".join(errors)
    return None


def missing_required_fields(config: dict, schema: dict) -> list[str]:
    """Names of required schema fields (with no default) absent from config.

    Group fields are reported dotted (``group.field``). The plugin can't
    start until these are set, but their absence must not block saving the
    fields the user has filled in so far.
    """
    missing: list[str] = []
    for key, field_def in schema.items():
        if not isinstance(field_def, dict):
            continue

        if field_def.get("type") == "group":
            sub_schema = field_def.get("fields", {})
            sub_config = config.get(key, {})
            if isinstance(sub_schema, dict) and isinstance(sub_config, dict):
                missing.extend(
                    f"{key}.{name}"
                    for name in missing_required_fields(sub_config, sub_schema)
                )
            continue

        if (
            field_def.get("required")
            and key not in config
            and "default" not in field_def
        ):
            missing.append(key)
    return missing


def _schema_for_plugin(plugin_id: str) -> dict | None:
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
    if plugin_class is None:
        return None
    schema: Any = getattr(plugin_class, "CONFIG_SCHEMA", None)
    if not schema or not isinstance(schema, dict):
        return None
    return schema


def validate_config_for_plugin(plugin_id: str, config: dict) -> str | None:
    """Validate config value types against the installed plugin's CONFIG_SCHEMA.

    Returns error string or None. A plugin that isn't installed (or has no
    schema) validates clean — config for missing plugins is stored as-is so
    it survives until the plugin is installed.
    """
    schema = _schema_for_plugin(plugin_id)
    if schema is None:
        return None
    return validate_plugin_config(config, schema)


def missing_required_for_plugin(plugin_id: str, config: dict) -> list[str]:
    """Required-field names still absent from config, per the plugin's schema.

    Empty for a plugin that isn't installed or declares no schema.
    """
    schema = _schema_for_plugin(plugin_id)
    if schema is None:
        return []
    return missing_required_fields(config, schema)


def plugin_config_problems(plugin_id: str, config: dict) -> list[str]:
    """The installed plugin's own sentences about what ``config`` gets wrong.

    Asks the plugin class's optional ``validate_config``. A plugin that is not
    installed or has no hook has no opinion, and so does a hook that raises or
    answers with anything but a list of sentences: a broken hook never blocks
    a save. The hook is handed a copy, so it cannot change what is stored.
    """
    from openavc.core.plugin_loader import _PLUGIN_CLASS_REGISTRY

    plugin_class = _PLUGIN_CLASS_REGISTRY.get(plugin_id)
    hook = getattr(plugin_class, "validate_config", None) if plugin_class else None
    if not callable(hook):
        return []
    if inspect.iscoroutinefunction(hook):
        log.warning("Plugin '%s': validate_config must not be async; not asked", plugin_id)
        return []
    try:
        answer = hook(copy.deepcopy(config))
    except Exception:
        log.exception("Plugin '%s': validate_config failed; config not checked", plugin_id)
        return []
    if not isinstance(answer, (list, tuple)):
        log.warning("Plugin '%s': validate_config did not return a list; ignored", plugin_id)
        return []
    return [s for s in answer if isinstance(s, str) and s.strip()]


def config_problems_added(
    plugin_id: str, new_config: dict, stored_config: dict | None,
) -> tuple[list[str], list[str]]:
    """Split the plugin's problems with ``new_config`` in two: the ones this
    save adds (refused) and the ones the stored config already had (saved,
    and reported). A config editor that re-sends a whole stored config must
    not be refused for a problem it did not write and cannot draw.

    Sentences are compared with their list positions blanked, and counted:
    deleting an entry ahead of a stored problem renumbers it ("buttons[1]"
    becomes "buttons[0]") without making it new, while a second copy of a
    stored problem still is one.
    """
    problems = plugin_config_problems(plugin_id, new_config)
    if not problems:
        return [], []
    before = Counter(_unplaced(p) for p in plugin_config_problems(plugin_id, stored_config or {}))
    added: list[str] = []
    kept: list[str] = []
    for problem in problems:
        key = _unplaced(problem)
        if before[key] > 0:
            before[key] -= 1
            kept.append(problem)
        else:
            added.append(problem)
    return added, kept


def _unplaced(sentence: str) -> str:
    return re.sub(r"\[\d+\]", "[]", sentence)
