"""
Settings the device fills in: a config field's ``learned_from``.

A driver may declare, on a ``config_schema`` field, ``learned_from: <state
variable>``. The field is the integrator's statement of what the equipment is
(a model, a channel count, a MAC address) for building a project and
simulating it before the equipment exists. Once the device is connected to the
real equipment, the value it reports in that state variable is saved into the
field, so a value picked offline is corrected the first time the equipment is
reached and stays correct after a restart.

When it runs:

  - when the device connects (a value a driver reports while it is still
    connecting, before ``connected`` is true, is picked up here), and
  - whenever a learned state variable changes while the device is connected.

When it does nothing:

  - while the device is redirected to the simulator: a simulator reports what
    it was told, and saving that would write a guess into the real project;
  - while a setup action is running on the device (it owns the config then);
  - for a value the field does not accept: an enum value it does not list, a
    number outside its range, an empty value. That is logged once.

How it saves: the live driver's config and the device manager's stored copy
first (``merge_live_config``), so the reconcile does not read the change as an
edit and tear the device down; then the project through the engine's
bookkeeping persist, which coalesces writes made together, bumps the revision
and broadcasts ``project.reloaded`` (an open IDE tab refetches, or warns when it
holds unsaved edits). The project split (connection fields to the connections
table, the rest to the device's config) is ``device_config.apply_config_delta``,
the same one a setup action uses.
"""

from __future__ import annotations

from typing import Any

from openavc.core.device_config import apply_config_delta
from openavc.utils.logger import get_logger

log = get_logger(__name__)

_CONNECTED_EVENT = "device.connected."


def accepted_value(field_def: dict[str, Any], value: Any) -> tuple[bool, Any]:
    """The value as the field would store it, or ``(False, None)`` when the
    field does not accept it.

    An enum matches on the text of each listed value (a reported ``4`` matches
    a listed ``"4"`` or ``4``) and stores the listed value itself, so the type
    the field already uses is kept.
    """
    if value is None or isinstance(value, (dict, list)):
        return False, None
    if isinstance(value, str) and not value.strip():
        return False, None
    ftype = field_def.get("type", "string")
    if ftype == "enum":
        for option in field_def.get("values") or []:
            option_value = option.get("value") if isinstance(option, dict) else option
            if option_value is None or option_value == "":
                continue
            if str(option_value) == str(value).strip():
                return True, option_value
        return False, None
    if ftype in ("integer", "number"):
        if isinstance(value, bool):
            return False, None
        try:
            number = float(str(value).strip())
        except ValueError:
            return False, None
        if ftype == "integer":
            if number != int(number):
                return False, None
            number = int(number)
        low, high = field_def.get("min"), field_def.get("max")
        if isinstance(low, (int, float)) and number < low:
            return False, None
        if isinstance(high, (int, float)) and number > high:
            return False, None
        return True, number
    if ftype == "boolean":
        if isinstance(value, bool):
            return True, value
        text = str(value).strip().lower()
        if text in ("true", "1", "on", "yes"):
            return True, True
        if text in ("false", "0", "off", "no"):
            return True, False
        return False, None
    if ftype == "table":
        return False, None
    return True, str(value).strip()


def _same(current: Any, learned: Any) -> bool:
    if current == learned:
        return True
    return current is not None and str(current) == str(learned)


def learned_fields(driver: Any) -> dict[str, str]:
    """``{config field: state variable}`` for a driver's learned fields."""
    info = getattr(driver, "DRIVER_INFO", {}) or {}
    schema = info.get("config_schema") or {}
    fields: dict[str, str] = {}
    if not isinstance(schema, dict):
        return fields
    for name, field_def in schema.items():
        if not isinstance(field_def, dict):
            continue
        source = field_def.get("learned_from")
        if isinstance(source, str) and source.strip() and not field_def.get("secret"):
            fields[name] = source.strip()
    return fields


