"""The note that tells a machine it has no browser coverage.

`tests/e2e` is excluded from every default run, so a machine without the
Playwright extra produces a run that looks complete and isn't. These pin the
one line that says so, and the two cases where it stays quiet.
"""

from tests import conftest


def test_quiet_when_the_extra_is_installed(monkeypatch):
    """A machine that can run them needs a command, not a warning."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(conftest, "_browser_extra_installed", lambda: True)
    assert conftest.e2e_availability_note() is None


def test_quiet_on_ci(monkeypatch):
    """CI runs the browser suite in its own job, so the note would mislead."""
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(conftest, "_browser_extra_installed", lambda: False)
    assert conftest.e2e_availability_note() is None


def test_names_both_commands_when_unavailable(monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(conftest, "_browser_extra_installed", lambda: False)
    note = conftest.e2e_availability_note()
    assert note is not None
    assert "tests/e2e" in note
    # Installing the extra is only half of it -- the browser is a second step.
    assert 'pip install -e ".[dev]"' in note
    assert "playwright install chromium" in note
    # And the run needs addopts cleared, or it collects nothing.
    assert 'pytest tests/e2e -o addopts=""' in note


def test_the_probe_matches_the_extra_this_repo_declares():
    """The note is only as good as the package name it looks for."""
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert "pytest-playwright" in pyproject.read_text(encoding="utf-8")
