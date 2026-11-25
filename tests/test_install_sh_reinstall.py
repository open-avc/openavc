"""A re-run of install.sh over an install replaces it the way an update does.

``curl -sSL https://get.openavc.com | bash`` on a machine that already runs
OpenAVC upgrades it in place, by the apply library's rule
(``installer/apply-lib.sh``, which install.sh carries), the same one the update
helper applies an in-app update by: the release is unpacked beside the install
and checked there, and only then is the running install moved aside, whole, as
the copy to roll back to, with the folders no release ships (the Python
environment, the image's launchers, the legacy driver folders) carried into
the new one. A release that cannot be installed leaves the install, and the
copy an earlier update kept, as they were.

Unpacked over the install instead, a release left behind every file it no
longer ships, for Python's imports and the driver loader to find, and a
damaged archive left the install half-written with that older copy already
deleted.

The service unit is the one the release ships, made for the host by the
library's ``host_unit``, which the update helper's unit sync also builds with,
so on a host without CAP_NET_RAW the first start after a reinstall finds
nothing to rewrite and schedules no restart.

These run the real install.sh functions against real directories, with a
stand-in ``systemctl`` that records what it is asked, so nothing on the machine
running them is stopped or started.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests import gates

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "installer" / "install.sh"
HELPER_SCRIPT = REPO_ROOT / "installer" / "update-helper.sh"
APPLY_LIB = REPO_ROOT / "installer" / "apply-lib.sh"
SHIPPED_UNIT = REPO_ROOT / "installer" / "openavc.service"

# install.sh installs Linux, so it is not run on Windows at all.
_BASH = shutil.which("bash") if sys.platform != "win32" else None
pytestmark = gates.skipif_missing(gates.BASH, None if _BASH else "bash not available")

TIMEOUT = 90  # a hang detector: the work is a handful of tar, cp and mv calls

# /proc/self/status's bounding-set line on a normal host, and on one started
# with CAP_NET_RAW (capability 13) dropped.
CAPBND_ALL = "CapBnd:\t000001ffffffffff\n"
CAPBND_NO_NET_RAW = "CapBnd:\t000001ffffffdfff\n"


def _write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def _make_tree(root: Path, version: str, *, unit: bool = True, extra: str | None = None) -> None:
    """The files a release's Linux archive carries, at ``version``."""
    _write(root / "pyproject.toml", f'[project]\nversion = "{version}"\n')
    _write(root / "requirements.txt", "fastapi\n")
    _write(root / "openavc" / "__init__.py", "")
    _write(root / "openavc" / "main.py", f"# {version}\n")
    if extra:
        _write(root / "openavc" / extra, f"# only in {version}\n")
    _write(root / "installer" / "update-helper.sh", f"#!/bin/bash\n# helper {version}\n", 0o755)
    _write(root / "installer" / "firewall-sync.sh", f"#!/bin/bash\n# firewall {version}\n", 0o755)
    if unit:
        shutil.copy2(SHIPPED_UNIT, root / "installer" / "openavc.service")


def _make_install(app: Path, version: str = "1.0.0") -> None:
    """An install as install.sh leaves one: the release, the two helpers it
    copies to the top, and what no release ships."""
    _make_tree(app, version, extra="only_in_the_old_release.py")
    for name in ("update-helper.sh", "firewall-sync.sh"):
        shutil.copy2(app / "installer" / name, app / name)
    _write(app / "venv" / "bin" / "python3", "#!/bin/sh\nexit 0\n", 0o755)
    _write(app / "scripts" / "panel-kiosk.sh", "#!/bin/sh\n# the image's launcher\n", 0o755)
    _write(app / "driver_repo" / "left_to_drain.avcdriver", "id: left_to_drain\n")


def _make_archive(path: Path, version: str = "2.0.0", *, unit: bool = True) -> Path:
    tree = path.parent / f"tree-{version}"
    _make_tree(tree, version, unit=unit)
    with tarfile.open(path, "w:gz") as tar:
        for item in sorted(tree.iterdir()):
            tar.add(item, arcname=item.name)
    shutil.rmtree(tree)
    return path


