"""What a YAML driver's ``DRIVER_INFO`` says: the fields copied from its
definition, and the entry each of its commands gets.

The platform reads a live driver's ``DRIVER_INFO``, never its file, so a
definition field the platform uses and this module does not copy is inert
for a YAML driver while working for a Python one, which writes
``DRIVER_INFO`` itself. ``tests/test_driver_field_reaches_runtime.py`` fails a
contract field that reaches neither these tables nor its list of fields read
elsewhere.

The rules worth knowing before changing anything here:

* **The fields every driver has** take a default when the file leaves them
  out: the id ``unknown``, the name the id, manufacturer ``Generic``,
  category ``utility``, version ``1.0.0``, author ``Community``, transport
  ``tcp``, an empty description, and an empty ``default_config``,
  ``config_schema`` and ``state_variables``.
* **The rest are copied as written, only when present**, whatever the
  value, in the order the tables give (``_INFO_FIELDS``, then ``commands``,
  then ``_INFO_FIELDS_AFTER_COMMANDS``).
* **A command's entry** is its label (else its name), its params, then each
  of ``_COMMAND_META_FIELDS`` it declares. The transport fields (``method``,
  ``path``, ``body``, ``address``, ``args``) are there so the IDE shows them.
  Most other command fields are read off the definition at send time, but
  some are read off ``DRIVER_INFO``: for example the connected gate and a
  promoted button's availability (``available_offline``, read by
  ``command_gates.available_offline`` and ``actions._command_availability``),
  the commanded-restart window (``restarts_device_for``,
  ``DeviceManager._arm_restart_window``), the device audit's declared effect
  (``sets``, ``query_for``) and every door a person sends a command from by
  hand (``confirm``). A field one of them starts reading goes into that
  table.
* **The driver-class factory and the inline-protocol merge both build
  command entries here** (``build_commands_meta``), so a command the device
  authored itself looks the same in the IDE as one from the file.
"""

from __future__ import annotations

from typing import Any

# Definition fields copied onto DRIVER_INFO as written, in the order they land
# there, because the platform reads them off a live driver rather than its
# file.
_INFO_FIELDS = (
    "help",
    "protocols",
    "discovery",
    "device_settings",
    # So a driver installed from a file, not the catalog, still says which
    # models it covers.
    "compatible_models",
    # BaseDriver validates child properties against it and adds the `online`
    # and `label` keys; the cloud state relay reads each property's
    # `cloud_priority`.
    "child_entity_types",
    # The typed ports other devices connect through (get_driver_bridge_ports,
    # the bridge resolver in core.device_config).
    "bridge",
    # Read on connect to subscribe the shared listener.
    "push",
    # The media one protocol runs over (["tcp", "serial"]); the connection
    # picks one, config["transport"] first.
    "transports",
    # The device page's Commands & Responses editor.
    "inline_protocol",
    # The device page's IR Codes editor.
    "ir_codes",
    # Where the routing lives, for the Matrix picker, which asks the live
    # driver (ui.matrix_inference).
    "routing",
)

# The same, landing after ``commands``.
_INFO_FIELDS_AFTER_COMMANDS = (
    # Promoted-command buttons and setup wizards, kept raw: the device info
    # (resolve_device_actions) folds quick_actions into one list, for YAML
    # and Python drivers alike.
    "actions",
    "quick_actions",
    # True, or a URL template: the Open Web UI action.
    "web_ui",
    "delimiter",
)

# The command fields a command's entry copies as written, in the order they
# land after the label and params.
_COMMAND_META_FIELDS = (
    "method", "path", "body", "address", "args", "help",
    "available_offline", "restarts_device_for", "sleeps_device", "sets", "query_for",
    "confirm",
)


def driver_info_for(driver_def: dict[str, Any]) -> dict[str, Any]:
    """The ``DRIVER_INFO`` a driver class built from ``driver_def`` carries."""
    driver_id = driver_def.get("id", "unknown")
    driver_info: dict[str, Any] = {
        "id": driver_id,
        "name": driver_def.get("name", driver_id),
        "manufacturer": driver_def.get("manufacturer", "Generic"),
        "category": driver_def.get("category", "utility"),
        "version": driver_def.get("version", "1.0.0"),
        "author": driver_def.get("author", "Community"),
        "description": driver_def.get("description", ""),
        "transport": driver_def.get("transport", "tcp"),
        "default_config": driver_def.get("default_config", {}),
        "config_schema": driver_def.get("config_schema", {}),
        "state_variables": driver_def.get("state_variables", {}),
    }
    _copy_fields(driver_info, driver_def, _INFO_FIELDS)

    # Each state variable that carries help is replaced, in the definition's
    # own dict (driver_info holds the same one), by a copy of itself; nothing
    # else differs, and that is all this does.
    state_vars = driver_info.get("state_variables", {})
    for var_name, var_def in state_vars.items():
        if isinstance(var_def, dict) and "help" in var_def:
            state_vars[var_name] = {**var_def}

    driver_info["commands"] = build_commands_meta(driver_def.get("commands", {}))
    _copy_fields(driver_info, driver_def, _INFO_FIELDS_AFTER_COMMANDS)
    return driver_info


def build_commands_meta(commands_def: dict[str, Any]) -> dict[str, Any]:
    """The ``commands`` entry of ``DRIVER_INFO`` for a commands map: one entry
    per command that is a mapping."""
    return {
        name: _command_meta(name, cmd_def)
        for name, cmd_def in (commands_def or {}).items()
        if isinstance(cmd_def, dict)
    }


def _command_meta(name: str, cmd_def: dict[str, Any]) -> dict[str, Any]:
    """One command's entry: its label (else its name), its params, then the
    fields it declares of ``_COMMAND_META_FIELDS``."""
    meta: dict[str, Any] = {
        "label": cmd_def.get("label", name),
        "params": cmd_def.get("params", {}),
    }
    _copy_fields(meta, cmd_def, _COMMAND_META_FIELDS)
    return meta


def _copy_fields(
    driver_info: dict[str, Any], driver_def: dict[str, Any], fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in driver_def:
            driver_info[field] = driver_def[field]
