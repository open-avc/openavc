"""The Raspberry Pi image's screen launcher (panel-kiosk.sh): it opens the
page once the server answers at all, and the server's own startup screen
shows a start in progress, or why it failed, from there.

The script runs here with stand-ins for curl, sleep and chromium, so what it
asks and what it opens are the script's own.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "installer" / "pi-image" / "stage-openavc" / "01-install-openavc" / "files" / "panel-kiosk.sh"
)

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None or shutil.which("python3") is None,
    reason="the launcher is a bash script for the Raspberry Pi image",
)


def _stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _launch(tmp_path, system: dict, curl_answers: bool) -> tuple[list[str], str]:
    """Run the launcher; what curl was asked and what chromium opened."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "system.json").write_text(json.dumps(system), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    asked = tmp_path / "asked"
    opened = tmp_path / "opened"
    _stand_in(bin_dir, "curl", f'echo "$@" >> "{asked}"\nexit {0 if curl_answers else 7}')
    _stand_in(bin_dir, "sleep", "exit 0")
    _stand_in(bin_dir, "chromium", f'for last; do :; done\necho "$last" > "{opened}"')
    env = dict(os.environ, OPENAVC_DATA_DIR=str(data), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    env.pop("DISPLAY", None)
    subprocess.run(["bash", str(SCRIPT)], env=env, check=True, capture_output=True, timeout=60)
    return asked.read_text(encoding="utf-8").splitlines(), opened.read_text(encoding="utf-8").strip()


def test_it_opens_the_page_once_the_server_answers_on_the_configured_port(tmp_path):
    asked, opened = _launch(tmp_path, {"network": {"http_port": 8090}}, curl_answers=True)

    assert asked == ["-sf -k http://localhost:8090/api/startup-status"]
    assert opened == "http://localhost:8090/setup"


def test_with_kiosk_mode_on_it_opens_the_panel_over_https_when_https_is_on(tmp_path):
    system = {"kiosk": {"enabled": True}, "tls": {"enabled": True, "port": 8444}}

    asked, opened = _launch(tmp_path, system, curl_answers=True)

    assert asked == ["-sf -k https://localhost:8444/api/startup-status"]
    assert opened == "https://localhost:8444/panel"


def test_the_fallback_page_asks_the_server_the_same_question():
    """The page shown when the server never answered goes on when it does:
    the same question as the wait before it, so the two agree."""
    script = SCRIPT.read_text(encoding="utf-8")
    fetch = next(line for line in script.splitlines() if "fetch(" in line)

    asked = re.findall(r"localhost:\$\{EFFECTIVE_PORT\}(/api/[a-z/-]+)", script)
    assert asked == ["/api/startup-status", "/api/startup-status"]
    assert "${PROTO}://localhost:${EFFECTIVE_PORT}/api/startup-status" in fetch