def _files(tree: Path) -> dict[str, bytes]:
    return {
        p.relative_to(tree).as_posix(): p.read_bytes()
        for p in sorted(tree.rglob("*")) if p.is_file()
    }


def _sourceable(tmp_path: Path) -> Path:
    """install.sh minus its closing ``main "$@"``, so sourcing it only defines
    things. A real file rather than a pipe: bash 3.2 drops most of a
    ``source <(...)`` and reports success."""
    body = INSTALL_SCRIPT.read_text(encoding="utf-8").rstrip("\n")
    head, last = body.rsplit("\n", 1)
    assert last == 'main "$@"', "install.sh no longer ends by calling main -- this harness is stale"
    return _write(tmp_path / "install-functions.sh", head + "\n")


class Box:
    """A machine for install.sh to run on, in a temporary directory."""

    def __init__(self, tmp_path: Path, *, capbnd: str = CAPBND_ALL, running: bool = True) -> None:
        self.tmp = tmp_path
        self.app = tmp_path / "opt" / "openavc"
        self.previous = tmp_path / "opt" / "openavc.previous"
        self.unit = tmp_path / "etc" / "openavc.service"
        self.unit.parent.mkdir(parents=True)
        self.status = _write(tmp_path / "proc-status", capbnd)
        self.calls = tmp_path / "systemctl.log"
        self.calls.touch()
        stubs = tmp_path / "bin"
        active = "exit 0" if running else "exit 3"
        _write(
            stubs / "systemctl",
            f'#!/bin/sh\necho "$*" >> "{self.calls}"\n[ "$1" = is-active ] && {active}\nexit 0\n',
            0o755,
        )
        self.path = f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin"
        self.functions = _sourceable(tmp_path)

    def run(self, steps: str, archive: Path | None = None, python: str = "python3") -> subprocess.CompletedProcess:
        script = f"""
            set -euo pipefail
            source "{self.functions}"
            INSTALL_DIR="{self.app}"
            SERVICE_FILE="{self.unit}"
            SERVICE_NAME=openavc
            OPENAVC_PROC_STATUS="{self.status}"
            ARCHIVE_PATH="{archive or ''}"
            PYTHON_BIN="{python}"
            {steps}
        """
        return subprocess.run(
            [_BASH, "-c", script], capture_output=True, text=True, timeout=TIMEOUT,
            env={"PATH": self.path, "HOME": str(self.tmp)},
        )

    def systemctl(self) -> list[str]:
        return self.calls.read_text().splitlines()


def _output(proc: subprocess.CompletedProcess) -> str:
    return f"exit {proc.returncode}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}"


# --- The swap ---------------------------------------------------------------


def test_a_reinstall_swaps_in_the_release_and_keeps_the_install_to_roll_back_to(tmp_path):
    box = Box(tmp_path)
    _make_install(box.app)
    before = _files(box.app)
    archive = _make_archive(tmp_path / "openavc-2.0.0-linux-amd64.tar.gz")

    proc = box.run("install_files", archive)
    assert proc.returncode == 0, _output(proc)

    after = _files(box.app)
    assert after["pyproject.toml"] == b'[project]\nversion = "2.0.0"\n'
    assert "openavc/only_in_the_old_release.py" not in after, (
        "a file the new release no longer ships survived the reinstall; Python's "
        "imports and the driver loader would still find it"
    )
    for kept in ("venv/bin/python3", "scripts/panel-kiosk.sh", "driver_repo/left_to_drain.avcdriver"):
        assert after.get(kept) == before[kept], f"{kept} was not carried into the new release"
    for name in ("update-helper.sh", "firewall-sync.sh"):
        assert after[name] == after[f"installer/{name}"], f"{name} at the top is not the new release's"
    assert _files(box.previous) == before, "the install that was running is not whole as the copy to roll back to"
    assert not archive.exists(), "the downloaded archive was left in /tmp"
    assert "stop openavc" in box.systemctl()


