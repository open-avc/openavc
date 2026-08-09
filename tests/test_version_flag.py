"""Asking the binary its version must not try to start the server.

There was no `--version` handling at all, so the flag fell through to `main()`,
which pre-flights the listening port before doing anything else. On a box where
OpenAVC is already running -- which is the normal case when you are checking
what version is installed -- that printed "HTTP port 8080 is already in use"
and wrote a startup-error marker, instead of answering.

Run it as a subprocess: the flag is handled at module scope, before the heavy
imports, and that placement is the fix. Importing the module in-process would
not exercise it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from openavc.utils.spawn import CREATE_NO_WINDOW
from openavc.version import __version__


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "openavc.main", *args],
        capture_output=True,
        text=True,
        timeout=120,
        creationflags=CREATE_NO_WINDOW,
    )


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_prints_the_version_and_exits_clean(flag: str) -> None:
    result = _run(flag)
    assert result.returncode == 0, (
        f"--version exited {result.returncode}; stderr={result.stderr[:400]}"
    )
    assert result.stdout.strip() == __version__


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_flag_never_reports_a_port_problem(flag: str) -> None:
    """The actual bug. The port is irrelevant to the question being asked."""
    result = _run(flag)
    combined = (result.stdout + result.stderr).lower()
    assert "already in use" not in combined
    assert "port" not in combined
