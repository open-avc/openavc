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
    it was told, and saving that would write a guess into the real project.
    After simulation stops, a value still as the simulator reported it is left
    until the equipment reports another (the state store announces changes
    only, so one the equipment reports exactly as the simulator did is saved
    when it next changes);
  - while a setup action is running on the device (it owns the config then);
  - for a value the field does not accept: an enum value it does not list, a
    number outside its range, an empty value. That is logged once.

How it saves: the project first, through the engine's queued bookkeeping
change (it may be running inside a reconcile, which holds the lock), which
coalesces writes made together, bumps the revision and broadcasts
``project.reloaded`` (an open IDE tab refetches, or warns when it holds unsaved
edits). Only once the file holds the value does the running device take it,
still under the lock (``Engine.take_device_settings``, as a setup action's
settings): the live driver's config and the device manager's stored copy move
together, so the next reconcile reads no edit and tears nothing down. A save
that fails leaves the device as the file holds it, and is tried again on a
timer, since the same report is not announced twice. A value waiting for its
save is not queued again by a later report. The project split (connection
fields to the connections table, the rest to the device's config) is
``device_config.apply_config_delta``, the same one a setup action uses.

What the driver built from a saved field follows it once the running device
takes it: a declared child roster built from it (``instances`` ``count_from``
or ``ids_from``) is rebuilt, as the IDE's Refresh from Device does, and a YAML
driver's reply patterns and derived values are worked out again. That is the
driver's own answer to the change (``BaseDriver._config_changed``, through
``DeviceManager.merge_live_config``), the same for a setup action's settings.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openavc.core.device_config import apply_config_delta
from openavc.drivers.spec import NUMERIC_TYPES, is_secret_setting
from openavc.drivers.truth_words import read_truth
from openavc.drivers.usable_number import number_value, usable_number
from openavc.utils.logger import get_logger

log = get_logger(__name__)

_CONNECTED_EVENT = "device.connected."


def _accepted_number(field_def: dict[str, Any], ftype: str, value: Any) -> tuple[bool, Any]:
    """A numeric field's reading of ``value``: a usable number
    (``usable_number``), whole for an integer field, inside the field's
    bounds where each bound is a usable number."""
    number = usable_number(value)
    if number is None:
        return False, None
    if ftype == "integer":
        if not number.is_integer():
            return False, None
        number = int(number)
    if not _within_bounds(field_def, number):
        return False, None
    return True, number


