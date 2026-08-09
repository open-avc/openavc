"""A rollback someone asked for must not be logged as a crash.

Both callers land in `perform_rollback`: the startup path that fires after a
failed update, and the REST path a person clicks. The log line asserted the new
version "failed after update" for both, so a deliberate downgrade was recorded
as a fault -- misleading exactly when someone opens the log to find out what
happened.
"""

from __future__ import annotations

import logging

import pytest

from openavc.updater import rollback as rb


@pytest.fixture
def staged(tmp_path):
    """A data dir with two cached installers, so the Windows path can pick one."""
    cache = tmp_path / "update-cache"
    cache.mkdir()
    for v in ("0.24.1", "0.25.0"):
        (cache / f"OpenAVC-Setup-{v}.exe").write_bytes(b"stub")
    return tmp_path


@pytest.fixture
def no_launch(monkeypatch):
    """Never actually schedule an installer or write a real marker."""
    monkeypatch.setattr(rb, "_launch_installer_via_scheduler", lambda *a, **k: True)


def _messages(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("automatic", [True, False])
def test_the_log_names_which_kind_of_rollback_this_is(
    staged, no_launch, caplog, automatic
) -> None:
    with caplog.at_level(logging.WARNING, logger="openavc.updater.rollback"):
        assert rb.perform_rollback(
            staged, from_version="0.24.1", to_version="0.25.0", automatic=automatic
        )
    text = _messages(caplog)
    assert ("automatic" if automatic else "manual") in text
    assert ("manual" if automatic else "automatic") not in text


def test_a_manual_rollback_is_not_described_as_a_failure(
    staged, no_launch, caplog
) -> None:
    """The whole bug: 'failed' is a claim about the software, not the operator."""
    with caplog.at_level(logging.WARNING, logger="openavc.updater.rollback"):
        rb.perform_rollback(
            staged, from_version="0.24.1", to_version="0.25.0", automatic=False
        )
    text = _messages(caplog).lower()
    assert "failed" not in text, (
        "a rollback the operator requested was logged as a failure of the "
        "version being left"
    )
    assert "0.24.1" in text and "0.25.0" in text, "both versions still named"


def test_an_automatic_rollback_still_says_it_failed_to_start(
    staged, no_launch, caplog
) -> None:
    """The counterweight -- that case really is a failure and must read as one."""
    with caplog.at_level(logging.WARNING, logger="openavc.updater.rollback"):
        rb.perform_rollback(
            staged, from_version="0.24.1", to_version="0.25.0", automatic=True
        )
    assert "failed to start" in _messages(caplog).lower()


def test_default_is_the_conservative_reading() -> None:
    """Nothing calls it positionally today, but a future caller that forgets the
    flag should not manufacture a crash report."""
    assert "requested" in rb._rollback_reason(False, "0.24.1", "0.25.0")
    assert "failed" not in rb._rollback_reason(False, "0.24.1", "0.25.0")
