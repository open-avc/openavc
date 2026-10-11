"""
A driver's required config fields, refused blank at the doors that change one device.

A ``config_schema`` field may say ``required: true``. The Add and Edit Device
dialogs refuse to save one left blank (``buildConfigFromForm`` in
``web/programmer/src/views/devices/deviceConfigCoerce.ts``, the same rule), and
so do the server doors that change one device's settings: ``PATCH
/api/devices/{id}``, ``PUT /api/connections/{id}`` and the AI's add and update
device tools. Each answers with the field's label: "Model is required."

A whole-project save, a project load and the whole-table connection routes
(``PUT /api/connections``, the import) never ask. One older device with a
blank field must not block every other edit or stop a project opening; that
device fails to connect and says why on its card, as before.

What counts:

- The value read is the device's merged config: the driver's
  ``default_config``, then ``device.config``, then the connections row, the
  layering ``core.device_config.resolve_device_config`` applies. A field with a
  default is never missing, and a stored password a request leaves out is
  still there.
- Blank is ``None`` or a string of whitespace. ``False`` and ``0`` are values.
- A ``table`` field is not checked: it is edited on the device page, never in
  the dialog that would be refused.
- ``host`` and ``port`` belong to the connection picker and apply only to the
  connection that uses them: ``host`` to a network connection, ``port`` to a
  network or direct serial one, neither to a device reached through a bridge.
  The picker's other fields (the serial line settings, the bridge binding) are
  never refused; the picker always shows a value for them.

The browser half mirrors ``PICKER_FIELD_CONNECTIONS``, ``PICKER_OWNED_FIELDS``
and the sentence; keep the two in sync.
"""

from __future__ import annotations

from typing import Any

from openavc.drivers.registry import get_driver_class, get_driver_default_config

#: The connection picker's fields that a driver may require, and the
#: connections each applies to. Mirrors PICKER_FIELD_CONNECTIONS in
#: deviceConfigCoerce.ts.
PICKER_FIELD_CONNECTIONS: dict[str, frozenset[str]] = {
    "host": frozenset({"network"}),
    "port": frozenset({"network", "serial"}),
}

#: Every field the connection picker (or an IR device's code editor) owns.
#: Those not in PICKER_FIELD_CONNECTIONS are never refused. Mirrors
#: SERIAL_PICKER_FIELDS + IR_PICKER_FIELDS in deviceConfigCoerce.ts.
PICKER_OWNED_FIELDS = frozenset({
    "transport", "host", "port", "bridge", "bridge_port", "usb_serial",
    "baudrate", "bytesize", "parity", "stopbits", "flow_control", "ir_codes",
})


def connection_kind(config: dict[str, Any], driver_transport: str) -> str:
    """``network``, ``serial`` or ``bridge``: how a merged config connects.

    Bridged when it is bound to a bridge port (the condition
    ``resolve_bridge_binding`` rewrites on) or its transport is ``bridge`` (an
    IR device); serial when its transport, else its driver's, is ``serial``.
    """
    if config.get("bridge") and config.get("bridge_port"):
        return "bridge"
    transport = str(config.get("transport") or driver_transport or "tcp").lower()
    if transport == "bridge":
        return "bridge"
    if transport == "serial":
        return "serial"
    return "network"


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def missing_required_fields(
    schema: dict[str, Any], config: dict[str, Any], connection: str,
) -> list[tuple[str, str]]:
    """``(key, label)`` of each required field ``config`` leaves blank, in
    schema order, skipping fields that do not apply to ``connection``."""
    missing: list[tuple[str, str]] = []
    for key, field in (schema or {}).items():
        if not isinstance(field, dict) or field.get("required") is not True:
            continue
        if field.get("type") == "table":
            continue
        if key in PICKER_OWNED_FIELDS:
            applies_to = PICKER_FIELD_CONNECTIONS.get(key)
            if applies_to is None or connection not in applies_to:
                continue
        if _is_blank(config.get(key)):
            missing.append((key, str(field.get("label") or key)))
    return missing


def required_sentence(labels: list[str]) -> str:
    """The refusal, naming every missing field by its label."""
    if len(labels) == 1:
        return f"{labels[0]} is required."
    return f"{', '.join(labels[:-1])} and {labels[-1]} are required."


def missing_for_driver(driver_id: str, config: dict[str, Any]) -> list[tuple[str, str]]:
    """The required fields a device on ``driver_id`` leaves blank, where
    ``config`` is its merged config. Empty when the driver is not installed:
    with no schema to read there is nothing to refuse."""
    cls = get_driver_class(driver_id)
    if cls is None:
        return []
    info = cls.DRIVER_INFO
    return missing_required_fields(
        info.get("config_schema") or {},
        config,
        connection_kind(config, str(info.get("transport") or "tcp")),
    )


def merged_device_config(
    driver_id: str, device_config: dict[str, Any], connection: dict[str, Any],
) -> dict[str, Any]:
    """Driver defaults, then ``device.config``, then the connections row."""
    return {
        **get_driver_default_config(driver_id),
        **(device_config or {}),
        **(connection or {}),
    }