def _within_bounds(field_def: dict[str, Any], number: float) -> bool:
    """Inside each of the field's bounds that is a usable number."""
    low, high = number_value(field_def.get("min")), number_value(field_def.get("max"))
    above_low = low is None or number >= low
    below_high = high is None or number <= high
    return above_low and below_high


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
    if ftype in NUMERIC_TYPES:
        return _accepted_number(field_def, ftype, value)
    if ftype == "boolean":
        flag = read_truth(value)
        return (False, None) if flag is None else (True, flag)
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
        # Never a login: a learned value is written straight into the project,
        # and a login belongs in this system's login file (core/device_logins).
        if isinstance(source, str) and source.strip() and not is_secret_setting(name, field_def):
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
        self._retry_handles: dict[str, asyncio.TimerHandle] = {}
        self._retry_delays: dict[str, float] = {}
        # What the simulator reported, by state key: what is left in state
        # after simulation stops is not the equipment's report.
        self._simulated: dict[str, Any] = {}
        # device -> (the driver that reported, {field: value} queued for the
        # file and not yet taken by that driver).
        self._waiting: dict[str, tuple[Any, dict[str, Any]]] = {}
        self._active = False

    def start(self) -> None:
        self._active = True
        self._event_ids.append(
            self._engine.events.on(f"{_CONNECTED_EVENT}*", self._on_connected)
        )
        # Devices that connected before this started.
        for device_id in list(self._engine.devices.get_device_configs()):
            if self._engine.state.get(f"device.{device_id}.connected") is True:
                self._watch_and_evaluate(device_id)

    def stop(self) -> None:
        self._active = False
        for handle in self._retry_handles.values():
            handle.cancel()
        self._retry_handles.clear()
        self._retry_delays.clear()
        for event_id in self._event_ids:
            self._engine.events.off(event_id)
        self._event_ids.clear()
        for state_id in self._state_ids:
            self._engine.state.unsubscribe(state_id)
        self._state_ids.clear()
        self._watched.clear()
        self._simulated.clear()
        self._waiting.clear()

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
        redirected = self._redirected(device_id)
        for source in set(fields.values()):
            key = f"device.{device_id}.{source}"
            if key not in self._watched:
                self._watched[key] = device_id
                self._state_ids.append(self._engine.state.subscribe(key, self._on_state))
            if redirected:
                # Reported while it connected to the simulator.
                self._simulated[key] = self._engine.state.get(key)
        self.evaluate(device_id)

    def _on_state(self, key: str, old: Any, new: Any, source: Any = None) -> None:
        device_id = self._watched.get(key)
        if device_id:
            if self._redirected(device_id):
                self._simulated[key] = new
            else:
                self._simulated.pop(key, None)
            self.evaluate(device_id)

    def _redirected(self, device_id: str) -> bool:
        simulation = getattr(self._engine, "simulation", None)
        return simulation is not None and simulation.is_redirected(device_id)

    def evaluate(self, device_id: str) -> dict[str, Any]:
        """Save every learned field of ``device_id`` whose reported value the
        field accepts and differs from what is saved. Returns what changed."""
        engine = self._engine
        driver = engine.devices.get_driver(device_id)
        if driver is None or getattr(engine, "project", None) is None:
            return {}
        if engine.state.get(f"device.{device_id}.connected") is not True:
            return {}
        if self._redirected(device_id):
            return {}
        setup_actions = getattr(engine, "setup_actions", None)
        if setup_actions is not None and setup_actions.is_running(device_id):
            return {}

        schema = (getattr(driver, "DRIVER_INFO", {}) or {}).get("config_schema") or {}
        config = {**(getattr(driver, "config", None) or {}), **self._waiting_for(device_id, driver)}
        delta: dict[str, Any] = {}
        for field, source in learned_fields(driver).items():
            key = f"device.{device_id}.{source}"
            reported = engine.state.get(key)
            if reported is None or reported == "":
                continue
            if key in self._simulated and _same(self._simulated[key], reported):
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
        if not delta:
            return {}
        self._save(device_id, driver, schema, delta)
        return delta

    def _waiting_for(self, device_id: str, driver: Any) -> dict[str, Any]:
        """What ``driver`` reported that is queued for the file and not yet
        taken; nothing for a driver that has been replaced since."""
        waiting = self._waiting.get(device_id)
        return waiting[1] if waiting and waiting[0] is driver else {}

    def _save(self, device_id: str, driver: Any, schema: dict[str, Any], delta: dict[str, Any]) -> None:
        """Queue ``delta`` for the file, taken by the running device once the
        file holds it (``_Report``)."""
        waiting = self._waiting.get(device_id)
        if not waiting or waiting[0] is not driver:
            waiting = self._waiting[device_id] = (driver, {})
        waiting[1].update(delta)
        labels = {field: (schema.get(field) or {}).get("label") or field for field in delta}
        _Report(self, device_id, driver, delta, labels).queue()

    def _done_waiting(self, report: "_Report") -> None:
        """Forget what ``report`` queued, unless a later report queued another
        value for the same field since."""
        waiting = self._waiting.get(report.device_id)
        if not waiting or waiting[0] is not report.driver:
            return
        queued = waiting[1]
        for field, value in report.delta.items():
            if queued.get(field) is value:
                del queued[field]
        if not queued:
            self._waiting.pop(report.device_id, None)

    def _retry_later(self, report: "_Report") -> None:
        """Try a failed save again on a timer, one bounded timer per device:
        the device still runs as the file holds it, and identical state
        reports do not emit another state change."""
        if not self._active or not self._engine._running:
            return
        old_handle = self._retry_handles.pop(report.device_id, None)
        if old_handle:
            old_handle.cancel()
        delay = self._retry_delays.get(report.device_id, 1.0)
        self._retry_delays[report.device_id] = min(delay * 2, 30.0)
        self._retry_handles[report.device_id] = asyncio.get_running_loop().call_later(
            delay, self._retry, report,
        )

    def _retry(self, report: "_Report") -> None:
        self._retry_handles.pop(report.device_id, None)
        if self._active and self._engine._running and report.still_current():
            if self.evaluate(report.device_id):
                return
        self._retry_delays.pop(report.device_id, None)

    def _saved(self, report: "_Report") -> None:
        """A report the file now holds: its retry is over."""
        if not self._active:
            return
        handle = self._retry_handles.pop(report.device_id, None)
        if handle:
            handle.cancel()
        self._retry_delays.pop(report.device_id, None)


class _Report:
    """One device's reported values on their way to the project file: written
    into the project current when the queued save runs, then taken by the
    running device, still under the lock; a failed save is tried again. A
    queued report belongs to the device that produced it, not a replacement
    with the same project device ID (``still_current``)."""

    def __init__(
        self, learned: LearnedConfig, device_id: str, driver: Any,
        delta: dict[str, Any], labels: dict[str, str],
    ) -> None:
        self._learned = learned
        self._engine = learned._engine
        self.device_id = device_id
        self.driver = driver
        self.delta = delta
        self._labels = labels
        self._project_id = self._engine.project.project.id
        self._written = False

    def queue(self) -> None:
        self._engine.schedule_bookkeeping_change(
            self.mutate, on_error=self.on_error, on_saved=self.on_saved,
            runtime_write=self.take_live,
        )

    def still_current(self) -> bool:
        project = self._engine.project
        if project is None or project.project.id != self._project_id:
            return False
        return self._engine.devices.get_driver(self.device_id) is self.driver

    def _landed(self) -> bool:
        """Written into the project, by the device still running."""
        return self._written and self.still_current()

    def mutate(self, project: Any) -> None:
        if self.still_current():
            self._written = apply_config_delta(project, self.device_id, self.delta)

    def take_live(self) -> None:
        """The file holds it: the running device takes it."""
        if not self._landed():
            return
        self._engine.take_device_settings(self.device_id, self.delta)
        for field, value in self.delta.items():
            log.info("[%s] %s set to %s, as the device reports", self.device_id, self._labels[field], value)

    def on_error(self) -> None:
        self._learned._done_waiting(self)
        if self._landed():
            self._learned._retry_later(self)

    def on_saved(self) -> None:
        self._learned._done_waiting(self)
        if self._landed():
            self._learned._saved(self)