def test_a_damaged_release_leaves_the_install_and_the_older_copy(tmp_path):
    box = Box(tmp_path)
    _make_install(box.app)
    _make_install(box.previous, "0.9.0")
    install, older = _files(box.app), _files(box.previous)
    archive = tmp_path / "openavc-2.0.0-linux-amd64.tar.gz"
    archive.write_bytes(b"not a gzip archive")

    proc = box.run("install_files", archive)
    assert proc.returncode != 0, _output(proc)
    assert _files(box.app) == install, "a release that would not unpack changed the install"
    assert _files(box.previous) == older, (
        "a release that would not unpack cost the copy an earlier update kept to roll back to"
    )
    assert not (tmp_path / "opt" / "openavc.new").exists(), "the staging folder was left behind"
    assert box.systemctl()[-1] == "start openavc", (
        "the install was left as it was but its service was left stopped:\n" + "\n".join(box.systemctl())
    )
    assert "could not be installed" in proc.stderr, _output(proc)


def test_a_release_without_its_service_unit_is_refused_before_anything_moves(tmp_path):
    box = Box(tmp_path)
    _make_install(box.app)
    install = _files(box.app)
    archive = _make_archive(tmp_path / "openavc-2.0.0-linux-amd64.tar.gz", unit=False)

    proc = box.run("install_files", archive)
    assert proc.returncode != 0, _output(proc)
    assert _files(box.app) == install, _output(proc)
    assert not box.previous.exists(), _output(proc)


def test_a_service_that_was_stopped_is_left_stopped_when_the_release_is_refused(tmp_path):
    box = Box(tmp_path, running=False)
    _make_install(box.app)
    archive = tmp_path / "openavc-2.0.0-linux-amd64.tar.gz"
    archive.write_bytes(b"not a gzip archive")

    proc = box.run("install_files", archive)
    assert proc.returncode != 0, _output(proc)
    assert "start openavc" not in box.systemctl(), box.systemctl()


def test_a_python_environment_that_cannot_be_made_puts_the_install_back(tmp_path):
    """The step after the swap: the release's dependencies. Failing there with
    the new code in place and its packages missing would leave a service that
    crash-loops on its first import, so the install that was running goes
    back, as the update helper does."""
    box = Box(tmp_path)
    _make_install(box.app)
    install = _files(box.app)
    archive = _make_archive(tmp_path / "openavc-2.0.0-linux-amd64.tar.gz")
    failing_python = _write(tmp_path / "bin" / "python-that-fails", "#!/bin/sh\nexit 1\n", 0o755)

    proc = box.run("install_files; create_venv", archive, python=str(failing_python))
    assert proc.returncode != 0, _output(proc)
    assert _files(box.app) == install, "the install that was running was not put back"
    assert box.systemctl()[-1] == "start openavc", box.systemctl()
    assert "could not be installed" in proc.stderr, _output(proc)


def test_a_fresh_install_is_unpacked_with_no_copy_to_roll_back_to(tmp_path):
    """create_user has already made the install folder as the service
    account's home, so a folder with no release in it is not an install."""
    box = Box(tmp_path)
    _write(box.app / ".profile", "# the account's home\n")
    archive = _make_archive(tmp_path / "openavc-2.0.0-linux-amd64.tar.gz")

    proc = box.run("install_files", archive)
    assert proc.returncode == 0, _output(proc)
    assert (box.app / "openavc" / "main.py").is_file()
    assert (box.app / "update-helper.sh").is_file()
    assert not box.previous.exists()
    assert "stop openavc" not in box.systemctl()


# --- The unit ----------------------------------------------------------------


def _install_service(box: Box) -> subprocess.CompletedProcess:
    _make_tree(box.app, "2.0.0")
    return box.run("install_service")


def test_the_unit_installed_is_the_one_the_release_ships(tmp_path):
    box = Box(tmp_path)
    proc = _install_service(box)
    assert proc.returncode == 0, _output(proc)
    assert box.unit.read_bytes() == SHIPPED_UNIT.read_bytes()
    assert box.systemctl()[:2] == ["daemon-reload", "enable openavc"], box.systemctl()


