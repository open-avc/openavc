"""A system running an older version than its last update installed says so.

An older installer run by hand, an old update task or a restored disk image
changes the program without writing update history, and nothing used to notice:
the Updates page showed the older version as if it were what the updater left.
At startup the manager compares the running version with the version the newest
history row installed, and reports the newer one in its status until an update
or rollback records a newer row. A newer version installed by hand is the
ordinary offline upgrade, and history that cannot say what ran says nothing.
"""

import json
import logging
from unittest.mock import patch

from openavc.updater.manager import UpdateManager


def _start(data_dir, running: str, rows: list[dict]) -> UpdateManager:
    (data_dir / "update-history.json").write_text(json.dumps(rows), encoding="utf-8")
    with patch("openavc.updater.manager.__version__", running):
        return UpdateManager(state_store=None, data_dir=data_dir)


def _older_than(data_dir, running: str, rows: list[dict]) -> str:
    return _start(data_dir, running, rows).get_status()["last_update_version"]


def _row(from_version: str, to_version: str, status: str = "success", **extra) -> dict:
    return {"from_version": from_version, "to_version": to_version, "status": status,
            "error": "", "rollback": False, **extra}


def test_an_older_installer_run_by_hand_is_reported(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.updater.history"):
        older = _older_than(tmp_path, "0.24.1", [_row("0.39.0", "0.40.0-rc1")])

    assert older == "0.40.0-rc1"
    assert "Running v0.24.1, older than v0.40.0-rc1" in caplog.text


def test_a_newer_version_installed_by_hand_says_nothing(tmp_path):
    assert _older_than(tmp_path, "0.40.0", [_row("0.38.0", "0.39.0")]) == ""


def test_the_version_the_update_installed_says_nothing(tmp_path):
    assert _older_than(tmp_path, "0.40.0", [_row("0.39.0", "0.40.0")]) == ""


def test_no_history_says_nothing(tmp_path):
    assert UpdateManager(state_store=None, data_dir=tmp_path).get_status()[
        "last_update_version"] == ""


def test_an_update_confirmed_at_this_start_records_what_ran(tmp_path):
    """Release tag 0.15.0, bundled version 0.14.0: the update applied, and the
    row now names the version that ran, so the tag never reads as newer."""
    mgr = _start(tmp_path, "0.14.0", [_row("0.13.0", "0.15.0", status="pending")])

    assert mgr.get_status()["last_update_version"] == ""
    saved = json.loads((tmp_path / "update-history.json").read_text())
    assert saved[0]["status"] == "success"
    assert saved[0]["running_version"] == "0.14.0"


def test_a_confirmed_row_names_what_ran_at_every_later_start(tmp_path):
    rows = [_row("0.13.0", "0.15.0", running_version="0.14.0")]
    assert _older_than(tmp_path, "0.14.0", rows) == ""
    assert _older_than(tmp_path, "0.12.0", rows) == "0.14.0"


def test_an_earlier_row_with_a_tag_note_gives_no_opinion(tmp_path):
    """Confirmed before the running version was recorded: its to_version is the
    tag, not what ran."""
    rows = [_row("0.13.0", "0.15.0", note="Applied; running v0.14.0 (release tag v0.15.0)")]
    assert _older_than(tmp_path, "0.14.0", rows) == ""


def test_a_reverted_update_expects_the_version_it_started_from(tmp_path):
    rows = [_row("0.31.0", "0.32.0", status="rolled_back")]
    assert _older_than(tmp_path, "0.31.0", rows) == ""
    assert _older_than(tmp_path, "0.30.0", rows) == "0.31.0"


def test_an_update_undone_since_the_last_start_says_nothing(tmp_path):
    """The automatic rollback restores the old version without writing history;
    this start corrects the row, and the old version is what is expected."""
    assert _older_than(tmp_path, "0.31.0", [_row("0.31.0", "0.32.0")]) == ""


def test_an_automatic_rollback_of_an_update_that_never_started_says_nothing(tmp_path):
    rows = [_row("0.31.0", "0.32.0", status="pending"), _row("0.30.0", "0.31.0")]
    assert _older_than(tmp_path, "0.31.0", rows) == ""


def test_a_rollback_from_the_updates_page_installed_its_target(tmp_path):
    rows = [_row("0.40.0", "0.39.0", rollback=True), _row("0.39.0", "0.40.0")]
    assert _older_than(tmp_path, "0.39.0", rows) == ""


def test_a_rollback_whose_target_was_not_known_gives_no_opinion(tmp_path):
    rows = [_row("0.40.0", "", rollback=True), _row("0.39.0", "0.40.0")]
    assert _older_than(tmp_path, "0.24.1", rows) == ""


def test_a_failed_update_moved_nothing(tmp_path):
    rows = [_row("0.40.0", "0.41.0", status="failed"), _row("0.39.0", "0.40.0")]
    assert _older_than(tmp_path, "0.24.1", rows) == "0.40.0"


def test_the_next_update_clears_it(tmp_path):
    rows = [_row("0.24.1", "0.41.0", running_version="0.41.0"), _row("0.39.0", "0.40.0-rc1")]
    assert _older_than(tmp_path, "0.41.0", rows) == ""
