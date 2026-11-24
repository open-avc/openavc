"""How an update or rollback ended, settled once at the next start
(updater/update_run.py ``settle``), and the history row that reads it.

Each test runs a door the way it runs on a system (an update, a rollback, the
automatic rollback at start), ends the process where a power cut, the update
helper or the version rolled back to would, and starts again: the run's end
and its own history row say what happened, and so does what the cloud is
sent (``system.update_last_result*``).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openavc.updater.manager import UpdateManager
from openavc.updater.platform import DeploymentType

pytestmark = pytest.mark.usefixtures("under_its_service")
URL = "https://github.com/open-avc/openavc/releases/download/v0.42.0/openavc-0.42.0-linux-x86_64.tar.gz"


class _State:
    def __init__(self):
        self.values: dict = {}

    def set(self, key, value, source=None):
        self.values[key] = value

    def get(self, key, default=None):
        return self.values.get(key, default)


def _start(data_dir: Path, running: str, state=None) -> UpdateManager:
    with patch("openavc.updater.manager.__version__", running):
        mgr = UpdateManager(state_store=state, data_dir=data_dir)
    mgr._deployment_type = DeploymentType.LINUX_PACKAGE
    return mgr


def _history(data_dir: Path) -> list[dict]:
    return json.loads((data_dir / "update-history.json").read_text(encoding="utf-8"))


def _row(data_dir: Path) -> tuple:
    row = _history(data_dir)[0]
    return row["from_version"], row["to_version"], row["status"], row.get("error", "")


def _published(data_dir: Path, running: str) -> tuple:
    state = _State()
    _start(data_dir, running, state)
    return tuple(state.values.get(f"system.update_last_result{k}") for k in ("", "_version", "_message"))


def _update_patches(mgr: UpdateManager, tmp_path: Path, download):
    return (
        patch("openavc.updater.manager.__version__", "0.41.0"),
        patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"),
        patch("openavc.updater.backup.cleanup_old_backups"),
        patch("openavc.updater.rollback.write_pending_marker"),
        patch.object(mgr, "_download_artifact", side_effect=download),
        patch.object(mgr, "_download_sidecar_sig"),
        patch.object(mgr, "_end_process"),
    )


async def _push(mgr: UpdateManager, tmp_path: Path, download=None):
    """The cloud's push to 0.42.0, from 0.41.0, its download ``download``."""
    import hashlib
    from contextlib import ExitStack

    async def fetched(url, filename):
        path = tmp_path / "update-cache" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"release")
        return path

    with ExitStack() as stack:
        for p in _update_patches(mgr, tmp_path, download or fetched):
            stack.enter_context(p)
        return await mgr.apply_cloud_update("0.42.0", URL, hashlib.sha256(b"release").hexdigest())


async def _roll_back(mgr: UpdateManager, tmp_path: Path, *, works: bool = True):
    with patch("openavc.updater.rollback.can_rollback", return_value=True), \
         patch("openavc.updater.rollback.perform_rollback", return_value=works), \
         patch("openavc.updater.rollback.rollback_target_version", return_value="0.41.0"), \
         patch("openavc.updater.backup.create_backup", return_value=tmp_path / "b.zip"), \
         patch("openavc.updater.backup.cleanup_old_backups"), \
         patch("openavc.system_config.APP_DIR", tmp_path), \
         patch("openavc.updater.manager.__version__", "0.42.0"), \
         patch.object(mgr, "_end_process"):
        return await mgr.rollback()


# ── Every run has a row of its own, from its start ──


@pytest.mark.asyncio
async def test_an_update_s_row_is_written_as_it_starts_and_its_failure_ends_that_row(tmp_path):
    seen = {}

    async def blip(url, filename):
        seen["row"] = _row(tmp_path)
        raise RuntimeError("network blip")

    result = await _push(_start(tmp_path, "0.41.0"), tmp_path, blip)

    assert seen["row"] == ("0.41.0", "0.42.0", "pending", "")
    assert len(_history(tmp_path)) == 1
    assert _row(tmp_path) == ("0.41.0", "0.42.0", "failed", result["error"])


# ── An update the process ended before its exit ──


