"""The macOS menu bar restarts after an in-app update or rollback swaps the app.

The menu bar is a LaunchAgent in the signed-in user's session. The launchd run
wrapper swaps /Applications/OpenAVC.app and starts the server, and the menu bar
went on running the code it started with, from deleted files, until the next
login. After a swap that applied, the wrapper now restarts a running menu bar
with ``launchctl kickstart -k``; one the user quit stays quit, and nothing about
it can stop or hold up the server.

These run the real openavc-macos-run.sh with a stand-in launchctl that records
what it was asked. A sandbox run that names no launchctl restarts nothing, so
the suite never touches the menu bar of the Mac it runs on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests import gates
from tests.test_update_system import (
    _BASH_MISSING,
    _BASH_PATH,
    _build_bundle_tarball,
    _build_fake_bundle,
    _fixup_instruction_paths,
    _read_bundle_version,
    _to_msys2_path,
)

MAC_WRAPPER = Path(__file__).resolve().parent.parent / "installer" / "openavc-macos-run.sh"
MENUBAR = "gui/501/com.openavc.menubar"

# What `launchctl print` shows for a loaded agent: the job's own state one tab
# in, and its endpoints' states deeper, which never say "running".
FAKE_LAUNCHCTL = r"""#!/bin/bash
echo "$*" >> "$FAKE_LAUNCHCTL_LOG"
if [ "$1" = print ]; then
    [ -n "${FAKE_PRINT_SLEEP:-}" ] && sleep "$FAKE_PRINT_SLEEP"
    [ -n "${FAKE_MENUBAR_STATE:-}" ] || exit 113
    printf '%s = {\n\tactive count = 1\n\tstate = %s\n\n\tendpoints = {\n\t\t"x" = {\n\t\t\tstate = active\n\t\t}\n\t}\n}\n' "$2" "$FAKE_MENUBAR_STATE"
    exit 0
