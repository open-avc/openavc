"""Every reader finds a device setting's value in the same state variable.

A device setting names the state variable its current value is read back
from with ``state_key``. Left out, null or "" it is the setting's own key
(``drivers/setting_model.read_setting``), and every reader asks that one
reading: the checker on both surfaces, the runtime's setting write and its
read-back wait, the device's settings answer the device page reads, the
simulator's setting handlers, and the Device Audit's Listen and settings
steps.

Against an invented device (Acme) whose three settings each leave their
``state_key`` out one of those ways.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from openavc.audit.driver_choice import DriverChoice
from openavc.audit.listen import start_listen
from openavc.audit.passes import DriverRun
from openavc.audit.session import AuditOptions, AuditSession, AuditTarget
from openavc.audit.settings import settings_for
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.pending_settings import readback_state_key
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.base import BaseDriver
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.python_info import python_driver_info_issues
from openavc.drivers.registry import _DRIVER_REGISTRY
from openavc.drivers.setting_checks import device_setting_state_key_errors
from openavc.drivers.setting_model import read_setting
from openavc.simulator.setting_handlers import setting_handlers
from openavc.core.device_traffic import get_traffic_recorder
from openavc.utils.log_redaction import get_secret_registry

#: The ways a setting leaves its state variable to its own key.
FORMS: dict[str, dict[str, Any]] = {
    "left out": {},
    "null": {"state_key": None},
    "empty": {"state_key": ""},
}

#: Each setting's own key, one per form, and the state variable of that name.
KEYS = {"left out": "level", "null": "bass", "empty": "treble"}

# A scale on each variable, so a write that finds it goes out converted.
VARIABLES = {key: {"type": "integer", "label": key.title(), "scale": 0.5} for key in KEYS.values()}


def _setting(form: str) -> dict[str, Any]:
    key = KEYS[form]
    return {
        "type": "integer", "label": key.title(), **FORMS[form],
        "write": {"send": f"{key.upper()} {{value}}\\r"},
    }


SETTINGS = {KEYS[form]: _setting(form) for form in FORMS}

DRIVER = {
    "id": "acme_tone",
    "name": "Acme Tone",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "default_config": {"port": 1, "poll_interval": 60},
    "state_variables": VARIABLES,
    "commands": {"ping": {"label": "Ping", "send": "PING\\r"}},
    "device_settings": SETTINGS,
    "polling": {"queries": ["TONE?\\r"]},
    "responses": [{
        "match": r"TONE=(\d+),(\d+),(\d+)",
        "set": {"level": "$1", "bass": "$2", "treble": "$3"},
    }],
}


# -- the reading ------------------------------------------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_left_out_null_or_empty_reads_as_the_settings_own_key(form: str) -> None:
    assert read_setting("level", {**FORMS[form]}).state_key == "level"


def test_a_named_variable_is_read_as_named_and_anything_else_as_the_own_key() -> None:
    assert read_setting("level", {"state_key": "volume"}).state_key == "volume"
    for written in (5, True, ["volume"], {"name": "volume"}):
        assert read_setting("level", {"state_key": written}).state_key == "level"
        assert read_setting("level", {"state_key": written}).not_text
    for form in FORMS.values():
        assert not read_setting("level", form).not_text
    assert read_setting("level", None).state_key == "level"


def test_the_checker_leaves_a_state_key_that_is_not_text_to_the_shape_walk() -> None:
    """It says "must be a string" where a driver file is written; the
    setting's own check would only name a variable the author never wrote."""
    assert device_setting_state_key_errors("here", "level", {"state_key": ["bass"]}, {"bass": {}}) == []


# -- the checker --------------------------------------------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_the_checker_takes_each_form_whose_own_key_is_declared(form: str) -> None:
    key = KEYS[form]
    assert device_setting_state_key_errors("here", key, _setting(form), VARIABLES) == []


@pytest.mark.parametrize("form", FORMS)
def test_the_checker_names_the_own_key_when_no_variable_holds_it(form: str) -> None:
    key = KEYS[form]
    others = {name: decl for name, decl in VARIABLES.items() if name != key}
    assert device_setting_state_key_errors("here", key, _setting(form), others) == [
        f"here: state_key '{key}' is not a declared state variable, so the setting would never read back"
    ]


def test_an_empty_state_key_passes_the_author_doors_on_both_surfaces() -> None:
    settings = {key: SETTINGS[key] for key in ("level", "treble")}
    yaml_driver = {**DRIVER, "device_settings": settings}
    assert [e for e in validate_driver_definition(yaml_driver, strict=True) if "state_key" in e] == []
    assert [e for e in python_driver_info_issues({**DRIVER, "device_settings": settings}) if "state_key" in e] == []


def test_a_null_state_key_is_refused_in_a_driver_file_and_read_as_the_own_key() -> None:
    """The contract types ``state_key`` as text, so the shape walk refuses a
    null where a driver file is written; nothing else does."""
    assert [e for e in validate_driver_definition(DRIVER, strict=True) if "state_key" in e] == [
        "device_settings.bass.state_key: must be a string"
    ]
    assert [e for e in validate_driver_definition(DRIVER) if "state_key" in e] == []


# -- the runtime ----------------------------------------------------------------------


