"""What a start that failed does next (openavc/start_retry.py), Start again
(``POST /api/start-again``), and the startup page that shows both.

Under a service manager a failed start shows its reason for a minute, then
ends with a non-zero code for the manager to start it again; the third
automatic restart in a row that fails waits on the page for Start again, and
so does any failed start nothing would restart. Which starts are automatic
restarts is decided by the record a retry leaves in the status folder: taken
by the next start, counted only when that start came soon after it on the
same boot. Start again restarts only a failed start.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc import restart, start_retry, status_folder
from openavc.api.routes import system
from openavc.core.start_stages import StartRun
from openavc.middleware import startup_splash
from openavc.middleware.startup_splash import (
    FAILED, START_AGAIN_HEADER, START_AGAIN_PATH, RunState, StartupSplashMiddleware,
)
from tests import gates


@pytest.fixture
def folder(monkeypatch, tmp_path):
    """The status folder, in this test's own data folder."""
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    return tmp_path / status_folder.STATUS_DIR_NAME


@pytest.fixture
def managed(monkeypatch):
    """A service manager starts this process again once it ends."""
    monkeypatch.setenv("OPENAVC_SERVICE_MANAGED", "1")


@pytest.fixture
def unmanaged(monkeypatch, tmp_path):
    """Nothing starts this process again (a dev session)."""
    from openavc.updater import platform

    monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(platform, "_is_docker", lambda: False)
    monkeypatch.setattr(platform, "_DEPLOYMENT_MARKER", tmp_path / "no-marker")


@pytest.fixture
def exits(monkeypatch):
    """The process exit and the stop's watchdog, recorded instead of run."""
    codes: list[int] = []
    monkeypatch.setattr(restart.os, "_exit", codes.append)
    monkeypatch.setattr(restart.threading, "Timer", lambda interval, function: Mock())
    return codes


@pytest.fixture
def hold(monkeypatch):
    """The page's wait, recorded with what the record answered during it."""
    seen = SimpleNamespace(waits=[], answers=[], during=None)

    async def sleep(seconds):
        seen.waits.append(seconds)
        if seen.during is not None:
            seen.during()

    monkeypatch.setattr(start_retry.asyncio, "sleep", sleep)
    return seen


def _engine():
    return SimpleNamespace(shutdown=AsyncMock(), stopping=False, start_failed=Mock())


def _failed() -> RunState:
    record = RunState()
    run = StartRun()
    with run.stage("loading the project"):
        pass
    record.stopped(run, RuntimeError("project unreadable"))
    return record


async def _fail_once(restarts: int, engine=None) -> RunState:
    """One process whose start failed, as ``openavc.main`` runs what follows."""
    restart.reset()
    record = _failed()
    await start_retry.after_failure(engine or _engine(), record, restarts)
    await asyncio.gather(*restart._exiting)
    return record


def _recorded(folder: Path) -> dict | None:
    path = folder / status_folder.START_RETRY_FILE_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# --- the wait, then the exit -------------------------------------------------


async def test_under_a_service_manager_the_reason_shows_a_minute_then_the_process_ends(
        folder, managed, exits, hold):
    record = _failed()
    engine = _engine()
    hold.during = lambda: hold.answers.append(record.answer())

    await start_retry.after_failure(engine, record, 0)
    await asyncio.gather(*restart._exiting)

    assert hold.waits == [start_retry.HOLD_SECONDS] == [60]
    (during,) = hold.answers
    assert (during["phase"], during["held"], during["retry_in"]) == (FAILED, False, 60)
    # The stop every restart runs, then an exit a service manager reads as a
    # failure and starts again: not 0, and not the code that leaves NSSM stopped.
    engine.shutdown.assert_awaited_once()
    assert exits == [restart.RETRY_EXIT_CODE]
    assert restart.RETRY_EXIT_CODE not in (0, restart.LEAVE_STOPPED_EXIT_CODE)
    assert _recorded(folder)["restarts"] == 1
    # The failed phase stands for the tray until the next start writes its own.
    assert record.phase == FAILED


