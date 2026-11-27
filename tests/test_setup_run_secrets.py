"""A setup run's secret parameters are masked in everything the run sends,
keeps and logs, whether or not the run saves them.

A wizard is given a key or a password before the device has it, so a wrong
value is the common case: the run is refused and nothing is saved. The
device's masking learns each secret parameter's value as the run starts, so
the run's progress, its last message (what a wizard whose connection dropped
asks for, ``GET /api/devices/{id}/setup-runs/{run_id}``) and the log's
account of its failure carry it as ``***``. An invented device (Acme), never a
real product.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import pytest

from openavc.core import setup_actions
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.setup_actions import SetupActionRunner
from openavc.core.state_store import StateStore
from openavc.core.ws_hub import PROGRAMMER, frame_as
from openavc.drivers.actions import resolve_device_actions
from openavc.drivers.base import BaseDriver
from openavc.utils.log_redaction import REDACTED, SecretRedactionFilter, get_secret_registry

KEY = "Zq7-pair-4410"
WORD = "hunter2-acme"
ROOM = "Room 204"


class _AcmePairDriver(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_pair",
        "name": "Acme Pairable Widget",
        "transport": "tcp",
        "state_variables": {},
        "commands": {},
        "actions": [
            {
                "id": "pair",
                "kind": "setup",
                "label": "Pair",
                "availability": "offline",
                "params": {
                    "key": {"type": "string", "secret": True, "required": True},
                    # Named password: the one name that declares itself.
                    "password": {"type": "string"},
                    "room": {"type": "string"},
                },
            }
        ],
    }

    # What the handler does once it has said which key it is trying.
    ending = "done"

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def run_setup_action(self, action_id, params, progress) -> dict:
        key, word = params["key"], params.get("password", "")
        await progress(f"Trying key {key} with {word} for {params.get('room')}", 10)
        if self.ending == "raise":
            raise RuntimeError(f"the widget refused key {key}")
        if self.ending == "refused":
            return {"success": False, "message": f"Key {key} was refused", "tried": [key, word]}
        return {"paired_with": key, "room": params.get("room")}


class _Engine:
    def __init__(self, dm: DeviceManager) -> None:
        self.devices = dm
        self.project = None
        self.ws_messages: list[dict[str, Any]] = []

    async def broadcast_ws(self, message: dict[str, Any]) -> None:
        self.ws_messages.append(message)


def _runner(ending: str) -> tuple[SetupActionRunner, _Engine, dict[str, Any]]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    driver = _AcmePairDriver("dev1", {"host": "h", "port": 23}, state, events)
    driver.ending = ending
    # Online, so the run's end hands back a connected device and starts no
    # reconnect loop.
    driver.set_state("connected", True)
    dm._devices["dev1"] = driver
    dm._device_configs["dev1"] = {
        "id": "dev1", "driver": "acme_pair", "name": "Dev 1",
        "config": {"host": "h", "port": 23}, "enabled": True,
    }
    engine = _Engine(dm)
    action = next(a for a in resolve_device_actions(driver.DRIVER_INFO) if a["kind"] == "setup")
    return SetupActionRunner(engine), engine, action


async def _run(ending: str) -> tuple[SetupActionRunner, _Engine, str]:
    runner, engine, action = _runner(ending)
    started = await runner.start("dev1", action, {"key": KEY, "password": WORD, "room": ROOM})
    await asyncio.gather(*list(runner._tasks), return_exceptions=True)
    return runner, engine, started["run_id"]


def _sent(engine: _Engine) -> list[Any]:
    """What the Programmer is sent of each message, through the hub's rule."""
    return [frame_as(m, PROGRAMMER) for m in engine.ws_messages if m["type"] == "action.progress"]


def _assert_kept_out(where: Any) -> None:
    text = repr(where)
    assert KEY not in text and WORD not in text, text


@pytest.mark.parametrize("ending", ["done", "raise", "refused"])
async def test_a_run_s_progress_never_carries_a_secret_parameter(ending):
    _runner_, engine, _run_id = await _run(ending)
    sent = _sent(engine)
    assert any(m["step"] == f"Trying key {REDACTED} with {REDACTED} for {ROOM}" for m in sent)
    _assert_kept_out(sent)


async def test_a_refused_run_s_error_and_result_never_carry_it():
    _runner_, engine, _run_id = await _run("refused")
    last = _sent(engine)[-1]
    assert last["status"] == "error"
    assert last["error"] == f"Key {REDACTED} was refused"
    assert last["result"]["tried"] == [REDACTED, REDACTED]


async def test_a_failed_run_s_error_never_carries_it():
    _runner_, engine, _run_id = await _run("raise")
    last = _sent(engine)[-1]
    assert last["status"] == "error"
    assert last["error"] == f"the widget refused key {REDACTED}"


@pytest.mark.parametrize("ending", ["done", "raise", "refused"])
async def test_the_kept_result_never_carries_it(ending):
    runner, _engine, run_id = await _run(ending)
    kept = runner.outcome("dev1", run_id)
    assert kept is not None and kept["status"] in ("done", "error")
    assert kept["run_id"] == run_id
    _assert_kept_out(kept)


async def test_the_kept_result_stays_masked_once_the_device_is_forgotten():
    # A removed device's runtime secrets are forgotten, but its last run is
    # still answered: the answer was masked when it was kept.
    runner, _engine, run_id = await _run("refused")
    get_secret_registry().forget("dev1")
    kept = runner.outcome("dev1", run_id)
    assert kept["error"] == f"Key {REDACTED} was refused"
    _assert_kept_out(kept)


async def test_the_log_s_account_of_a_failed_run_never_carries_it():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(SecretRedactionFilter())
    logger = setup_actions.log
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        await _run("raise")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    written = stream.getvalue()
    assert f"RuntimeError: the widget refused key {REDACTED}" in written
    assert KEY not in written


async def test_a_parameter_nothing_marks_secret_is_not_learned():
    _runner_, engine, _run_id = await _run("done")
    assert _sent(engine)[-1]["result"] == {"paired_with": REDACTED, "room": ROOM}
    assert get_secret_registry().masked("log", f"in {ROOM}") == f"in {ROOM}"
