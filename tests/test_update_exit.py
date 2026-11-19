"""An applied update or rollback ends the process through the restart door
(``openavc.restart.exit_for_update``): the stop every restart runs, then the
exit code the install's service manager reads, with the rollback's device
logins put back after the stop.

Each test arms the door with a stand-in engine whose stop is recorded, and
records the process exit, the watchdog timer and the door's wait instead of
running them; nothing here stops a real engine or ends the process.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc import restart
from openavc.updater.checker import ReleaseInfo
from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType
from openavc.updater.rollback import INSTALLER_LEAD_SECONDS

# The door's wait is patched away; the tests yield to the loop with this.
_yield = asyncio.sleep

WINDOWS = DeploymentType.WINDOWS_INSTALLER
LINUX = DeploymentType.LINUX_PACKAGE


class _Exited(BaseException):
    """Stand-in for os._exit. BaseException so no ``except Exception`` on the
    way reads a process exit as a failure."""

    def __init__(self, code: int) -> None:
        self.code = code


@pytest.fixture
def door(monkeypatch, under_its_service):
    """The door armed with an engine that is running. ``order`` records the
    engine's stop and each exit; ``timers`` the watchdogs armed; ``hold``,
    while clear, keeps the stop from finishing."""
    seen = SimpleNamespace(order=[], timers=[], hold=asyncio.Event())
    seen.hold.set()

    class Timer:
        def __init__(self, interval, function):
            self.interval, self.function = interval, function
            seen.timers.append(self)

        def start(self):
            pass

    async def no_wait(_seconds):
        pass

    def exit_(code):
        seen.order.append(("exit", code))
        raise _Exited(code)

    async def shutdown(_deadline):
        seen.order.append("stop")
        await seen.hold.wait()

    seen.engine = SimpleNamespace(stopping=False, shutdown=shutdown)
    monkeypatch.setattr("threading.Timer", Timer)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr(os, "_exit", exit_)
    restart.accept_restarts(seen.engine)
    return seen


async def _settle() -> None:
    for _ in range(3):
        await _yield(0)


async def _the_exit() -> int:
    with pytest.raises(_Exited) as exited:
        await asyncio.gather(*restart._exiting)
    return exited.value.code


def _manager(tmp_path: Path, deployment: DeploymentType) -> UpdateManager:
    mgr = UpdateManager(state_store=MagicMock(), data_dir=tmp_path, project_path=tmp_path / "project.avc")
    mgr._deployment_type = deployment
    mgr._checker._last_check_result = ReleaseInfo(
        version="9.9.9", tag="v9.9.9", prerelease=False, changelog="",
        published_at="2026-11-01T00:00:00Z",
    )
    mgr._download_update = AsyncMock(return_value=tmp_path / "artifact")
    mgr._download_artifact = AsyncMock(return_value=tmp_path / "artifact")
    mgr._verify_hash = AsyncMock()
    mgr._apply_linux = MagicMock()
    mgr._apply_windows = MagicMock()
    return mgr


def _quiet_backups():
    return patch("openavc.updater.backup.create_backup", return_value=Path("b.zip")), \
        patch("openavc.updater.backup.cleanup_old_backups")


async def _update(mgr):
    backup, trim = _quiet_backups()
    with backup, trim:
        return await mgr.apply_update()


async def _cloud_update(mgr):
    backup, trim = _quiet_backups()
    with backup, trim, patch.object(mgr, "_consumes_signed_tarball", return_value=False):
        return await mgr.apply_cloud_update(
            "9.9.9", "https://github.com/open-avc/openavc/releases/download/v9.9.9/a.tar.gz", "ab" * 32,
        )


async def _rollback(mgr):
    backup, trim = _quiet_backups()
    with backup, trim, \
            patch("openavc.updater.rollback.can_rollback", return_value=True), \
            patch("openavc.updater.rollback.rollback_target_version", return_value="9.8.0"), \
            patch("openavc.updater.rollback.perform_rollback", return_value=True):
        return await mgr.rollback()


EXITS = {"update": _update, "cloud update": _cloud_update, "rollback": _rollback}


@pytest.mark.asyncio
@pytest.mark.parametrize("deployment, code", [(WINDOWS, 42), (LINUX, 0)])
@pytest.mark.parametrize("which", sorted(EXITS))
async def test_each_exit_runs_the_stop_then_ends_with_the_install_s_code(
    door, tmp_path, which, deployment, code, monkeypatch,
):
    monkeypatch.setattr(
        "openavc.core.device_logins.put_back_for_rollback",
        lambda _data, _project, then: then(),
    )
    mgr = _manager(tmp_path, deployment)

    answer = await EXITS[which](mgr)

    assert answer["success"] is True
    assert await _the_exit() == code
    assert door.order == ["stop", ("exit", code)]
    # A stop that hangs ends at the watchdog, with the same code: Windows'
    # NSSM reading 0 would start the old version under the installer.
    (watchdog,) = door.timers
    assert watchdog.interval == restart.WATCHDOG_SECONDS
    ended = []
    monkeypatch.setattr(os, "_exit", ended.append)
    watchdog.function()
    # (Each end returns here, so a rollback's watchdog ends twice: its last
    # step's thread, then the watchdog itself.)
    assert ended and set(ended) == {code}


@pytest.mark.asyncio
async def test_a_rollback_puts_the_logins_back_after_the_stop_holding_the_save_lock(
    door, tmp_path, monkeypatch,
):
    from openavc.core.project_loader import _project_save_lock

    def put_back(data_dir, project_path, then):
        door.order.append(("put back", data_dir, project_path))
        with _project_save_lock:
            door.order.append(("locked", _project_save_lock.locked()))
            then()

    monkeypatch.setattr("openavc.core.device_logins.put_back_for_rollback", put_back)
    mgr = _manager(tmp_path, LINUX)

    await _rollback(mgr)

    assert await _the_exit() == 0
    # After the stop, whose saves write the running project with its markers.
    assert door.order == [
        "stop",
        ("put back", tmp_path, tmp_path / "project.avc"),
        ("locked", True),
        ("exit", 0),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(EXITS))
async def test_while_the_exit_runs_another_update_or_a_rollback_is_refused(door, tmp_path, which):
    door.hold.clear()
    mgr = _manager(tmp_path, LINUX)
    assert (await _update(mgr))["success"] is True
    await _settle()

    answer = await EXITS[which](mgr)

    assert answer["success"] is False
    assert "in progress" in answer["error"]
    assert mgr.update_in_progress
    door.hold.set()
    await _the_exit()


@pytest.mark.asyncio
async def test_a_restart_already_under_way_ends_with_the_update_s_code_and_last_step(door):
    door.hold.clear()
    assert restart.request_exit("graceful", "api").accepted
    await _settle()
    assert door.order == ["stop"]

    assert restart.exit_for_update("rollback", 42, lambda end: (door.order.append("last"), end()))
    door.hold.set()

    assert await _the_exit() == 42
    assert door.order == ["stop", "last", ("exit", 42)]
    # One stop: the update took on the exit under way, it did not start its own.
    assert len(door.timers) == 1


@pytest.mark.asyncio
async def test_a_service_stop_under_way_ends_with_the_update_s_code_and_last_step(door):
    door.engine.stopping = True

    async def shutdown(_deadline):
        # Reached while the lifespan's stop runs.
        door.order.append("stop")
        assert restart.exit_for_update("update", 42, lambda end: (door.order.append("last"), end()))

    door.engine.shutdown = shutdown

    with pytest.raises(_Exited) as ended:
        await restart.stop_for_service(door.engine)

    assert ended.value.code == 42
    assert door.order == ["stop", "last", ("exit", 42)]
    assert restart._exiting == set()


@pytest.mark.asyncio
async def test_a_service_stop_with_no_update_leaves_the_lifespan_to_end(door):
    await restart.stop_for_service(door.engine)
    assert door.order == ["stop"]


@pytest.mark.asyncio
async def test_before_the_door_is_armed_nothing_ends_and_the_update_is_not_held(door, tmp_path, caplog):
    restart.reset()
    mgr = _manager(tmp_path, LINUX)

    answer = await _update(mgr)

    assert answer["success"] is True
    assert restart._exiting == set()
    assert door.order == []
    assert not mgr.update_in_progress
    assert "takes effect when the server next starts" in caplog.text


def test_the_watchdog_ends_the_process_when_the_last_step_hangs(monkeypatch):
    import threading

    ended = []
    monkeypatch.setattr(os, "_exit", ended.append)
    monkeypatch.setattr(restart, "LAST_STEP_SECONDS", 0.05)
    stuck = threading.Event()
    ending = restart._Ending()
    ending.take_on(42, lambda _end: stuck.wait(5))

    ending.finish_from_watchdog()

    assert ended == [42]
    stuck.set()


def test_the_watchdog_runs_the_last_step_the_stop_never_reached(monkeypatch):
    ended = []
    monkeypatch.setattr(os, "_exit", ended.append)
    ran = []
    ending = restart._Ending()
    ending.take_on(0, lambda end: (ran.append("put back"), end()))

    ending.finish_from_watchdog()

    assert ran == ["put back"]
    assert ended[0] == 0


def test_the_windows_installer_is_scheduled_to_start_after_the_longest_exit():
    # The installer task is set to run INSTALLER_LEAD_SECONDS after it is
    # scheduled (to the second); the server, ending through the door, waits
    # and then ends within its longest exit, before that. An installer that
    # does find it running stops the service itself (installer/setup.iss).
    wait = restart.WAIT_SECONDS[restart.DEFAULT_MODE]
    assert wait + restart.LONGEST_EXIT_SECONDS < INSTALLER_LEAD_SECONDS


@pytest.mark.asyncio
async def test_after_a_failed_start_an_update_still_stops_and_ends_with_its_code(door, tmp_path):
    from openavc.core.start_stages import StartRun
    from openavc.middleware.startup_splash import FAILED, STOPPING, RunState

    # A start that failed arms the door with its record (openavc/start_retry.py).
    record = RunState()
    record.stopped(StartRun(), RuntimeError("the project would not load"))
    assert record.phase == FAILED
    restart.accept_restarts(door.engine, record)

    assert (await _cloud_update(_manager(tmp_path, WINDOWS)))["success"] is True

    assert await _the_exit() == 42
    assert door.order == ["stop", ("exit", 42)]
    assert record.phase == STOPPING


@pytest.mark.asyncio
async def test_a_failed_start_s_retry_ends_with_its_own_code_even_at_the_watchdog(door, monkeypatch):
    assert restart.exit_to_retry()

    assert await _the_exit() == restart.RETRY_EXIT_CODE
    (watchdog,) = door.timers
    ended = []
    monkeypatch.setattr(os, "_exit", ended.append)
    watchdog.function()
    assert ended == [restart.RETRY_EXIT_CODE]


@pytest.mark.asyncio
async def test_an_update_during_a_failed_start_s_retry_ends_with_the_update_s_code(door):
    door.hold.clear()
    assert restart.exit_to_retry()
    await _settle()

    assert restart.exit_for_update("update", 42, lambda end: (door.order.append("last"), end()))
    door.hold.set()

    # Not the retry's code: NSSM reading it would start the old version again
    # under the installer.
    assert await _the_exit() == 42
    assert door.order == ["stop", "last", ("exit", 42)]