def test_a_release_without_its_unit_installs_no_unit_of_its_own(tmp_path):
    """Every release ships installer/openavc.service; install.sh carries no
    second copy that could drift from it."""
    box = Box(tmp_path)
    _make_tree(box.app, "2.0.0", unit=False)
    proc = box.run("install_service")
    assert proc.returncode != 0, _output(proc)
    assert not box.unit.exists(), _output(proc)


def _host_unit_as_the_helper_builds_it(box: Box) -> bytes:
    """What the update helper's sync_unit compares the installed unit with:
    the shipped unit, made for this host by the library."""
    out = box.tmp / "helper-desired.service"
    script = f"""
        set -u
        DATA_DIR="{box.tmp}"
        OPENAVC_PROC_STATUS="{box.status}"
        source "{APPLY_LIB}"
        host_unit "{SHIPPED_UNIT}" "{out}"
    """
    proc = subprocess.run([_BASH, "-c", script], capture_output=True, text=True, timeout=TIMEOUT)
    assert proc.returncode == 0, _output(proc)
    return out.read_bytes()


def test_without_cap_net_raw_the_unit_is_the_one_the_update_helper_keeps(tmp_path):
    box = Box(tmp_path, capbnd=CAPBND_NO_NET_RAW)
    proc = _install_service(box)
    assert proc.returncode == 0, _output(proc)

    written = box.unit.read_text(encoding="utf-8")
    assert "\nAmbientCapabilities=CAP_NET_RAW" not in written, (
        "the unit still asks for CAP_NET_RAW on a host that lacks it, so systemd refuses to start it"
    )
    assert box.unit.read_bytes() == _host_unit_as_the_helper_builds_it(box), (
        "install.sh and the update helper make the unit differently on a host without "
        "CAP_NET_RAW, so the first start after a reinstall rewrites it and restarts the service"
    )


def test_with_cap_net_raw_the_library_leaves_the_unit_as_shipped(tmp_path):
    box = Box(tmp_path)
    assert _host_unit_as_the_helper_builds_it(box) == SHIPPED_UNIT.read_bytes()


def test_one_place_words_the_disabled_capability_line():
    """The update helper rewrites the unit, and restarts the service, whenever
    what it builds differs from what is installed, so every script that writes
    the unit has to build it the same way: with the library's host_unit."""
    helper = HELPER_SCRIPT.read_text(encoding="utf-8")
    sync_unit = helper[helper.index("sync_unit() {"):]
    sync_unit = sync_unit[: sync_unit.index("\n}\n")]
    assert 'host_unit "$src"' in sync_unit, "update-helper.sh's sync_unit no longer builds the unit with host_unit"

    library = APPLY_LIB.read_text(encoding="utf-8")
    install_sh = INSTALL_SCRIPT.read_text(encoding="utf-8").replace(library, "")
    for name, text in (("install.sh", install_sh), ("update-helper.sh", helper)):
        assert "CAP_NET_RAW unavailable" not in text, (
            f"{name} words the disabled-capability line itself; build the unit with host_unit"
        )
        assert "<< 'UNIT'" not in text, f"{name} writes a unit of its own"


@pytest.mark.parametrize("fixture", ["reinstall", "fresh"])
def test_the_whole_upgrade_leaves_a_unit_and_a_runnable_tree(tmp_path, fixture):
    """install_files then install_service, as main runs them."""
    box = Box(tmp_path)
    if fixture == "reinstall":
        _make_install(box.app)
    archive = _make_archive(tmp_path / "openavc-2.0.0-linux-amd64.tar.gz")
    proc = box.run("install_files; install_service", archive)
    assert proc.returncode == 0, _output(proc)
    assert box.unit.read_bytes() == SHIPPED_UNIT.read_bytes()
    assert (box.app / "openavc" / "main.py").read_text() == "# 2.0.0\n"
