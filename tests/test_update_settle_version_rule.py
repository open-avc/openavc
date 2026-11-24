"""Whether an update applied is read by the version rule.

At the next start the updater decides how an update ended: it applied when
the system runs the release it was sent to, or no longer runs the one it left.
Two places make that call (the history at load, the pending marker at
confirm) and a third corrects a row the automatic rollback undid. Each one
compares versions, and ``0.41.0-rc1`` and ``0.41.0rc1`` are one version
(``openavc/utils/version_rule.py``); compared as text, a failed update from
one spelling to a system reporting the other reads as applied.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import settled_applied
from openavc.updater.checker import same_version, update_applied
from openavc.updater.manager import UpdateManager
from openavc.updater.rollback import confirm_startup, read_pending_marker, write_pending_marker

CORPUS = json.loads(
    (Path(__file__).parent / "fixtures" / "version_rule_cases.json").read_text(encoding="utf-8")
)
CLASSES = [cls["versions"] for cls in CORPUS["ascending"]]
#: Each spelling of one release, against every other spelling of it.
SAME = [(a, b) for versions in CLASSES for a in versions for b in versions]
#: The first spelling of each release against the first of the next one.
STEPS = [(older[0], newer[0]) for older, newer in zip(CLASSES, CLASSES[1:])]
UNREADABLE = [text for text in CORPUS["unreadable"] if isinstance(text, str)]


# ── the rule the three ask ──────────────────────────────────────────────


@pytest.mark.parametrize("a,b", SAME, ids=[f"{a!r}={b!r}" for a, b in SAME])
def test_every_spelling_of_a_release_is_the_same_version(a, b):
    assert same_version(a, b)


@pytest.mark.parametrize("older,newer", STEPS, ids=[f"{a!r}<{b!r}" for a, b in STEPS])
def test_two_releases_are_not_the_same_version(older, newer):
    assert not same_version(older, newer)
    assert not same_version(newer, older)


@pytest.mark.parametrize("text", UNREADABLE[:40], ids=[repr(t) for t in UNREADABLE[:40]])
def test_a_value_that_is_no_version_is_the_same_only_as_itself(text):
    assert same_version(text, text)
    assert not same_version(text, "0.40.0")
    assert not same_version("0.40.0", text)


@pytest.mark.parametrize("a,b", SAME, ids=[f"{a!r}={b!r}" for a, b in SAME])
def test_an_update_that_left_the_system_where_it_was_did_not_apply(a, b):
    """Running ``a``, having left ``b``, on its way to some other release."""
    assert not update_applied(a, b, "99.0.0")


@pytest.mark.parametrize("older,newer", STEPS, ids=[f"{a!r}->{b!r}" for a, b in STEPS])
def test_an_update_that_moved_the_system_applied(older, newer):
    assert update_applied(newer, older, newer)
    # The release tag and the version it bundles may differ: moving off the
    # version it left is enough.
    assert update_applied(newer, older, "99.0.0")


def test_reaching_the_target_in_another_spelling_applied():
    assert update_applied("0.41.0rc1", "", "0.41.0-rc1")


# ── the history at load ─────────────────────────────────────────────────


def _manager_with(tmp_path: Path, row: dict, running: str) -> UpdateManager:
    (tmp_path / "update-history.json").write_text(json.dumps([row]), encoding="utf-8")
    with patch("openavc.updater.manager.__version__", running):
        return UpdateManager(state_store=None, data_dir=tmp_path)


def test_a_pending_update_that_did_not_move_the_system_failed(tmp_path):
    """It left 0.41.0-rc1 and the system reports 0.41.0rc1: still there."""
    mgr = _manager_with(
        tmp_path,
        {"from_version": "0.41.0-rc1", "to_version": "0.41.0", "status": "pending"},
        running="0.41.0rc1",
    )
    assert mgr._history[0]["status"] == "failed"


def test_a_pending_update_that_reached_its_target_in_another_spelling_carries_no_note(tmp_path):
    mgr = _manager_with(
        tmp_path,
        {"from_version": "0.40.0", "to_version": "0.41.0-rc1", "status": "pending"},
        running="0.41.0rc1",
    )
    row = mgr._history[0]
    assert row["status"] == "success"
    assert row["running_version"] == "0.41.0rc1"
    assert "note" not in row


def test_an_update_back_on_the_version_it_left_reads_reverted(tmp_path):
    """The automatic rollback put it back on 0.41.0-rc1, reported as 0.41.0rc1."""
    mgr = _manager_with(
        tmp_path,
        {"from_version": "0.41.0-rc1", "to_version": "0.41.0", "status": "success"},
        running="0.41.0rc1",
    )
    assert mgr._history[0]["status"] == "rolled_back"


def test_a_row_from_a_release_to_itself_spelled_another_way_is_left_alone(tmp_path):
    mgr = _manager_with(
        tmp_path,
        {"from_version": "0.41.0-rc1", "to_version": "0.41.0rc1", "status": "success"},
        running="0.41.0-rc1",
    )
    assert mgr._history[0]["status"] == "success"


# ── the pending marker at confirm ───────────────────────────────────────


def test_a_marker_whose_update_did_not_move_the_system_is_not_confirmed(tmp_path, caplog):
    write_pending_marker(tmp_path, "0.41.0-rc1", "0.41.0")
    with patch("openavc.version.__version__", "0.41.0rc1"):
        with caplog.at_level(logging.INFO, logger="openavc.updater.rollback"):
            confirm_startup(tmp_path, settled_applied(tmp_path, "0.41.0rc1"))
    assert read_pending_marker(tmp_path) is None
    assert "did not take effect" in caplog.text
    assert "confirmed successful" not in caplog.text


def test_a_marker_whose_target_runs_in_another_spelling_is_confirmed(tmp_path, caplog):
    write_pending_marker(tmp_path, "", "0.41.0-rc1")
    with patch("openavc.version.__version__", "0.41.0rc1"):
        with caplog.at_level(logging.INFO, logger="openavc.updater.rollback"):
            confirm_startup(tmp_path, settled_applied(tmp_path, "0.41.0rc1"))
    assert "confirmed successful" in caplog.text