@pytest.mark.parametrize("now", [123.456, 0.1, 98765.4321])
def test_the_countdown_starts_at_the_whole_hold(monkeypatch, now):
    """Read at once, the countdown says the hold itself: a clock that has not
    moved since the retry began (Windows reads it to about 16 ms) leaves only
    the float's rounding, never a second more."""
    monkeypatch.setattr(startup_splash.time, "monotonic", lambda: now)
    record = _failed()
    record.retrying(start_retry.HOLD_SECONDS)

    assert record.answer()["retry_in"] == start_retry.HOLD_SECONDS


async def test_the_third_automatic_restart_that_fails_waits_for_start_again(
        folder, managed, exits, hold):
    seen = []
    for _ in range(start_retry.MAX_AUTOMATIC_RESTARTS + 1):
        restarts = start_retry.automatic_restarts()
        record = await _fail_once(restarts)
        seen.append((restarts, record.answer()["held"]))

    assert seen == [(0, False), (1, False), (2, False), (3, True)]
    assert exits == [restart.RETRY_EXIT_CODE] * 3
    # Waiting, it leaves no record: whatever starts it next starts a run of its own.
    assert _recorded(folder) is None


async def test_with_nothing_to_restart_it_a_failed_start_waits_at_once(folder, unmanaged, exits, hold):
    engine = _engine()
    record = await _fail_once(0, engine)

    assert record.answer()["held"] is True and record.answer()["retry_in"] is None
    assert hold.waits == [] and exits == []
    # The door is armed, so Start again and a restart from the cloud work.
    assert restart._engine is engine


async def test_a_stop_during_the_wait_ends_it_with_no_retry(folder, managed, exits):
    record = _failed()
    waiting = asyncio.create_task(start_retry.after_failure(_engine(), record, 0))
    await asyncio.sleep(0.05)
    assert record.answer()["retry_in"] == 60

    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert exits == [] and _recorded(folder) is None


async def test_start_again_during_the_wait_is_the_only_exit(folder, managed, exits, hold, monkeypatch):
    restart.reset()
    record = _failed()
    pressed = []
    monkeypatch.setattr(restart, "_exit_after", AsyncMock())
    hold.during = lambda: pressed.append(start_retry.start_again(record))

    await start_retry.after_failure(_engine(), record, 0)

    assert [answer.accepted for answer in pressed] == [True]
    # The retry stood aside: no retry exit, and no record, so the start
    # Start again makes has every retry ahead of it.
    assert exits == [] and _recorded(folder) is None


async def test_while_an_update_is_being_installed_the_retry_waits_for_it(folder, managed, exits, hold):
    """A start that failed can still take an update (the cloud's), and a
    retry now would cut it off: the hold goes round again until the update is
    done, and ends the process only if it failed."""
    sentence = "An update to v9.9.9 is being installed. OpenAVC restarts by itself when it is done."
    updates = SimpleNamespace(sentence=sentence)
    updates.restart_refusal = lambda: updates.sentence
    engine = _engine()
    engine.update_manager = updates
    record = _failed()

    def during():
        if len(hold.waits) == 2:
            updates.sentence = ""

    hold.during = during
    await start_retry.after_failure(engine, record, 0)
    await asyncio.gather(*restart._exiting)

    assert hold.waits == [start_retry.HOLD_SECONDS] * 2
    assert exits == [restart.RETRY_EXIT_CODE]
    assert _recorded(folder)["restarts"] == 1


def test_the_retry_exit_needs_the_door_armed(exits):
    restart.reset()
    assert restart.exit_to_retry() is False
    assert exits == []


# --- which starts are automatic restarts --------------------------------------


def _write(folder: Path, **record) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    full = {
        "restarts": 2,
        "monotonic": time.monotonic() - 10,
        "computer_started": time.time() - time.monotonic(),
    }
    full.update(record)
    (folder / status_folder.START_RETRY_FILE_NAME).write_text(json.dumps(full), encoding="utf-8")


