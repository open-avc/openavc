"""The root-privileged helper script, run for real.

`installer/openavc-privileged-helper.sh` is the only part of OpenAVC that runs
as root on an appliance, and its `set_password` branch just changed: it takes
the password from the request file, because the stored one is a digest now. The
server half is covered next door in `test_host_control.py`; nothing covered this
half, and a comment in that file claimed otherwise.

Run against a temp spool with `chpasswd` and `passwd` stubbed onto PATH, so what
is exercised is the script's own parsing and drain behaviour — the two things a
mistake here would break silently, in the one place a mistake is a root action.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "installer" / "openavc-privileged-helper.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="the helper is a bash script that only ever runs on Linux",
)


@pytest.fixture
def appliance(tmp_path):
    """A temp data dir, a stub PATH, and a runner for the helper."""
    data_dir = tmp_path / "data"
    (data_dir / "priv-requests").mkdir(parents=True)
    (data_dir / "priv-results").mkdir(parents=True)
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir()
    calls = tmp_path / "calls"
    calls.mkdir()

    for name in ("chpasswd", "passwd"):
        stub = stub_bin / name
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s" "$*" > "{calls}/{name}.args"\n'
            f'cat > "{calls}/{name}.stdin"\n'
            "exit 0\n"
        )
        stub.chmod(0o755)

    class Appliance:
        def __init__(self):
            self.data_dir = data_dir
            self.requests = data_dir / "priv-requests"
            self.results = data_dir / "priv-results"
            self.stub_bin = stub_bin

        def submit(self, req_id: str, body: dict) -> Path:
            path = self.requests / f"{req_id}.json"
            path.write_text(json.dumps(body))
            return path

        def run(self) -> subprocess.CompletedProcess:
            env = dict(os.environ)
            env["PATH"] = f"{stub_bin}{os.pathsep}{env['PATH']}"
            env["PYTHON"] = sys.executable
            return subprocess.run(
                ["bash", str(HELPER), str(data_dir)],
                capture_output=True, text=True, timeout=30, env=env,
            )

        def stdin_to(self, name: str) -> str:
            path = calls / f"{name}.stdin"
            return path.read_text() if path.exists() else ""

        def args_to(self, name: str) -> str:
            path = calls / f"{name}.args"
            return path.read_text() if path.exists() else ""

        def called(self, name: str) -> bool:
            return (calls / f"{name}.args").exists()

    return Appliance()


def test_set_password_takes_the_password_from_the_request(appliance):
    appliance.submit("req01", {"action": "set_password", "password": "topsecretpw123"})
    result = appliance.run()
    assert result.returncode == 0, result.stderr
    assert appliance.stdin_to("chpasswd") == "openavc:topsecretpw123\n"


def test_the_request_is_deleted_after_use(appliance):
    """The plaintext exists on disk for the moment between the server's write
    and this. That is the whole security argument, so it is worth a test."""
    req = appliance.submit("req01", {"action": "set_password", "password": "topsecretpw123"})
    appliance.run()
    assert not req.exists()


def test_the_request_is_deleted_even_when_chpasswd_fails(appliance):
    stub = appliance.stub_bin / "chpasswd"
    stub.write_text("#!/bin/bash\nexit 1\n")
    stub.chmod(0o755)
    req = appliance.submit("req01", {"action": "set_password", "password": "topsecretpw123"})
    result = appliance.run()
    assert not req.exists()
    assert "chpasswd failed" in result.stdout


def test_a_password_with_shell_metacharacters_is_passed_through_intact(appliance):
    """It is read out of JSON and piped, never interpolated into a command."""
    nasty = "a$(whoami)`id`;rm -rf /\"'\\ b"
    appliance.submit("req01", {"action": "set_password", "password": nasty})
    appliance.run()
    assert appliance.stdin_to("chpasswd") == f"openavc:{nasty}\n"


def test_the_password_is_piped_rather_than_passed_as_an_argument(appliance):
    """`ps` on a busy appliance must not be able to show it. chpasswd is the
    only external command in this branch and it reads the password on stdin;
    the printf that builds that line is a bash builtin and forks nothing."""
    appliance.submit("req01", {"action": "set_password", "password": "topsecretpw123"})
    appliance.run()
    assert appliance.args_to("chpasswd") == ""
    assert appliance.stdin_to("chpasswd") == "openavc:topsecretpw123\n"


def test_no_password_locks_the_account(appliance):
    """An unclaimed or cleared instance must leave no usable OS login."""
    appliance.submit("req01", {"action": "set_password", "password": ""})
    result = appliance.run()
    assert appliance.called("passwd") is True
    assert not appliance.called("chpasswd")
    assert "locked openavc" in result.stdout


def test_a_request_with_no_password_field_locks_rather_than_guesses(appliance):
    """What an OLD server's request looks like. It must not fall back to
    reading system.json — that read is what the whole change removed."""
    (appliance.data_dir / "system.json").write_text(
        json.dumps({"auth": {"programmer_password": "left-over-cleartext"}})
    )
    appliance.submit("req01", {"action": "set_password"})
    appliance.run()
    assert not appliance.called("chpasswd")
    assert appliance.called("passwd") is True


def test_an_unknown_action_is_refused_and_drained(appliance):
    req = appliance.submit("req01", {"action": "rm -rf /"})
    result = appliance.run()
    assert "unknown action" in result.stdout
    assert not req.exists()
    assert not appliance.called("chpasswd")


def test_a_result_is_written_only_when_one_was_asked_for(appliance):
    appliance.submit("req01", {"action": "set_password", "password": "topsecretpw123"})
    appliance.submit("req02", {"action": "set_password", "password": "x", "want_result": True})
    appliance.run()
    assert not (appliance.results / "req01.json").exists()
    assert json.loads((appliance.results / "req02.json").read_text())["ok"] is True


# --- the file has to actually reach the appliance -----------------------------

# Each build path that must carry the helper, and the file that decides.
# `installer/firewall-sync.sh` is the cautionary case: it is copied by
# install.sh and by update-helper.sh, referenced by a comment as an
# ExecStartPre, and packed by no release archive at all — so both copies are
# dead on every shipped install and nobody noticed. A helper that ships in one
# build path and not another is worse here, because the two would disagree
# about whether an appliance can sync its OS password.
PACKERS = {
    "linux release archive (what an in-app update installs)":
        REPO_ROOT / ".github" / "workflows" / "release.yml",
    "pi image (CI, the published one)":
        REPO_ROOT / ".github" / "workflows" / "release-pi.yml",
    "pi image (local build script)":
        REPO_ROOT / "installer" / "pi-image" / "build.sh",
    "pi-gen stage that installs it into the rootfs":
        REPO_ROOT / "installer" / "pi-image" / "stage-openavc" / "01-install-openavc" / "00-run.sh",
}


@pytest.mark.parametrize("label", sorted(PACKERS))
def test_the_build_path_carries_the_privileged_helper(label: str) -> None:
    path = PACKERS[label]
    assert path.is_file(), f"{path} is missing — this test points at the wrong file"
    assert "openavc-privileged-helper.sh" in path.read_text(encoding="utf-8"), (
        f"{path.name} does not carry installer/openavc-privileged-helper.sh, so "
        f"{label} would ship without it"
    )


def test_the_stage_script_no_longer_writes_its_own_copy() -> None:
    """The pi-gen stage used to hold the whole script in a heredoc, which is
    what froze it at image-build time. One copy, in installer/."""
    chroot = (
        REPO_ROOT / "installer" / "pi-image" / "stage-openavc"
        / "02-configure" / "00-run-chroot.sh"
    )
    source = chroot.read_text(encoding="utf-8")
    assert "<< 'HELPER'" not in source
    assert "| chpasswd" not in source
    assert "set_password)" not in source