class LearnedConfig:
    """Saves the values devices report into the config fields that ask for it."""

    def __init__(self, engine: Any):
        self._engine = engine
        self._event_ids: list[str] = []
        self._state_ids: list[str] = []
        # state key -> device id, for the keys this watches.
        self._watched: dict[str, str] = {}
        # (device, field, value) already logged as not accepted.
        self._refused: set[tuple[str, str, str]] = set()

    def start(self) -> None:
        self._event_ids.append(
            self._engine.events.on(f"{_CONNECTED_EVENT}*", self._on_connected)
        )
        # Devices that connected before this started.
        for device_id in list(self._engine.devices.get_device_configs()):
            if self._engine.state.get(f"device.{device_id}.connected") is True:
                self._watch_and_evaluate(device_id)

    def stop(self) -> None:
        for event_id in self._event_ids:
            self._engine.events.off(event_id)
        self._event_ids.clear()
        for state_id in self._state_ids:
            self._engine.state.unsubscribe(state_id)
        self._state_ids.clear()
        self._watched.clear()

    async def _on_connected(self, event: str, payload: Any = None) -> None:
        device_id = event[len(_CONNECTED_EVENT):]
        if device_id:
            self._watch_and_evaluate(device_id)

    def _watch_and_evaluate(self, device_id: str) -> None:
        driver = self._engine.devices.get_driver(device_id)
        if driver is None:
            return
        fields = learned_fields(driver)
        if not fields:
            return
        for source in set(fields.values()):
            key = f"device.{device_id}.{source}"
            if key not in self._watched:
                self._watched[key] = device_id
                self._state_ids.append(self._engine.state.subscribe(key, self._on_state))
        self.evaluate(device_id)

    def _on_state(self, key: str, old: Any, new: Any, source: Any = None) -> None:
        device_id = self._watched.get(key)
        if device_id:
            self.evaluate(device_id)

    def evaluate(self, device_id: str) -> dict[str, Any]:
        """Save every learned field of ``device_id`` whose reported value the
        field accepts and differs from what is saved. Returns what changed."""
        engine = self._engine
        driver = engine.devices.get_driver(device_id)
        if driver is None or getattr(engine, "project", None) is None:
            return {}
        if engine.state.get(f"device.{device_id}.connected") is not True:
            return {}
        simulation = getattr(engine, "simulation", None)
        if simulation is not None and simulation.is_redirected(device_id):
            return {}
        setup_actions = getattr(engine, "setup_actions", None)
        if setup_actions is not None and setup_actions.is_running(device_id):
            return {}

        schema = (getattr(driver, "DRIVER_INFO", {}) or {}).get("config_schema") or {}
        config = getattr(driver, "config", None) or {}
        delta: dict[str, Any] = {}
        previous: dict[str, Any] = {}
        for field, source in learned_fields(driver).items():
            reported = engine.state.get(f"device.{device_id}.{source}")
            if reported is None or reported == "":
                continue
            ok, value = accepted_value(schema.get(field) or {}, reported)
            if not ok:
                marker = (device_id, field, str(reported))
                if marker not in self._refused:
                    self._refused.add(marker)
                    log.info(
                        "[%s] Not saving %s: the device reports %r, which the "
                        "setting does not accept",
                        device_id, field, reported,
                    )
                continue
            if _same(config.get(field), value):
                continue
            delta[field] = value
            previous[field] = config.get(field)
        if not delta:
            return {}

        engine.devices.merge_live_config(device_id, delta)

        def mutate(project: Any) -> None:
            apply_config_delta(project, device_id, delta)

        def on_error() -> None:
            # The save failed: put the live config back so it matches the
            # project, and the next report tries again.
            engine.devices.merge_live_config(device_id, previous)

        engine.schedule_bookkeeping_change(mutate, on_error=on_error)
        for field, value in delta.items():
            label = (schema.get(field) or {}).get("label") or field
            log.info("[%s] %s set to %s, as the device reports", device_id, label, value)
        return delta
