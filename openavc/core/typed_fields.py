"""
Stray spaces around a typed value in a device's settings: the one rule that trims them.

A person types or pastes an IP address, a port, a login name, a MAC address or
a URL into a device's settings, and a space before or after it is invisible
and never meant. Kept, it breaks some devices and not others: a TCP device
cannot dial ``" 192.0.2.10"`` and reads unreachable, while an HTTP device on
the same address connects anyway.

What is trimmed, surrounding whitespace only:

- every connection field (``CONNECTION_FIELDS`` in ``drivers/spec.py``: host,
  port, username, base URL, the serial line, the bridge binding, the USB
  adapter) except ``password``, whatever the driver says of it;
- every field a driver's ``config_schema`` declares as typed text or a number
  (``string``, ``integer``, ``number``, ``float``; a field with no type is a
  string), unless it is a secret or offers a list of values to pick from.

Never trimmed: a password, any ``secret: true`` or ``type: password`` field (a
space can be part of one; the one rule is ``drivers/spec.is_secret_setting``),
a multi-line ``text`` field, a table, a picked
value, and anything that is not a string. A device whose driver is not
installed has no schema to read, so only its connection fields are trimmed.
IDs are never touched here: a device's ID is named by its bindings, macros
and scripts, so rewriting it would orphan them (the Add Device form allows
only letters, numbers and underscores in one).

It is applied in two places, so no door needs code of its own:

- where a project is written (``Engine._apply_project_locked``), which every
  door that saves a device's settings ends in: ``PATCH /api/devices/{id}``,
  the connection routes, the whole-project save, a device added from
  Discovery and the AI device tools. The stored value is clean.
- where a device's connection is resolved
  (``core.device_config.resolve_device_config``), so a project saved with a
  space before this rule existed dials the trimmed value when it loads,
  without an edit. The server's boot reads the project around the write seam,
  so for an untouched project this is the only place it is trimmed.

The Add and Edit Device dialogs trim on save by the same rule
(``trimsTypedValue`` in ``web/programmer/src/views/devices/deviceConfigCoerce.ts``),
so what the form shows is what is stored; keep the two in sync.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.registry import get_driver_class
from openavc.drivers.spec import CONNECTION_FIELDS, PASSWORD_FIELD, is_secret_setting

#: Connection fields trimmed whatever the driver's schema says of them.
TRIMMED_CONNECTION_FIELDS: frozenset[str] = CONNECTION_FIELDS - {PASSWORD_FIELD}

#: The schema types a person types a single value into.
TRIMMED_SCHEMA_TYPES: frozenset[str] = frozenset({"string", "integer", "number", "float"})


def trims_value(key: str, field: Any) -> bool:
    """Whether a value saved under ``key`` loses its surrounding whitespace.

    ``field`` is the driver's ``config_schema`` entry for ``key``, or None when
    the schema does not describe it.
    """
    if is_secret_setting(key, field):
        return False
    if key in TRIMMED_CONNECTION_FIELDS:
        return True
    if not isinstance(field, dict):
        return False
    if field.get("values"):
        return False
    return str(field.get("type") or "string") in TRIMMED_SCHEMA_TYPES


def _schema_of(driver_id: str) -> dict[str, Any]:
    cls = get_driver_class(driver_id)
    if cls is None:
        return {}
    schema = cls.DRIVER_INFO.get("config_schema")
    return schema if isinstance(schema, dict) else {}


def trim_typed_fields(driver_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """``config`` with every value :func:`trims_value` names trimmed.

    Returns ``config`` itself when nothing changes, else a new dict.
    """
    schema = _schema_of(driver_id)
    trimmed: dict[str, Any] | None = None
    for key, value in config.items():
        if not isinstance(value, str) or value == value.strip():
            continue
        if not trims_value(key, schema.get(key)):
            continue
        if trimmed is None:
            trimmed = dict(config)
        trimmed[key] = value.strip()
    return config if trimmed is None else trimmed


def trim_device_settings(project) -> None:
    """Trim every device's saved settings in ``project``, in place: its
    ``config`` and its row in the connections table."""
    for device in getattr(project, "devices", None) or []:
        config = device.config or {}
        clean = trim_typed_fields(device.driver, config)
        if clean is not config:
            device.config = clean
        row = project.connections.get(device.id)
        if isinstance(row, dict):
            clean_row = trim_typed_fields(device.driver, row)
            if clean_row is not row:
                project.connections[device.id] = clean_row
