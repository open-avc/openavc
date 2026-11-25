"""Whether a product's firmware owns updates is decided in three places, which must agree.

The server asks ``openavc.updater.host.get_firmware_host()``. Before the server
starts, the Linux update helper (``installer/update-helper.sh``, which also
runs on the Pi and appliance hardware) and the macOS launch wrapper
(``installer/openavc-macos-run.sh``) decide the same thing in bash, before they
read any update instruction the server left on disk. They cannot ask the
server: they run before it, and as root, so they read the host's file
themselves. If they disagreed, the Updates page could say the product's
firmware owns updates while the helper still applied an update or rollback left
on disk at the next start, or the other way round.

Each case below sets the ``OPENAVC_FIRMWARE_HOST`` variable (or leaves it
unset) and puts something, or nothing, at the default path, then asks all
three. The server has three answers (no firmware host, a firmware host, a
firmware host whose file could not be read) and the scripts two (act on the
instructions, or leave every one for the firmware). A host whose file could not
be read keeps updates off too, so the comparison is ``get_firmware_host() is
not None`` against whether the script left a rollback instruction where it
was. The helper also takes the file as its third argument, for a supervisor
that does not pass the server's environment: given that way it must answer as
the variable does.

The scripts run as shipped, with the one default path they name replaced by a
path under ``tmp_path`` (the first test checks that each names the server's
``DEFAULT_HOST_FILE`` exactly once), so the branch that looks at that file is
reached without touching ``/etc``.

Not in the corpus: a default path whose folder is not a folder or cannot be
searched. The server counts those as a file it could not read and the scripts
as no file, but the default path's folder is ``/etc``, which every account can
search, and the scripts run as root.

The scripts run on Linux and macOS only (on Windows the server's own rule is
that no implicit file is looked for), so this module needs a POSIX bash.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from openavc.updater import host
from openavc.updater.host import DEFAULT_HOST_FILE
from tests import gates

INSTALLER = Path(__file__).resolve().parents[1] / "installer"
SCRIPTS = ("update-helper.sh", "openavc-macos-run.sh")

_BASH = shutil.which("bash") if os.name != "nt" else None
pytestmark = gates.skipif_missing(
    gates.BASH,
    None if _BASH else "no POSIX bash: the update helper and the macOS wrapper run on Linux and macOS",
)

VALID = {"schema": 1, "update_owner": "firmware", "firmware_version": "fixture-1.0"}

# What the variable is set to: None leaves it unset; "<host>" names a valid
# host file away from the default path; "<missing>" an absolute path to
# nothing; anything else is used as written.
HOST_ELSEWHERE = "<host>"
MISSING = "<missing>"


@dataclass(frozen=True)
class Case:
    name: str
    variable: str | None
    default: str  # what is at the default path; see _lay_default
    firmware: bool


CASES = [
    Case("nothing set and nothing at the default path", None, "absent", False),
    Case("a host file at the default path", None, "valid", True),
    Case("a file at the default path that is not a host file", None, "malformed", True),
    Case("an empty file at the default path", None, "empty", True),
    Case("a folder at the default path", None, "folder", True),
    Case("a broken link at the default path", None, "broken link", True),
    Case("a link at the default path to a host file", None, "link to host", True),
    Case("a file at the default path this account cannot read", None, "unreadable", True),
    Case("the variable names a host file", HOST_ELSEWHERE, "absent", True),
    Case("the variable names a file that is not there", MISSING, "absent", True),
    Case("the variable names a missing file over a default one", MISSING, "valid", True),
    Case("the variable is empty", "", "absent", True),
    Case("the variable is a relative path", "relative-host.json", "absent", True),
]


def _lay_default(path: Path, state: str, elsewhere: Path) -> None:
    if state == "absent":
        return
    if state == "valid":
        path.write_text(json.dumps(VALID), encoding="utf-8")
    elif state == "malformed":
        path.write_text("{", encoding="utf-8")
    elif state == "empty":
        path.write_bytes(b"")
    elif state == "folder":
        path.mkdir()
    elif state == "broken link":
        path.symlink_to(path.parent / "nothing-here.json")
    elif state == "link to host":
        path.symlink_to(elsewhere)
    elif state == "unreadable":
        path.write_text(json.dumps(VALID), encoding="utf-8")
        path.chmod(0)
    else:  # pragma: no cover - a typo in the table above
        raise AssertionError(f"no such default-path state: {state}")


class World:
    """One case's host: the default path, the variable's value, and copies of
    the two scripts that look at that default path instead of ``/etc``."""

    def __init__(self, tmp_path: Path, case: Case) -> None:
        self.root = tmp_path
        hosts = tmp_path / "host"
        hosts.mkdir()
        elsewhere = hosts / "provisioned.json"
        elsewhere.write_text(json.dumps(VALID), encoding="utf-8")
        self.default = hosts / "openavc-firmware-host.json"
        _lay_default(self.default, case.default, elsewhere)
        self.variable = {
            None: None, HOST_ELSEWHERE: str(elsewhere), MISSING: str(hosts / "absent.json"),
        }.get(case.variable, case.variable)

        self.installer = tmp_path / "installer"
        self.installer.mkdir()
        shutil.copy2(INSTALLER / "apply-lib.sh", self.installer / "apply-lib.sh")
        for name in SCRIPTS:
            text = (INSTALLER / name).read_text(encoding="utf-8")
            assert text.count(DEFAULT_HOST_FILE) == 1, (name, DEFAULT_HOST_FILE)
            copy = self.installer / name
            copy.write_text(text.replace(DEFAULT_HOST_FILE, str(self.default)), encoding="utf-8")
            copy.chmod(0o755)

        self.data = tmp_path / "data"
        self.data.mkdir()
        self.linux_app = tmp_path / "opt-openavc"
        self.linux_app.mkdir()
        self.boot = tmp_path / "boot"
        self.boot.mkdir()
        self.mac_app = tmp_path / "OpenAVC.app"
        server = self.mac_app / "Contents" / "Resources" / "server" / "openavc-server"
        server.parent.mkdir(parents=True)
        server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        server.chmod(0o755)

    def server_says_firmware(self, monkeypatch) -> bool:
        monkeypatch.setattr(host, "DEFAULT_HOST_FILE", str(self.default))
        if self.variable is None:
            monkeypatch.delenv("OPENAVC_FIRMWARE_HOST", raising=False)
        else:
            monkeypatch.setenv("OPENAVC_FIRMWARE_HOST", self.variable)
        host.get_firmware_host.cache_clear()
        try:
            return host.get_firmware_host() is not None
        finally:
            host.get_firmware_host.cache_clear()

    def _environment(self, *, with_variable: bool) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAVC_")}
        env["OPENAVC_BOOT_DIRS"] = str(self.boot)
        if with_variable and self.variable is not None:
            env["OPENAVC_FIRMWARE_HOST"] = self.variable
        return env

    def _left_the_rollback(self, command: list[str], env: dict[str, str]) -> bool:
        """Run a script with a rollback asked for and nothing to roll back to:
        a script that acts on it removes the instruction, one that leaves it to
        the firmware does not."""
        marker = self.data / "apply-rollback"
        marker.write_text("{}", encoding="utf-8")
        done = subprocess.run(
            [_BASH, *command], env=env, cwd=self.root,
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        left = marker.exists()
        marker.unlink(missing_ok=True)
        return left

    def helper_says_firmware(self, *, as_argument: bool = False) -> bool:
        command = [str(self.installer / "update-helper.sh"), str(self.data), str(self.linux_app)]
        if as_argument:
            command.append(self.variable)
        return self._left_the_rollback(command, self._environment(with_variable=not as_argument))

    def wrapper_says_firmware(self) -> bool:
        env = self._environment(with_variable=True)
        env.update({"OPENAVC_APP": str(self.mac_app), "OPENAVC_DATA_DIR": str(self.data)})
        return self._left_the_rollback([str(self.installer / "openavc-macos-run.sh")], env)


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_script_reads_the_servers_default_path(name):
    text = (INSTALLER / name).read_text(encoding="utf-8")
    assert text.count(DEFAULT_HOST_FILE) == 1, (
        f"{name} must name {DEFAULT_HOST_FILE} once, the path openavc/updater/host.py "
        "reads when the variable names no other file"
    )


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_the_server_and_both_scripts_agree(tmp_path, monkeypatch, case):
    world = World(tmp_path, case)
    answers = {
        "server (updater/host.py)": world.server_says_firmware(monkeypatch),
        "update-helper.sh": world.helper_says_firmware(),
        "openavc-macos-run.sh": world.wrapper_says_firmware(),
    }
    if case.variable is not None:
        answers["update-helper.sh, the file as its argument"] = world.helper_says_firmware(
            as_argument=True,
        )
    expected = {who: case.firmware for who in answers}
    assert answers == expected, f"firmware owns updates? ({case.name})"