@pytest.mark.asyncio
async def test_an_update_cut_off_while_downloading_reads_failed_at_the_next_start(tmp_path):
    async def power_cut(url, filename):
        raise SystemExit  # the process ends here: nothing after it runs

    with pytest.raises(SystemExit):
        await _push(_start(tmp_path, "0.41.0"), tmp_path, power_cut)

    run = _start(tmp_path, "0.41.0").run_record.run
    sentence = "Update stopped while downloading: OpenAVC restarted before it was applied"
    assert (run.end, run.error) == ("failed", sentence)
    assert _row(tmp_path) == ("0.41.0", "0.42.0", "failed", sentence)
    assert _published(tmp_path, "0.41.0") == ("failed", "0.42.0", sentence)


@pytest.mark.asyncio
async def test_an_update_cut_off_after_its_files_were_handed_over_reads_applied_when_it_runs(tmp_path):
    """Cut off while applying, after the helper's instruction was written:
    the helper applies it at the next start, so the version decides."""
    mgr = _start(tmp_path, "0.41.0")

    def handed_over_then_cut(*args):
        raise SystemExit

    with patch.object(mgr, "_apply_linux", side_effect=handed_over_then_cut), pytest.raises(SystemExit):
        await _push(mgr, tmp_path)

    assert _start(tmp_path, "0.42.0").run_record.run.end == "success"
    assert _row(tmp_path)[2:] == ("success", "")


# ── An update the helper set aside ──


def _set_aside(data_dir: Path, *, final: bool) -> None:
    (data_dir / "apply-update.retry").write_text(json.dumps({
        "artifact": "/x.tar.gz", "to_version": "0.42.0", "attempts": 3 if final else 1,
        "reason": "could not download the release's dependencies", "final": final,
    }), encoding="utf-8")


@pytest.mark.asyncio
async def test_an_update_the_helper_will_try_again_stays_pending_with_its_reason(tmp_path):
    await _push(_start(tmp_path, "0.41.0"), tmp_path)
    _set_aside(tmp_path, final=False)

    sentence = ("Update not applied yet (could not download the release's dependencies); "
                "it is tried again at the next start")
    run = _start(tmp_path, "0.41.0").run_record.run
    assert (run.end, run.error) == ("pending", sentence)
    assert _row(tmp_path)[2:] == ("pending", sentence)

    # The helper's next try applies it.
    (tmp_path / "apply-update.retry").unlink()
    assert _start(tmp_path, "0.42.0").run_record.run.end == "success"
    assert _row(tmp_path)[2:] == ("success", "")


@pytest.mark.asyncio
async def test_an_update_the_helper_gave_up_on_reads_failed_with_its_reason(tmp_path):
    await _push(_start(tmp_path, "0.41.0"), tmp_path)
    _set_aside(tmp_path, final=True)

    sentence = ("Update did not apply (could not download the release's dependencies); "
                "install v0.42.0 again once that is fixed")
    assert _published(tmp_path, "0.41.0") == ("failed", "0.42.0", sentence)
    assert _row(tmp_path)[2:] == ("failed", sentence)


@pytest.mark.asyncio
async def test_an_update_set_aside_for_another_release_says_only_that_it_did_not_apply(tmp_path):
    await _push(_start(tmp_path, "0.41.0"), tmp_path)
    (tmp_path / "apply-update.retry").write_text(json.dumps({
        "to_version": "0.40.5", "attempts": 1, "reason": "elsewhere", "final": False,
    }), encoding="utf-8")

    assert _row(tmp_path)[2] == "pending"
    _start(tmp_path, "0.41.0")
    assert _row(tmp_path)[2:] == ("failed", "Update did not apply: expected v0.42.0, running v0.41.0")


# ── A rollback ──


@pytest.mark.asyncio
async def test_a_rollback_reads_success_once_the_version_it_restored_runs(tmp_path):
    assert (await _roll_back(_start(tmp_path, "0.42.0"), tmp_path))["success"] is True
    assert _row(tmp_path)[2] == "pending"

    _start(tmp_path, "0.41.0")
    assert _row(tmp_path) == ("0.42.0", "0.41.0", "success", "")
    assert _history(tmp_path)[0]["rollback"] is True


@pytest.mark.asyncio
async def test_a_rollback_that_did_not_take_effect_does_not_read_success(tmp_path):
    await _roll_back(_start(tmp_path, "0.42.0"), tmp_path)

    _start(tmp_path, "0.42.0")
    assert _row(tmp_path) == ("0.42.0", "0.41.0", "failed", "Rollback did not take effect: still running v0.42.0")


