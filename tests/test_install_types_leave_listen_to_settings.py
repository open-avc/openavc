"""Every install type leaves who can connect to Settings.

Each installer used to put OPENAVC_BIND=0.0.0.0 in the service's start-up
settings, which held Settings > Network > Accept connections from at every
network and locked it. The installed default is every network anyway, so the
variable now only appears where someone set it on purpose:

- Windows: install-service.bat and the update's merge
  (test_installer_keeps_admin_settings.py).
- Linux and the Pi: the systemd unit and install.sh's copy of it; an update
  replaces the installed unit at the next start (update-helper.sh sync_unit),
  and an override an administrator wrote stays. The Pi's seeded system.json
  no longer carries a bind address either.
- macOS: the LaunchDaemon plist. An in-app update never replaces the
  installed one, so the run wrapper removes the 0.0.0.0 earlier installers
  put there, and starts the server with OPENAVC_BIND as the installed file
  holds it now, because launchd keeps what it loaded until the job is loaded
  again.
- Docker: the image keeps 0.0.0.0 on purpose; the compose file's ports:
  decides who can connect, and Settings says so.

The wrapper runs below are the real script under bash against a real plist.
On a Mac they use the real PlistBuddy; elsewhere a stand-in that reads and
writes the plist the same way.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests import gates
from tests.test_update_system import _BASH_MISSING, _BASH_PATH, _to_msys2_path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "installer"
UNIT = INSTALLER / "openavc.service"
INSTALL_SH = INSTALLER / "install.sh"
PI_SYSTEM_JSON = (
    INSTALLER / "pi-image" / "stage-openavc" / "01-install-openavc" / "files"
    / "default-system.json"
)
MAC_PLIST = INSTALLER / "com.openavc.server.plist"
MAC_WRAPPER = INSTALLER / "openavc-macos-run.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _environment(unit_text: str) -> list[str]:
    return re.findall(r"^Environment=(\S+)$", unit_text, re.M)


def _inline_fallback_unit() -> str:
    """The unit install.sh writes when installer/openavc.service is missing."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = re.search(r"<< 'UNIT'\n(.*?)\nUNIT\n", source, re.S)
    assert body, "install.sh no longer writes an inline unit heredoc -- this parser is stale"
    return body.group(1)


# -- Linux and the Pi ---------------------------------------------------------


def test_the_systemd_unit_leaves_who_can_connect_to_settings():
    names = [entry.split("=", 1)[0] for entry in _environment(UNIT.read_text(encoding="utf-8"))]
    assert "OPENAVC_DATA_DIR" in names, "the parser no longer finds the unit's variables"
    assert "OPENAVC_BIND" not in names


def test_install_sh_writes_the_same_variables_as_the_shipped_unit():
    """The copy is used only when the shipped unit is missing, which is when
    nobody would notice it drifting."""
    assert _environment(_inline_fallback_unit()) == _environment(UNIT.read_text(encoding="utf-8"))


def test_the_pi_image_seeds_no_bind_address():
    """The installed default is every network; a seeded value is only one more
    place for it to be read from."""
    seeded = json.loads(PI_SYSTEM_JSON.read_text(encoding="utf-8"))
    assert "bind_address" not in seeded["network"]
    assert "accept_connections_from" not in seeded["network"]


# -- macOS ---------------------------------------------------------------------


def test_the_launch_settings_leave_who_can_connect_to_settings():
    with MAC_PLIST.open("rb") as handle:
        daemon = plistlib.load(handle)
    environment = daemon["EnvironmentVariables"]
    assert "OPENAVC_DATA_DIR" in environment
    assert "OPENAVC_BIND" not in environment


# -- Docker --------------------------------------------------------------------