def test_a_start_soon_after_a_retry_is_that_retry_and_takes_its_record(folder):
    _write(folder)

    assert start_retry.automatic_restarts() == 2
    # Taken: a later start (a manual restart, Start again) is a start of its own.
    assert start_retry.automatic_restarts() == 0


def test_a_start_long_after_the_retry_has_the_whole_run_ahead(folder):
    _write(folder, monotonic=time.monotonic() - start_retry.RESTART_GAP_SECONDS - 5)
    assert start_retry.automatic_restarts() == 0


def test_a_start_on_a_new_boot_has_the_whole_run_ahead(folder):
    _write(folder, computer_started=time.time() - time.monotonic() - 3600)
    assert start_retry.automatic_restarts() == 0


def test_a_start_with_no_record_or_an_unreadable_one_is_the_first(folder):
    assert start_retry.automatic_restarts() == 0
    folder.mkdir(parents=True, exist_ok=True)
    (folder / status_folder.START_RETRY_FILE_NAME).write_text("{not json", encoding="utf-8")
    assert start_retry.automatic_restarts() == 0
    assert not (folder / status_folder.START_RETRY_FILE_NAME).exists()


# --- Start again ----------------------------------------------------------------


def _client(record: RunState) -> TestClient:
    app = FastAPI()
    app.state.run_state = record
    app.include_router(system.open_router, prefix="/api")
    app.add_middleware(StartupSplashMiddleware)
    return TestClient(app)


@pytest.fixture
def door(monkeypatch):
    """Arms the restart door for a record, as a failed start does, with the
    exit it starts recorded."""
    restart.reset()
    started = AsyncMock()
    monkeypatch.setattr(restart, "_exit_after", started)

    def arm(record: RunState) -> RunState:
        restart.accept_restarts(_engine(), record)
        return record

    return SimpleNamespace(started=started, arm=arm)


def test_start_again_on_a_failed_start_restarts_it(door):
    record = door.arm(_failed())
    reply = _client(record).post(START_AGAIN_PATH, headers={START_AGAIN_HEADER: "1"})

    assert reply.status_code == 202
    assert reply.json() == {"status": "starting_again", "wait_seconds": 2}
    door.started.assert_awaited_once()
    assert record.phase == "stopping"


def _started() -> RunState:
    record = RunState()
    record.started(StartRun())
    return record


@pytest.mark.parametrize("record", [RunState, _started], ids=["starting", "ready"])
def test_start_again_never_restarts_a_server_that_has_not_failed(door, record):
    reply = _client(door.arm(record())).post(START_AGAIN_PATH, headers={START_AGAIN_HEADER: "1"})

    assert reply.status_code == 409
    assert reply.json() == {"detail": "OpenAVC has not failed to start, so there is nothing to start again."}
    door.started.assert_not_awaited()


def test_start_again_from_a_page_that_is_not_openavc_s_is_refused(door):
    reply = _client(door.arm(_failed())).post(START_AGAIN_PATH)

    assert reply.status_code == 403
    assert reply.json() == {"detail": "Press Start again on OpenAVC's own page."}
    door.started.assert_not_awaited()


def test_start_again_while_an_update_is_being_installed_says_so(door):
    sentence = "An update to v9.9.9 is being installed. OpenAVC restarts by itself when it is done."
    record = _failed()
    engine = _engine()
    engine.update_manager = SimpleNamespace(restart_refusal=lambda: sentence)
    restart.accept_restarts(engine, record)

    reply = _client(record).post(START_AGAIN_PATH, headers={START_AGAIN_HEADER: "1"})

    assert reply.status_code == 409
    assert reply.json() == {"detail": sentence}
    door.started.assert_not_awaited()


def test_start_again_pressed_twice_is_one_restart(door):
    client = _client(door.arm(_failed()))
    assert client.post(START_AGAIN_PATH, headers={START_AGAIN_HEADER: "1"}).status_code == 202

    again = client.post(START_AGAIN_PATH, headers={START_AGAIN_HEADER: "1"})
    assert again.status_code == 409
    assert again.json() == {"detail": "OpenAVC is already stopping."}
    door.started.assert_awaited_once()