fi
exit "${FAKE_KICKSTART_EXIT:-0}"
"""


def _bash_path(path: Path) -> str:
    return _to_msys2_path(str(path)) if sys.platform == "win32" else str(path)


@pytest.fixture
def mac(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    app = tmp_path / "OpenAVC.app"
    _build_fake_bundle(app, "1.0.0")
    launchctl = tmp_path / "launchctl"
    launchctl.write_text(FAKE_LAUNCHCTL, encoding="utf-8")
    launchctl.chmod(0o755)
    log = tmp_path / "launchctl.log"

    class Mac:
        def __init__(self):
            self.app, self.data, self.log = app, data, log

        def stage_update(self):
            tarball = _build_bundle_tarball(tmp_path / "staging", "2.0.0")
            (data / "apply-update.json").write_text(json.dumps({
                "artifact": str(tarball), "from_version": "1.0.0", "to_version": "2.0.0",
            }))

        def stage_rollback(self):
            _build_fake_bundle(Path(f"{app}.previous"), "0.9.0")
            (data / "apply-rollback").write_text("")

        def run(self, *, state="running", uid="501", own_launchctl=True, **extra):
            _fixup_instruction_paths(data)
            environment = {
                key: value for key, value in os.environ.items()
                if not key.startswith(("OPENAVC_", "FAKE_"))
            }
            environment.update({
                "PYTHON": sys.executable,
                "OPENAVC_APP": _bash_path(app),
                "OPENAVC_DATA_DIR": _bash_path(data),
                "FAKE_LAUNCHCTL_LOG": _bash_path(log),
                "FAKE_MENUBAR_STATE": state,
                **extra,
            })
            if uid is not None:
                environment["OPENAVC_CONSOLE_UID"] = uid
            if own_launchctl:
                environment["OPENAVC_LAUNCHCTL"] = _bash_path(launchctl)
            else:
                # A launchctl first on PATH is still not used by a sandbox run.
                environment["PATH"] = os.pathsep.join([str(tmp_path), environment.get("PATH", "")])
            # Files rather than pipes: a restart still running when the server
            # starts keeps its copy of the output open, and a pipe would make
            # this wait for it.
            out, err = tmp_path / "stdout.txt", tmp_path / "stderr.txt"
            with out.open("w") as stdout, err.open("w") as stderr:
                done = subprocess.run(
                    [_BASH_PATH, str(MAC_WRAPPER)],
                    stdout=stdout, stderr=stderr, timeout=60, env=environment,
                )
            assert done.returncode == 0, err.read_text()
            text = out.read_text()
            assert "openavc-server stub" in text  # the server always starts
            return text

        def asked(self) -> list[str]:
            if not log.exists():
                return []
            return log.read_text(encoding="utf-8").splitlines()

    return Mac()


pytestmark = gates.skipif_missing(gates.BASH, _BASH_MISSING)


def test_an_applied_update_restarts_a_running_menu_bar(mac):
    mac.stage_update()

    out = mac.run()

    assert _read_bundle_version(mac.app) == "2.0.0"
    assert mac.asked() == [f"print {MENUBAR}", f"kickstart -k {MENUBAR}"]
    assert "restarted the menu bar" in out


def test_a_rollback_restarts_it_too(mac):
    mac.stage_rollback()

    mac.run()

    assert _read_bundle_version(mac.app) == "0.9.0"
    assert mac.asked() == [f"print {MENUBAR}", f"kickstart -k {MENUBAR}"]


def test_a_menu_bar_someone_quit_stays_quit(mac):
    mac.stage_update()

    mac.run(state="not running")

    assert mac.asked() == [f"print {MENUBAR}"]


def test_a_menu_bar_that_is_not_loaded_is_left_alone(mac):
    mac.stage_update()

    mac.run(state="")

    assert mac.asked() == [f"print {MENUBAR}"]


@pytest.mark.parametrize("uid", ["0", "", "nobody"])
def test_with_nobody_signed_in_at_the_screen_nothing_is_asked(mac, uid):
    mac.stage_update()

    mac.run(uid=uid)

    assert _read_bundle_version(mac.app) == "2.0.0"
    assert mac.asked() == []


def test_a_start_with_nothing_to_apply_restarts_nothing(mac):
    mac.run()

    assert mac.asked() == []


def test_an_update_that_did_not_apply_restarts_nothing(mac):
    (mac.data / "apply-update.json").write_text(json.dumps({
        "artifact": str(mac.data / "missing.tar.gz"), "to_version": "2.0.0",
    }))

    mac.run()

    assert _read_bundle_version(mac.app) == "1.0.0"
    assert mac.asked() == []


def test_a_restart_that_fails_still_starts_the_server(mac):
    mac.stage_update()

    out = mac.run(FAKE_KICKSTART_EXIT="1")

    assert "could not restart the menu bar" in out


def test_a_sandbox_run_that_names_no_launchctl_restarts_nothing(mac):
    mac.stage_update()

    mac.run(own_launchctl=False)

    assert _read_bundle_version(mac.app) == "2.0.0"
    assert mac.asked() == []


def test_a_launchctl_that_hangs_holds_the_server_back_five_seconds_at_most(mac):
    mac.stage_update()

    started = time.monotonic()
    mac.run(FAKE_PRINT_SLEEP="12")

    assert time.monotonic() - started < 10


def test_the_installed_app_restarts_its_menu_bar_with_the_system_launchctl():
    """The installed bundle's run is the one a test cannot make (it needs root
    and the real menu bar); its launchctl is the system's."""
    script = MAC_WRAPPER.read_text(encoding="utf-8")
    assert 'if [ "$APP" = "/Applications/OpenAVC.app" ]; then\n    LAUNCHCTL="${OPENAVC_LAUNCHCTL:-/bin/launchctl}"' in script
    assert 'LAUNCHCTL="${OPENAVC_LAUNCHCTL:-}"' in script