@pytest.mark.asyncio
async def test_a_rollback_that_failed_at_once_has_a_failed_row_of_its_own(tmp_path):
    await _roll_back(_start(tmp_path, "0.42.0"), tmp_path, works=False)

    assert _row(tmp_path) == ("0.42.0", "0.41.0", "failed", "Rollback failed")
    assert _start(tmp_path, "0.42.0").run_record.run.end == "failed"


# ── The automatic rollback ──


class _Exited(BaseException):
    pass


@pytest.mark.parametrize("settled_before", [False, True], ids=["crashed-early", "settled-first"])
@pytest.mark.asyncio
async def test_an_update_the_automatic_rollback_undid_reads_reverted(tmp_path, settled_before):
    """The new version failed to start twice and went back. Whether or not
    its first start got as far as the update manager, the row reads reverted
    once the version it went back to runs."""
    import openavc.main as main
    from openavc.updater.rollback import write_pending_marker

    await _push(_start(tmp_path, "0.41.0"), tmp_path)
    write_pending_marker(tmp_path, "0.41.0", "0.42.0")
    if settled_before:
        _start(tmp_path, "0.42.0")
    with patch("openavc.updater.rollback.check_rollback_needed", return_value=True), \
         patch("openavc.updater.rollback.restore_pre_update_data"), \
         patch("openavc.core.device_logins.put_back_for_rollback"), \
         patch("openavc.updater.rollback.perform_rollback", return_value=True), \
         patch("openavc.updater.manager.__version__", "0.42.0"), \
         patch.object(main.os, "_exit", side_effect=_Exited), \
         pytest.raises(_Exited):
        main._roll_back_a_failed_update(tmp_path)

    assert _row(tmp_path)[2] == "success"  # what an earlier release reads as reverted
    assert _published(tmp_path, "0.41.0") == ("rolled_back", "0.42.0", "Update was reverted; running v0.41.0")


# ── Which run is the last, by the rows, never by the clock ──


@pytest.mark.asyncio
async def test_a_later_row_an_earlier_release_wrote_is_the_last_run_whatever_its_clock_said(tmp_path):
    """Rolled back to a release that writes no run file, which then updated,
    on hardware whose clock read 1970 at boot: its row is still the later run."""
    await _roll_back(_start(tmp_path, "0.42.0"), tmp_path)
    later = {"from_version": "0.41.0", "to_version": "0.42.0", "status": "pending", "error": "",
             "rollback": False, "timestamp": "1970-01-01T00:00:07+00:00"}
    (tmp_path / "update-history.json").write_text(json.dumps([later, *_history(tmp_path)]), encoding="utf-8")

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.source, run.from_version, run.target, run.end) == ("unrecorded", "0.41.0", "0.42.0", "success")


@pytest.mark.asyncio
async def test_a_run_whose_own_row_is_newest_is_the_last_run_whatever_the_clock_says(tmp_path):
    """A run begun while the clock read later than it does now (set back at
    boot) is still the last run: its row is the newest."""
    with patch("openavc.updater.update_run._now", return_value="2099-01-01T00:00:00+00:00"):
        await _roll_back(_start(tmp_path, "0.42.0"), tmp_path, works=False)

    run = _start(tmp_path, "0.42.0").run_record.run
    assert (run.source, run.end, run.error) == ("previous", "failed", "Rollback failed")


# ── Confirming the start asks the settled end ──


def test_confirming_the_start_asks_the_end_the_start_settled(tmp_path, caplog):
    """The pending-update marker's versions are not compared a second time:
    a start that settled the update failed is not confirmed, whatever the
    marker names."""
    import logging

    from openavc.updater.rollback import confirm_startup, write_pending_marker

    write_pending_marker(tmp_path, "0.41.0", "0.42.0")
    with patch("openavc.version.__version__", "0.42.0"), \
         caplog.at_level(logging.INFO, logger="openavc.updater.rollback"):
        confirm_startup(tmp_path, SimpleNamespace(last_update_applied=False))
    assert "did not take effect" in caplog.text
    assert "confirmed successful" not in caplog.text


def test_cancelled_run_is_failed_not_cut_off(tmp_path):
    """A run the process cancelled ended failed at once; the next start does
    not read it again as cut off."""
    async def go():
        mgr = _start(tmp_path, "0.41.0")

        async def cancelled(url, filename):
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _push(mgr, tmp_path, cancelled)

    asyncio.run(go())
    _start(tmp_path, "0.41.0")
    assert _row(tmp_path)[2:] == ("failed", "Update cancelled")