def test_the_docker_image_keeps_every_address():
    """Inside a bridge-networked container only 0.0.0.0 is reachable through
    the published port; the host's compose file decides who can connect."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    assert "ENV OPENAVC_BIND=0.0.0.0" in lines


# -- the macOS run wrapper, run for real ---------------------------------------

FAKE_PLISTBUDDY = r'''
import plistlib, sys

sys.stdout.reconfigure(newline="\n")  # what PlistBuddy prints, on Windows too

def main(argv):
    if len(argv) != 3 or argv[0] != "-c":
        return 2
    command, path = argv[1], argv[2]
    verb, _, keypath = command.partition(" ")
    keys = keypath.lstrip(":").split(":")
    try:
        with open(path, "rb") as handle:
            root = plistlib.load(handle)
    except Exception:
        print(f'Error Reading File: {path}')
        return 1
    parent = root
    for key in keys[:-1]:
        parent = parent.get(key) if isinstance(parent, dict) else None
    if not isinstance(parent, dict) or keys[-1] not in parent:
        print(f'{verb}: Entry, ":{":".join(keys)}", Does Not Exist')
        return 1
    if verb == "Print":
        print(parent[keys[-1]])
        return 0
    if verb == "Delete":
        del parent[keys[-1]]
        with open(path, "wb") as handle:
            plistlib.dump(root, handle)
        return 0
    return 2

sys.exit(main(sys.argv[1:]))
'''

REAL_PLISTBUDDY = Path("/usr/libexec/PlistBuddy")

INSTALLED = {
    "Label": "com.openavc.server",
    "EnvironmentVariables": {
        "OPENAVC_DATA_DIR": "/Library/Application Support/OpenAVC",
        "OPENAVC_ALLOW_ANONYMOUS": "false",
        "OPENAVC_SERVICE_MANAGED": "1",
        "HTTPS_PROXY": "http://proxy.campus.example:3128",
    },
}


def _bash_path(path: Path) -> str:
    return _to_msys2_path(str(path)) if sys.platform == "win32" else str(path)


@pytest.fixture
def mac(tmp_path):
    """A sandboxed Mac: an app bundle whose server prints the OPENAVC_BIND it
    was started with, installed launch settings, and a PlistBuddy."""
    gates.skip_or_fail(gates.BASH, _BASH_MISSING)
    app = tmp_path / "OpenAVC.app"
    server = app / "Contents" / "Resources" / "server" / "openavc-server"
    server.parent.mkdir(parents=True)
    server.write_bytes(b'#!/bin/sh\necho "OPENAVC_BIND=${OPENAVC_BIND-<unset>}"\n')
    server.chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    if sys.platform == "darwin" and REAL_PLISTBUDDY.exists():
        plistbuddy = REAL_PLISTBUDDY
    else:
        fake = tmp_path / "fake_plistbuddy.py"
        fake.write_text(FAKE_PLISTBUDDY, encoding="utf-8")
        plistbuddy = tmp_path / "PlistBuddy"
        plistbuddy.write_bytes(
            f'#!/bin/sh\nexec "{_bash_path(Path(sys.executable))}" "{_bash_path(fake)}" "$@"\n'
            .encode("utf-8")
        )
        plistbuddy.chmod(0o755)
    plist = tmp_path / "com.openavc.server.plist"

    class Mac:
        daemon_plist = plist

        def install(self, **environment: str) -> None:
            daemon = json.loads(json.dumps(INSTALLED))
            daemon["EnvironmentVariables"].update(environment)
            with plist.open("wb") as handle:
                plistlib.dump(daemon, handle)

        def installed(self) -> dict:
            with plist.open("rb") as handle:
                return plistlib.load(handle)

        def run(self, *, loaded_bind: str | None = None, name_plist: bool = True,
                extra: dict | None = None) -> str:
            """Run the wrapper as launchd would, with ``loaded_bind`` as the
            OPENAVC_BIND launchd read when it loaded the job; return the one
            the server got."""
            environment = {
                key: value for key, value in os.environ.items()
                if key not in ("OPENAVC_BIND", "OPENAVC_FIRMWARE_HOST", "OPENAVC_DAEMON_PLIST")
            }
            environment.update({
                "OPENAVC_APP": _bash_path(app),
                "OPENAVC_DATA_DIR": _bash_path(data),
                "PLISTBUDDY": _bash_path(plistbuddy),
                **(extra or {}),
            })
            if name_plist:
                environment["OPENAVC_DAEMON_PLIST"] = _bash_path(plist)
            if loaded_bind is not None:
                environment["OPENAVC_BIND"] = loaded_bind
            done = subprocess.run(
                [_BASH_PATH, str(MAC_WRAPPER)],
                capture_output=True, text=True, timeout=60, env=environment,
            )
            assert done.returncode == 0, done.stderr
            return done.stdout.strip().splitlines()[-1]

    return Mac()


def test_an_update_removes_the_bind_earlier_installers_set(mac):
    mac.install(OPENAVC_BIND="0.0.0.0")

    started_with = mac.run(loaded_bind="0.0.0.0")

    assert started_with == "OPENAVC_BIND=<unset>"
    assert mac.installed() == INSTALLED


def test_the_rewritten_launch_settings_stay_root_owned():
    """launchd refuses a daemon plist that is not root's or that others can
    write; the wrapper runs as root, so this is what a test run cannot show."""
    script = MAC_WRAPPER.read_text(encoding="utf-8")
    drop = script[script.index("drop_earlier_installer_bind() {"):]
    drop = drop[:drop.index("\n}\n")]
    deleted = drop[drop.index("Delete :EnvironmentVariables:OPENAVC_BIND"):]
    assert 'chown root:wheel "$DAEMON_PLIST"' in deleted
    assert 'chmod 644 "$DAEMON_PLIST"' in deleted


def test_launchds_stale_copy_does_not_reach_the_server(mac):
    """Removed from the file, by the wrapper or an administrator, but still in
    what launchd loaded: a restart from Settings must not bring it back."""
    mac.install()

    assert mac.run(loaded_bind="0.0.0.0") == "OPENAVC_BIND=<unset>"
    assert mac.installed() == INSTALLED


@pytest.mark.parametrize("chosen", ["10.20.30.5", "127.0.0.1", "::"])
def test_a_bind_someone_set_on_purpose_stays_and_applies(mac, chosen):
    mac.install(OPENAVC_BIND=chosen)

    assert mac.run(loaded_bind="0.0.0.0") == f"OPENAVC_BIND={chosen}"
    assert mac.installed()["EnvironmentVariables"]["OPENAVC_BIND"] == chosen


def test_a_bind_added_to_the_file_applies_at_the_next_start(mac):
    mac.install(OPENAVC_BIND="10.20.30.5")

    assert mac.run(loaded_bind=None) == "OPENAVC_BIND=10.20.30.5"


def test_a_sandbox_run_leaves_the_launch_settings_alone(mac):
    """OPENAVC_APP elsewhere and no file named: nothing is read or changed."""
    mac.install(OPENAVC_BIND="0.0.0.0")
    before = mac.daemon_plist.read_bytes()

    assert mac.run(loaded_bind="0.0.0.0", name_plist=False) == "OPENAVC_BIND=0.0.0.0"
    assert mac.daemon_plist.read_bytes() == before


def test_unreadable_launch_settings_change_nothing(mac):
    mac.daemon_plist.write_bytes(b"not a plist")

    assert mac.run(loaded_bind="0.0.0.0") == "OPENAVC_BIND=0.0.0.0"
    assert mac.daemon_plist.read_bytes() == b"not a plist"


def test_a_firmware_host_keeps_its_launch_settings(mac, tmp_path):
    """The product's firmware owns its configuration, as it owns its updates."""
    mac.install(OPENAVC_BIND="0.0.0.0")
    before = mac.daemon_plist.read_bytes()
    host = tmp_path / "firmware-host.json"
    host.write_text("{}", encoding="utf-8")

    started_with = mac.run(
        loaded_bind="0.0.0.0", extra={"OPENAVC_FIRMWARE_HOST": _bash_path(host)},
    )

    assert started_with == "OPENAVC_BIND=0.0.0.0"
    assert mac.daemon_plist.read_bytes() == before
