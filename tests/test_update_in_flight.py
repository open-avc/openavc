"""An update or rollback is in flight from its start until it fails or the
process ends (updater/update_run.py ``RunRecord.in_flight``). While it is,
another update, a rollback, a target the cloud stages and the maintenance
window are refused, each with a sentence naming the run; a restart is refused
until the run reaches its exit, which then ends the process itself.

The restart door is armed with a stand-in engine (tests/test_update_exit.py's
``door``), whose stop and exit are recorded instead of run.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import time as dt_time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openavc import restart
from openavc.updater import update_run
from tests.test_cloud_update_not_newer import _handler, _send, _update as _software_update
from tests import test_update_exit
from tests.test_update_exit import (
    EXITS,
    LINUX,
    _cloud_update,
    _manager,
    _settle,
    _the_exit,
    _update,
    _yield,
)

# The restart door armed with a stand-in engine (a fixture).
door = test_update_exit.door

pytestmark = pytest.mark.usefixtures("under_its_service")

INSTALLING = "An update to v9.9.9 is being installed."
ROLLING_BACK = "A rollback to v9.8.0 is being installed."
RESTARTS_BY_ITSELF = "OpenAVC restarts by itself when it is done."
TRY_AGAIN = "Try again once it has finished."


async def _until(reached) -> None:
    for _ in range(500):
        if reached():
            return
        await _yield(0.01)
    raise AssertionError("the run never reached the step")


class _HeldBackup:
    """``create_backup`` whose first call waits for ``go``; any later one
    returns at once."""

    def __init__(self) -> None:
        self.go = threading.Event()
        self.calls = 0

    def __call__(self, *_args, **_kwargs) -> Path:
        self.calls += 1
        if self.calls == 1:
            self.go.wait(5)
        return Path("b.zip")


async def _rollback_held_at_its_backup(mgr, held: _HeldBackup):
    with patch("openavc.updater.backup.create_backup", held), \
            patch("openavc.updater.backup.cleanup_old_backups"), \
            patch("openavc.updater.rollback.can_rollback", return_value=True), \
            patch("openavc.updater.rollback.rollback_target_version", return_value="9.8.0"), \
            patch("openavc.updater.rollback.perform_rollback", return_value=True):
        return await mgr.rollback()


def _hold_the_download(mgr, tmp_path):
    """The update's download waits for ``go``; ``downloading`` is set once it
    has begun."""
    downloading, go = asyncio.Event(), asyncio.Event()

    async def download(_release):
        downloading.set()
        await go.wait()
        return tmp_path / "artifact"

    mgr._download_update = download
    return downloading, go


# ── Another update or a rollback ──


@pytest.mark.asyncio
@pytest.mark.parametrize("which", sorted(EXITS))
async def test_while_a_rollback_runs_another_update_or_rollback_is_refused(door, tmp_path, which):
    mgr = _manager(tmp_path, LINUX)
    held = _HeldBackup()
    rolling_back = asyncio.create_task(_rollback_held_at_its_backup(mgr, held))
    await _until(lambda: held.calls == 1)

    answer = await EXITS[which](mgr)

    held.go.set()
    assert (await rolling_back)["success"] is True
    assert answer == {"success": False, "error": f"{ROLLING_BACK} {TRY_AGAIN}"}
    assert await _the_exit() == 0


@pytest.mark.asyncio
async def test_an_update_applied_before_the_door_is_armed_stays_in_flight(door, tmp_path):
    """Nothing ends the process yet, so the update waits for the next start:
    another update is refused, and a restart is what starts it."""
    restart.reset()
    mgr = _manager(tmp_path, LINUX)
    assert (await _update(mgr))["success"] is True

    again = await _cloud_update(mgr)

    assert again == {
        "success": False,
        "error": "The update to v9.9.9 is applied and takes effect when OpenAVC restarts.",
    }
    assert mgr.update_in_progress
    door.engine.update_manager = mgr
    restart.accept_restarts(door.engine)
    assert restart.request_exit("graceful", "api").accepted
    assert await _the_exit() == 0


@pytest.mark.asyncio
async def test_a_check_while_the_update_ends_the_process_leaves_its_status(door, tmp_path):
    door.hold.clear()
    mgr = _manager(tmp_path, LINUX)
    assert (await _update(mgr))["success"] is True
    await _settle()
    mgr._state.set.reset_mock()
    mgr._checker.check = AsyncMock(return_value=None)

    await mgr.check_for_updates("stable")

    written = {call.args[0] for call in mgr._state.set.call_args_list}
    assert "system.update_status" not in written
    assert "system.update_error" not in written
    door.hold.set()
    await _the_exit()


# ── A rollback that does not finish ends its run ──


@pytest.mark.asyncio
async def test_a_rollback_that_raises_ends_its_run_failed(tmp_path):
    mgr = _manager(tmp_path, LINUX)
    with patch("openavc.updater.backup.create_backup", return_value=Path("b.zip")), \
            patch("openavc.updater.backup.cleanup_old_backups"), \
            patch("openavc.updater.rollback.can_rollback", return_value=True), \
            patch("openavc.updater.rollback.rollback_target_version", return_value="9.8.0"), \
            patch("openavc.updater.rollback.perform_rollback", side_effect=OSError("disk full")):
        answer = await mgr.rollback()

    assert answer == {"success": False, "error": "Rollback failed. Check server logs."}
    assert not mgr.update_in_progress
    assert (mgr.run_record.run.end, mgr.run_record.run.error) == ("failed", "Rollback failed")


@pytest.mark.asyncio
async def test_a_rollback_cancelled_during_its_backup_ends_its_run_failed(door, tmp_path):
    mgr = _manager(tmp_path, LINUX)
    held = _HeldBackup()
    rolling_back = asyncio.create_task(_rollback_held_at_its_backup(mgr, held))
    await _until(lambda: held.calls == 1)

    rolling_back.cancel()
    held.go.set()
    with pytest.raises(asyncio.CancelledError):
        await rolling_back

    assert not mgr.update_in_progress
    assert (mgr.run_record.run.end, mgr.run_record.run.error) == ("failed", "Rollback cancelled")


# ── A target the cloud stages ──


@pytest.mark.asyncio
async def test_a_target_the_cloud_stages_while_an_update_runs_is_refused(door, tmp_path):
    mgr = _manager(tmp_path, LINUX)
    downloading, go = _hold_the_download(mgr, tmp_path)
    updating = asyncio.create_task(_update(mgr))
    await downloading.wait()
    agent, handler = _handler(mgr)

    await _send(handler, _software_update("9.10.0", auto_restart=False))

    [(_type, result)] = agent.sent
    assert (result["success"], result["error"]) == (False, f"{INSTALLING} {TRY_AGAIN}")
    assert not (tmp_path / "staged-update.json").exists()
    staged = [c for c in mgr._state.set.call_args_list if c.args[0] == "system.update_staged_version"]
    assert staged == []
    go.set()
    assert (await updating)["success"] is True
    await _the_exit()


# ── The maintenance window ──


@pytest.mark.asyncio
async def test_the_maintenance_window_waits_a_day_while_an_update_is_in_flight(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, LINUX)
    mgr.run_record.begin(update_run.UpdateRun("9.9.9", update_run.CHECKED, from_version="1.0.0"))
    mgr.check_for_updates = AsyncMock()
    mgr.apply_update = AsyncMock()
    monkeypatch.setattr(mgr, "_in_window", lambda *_args: True)
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        raise asyncio.CancelledError  # the policy changing ends the loop here

    monkeypatch.setattr(asyncio, "sleep", sleep)

    await mgr._maintenance_window_loop(dt_time(2), dt_time(4), None)

    assert waits == [24 * 3600]
    mgr.check_for_updates.assert_not_awaited()
    mgr.apply_update.assert_not_awaited()


# ── A restart ──


@pytest.mark.asyncio
async def test_a_restart_while_an_update_downloads_is_refused_and_the_update_goes_on(door, tmp_path):
    mgr = _manager(tmp_path, LINUX)
    downloading, go = _hold_the_download(mgr, tmp_path)
    door.engine.update_manager = mgr
    updating = asyncio.create_task(_update(mgr))
    await downloading.wait()

    answer = restart.request_exit("graceful", "api")
    retry = restart.exit_to_retry()

    assert (answer.accepted, answer.refusal) == (False, restart.UPDATING)
    assert answer.sentence == f"{INSTALLING} {RESTARTS_BY_ITSELF}"
    assert retry is False
    assert restart._exiting == set() and door.order == []
    go.set()
    assert (await updating)["success"] is True
    assert await _the_exit() == 0
    assert door.order == ["stop", ("exit", 0)]


@pytest.mark.asyncio
async def test_a_restart_while_a_rollback_backs_up_is_refused(door, tmp_path):
    mgr = _manager(tmp_path, LINUX)
    held = _HeldBackup()
    door.engine.update_manager = mgr
    rolling_back = asyncio.create_task(_rollback_held_at_its_backup(mgr, held))
    await _until(lambda: held.calls == 1)

    answer = restart.request_exit("hard", "cloud")

    held.go.set()
    assert (await rolling_back)["success"] is True
    assert (answer.refusal, answer.sentence) == (restart.UPDATING, f"{ROLLING_BACK} {RESTARTS_BY_ITSELF}")
    await _the_exit()


@pytest.mark.asyncio
async def test_a_restart_after_an_update_failed_is_accepted(door, tmp_path):
    mgr = _manager(tmp_path, LINUX)
    mgr._download_update = AsyncMock(side_effect=OSError("connection reset"))
    door.engine.update_manager = mgr

    assert (await _update(mgr))["success"] is False

    assert restart.request_exit("graceful", "api").accepted
    await _the_exit()