def test_before_the_start_finishes_only_the_start_again_post_gets_through(door):
    client = _client(door.arm(_failed()))
    assert client.get(START_AGAIN_PATH).status_code == 503
    assert client.post("/api/start-again-later").status_code == 503


# --- system.started ---------------------------------------------------------------


def test_a_failed_start_reads_as_not_started(tmp_path):
    from openavc.core.engine import Engine

    engine = Engine(str(tmp_path / "project.avc"))
    engine.state.set("system.started", True, source="system")

    engine.start_failed()

    assert engine.state.get("system.started") is False


# --- the startup page ----------------------------------------------------------------

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "startup_page_harness.cjs"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"

STARTING_ANSWER = {"phase": "starting", "ready": False, "error": None, "held": False, "retry_in": None}
READY_ANSWER = {"phase": "ready", "ready": True, "error": None, "held": False, "retry_in": None}
REASON = "Loading the project failed: project unreadable"


def _failed_answer(**fields) -> dict:
    return dict({"phase": "failed", "ready": False, "error": REASON, "held": False, "retry_in": None}, **fields)


def _page(tmp_path, answers, press_after=None, start_again=None) -> dict:
    if shutil.which("node") is None:
        gates.skip_or_fail(gates.NODE, "node not installed")
    if not (NODE_MODULES / "jsdom").is_dir():
        gates.skip_or_fail(gates.NODE, "jsdom not installed (run `npm ci` in openavc/web/programmer)")
    page = tmp_path / "page.html"
    page.write_text(startup_splash._STARTUP_PAGE, encoding="utf-8")
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({
        "answers": answers, "press_after": press_after, "start_again": start_again,
    }), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), str(page), str(scenario)],
        capture_output=True, text=True, cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)}, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_page_keeps_asking_after_a_failure_and_reaches_the_next_start(tmp_path):
    seen = _page(tmp_path, [
        _failed_answer(retry_in=45),
        "unreachable",
        STARTING_ANSWER,
        READY_ANSWER,
    ])
    first, gone, again, ready = seen["screens"]

    assert first == {
        "message": "OpenAVC could not start", "spinner": False, "error": REASON,
        "next": "Trying again in 45 seconds.", "start_again": False, "reloads": 0,
    }
    # Between the process that ended and the next one, and as the next one starts.
    assert (gone["message"], gone["spinner"], gone["error"]) == ("Starting again...", True, None)
    assert (again["message"], again["spinner"]) == ("Starting again...", True)
    assert ready["reloads"] == 1


def test_the_last_second_of_the_countdown_is_one_second(tmp_path):
    seen = _page(tmp_path, [_failed_answer(retry_in=1)])
    assert seen["screens"][0]["next"] == "Trying again in 1 second."


def test_a_failed_start_that_waits_offers_start_again_and_presses_it(tmp_path):
    seen = _page(tmp_path, [_failed_answer(held=True), STARTING_ANSWER], press_after=0)
    held, pressed = seen["screens"][:2]

    assert held["next"] == "Fix the problem above, then press Start again."
    assert held["start_again"] is True and held["error"] == REASON
    assert seen["posts"] == [{
        "url": "/api/start-again", "method": "POST", "headers": {START_AGAIN_HEADER: "1"},
    }]
    assert (pressed["message"], pressed["start_again"]) == ("Starting again...", False)
    assert seen["still_asking"]


def test_a_refused_start_again_says_why(tmp_path):
    refusal = {"status": 409, "body": {"detail": "OpenAVC is already stopping."}}
    seen = _page(tmp_path, [_failed_answer(held=True)], press_after=0, start_again=refusal)
    pressed = seen["screens"][1]

    assert pressed["next"] == "OpenAVC is already stopping."
    assert pressed["error"] == REASON
