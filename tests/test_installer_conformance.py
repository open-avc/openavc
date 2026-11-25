"""The Windows installer and the macOS .pkg put a release in place as an update does.

An in-app update on Linux and the Mac, and a re-run of install.sh, put a
release in place through installer/apply-lib.sh. The Windows installer
(installer/setup.iss) and the .pkg (installer/macos/scripts/) do it by code of
their own, and these tests hold them to what the library decides:

- the copy kept to roll back to is the install that was running, and an older
  one never outlives an install. On Windows that copy is the running version's
  installer in the data folder's update-cache, which a rollback runs again;
- what the server left for its next start (an update staged or set aside to
  try again, a rollback) is dropped once a release is in place, so that start
  cannot put an older release over it or roll it back. Windows has none: its
  update and rollback run an installer, never an instruction;
- the data folder is the system's, and neither installer deletes from it.

Both lay the release over the old copy where the library unpacks it into a
fresh folder, so a file a release removed stays unless the installer deletes
it by name. The two delete the same names: the layout releases before 0.25.0
left in the bundle.

The .pkg's scripts run here for real in a sandbox: OPENAVC_APP and
OPENAVC_DATA_DIR point into a temporary folder, which also keeps them off the
launchd of the Mac running the tests. Installer's own step between them,
laying the payload over the app, is a copy over it. The Windows installer is
read: Inno Setup runs only on Windows, and what it deletes and caches is
declared in its sections.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path


from tests import gates
from tests.test_update_system import (
    _BASH_MISSING,
    _BASH_PATH,
    _as_bash_path,
    _build_bundle_tarball,
    _build_fake_bundle,
    _mac_install_with_a_previous_copy,
    _read_bundle_version,
    _run_mac_wrapper,
    _stage_mac_update,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = REPO_ROOT / "installer" / "apply-lib.sh"
PREINSTALL = REPO_ROOT / "installer" / "macos" / "scripts" / "preinstall"
POSTINSTALL = REPO_ROOT / "installer" / "macos" / "scripts" / "postinstall"

# What the Mac's next start acts on: the names openavc-macos-run.sh reads and
# the one the library sets an update aside under.
INSTRUCTIONS = ("apply-update.json", "apply-update.retry", "apply-rollback")

# The bundle's frozen payload, where a release's own files live.
INTERNAL = Path("Contents", "Resources", "server", "_internal")

# Records what it was asked, for the one test that lets the .pkg's preinstall
# stop services.
FAKE_LAUNCHCTL = '#!/bin/bash\necho "$*" >> "$FAKE_LAUNCHCTL_LOG"\nexit 0\n'

needs_bash = gates.skipif_missing(gates.BASH, _BASH_MISSING)


# --- The .pkg, run in a sandbox ----------------------------------------------


def _sandbox(app: Path, data: Path, **extra: str) -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("OPENAVC_", "FAKE_"))
    }
    environment.update({
        "OPENAVC_APP": _as_bash_path(app),
        "OPENAVC_DATA_DIR": _as_bash_path(data),
        "OPENAVC_LOG_DIR": _as_bash_path(data.parent / "Logs"),
        **extra,
    })
    return environment


def _run_script(script: Path, app: Path, data: Path, **extra: str) -> str:
    done = subprocess.run(
        [_BASH_PATH, _as_bash_path(script)],
        capture_output=True, text=True, timeout=60, env=_sandbox(app, data, **extra),
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def _payload(tmp_path: Path, version: str) -> Path:
    """The .pkg's payload: a bundle as build-macos.sh makes it, the apply
    library beside the run wrapper."""
    bundle = tmp_path / f"payload-{version}" / "OpenAVC.app"
    _build_fake_bundle(bundle, version)
    beside_wrapper = bundle / "Contents" / "MacOS"
    beside_wrapper.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LIBRARY, beside_wrapper / "apply-lib.sh")
    return bundle


def _install_pkg(tmp_path: Path, app: Path, data: Path, version: str) -> None:
    """A .pkg install: preinstall, Installer laying the payload over the app
    (it merges into a bundle that is there), postinstall."""
    _run_script(PREINSTALL, app, data)
    shutil.copytree(_payload(tmp_path, version), app, dirs_exist_ok=True)
    _run_script(POSTINSTALL, app, data)


def _outcome(app: Path, data: Path) -> dict:
    """What an install leaves: the release in place, the copy Rollback offers,
    and what the next start would act on."""
    previous = Path(f"{app}.previous")
    return {
        "app": _read_bundle_version(app),
        "previous": _read_bundle_version(previous) if previous.exists() else None,
        "instructions": [name for name in INSTRUCTIONS if (data / name).exists()],
    }


def _data_files(data: Path) -> list[str]:
    return sorted(str(path.relative_to(data)) for path in data.rglob("*") if path.is_file())


def _with_projects_and_cache(data: Path) -> None:
    (data / "projects" / "default").mkdir(parents=True)
    (data / "projects" / "default" / "project.avc").write_text("{}")
    (data / "update-cache").mkdir()
    (data / "update-cache" / "openavc-1.0.0-macos-arm64.tar.gz").write_bytes(b"")


@needs_bash
def test_a_pkg_install_leaves_what_an_update_through_the_library_leaves(tmp_path):
    """The library's answer first: an in-app update from 1.0.0 to 2.0.0 on a
    Mac an earlier update left a 0.9.0 copy on. Then the .pkg of 2.0.0 over
    the same Mac."""
    by_update = tmp_path / "update"
    by_update.mkdir()
    data, app, _ = _mac_install_with_a_previous_copy(by_update)
    _stage_mac_update(data, _build_bundle_tarball(by_update / "staging", "2.0.0"))
    assert _run_mac_wrapper(data, app).returncode == 0
    expected = _outcome(app, data)

    by_pkg = tmp_path / "pkg"
    by_pkg.mkdir()
    data, app, _ = _mac_install_with_a_previous_copy(by_pkg)
    _with_projects_and_cache(data)
    kept = _data_files(data)
    _install_pkg(by_pkg, app, data, "2.0.0")

    assert _outcome(app, data) == expected, (
        "after the .pkg, Rollback would offer another copy than the update leaves: "
        "the copy kept is the app the install replaced"
    )
    assert _data_files(data) == kept, "the .pkg deleted something in the data folder"


@needs_bash
def test_a_first_pkg_install_leaves_no_copy_to_roll_back_to(tmp_path):
    """A copy left with no app beside it (the app dragged to the Trash rather
    than uninstalled) is no install this one replaces."""
    data = tmp_path / "Application Support"
    data.mkdir()
    app = tmp_path / "Applications" / "OpenAVC.app"
    _build_fake_bundle(Path(f"{app}.previous"), "0.9.0")

    _install_pkg(tmp_path, app, data, "2.0.0")

    assert _outcome(app, data) == {"app": "2.0.0", "previous": None, "instructions": []}




@needs_bash
def test_the_pkg_stops_the_server_and_the_menu_bar_before_it_copies_the_app(tmp_path):
    data, app, _ = _mac_install_with_a_previous_copy(tmp_path)
    launchctl = tmp_path / "launchctl"
    launchctl.write_bytes(FAKE_LAUNCHCTL.encode("utf-8"))
    launchctl.chmod(0o755)
    log = tmp_path / "launchctl.log"

    _run_script(
        PREINSTALL, app, data,
        OPENAVC_LAUNCHCTL=_as_bash_path(launchctl),
        OPENAVC_CONSOLE_UID="501",
        FAKE_LAUNCHCTL_LOG=_as_bash_path(log),
    )

    assert log.read_text().splitlines() == [
        "bootout system/com.openavc.server",
        "bootout gui/501/com.openavc.menubar",
    ]


@needs_bash
def test_a_sandbox_run_that_names_no_launchctl_stops_nothing(tmp_path):
    """So running these tests never stops the server or the menu bar of the
    Mac they run on, even with a launchctl first on the PATH."""
    data, app, _ = _mac_install_with_a_previous_copy(tmp_path)
    launchctl = tmp_path / "bin" / "launchctl"
    launchctl.parent.mkdir()
    launchctl.write_bytes(FAKE_LAUNCHCTL.encode("utf-8"))
    launchctl.chmod(0o755)
    log = tmp_path / "launchctl.log"

    _run_script(
        PREINSTALL, app, data,
        PATH=os.pathsep.join([str(launchctl.parent), os.environ.get("PATH", "")]),
        FAKE_LAUNCHCTL_LOG=_as_bash_path(log),
    )
    _install_pkg(tmp_path, app, data, "2.0.0")

    assert not log.exists()


def test_the_installed_pkg_stops_its_services_with_the_system_launchctl():
    """The installed app's run is the one a test cannot make (it needs root
    and the real services); its launchctl is the system's."""
    script = PREINSTALL.read_text(encoding="utf-8")
    assert 'if [ "$APP" = "/Applications/OpenAVC.app" ]; then\n    LAUNCHCTL="${OPENAVC_LAUNCHCTL:-/bin/launchctl}"' in script
    assert 'APP="${OPENAVC_APP:-/Applications/OpenAVC.app}"' in script
    postinstall = POSTINSTALL.read_text(encoding="utf-8")
    assert 'APP="${OPENAVC_APP:-/Applications/OpenAVC.app}"' in postinstall


# --- What the .pkg deletes in the bundle -------------------------------




def _pkg_stale_layout() -> list[str]:
    found = re.search(r"^for stale in ([^;]+); do$", PREINSTALL.read_text(encoding="utf-8"), re.M)
    assert found, "preinstall has no `for stale in ...; do` loop; this reads the wrong lines"
    return sorted(found.group(1).split())



@needs_bash
def test_the_pkg_deletes_them_after_it_keeps_the_app_as_it_ran(tmp_path):
    data, app, previous = _mac_install_with_a_previous_copy(tmp_path)
    stale = _pkg_stale_layout()
    for name in [*stale, "openavc"]:
        (app / INTERNAL / name).mkdir(parents=True)
        (app / INTERNAL / name / "__init__.py").write_text("")

    _run_script(PREINSTALL, app, data)

    assert sorted(p.name for p in (app / INTERNAL).iterdir()) == ["openavc"]
    assert sorted(p.name for p in (previous / INTERNAL).iterdir()) == sorted([*stale, "openavc"]), (
        "the copy kept to roll back to is not the app as it ran"
    )
