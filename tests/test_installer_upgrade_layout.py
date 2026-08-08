"""The first upgrade onto the `openavc` package has to clear the old layout.

Up to 0.24.1 an install unpacked `server/`, `simulator/`, `web/` and `themes/`
at the top of the install directory. 0.25.0 puts all four inside `openavc/`.
Nothing about that is automatic: `install.sh` untars over whatever is already
there, and the Windows and macOS installers merge rather than replace. Left
alone, the pre-0.25.0 directories survive the upgrade beside the new package.

That is not clutter. The Linux service runs with `WorkingDirectory=/opt/openavc`
and the frozen builds carry `_internal` on `sys.path`, so a driver in
`driver_repo/` that still says `from server.…` or `from simulator.…` would find
the stale tree and import a whole second copy of the platform -- its own state
store, its own event bus, its own root-logger handlers -- instead of failing
with the one-sentence message that tells the user to update the driver from
Browse Drivers.

The Linux half runs the real `install.sh` function against a real directory,
in the style of the update-helper tests next door. The Windows and macOS halves
are read from their installer files, which is the only thing a test on this
machine can do about them.
"""

import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests import gates

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "installer" / "install.sh"
SETUP_ISS = REPO_ROOT / "installer" / "setup.iss"
MACOS_PREINSTALL = REPO_ROOT / "installer" / "macos" / "scripts" / "preinstall"

# The four names that moved inside the package.
PRE_RENAME_DIRS = ("server", "simulator", "web", "themes")

_BASH = shutil.which("bash") if sys.platform != "win32" else None
_BASH_MISSING = None if _BASH else "bash not available"


def _make_pre_rename_install(install_dir: Path) -> None:
    """A /opt/openavc as 0.24.1 left it: the four names at the top level."""
    install_dir.mkdir(parents=True)
    (install_dir / "pyproject.toml").write_text('version = "0.24.1"\n')
    for name in PRE_RENAME_DIRS:
        d = install_dir / name
        d.mkdir()
        (d / "__init__.py").write_text("")
    (install_dir / "server" / "main.py").write_text("# 0.24.1 entry point\n")


def _make_release_tarball(path: Path) -> None:
    """A 0.25.0 archive: one top-level `openavc`, plus the loose files."""
    staging = path.parent / "staging"
    pkg = staging / "openavc"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "main.py").write_text("# 0.25.0 entry point\n")
    (staging / "pyproject.toml").write_text('version = "0.25.0"\n')
    with tarfile.open(path, "w:gz") as tar:
        for item in sorted(staging.iterdir()):
            tar.add(item, arcname=item.name)
    shutil.rmtree(staging)


def _run_install_files(tmp_path: Path, install_dir: Path) -> subprocess.CompletedProcess:
    """Source the real install.sh (minus its `main` call) and run install_files."""
    archive = tmp_path / "openavc.tar.gz"
    _make_release_tarball(archive)

    # Everything but the final `main "$@"`, so sourcing only defines things.
    # Written to a real file rather than piped: bash 3.2 (what macOS ships)
    # drops most of a `source <(...)` FIFO and then reports success, which
    # looks exactly like a script that defines no functions.
    sourceable = tmp_path / "install_lib.sh"
    body = INSTALL_SCRIPT.read_text(encoding="utf-8").rstrip("\n").rsplit("\n", 1)[0]
    sourceable.write_text(body + "\n", encoding="utf-8")

    script = f"""
        set -euo pipefail
        source {sourceable!s}
        INSTALL_DIR={install_dir!s}
        ARCHIVE_PATH={archive!s}
        SERVICE_NAME=openavc-test-not-a-real-service
        install_files
    """
    return subprocess.run(
        [_BASH, "-c", script], capture_output=True, text=True, timeout=60
    )


@gates.skipif_missing(gates.BASH, _BASH_MISSING)
def test_upgrading_from_the_old_layout_removes_it(tmp_path):
    install_dir = tmp_path / "opt" / "openavc"
    _make_pre_rename_install(install_dir)

    result = _run_install_files(tmp_path, install_dir)
    assert result.returncode == 0, result.stderr

    assert (install_dir / "openavc" / "main.py").is_file(), "new package not unpacked"
    for name in PRE_RENAME_DIRS:
        assert not (install_dir / name).exists(), (
            f"{name}/ survived the upgrade; a pre-rename driver would import it"
        )


@gates.skipif_missing(gates.BASH, _BASH_MISSING)
def test_the_old_layout_still_counts_as_an_upgrade(tmp_path):
    """The snapshot is the rollback target, and it is only taken on an upgrade.

    Detecting the old install by `openavc/` alone would read 0.24.1 as a fresh
    install -- no service stop, and nothing to roll back to -- on the one
    upgrade where that matters most.
    """
    install_dir = tmp_path / "opt" / "openavc"
    _make_pre_rename_install(install_dir)

    result = _run_install_files(tmp_path, install_dir)
    assert result.returncode == 0, result.stderr

    previous = Path(str(install_dir) + ".previous")
    assert previous.is_dir(), "no .previous snapshot taken for a pre-rename upgrade"
    for name in PRE_RENAME_DIRS:
        assert (previous / name).is_dir(), f"{name}/ missing from the rollback snapshot"


@gates.skipif_missing(gates.BASH, _BASH_MISSING)
def test_a_fresh_install_takes_no_snapshot(tmp_path):
    install_dir = tmp_path / "opt" / "openavc"

    result = _run_install_files(tmp_path, install_dir)
    assert result.returncode == 0, result.stderr

    assert (install_dir / "openavc" / "main.py").is_file()
    assert not Path(str(install_dir) + ".previous").exists()


@pytest.mark.parametrize("stale", PRE_RENAME_DIRS)
def test_windows_installer_deletes_the_old_bundle_layout(stale):
    """Inno copies over what is there and never removes anything on its own."""
    text = SETUP_ISS.read_text(encoding="utf-8")
    assert "[InstallDelete]" in text, "setup.iss has no [InstallDelete] section"
    assert f'Name: "{{app}}\\_internal\\{stale}"' in text, (
        f"setup.iss does not remove the pre-rename _internal\\{stale}"
    )


@pytest.mark.parametrize("stale", PRE_RENAME_DIRS)
def test_macos_preinstall_deletes_the_old_bundle_layout(stale):
    """Installer upgrades a bundle by merging, so the old payload would stay."""
    text = MACOS_PREINSTALL.read_text(encoding="utf-8")
    assert "_internal" in text, "preinstall does not touch the frozen _internal dir"
    assert stale in text, f"preinstall does not remove the pre-rename {stale}"
