"""A sandboxed Mac for running the real macOS run wrapper against a real plist.

``sandboxed_mac(tmp_path)`` gives an app bundle whose server prints the
OPENAVC_BIND it was started with, installed launch settings, and a
PlistBuddy: the real one on a Mac, elsewhere a stand-in that reads and writes
the plist the same way. Each test file that runs the wrapper makes its own
``mac`` fixture from it.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from tests import gates
from tests.test_update_system import _BASH_MISSING, _BASH_PATH, _to_msys2_path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAC_WRAPPER = REPO_ROOT / "installer" / "openavc-macos-run.sh"

FAKE_PLISTBUDDY = r'''
import plistlib, sys

sys.stdout.reconfigure(newline="\n")  # what PlistBuddy prints, on Windows too

TYPES = {"integer": int, "string": str}

def main(argv):
    if len(argv) != 3 or argv[0] != "-c":
        return 2
    command, path = argv[1], argv[2]
    verb, _, rest = command.partition(" ")
    keypath, _, typed = rest.partition(" ")
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
    if not isinstance(parent, dict):
        print(f'{verb}: Entry, ":{":".join(keys)}", Does Not Exist')
        return 1
    if verb == "Add":
        if keys[-1] in parent:
            print(f'Add: ":{":".join(keys)}" Entry Already Exists')
            return 1
        kind, _, value = typed.partition(" ")
        parent[keys[-1]] = TYPES[kind](value)
        with open(path, "wb") as handle:
            plistlib.dump(root, handle)
        return 0
    if keys[-1] not in parent:
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

# The launch settings a current .pkg installs, with one variable an
# administrator added.
INSTALLED = {
    "Label": "com.openavc.server",
    "ExitTimeOut": 20,
    "EnvironmentVariables": {
        "OPENAVC_DATA_DIR": "/Library/Application Support/OpenAVC",
        "OPENAVC_ALLOW_ANONYMOUS": "false",
        "OPENAVC_SERVICE_MANAGED": "1",
        "HTTPS_PROXY": "http://proxy.campus.example:3128",
    },
}


def bash_path(path: Path) -> str:
    return _to_msys2_path(str(path)) if sys.platform == "win32" else str(path)


def _plistbuddy(tmp_path: Path) -> Path:
    if sys.platform == "darwin" and REAL_PLISTBUDDY.exists():
        return REAL_PLISTBUDDY
    fake = tmp_path / "fake_plistbuddy.py"
    fake.write_text(FAKE_PLISTBUDDY, encoding="utf-8")
    plistbuddy = tmp_path / "PlistBuddy"
    plistbuddy.write_bytes(
        f'#!/bin/sh\nexec "{bash_path(Path(sys.executable))}" "{bash_path(fake)}" "$@"\n'
        .encode("utf-8")
    )
    plistbuddy.chmod(0o755)
    return plistbuddy


class SandboxedMac:
    """The wrapper's world in ``tmp_path``: install launch settings, run the
    wrapper as launchd would, read back what it left."""

    def __init__(self, tmp_path: Path) -> None:
        self.app = tmp_path / "OpenAVC.app"
        server = self.app / "Contents" / "Resources" / "server" / "openavc-server"
        server.parent.mkdir(parents=True)
        server.write_bytes(b'#!/bin/sh\necho "OPENAVC_BIND=${OPENAVC_BIND-<unset>}"\n')
        server.chmod(0o755)
        self.data = tmp_path / "data"
        self.data.mkdir()
        self.plistbuddy = _plistbuddy(tmp_path)
        self.daemon_plist = tmp_path / "com.openavc.server.plist"

    def install(self, *, settings: dict | None = None, **environment: str) -> None:
        """Install INSTALLED with ``environment`` added to its variables and
        ``settings`` over its top-level keys (a None value leaves one out)."""
        daemon = json.loads(json.dumps(INSTALLED))
        daemon["EnvironmentVariables"].update(environment)
        for key, value in (settings or {}).items():
            if value is None:
                daemon.pop(key, None)
            else:
                daemon[key] = value
        with self.daemon_plist.open("wb") as handle:
            plistlib.dump(daemon, handle)

    def installed(self) -> dict:
        with self.daemon_plist.open("rb") as handle:
            return plistlib.load(handle)

    def run(self, *, loaded_bind: str | None = None, name_plist: bool = True,
            extra: dict | None = None) -> str:
        """Run the wrapper as launchd would, with ``loaded_bind`` as the
        OPENAVC_BIND launchd read when it loaded the job; return the one the
        server got."""
        return self.run_output(
            loaded_bind=loaded_bind, name_plist=name_plist, extra=extra,
        ).strip().splitlines()[-1]

    def run_output(self, *, loaded_bind: str | None = None, name_plist: bool = True,
                   extra: dict | None = None) -> str:
        """Run the wrapper; return everything it and the server printed."""
        environment = {
            key: value for key, value in os.environ.items()
            if key not in ("OPENAVC_BIND", "OPENAVC_FIRMWARE_HOST", "OPENAVC_DAEMON_PLIST")
        }
        environment.update({
            "OPENAVC_APP": bash_path(self.app),
            "OPENAVC_DATA_DIR": bash_path(self.data),
            "PLISTBUDDY": bash_path(self.plistbuddy),
            **(extra or {}),
        })
        if name_plist:
            environment["OPENAVC_DAEMON_PLIST"] = bash_path(self.daemon_plist)
        if loaded_bind is not None:
            environment["OPENAVC_BIND"] = loaded_bind
        done = subprocess.run(
            [_BASH_PATH, str(MAC_WRAPPER)],
            capture_output=True, text=True, timeout=60, env=environment,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout


def sandboxed_mac(tmp_path: Path) -> SandboxedMac:
    gates.skip_or_fail(gates.BASH, _BASH_MISSING)
    return SandboxedMac(tmp_path)
