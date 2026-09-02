"""When an update is called good, and what the history says when it is undone.

Measured on an appliance panel, updating v0.31.0 -> v0.32.0: the new version
started, served for a minute, was restarted, and came back on v0.31.0 while
the Updates page went on reporting the update a success. Four separate things
were wrong, and these tests hold each of them:

1. The protection against exactly that was a graceful-shutdown handler
   (``reset_marker_attempts``), and an appliance never shuts down gracefully —
   its supervisor kills the server and Android's init kills it on reboot. So
   there was no protection at all: any restart inside the confirmation window
   reverted the update.
2. A deliberate reboot counted as a crashed startup.
3. The confirmation was a 60-second timer, on hardware whose job is to be
   power-cycled — while the update manager had already confirmed off a real
   check two seconds in.
4. The rollback never corrected the update history, so the page claimed an
   update that no longer existed. That is the only part a customer sees.

The fix inverts the test: an update is confirmed by evidence that the new
version RAN (the engine finished starting), not by the absence of a restart
over a fixed window. A marker that survives a boot therefore means that boot
never got the engine up, which is a fact no kill signal, power cut or reboot
can forge.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from openavc.updater.manager import UpdateManager
from openavc.updater.rollback import (
    check_rollback_needed,
    read_pending_marker,
    write_pending_marker,
)

MAIN_PY = Path(__file__).resolve().parent.parent / "openavc" / "main.py"
ENGINE_PY = Path(__file__).resolve().parent.parent / "openavc" / "core" / "engine.py"


# ===========================================================================
# 1 + 3: the update is confirmed when the engine starts, not on a timer
# ===========================================================================


class _Exited(BaseException):
    """Stand-in for os._exit, which a test cannot let run.

    Derived from BaseException on purpose: ``_initialize_engine`` wraps its
    body in ``except Exception``, so an ordinary exception would be swallowed
    and the test would read a process exit as a clean startup.
    """

    def __init__(self, code: int) -> None:
        self.code = code


def _run_startup(tmp_path: Path):
    """Drive openavc.main._initialize_engine with the engine itself stubbed.

    This is the production boot sequence — the rollback check, the engine
    start, and whatever bookkeeping follows it — with nothing mocked between
    the check and the confirmation. Returns the mock that stands in for
    perform_rollback so a caller can assert a rollback did or did not fire.
    """
    import openavc.main as main

    app = SimpleNamespace(state=SimpleNamespace(engine_ready=False, engine_error=None))
    cfg = SimpleNamespace(data_dir=tmp_path)

    with patch("openavc.system_config.get_system_config", return_value=cfg), \
            patch.object(main.engine, "start", new=AsyncMock()), \
            patch("openavc.updater.rollback.perform_rollback") as rollback, \
            patch("openavc.updater.rollback.restore_pre_update_data"), \
            patch.object(main.os, "_exit", side_effect=_Exited):
        rollback.return_value = True
        import asyncio
        asyncio.run(main._initialize_engine(app))
    return rollback


class TestTheEngineStartingIsWhatConfirmsAnUpdate:
    """An update is good once the new version got the engine up.

    The old rule was "no restart for 60 seconds", which is not a test of the
    new version at all — it is a test of whether anybody touched the box.
    """

    def test_a_successful_start_clears_the_marker_immediately(self, tmp_path):
        write_pending_marker(tmp_path, "1.0.0", "2.0.0")

        _run_startup(tmp_path)

        assert read_pending_marker(tmp_path) is None, (
            "the engine started, so the update is confirmed — nothing may be "
            "left for a later restart to trip over"
        )

    def test_an_ungraceful_restart_right_after_a_good_update_keeps_it(self, tmp_path):
        """The measured appliance failure, start to finish.

        The supervisor kills the server with a signal it never handles, so no
        graceful shutdown runs; the panel comes straight back up. Nothing here
        may roll back.
        """
        write_pending_marker(tmp_path, "0.31.0", "0.32.0")

        _run_startup(tmp_path)              # boot on 0.32.0, engine starts
        # (killed: no engine.stop(), no reset, seconds later ...)
        rollback = _run_startup(tmp_path)   # straight back up on 0.32.0

        rollback.assert_not_called()
        assert read_pending_marker(tmp_path) is None

    def test_a_startup_that_never_finishes_still_rolls_back(self, tmp_path):
        """The case automatic rollback exists for: the new version cannot run.

        Two boots that never reached a started engine, so the marker survives
        both and the attempt counter reaches the threshold.
        """
        write_pending_marker(tmp_path, "1.0.0", "2.0.0")

        assert check_rollback_needed(tmp_path) is False   # boot 1, then crash
        assert check_rollback_needed(tmp_path) is True    # boot 2, still dead

    def test_the_engine_no_longer_owns_a_confirmation_timer(self):
        """Defect 3: two confirmation concepts in one subsystem, disagreeing.

        The engine's 60-second sleep is gone; the updater is the only thing
        that decides an update is good.
        """
        source = ENGINE_PY.read_text(encoding="utf-8")
        assert "_confirm_startup_after_delay" not in source
        assert "confirm_startup" not in source

    def test_nothing_depends_on_a_graceful_shutdown_any_more(self):
        """Defect 1: the guard that was dead code on an appliance is gone.

        ``reset_marker_attempts`` existed only so a deliberate restart inside
        the window was not read as a crash. It was called from engine
        shutdown, which an appliance never reaches. Confirming at engine start
        removes the need for it entirely, so it must not survive as a second,
        weaker answer to the same question.
        """
        from openavc.updater import rollback

        assert not hasattr(rollback, "reset_marker_attempts")
        assert "reset_marker_attempts" not in ENGINE_PY.read_text(encoding="utf-8")

    def test_the_confirmation_runs_after_the_engine_is_up(self):
        """Order is the whole fix: confirming before a successful start would
        call every update good, including one that is about to fall over."""
        tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "_initialize_engine"
        )
        started_at = confirmed_at = None
        for node in ast.walk(func):
            if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                fn = node.value.func
                if isinstance(fn, ast.Attribute) and fn.attr == "start":
                    started_at = node.lineno
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "confirm_startup":
                    confirmed_at = node.lineno
        assert started_at is not None, "_initialize_engine no longer starts the engine"
        assert confirmed_at is not None, "_initialize_engine never confirms the update"
        assert confirmed_at > started_at


# ===========================================================================
# 2: a reboot is not a crash
# ===========================================================================


class TestARebootIsNotACrashedStartup:
    """A machine that restarted says nothing about whether the new version
    works. Only a startup that failed *while the machine kept running* is
    evidence of a crash loop — that is what a service manager relaunching a
    broken binary looks like, and it is the thing rollback is for.

    A panel losing power is a normal event in a room, and it leaves no
    shutdown handler behind to explain itself.
    """

    def test_a_restart_of_the_machine_starts_the_count_again(self, tmp_path):
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-a"):
            write_pending_marker(tmp_path, "1.0.0", "2.0.0")
            assert check_rollback_needed(tmp_path) is False
            assert read_pending_marker(tmp_path)["attempts"] == 1

        # Power cut mid-startup; the panel comes back on a new OS boot.
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-b"):
            assert check_rollback_needed(tmp_path) is False
            assert read_pending_marker(tmp_path)["attempts"] == 1

    def test_two_failed_starts_in_one_boot_still_roll_back(self, tmp_path):
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-a"):
            write_pending_marker(tmp_path, "1.0.0", "2.0.0")
            assert check_rollback_needed(tmp_path) is False
            assert check_rollback_needed(tmp_path) is True

    def test_a_crash_loop_that_survives_a_reboot_still_rolls_back(self, tmp_path):
        """Resetting on a new boot must not make rollback unreachable: the
        broken version crashes again in the new boot, and that second failure
        within it is what trips."""
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-a"):
            write_pending_marker(tmp_path, "1.0.0", "2.0.0")
            assert check_rollback_needed(tmp_path) is False
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-b"):
            assert check_rollback_needed(tmp_path) is False
            assert check_rollback_needed(tmp_path) is True

    def test_where_the_boot_cannot_be_read_every_attempt_counts(self, tmp_path):
        """Windows and macOS have no /proc. Falling back to counting every
        boot is what this code did everywhere before, and with confirmation
        moved to engine start it is no longer the trap it was."""
        with patch("openavc.updater.rollback._current_boot_id", return_value=None):
            write_pending_marker(tmp_path, "1.0.0", "2.0.0")
            assert check_rollback_needed(tmp_path) is False
            assert check_rollback_needed(tmp_path) is True

    def test_a_marker_from_an_older_version_has_no_boot_recorded(self, tmp_path):
        """An update applied by the previous release wrote no boot id. It must
        behave, not crash, and not silently become un-rollbackable."""
        (tmp_path / "pending-update").write_text(
            json.dumps({"from_version": "1.0.0", "to_version": "2.0.0", "attempts": 0}),
            encoding="utf-8",
        )
        with patch("openavc.updater.rollback._current_boot_id", return_value="boot-a"):
            assert check_rollback_needed(tmp_path) is False
            assert check_rollback_needed(tmp_path) is True

    def test_the_boot_id_reader_never_raises(self):
        """It reads a file that does not exist on two of three platforms."""
        from openavc.updater.rollback import _current_boot_id

        value = _current_boot_id()
        assert value is None or (isinstance(value, str) and value)


# ===========================================================================
# 4: a rollback corrects the history it undid
# ===========================================================================


def _history(tmp_path: Path) -> list[dict]:
    return json.loads((tmp_path / "update-history.json").read_text(encoding="utf-8"))


def _write_history(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "update-history.json").write_text(json.dumps(rows), encoding="utf-8")


def _boot_manager(tmp_path: Path, running: str) -> list[dict]:
    """Construct an UpdateManager the way a server start does, on ``running``."""
    with patch("openavc.updater.manager.__version__", running):
        UpdateManager(state_store=None, data_dir=tmp_path)
    return _history(tmp_path)


class TestHistoryCannotClaimAnUpdateThatWasUndone:
    """The panel's update-history.json still read
    ``{"to_version": "0.32.0", "status": "success"}`` after it had reverted to
    0.31.0. That is the whole of "it says it updated but it didn't", and it is
    the only part of this defect a customer ever sees.

    The correction is made from the end state — the version actually running —
    rather than announced by the rollback path on its way out. A rollback that
    is initiated may still not apply (a missing snapshot, an aborted helper),
    and a row claiming a revert that did not happen is the same lie pointed
    the other way.
    """

    def test_a_reverted_update_stops_reporting_success(self, tmp_path):
        _write_history(tmp_path, [{
            "from_version": "0.31.0", "to_version": "0.32.0",
            "status": "success", "error": "", "rollback": False,
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])

        rows = _boot_manager(tmp_path, running="0.31.0")

        assert rows[0]["status"] == "rolled_back"
        assert rows[0]["from_version"] == "0.31.0"
        assert rows[0]["to_version"] == "0.32.0"

    def test_an_update_that_stuck_is_left_alone(self, tmp_path):
        _write_history(tmp_path, [{
            "from_version": "0.31.0", "to_version": "0.32.0",
            "status": "success", "error": "", "rollback": False,
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])

        rows = _boot_manager(tmp_path, running="0.32.0")

        assert rows[0]["status"] == "success"

    def test_release_tag_skew_is_not_read_as_a_revert(self, tmp_path):
        """The row records the release tag while __version__ comes from the
        bundled pyproject, and the two are allowed to differ. The running
        version is neither the target nor where we started, so the update
        plainly applied — flipping it to rolled_back would invent a revert."""
        _write_history(tmp_path, [{
            "from_version": "1.0.0", "to_version": "2.0.0",
            "status": "success", "error": "", "rollback": False,
            "note": "Applied; running v2.0.1 (release tag v2.0.0)",
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])

        rows = _boot_manager(tmp_path, running="2.0.1")

        assert rows[0]["status"] == "success"

    def test_a_manual_rollback_row_is_left_alone(self, tmp_path):
        """A rollback entry already records the version it restored, and we
        are running it. Nothing to correct."""
        _write_history(tmp_path, [{
            "from_version": "2.0.0", "to_version": "1.0.0",
            "status": "success", "error": "", "rollback": True,
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])

        rows = _boot_manager(tmp_path, running="1.0.0")

        assert rows[0]["status"] == "success"
        assert rows[0]["rollback"] is True

    def test_only_the_newest_row_is_reconciled(self, tmp_path):
        """Older rows describe versions long gone; the running version says
        nothing about them."""
        _write_history(tmp_path, [
            {"from_version": "0.31.0", "to_version": "0.32.0", "status": "success",
             "rollback": False, "timestamp": "2026-09-02T18:43:42+00:00"},
            {"from_version": "0.30.0", "to_version": "0.31.0", "status": "success",
             "rollback": False, "timestamp": "2026-08-02T10:00:00+00:00"},
        ])

        rows = _boot_manager(tmp_path, running="0.31.0")

        assert rows[0]["status"] == "rolled_back"
        assert rows[1]["status"] == "success"

    def test_the_correction_is_written_to_disk(self, tmp_path):
        """A correction only held in memory is gone by the time anyone looks."""
        _write_history(tmp_path, [{
            "from_version": "0.31.0", "to_version": "0.32.0",
            "status": "success", "rollback": False,
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])
        _boot_manager(tmp_path, running="0.31.0")

        assert _history(tmp_path)[0]["status"] == "rolled_back"

    def test_a_crash_before_the_manager_ran_is_reported_as_failed(self, tmp_path):
        """The other order: the new version died before it ever loaded
        history, so the row is still pending when the rolled-back version
        boots. It must not become a success either."""
        _write_history(tmp_path, [{
            "from_version": "0.31.0", "to_version": "0.32.0",
            "status": "pending", "rollback": False,
            "timestamp": "2026-09-02T18:43:42+00:00",
        }])

        rows = _boot_manager(tmp_path, running="0.31.0")

        assert rows[0]["status"] == "failed"

    def test_the_reconciliation_survives_a_row_missing_fields(self, tmp_path):
        """History is a file on disk; a truncated or hand-edited row must not
        stop the server from starting."""
        _write_history(tmp_path, [{"status": "success"}])

        rows = _boot_manager(tmp_path, running="0.31.0")

        assert rows[0]["status"] == "success"
