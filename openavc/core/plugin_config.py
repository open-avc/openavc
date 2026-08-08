"""Plugin CONFIG_SCHEMA validation, shared by every config-write path.

The REST endpoint (openavc/api/plugins.py) and the cloud AI tool
(openavc/cloud/tools/plugin_tools.py) both persist plugin config; they must
accept and reject exactly the same shapes, so the validator lives here and
both import it.
"""

from typing import Any

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
