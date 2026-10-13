"""The macOS uninstaller leaves nothing of the application behind.

Not run here: it removes /Applications/OpenAVC.app and stops launchd jobs as
root. These read the script against the run wrapper instead, which is what
puts the rollback copy beside the application.
"""

from __future__ import annotations

import re
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "installer"
UNINSTALL = INSTALLER / "macos-uninstall.sh"
WRAPPER = INSTALLER / "openavc-macos-run.sh"


def _removed(script: str) -> set[str]:
    return set(re.findall(r'^rm -rf "([^"]+)"', script, re.M))


def test_it_removes_the_copy_an_update_keeps_for_rolling_back():
    """Left behind, a later fresh install offers a rollback to whatever
    version that copy was (one Mac had 0.24.1 there)."""
    wrapper = WRAPPER.read_text(encoding="utf-8")
    snapshot = re.search(r'^\s*PREVIOUS="([^"]+)"', wrapper, re.M)
    installed = re.search(r'^APP="\$\{OPENAVC_APP:-([^}]+)\}"$', wrapper, re.M)
    assert snapshot and installed, "the run wrapper changed how it names these -- this test is stale"
    assert snapshot.group(1) == "$APP.previous"

    script = UNINSTALL.read_text(encoding="utf-8")
    app = re.search(r'^APP="([^"]+)"$', script, re.M)
    assert app and app.group(1) == installed.group(1)
    assert {"$APP", "$APP.previous"} <= _removed(script)