@pytest.mark.parametrize("form", FORMS)
def test_the_read_back_waits_on_the_settings_own_key(form: str) -> None:
    key = KEYS[form]
    assert readback_state_key(key, _setting(form), VARIABLES) == key


@pytest.mark.parametrize("form", FORMS)
def test_a_setting_write_converts_through_the_settings_own_variable(form: str) -> None:
    driver = create_configurable_driver_class(DRIVER)("dev", {"host": "127.0.0.1"}, StateStore(), EventBus())
    key = KEYS[form]
    assert driver._setting_wire_value(key, _setting(form), 10) == 20


@pytest.mark.parametrize("form", FORMS)
def test_the_simulator_reads_a_write_into_the_settings_own_key(form: str) -> None:
    key = KEYS[form]
    asked: list[str] = []

    def var_def_for_key(name: str) -> dict:
        asked.append(name)
        return VARIABLES.get(name, {})

    definition = {**DRIVER, "device_settings": {key: _setting(form)}}
    (handler,) = setting_handlers(definition, lambda: {}, var_def_for_key)
    assert handler.response_var == key
    assert handler.state_changes == {key: 1}
    assert asked == [key]


class _Tone(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {**DRIVER, "id": "acme_tone_py", "polling": {}, "responses": []}

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def stop_polling(self) -> None:
        return None


async def test_the_settings_answer_serves_each_settings_variable_and_its_value() -> None:
    _DRIVER_REGISTRY["acme_tone_py"] = _Tone
    try:
        state = StateStore()
        events = EventBus()
        state.set_event_bus(events)
        manager = DeviceManager(state, events)
        await manager.add_device({"id": "dev", "driver": "acme_tone_py", "name": "Dev", "config": {}})
        for n, key in enumerate(KEYS.values(), start=1):
            state.set(f"device.dev.{key}", n * 10)

        served = manager.get_device_settings("dev")
    finally:
        _DRIVER_REGISTRY.pop("acme_tone_py", None)

    assert {key: (entry["state_key"], entry["current_value"]) for key, entry in served.items()} == {
        "level": ("level", 10), "bass": ("bass", 20), "treble": ("treble", 30),
    }


# -- the Device Audit -------------------------------------------------------------------


@pytest.fixture
def tone_driver():
    _DRIVER_REGISTRY[DRIVER["id"]] = create_configurable_driver_class(DRIVER)
    yield
    _DRIVER_REGISTRY.pop(DRIVER["id"], None)
    get_traffic_recorder().clear()
    get_secret_registry().clear()


async def _fake_device():
    """Answers its status query with its three levels."""

    async def handle(reader, writer):
        try:
            while True:
                line = (await reader.readuntil(b"\r")).decode().strip()
                if line == "TONE?":
                    writer.write(b"TONE=40,4,6\r")
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_the_audit_reads_each_setting_back_from_its_own_key(tone_driver) -> None:
    """The Listen step's table and the settings step's list both show each
    setting's value, read from the variable of its own name."""
    server, port = await _fake_device()
    session = AuditSession("tone", AuditTarget("127.0.0.1", "127.0.0.1"), AuditOptions())
    run = DriverRun(index=0, choice=DriverChoice(
        driver_id=DRIVER["id"], identity={"name": "Acme Tone", "version": "1.0.0"},
    ))
    run.config = {"host": "127.0.0.1", "port": port}
    session.runs.append(run)
    try:
        listen = await start_listen(
            session, run, min_seconds=0.3, min_cycles=1, max_seconds=5.0, flush_seconds=0.05,
        )
        loop = asyncio.get_running_loop()
        end = loop.time() + 8
        while listen.sandbox.device_state().get("treble") is None:
            assert loop.time() < end, "the device never answered"
            await asyncio.sleep(0.02)

        table = {row["key"]: row for row in listen.status_table()["settings"]}
        catalog = {entry["key"]: entry for entry in settings_for(session, run).catalog()}
    finally:
        await run.stop()
        server.close()

    # The device's 40, 4 and 6, each read through its variable's scale.
    assert {key: (row["state_key"], row["populated"], row["value"]) for key, row in table.items()} == {
        "level": ("level", True, 20), "bass": ("bass", True, 2), "treble": ("treble", True, 3),
    }
    assert {key: (entry["state_key"], entry["can_write"], entry["value"]) for key, entry in catalog.items()} == {
        "level": ("level", True, 20), "bass": ("bass", True, 2), "treble": ("treble", True, 3),
    }


# -- one reading ----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1] / "openavc"

# A module reading a ``state_key`` out of a mapping itself.
_OWN_READING = re.compile(r"""\.get\(\s*["']state_key["']""")

# Each module allowed one, and why: a ``state_key`` that is not a setting's.
_OWN_READING_ALLOWED = {
    "core/trigger_engine.py": "a state-change trigger's key",
    "core/condition_eval.py": "a state-change trigger's key, in its own condition",
    "audit/command_text.py": "the audit's own effect and query records",
}


def test_no_module_reads_a_settings_state_key_for_itself() -> None:
    found = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if _OWN_READING.search(path.read_text(encoding="utf-8"))
    )
    assert [f for f in found if f not in _OWN_READING_ALLOWED] == ["drivers/setting_model.py"]
    # An allowance with nothing left to allow goes too.
    assert sorted(_OWN_READING_ALLOWED) == [f for f in found if f in _OWN_READING_ALLOWED]
