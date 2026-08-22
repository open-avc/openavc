"""
Platform detection for the update system.

Detects how OpenAVC was installed to determine update behavior:
- windows_installer: Silent installer re-run
- linux_package: Archive extraction + systemd restart
- macos_app: Archive extraction + LaunchDaemon restart (.pkg-installed .app;
  the root launchd wrapper swaps the app bundle on the next launch)
- android_appliance: Archive extraction + supervised-process restart
  (pre-provisioned appliance hardware; the device supervisor applies the
  staged update on the next launch)
- docker: Notification only (containers are immutable)
- git_dev: Notification only (developer manages source)
- unknown: Notification only
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

from openavc.system_config import INSTALL_DIR, _is_docker


class DeploymentType(str, Enum):
    WINDOWS_INSTALLER = "windows_installer"
    LINUX_PACKAGE = "linux_package"
    MACOS_APP = "macos_app"
    ANDROID_APPLIANCE = "android_appliance"
    DOCKER = "docker"
    GIT_DEV = "git_dev"
    UNKNOWN = "unknown"


# Explicit deployment marker, written at provisioning time by appliance
# images whose deployment type can't be inferred from filesystem heuristics.
# Content is one DeploymentType value. Checked before every heuristic —
# provisioning intent beats inference.
_DEPLOYMENT_MARKER = Path("/etc/openavc-deployment")


def _explicit_deployment_type() -> DeploymentType | None:
    try:
        value = _DEPLOYMENT_MARKER.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return DeploymentType(value)
    except ValueError:
        return None


def _is_git_checkout(app_dir: Path) -> bool:
    """Detect if running from a git checkout."""
    return (app_dir / ".git").exists()


def _is_windows_installer(app_dir: Path) -> bool:
    """Detect if installed via Windows installer (NSSM service)."""
    if sys.platform != "win32":
        return False
    # Windows installer places files in Program Files with uninstaller
    uninstall = app_dir / "unins000.exe"
    return uninstall.exists()


def _is_linux_package(app_dir: Path) -> bool:
    """Detect if installed via Linux installer script."""
    if sys.platform == "win32":
        return False
    # Linux installer places app in /opt/openavc with venv
    return str(app_dir).startswith("/opt/openavc") and (app_dir / "venv").is_dir()


def _is_macos_app(app_dir: Path) -> bool:
    """Detect a frozen macOS .app bundle install (shipped in the .pkg).

    The frozen server binary lives at OpenAVC.app/Contents/MacOS/openavc-server,
    so the install dir sits inside a *.app bundle. A plain `python -m
    openavc.main` run on macOS (dev/source) is not frozen and won't match — it
    falls through to git_dev/unknown like any other source run.
    """
    if sys.platform != "darwin":
        return False
    if not getattr(sys, "frozen", False):
        return False
    return any(part.endswith(".app") for part in app_dir.parts)


def get_install_dir() -> Path:
    """Get the installation directory.

    In a PyInstaller frozen bundle, __file__ resolves inside _internal/
    which doesn't contain installer artifacts like unins000.exe. Use the
    directory containing the executable instead.
    """
    return INSTALL_DIR


def detect_deployment_type(app_dir: Path | None = None) -> DeploymentType:
    """Detect how OpenAVC was deployed.

    Order matters: Docker is checked first (it could also have a venv),
    then installer-specific markers, then git, then unknown.
    """
    if app_dir is None:
        app_dir = INSTALL_DIR

    explicit = _explicit_deployment_type()
    if explicit is not None:
        return explicit

    if _is_docker():
        return DeploymentType.DOCKER

    if _is_windows_installer(app_dir):
        return DeploymentType.WINDOWS_INSTALLER

    if _is_linux_package(app_dir):
        return DeploymentType.LINUX_PACKAGE

    if _is_macos_app(app_dir):
        return DeploymentType.MACOS_APP

    if _is_git_checkout(app_dir):
        return DeploymentType.GIT_DEV

    return DeploymentType.UNKNOWN


def can_self_update(deployment_type: DeploymentType) -> bool:
    """Whether this deployment type supports in-app self-update."""
    return deployment_type in (
        DeploymentType.WINDOWS_INSTALLER,
        DeploymentType.LINUX_PACKAGE,
        DeploymentType.MACOS_APP,
        DeploymentType.ANDROID_APPLIANCE,
    )


# Where the full per-deployment procedures live. Both instruction helpers below
# stay short enough to read in a card and point here for the steps.
OFFLINE_GUIDE_URL = "https://docs.openavc.com/updates#updating-a-system-with-no-internet-access"


def update_instructions(deployment_type: DeploymentType, version: str) -> str:
    """Human-readable update instructions for a deployment that won't self-update.

    Every deployment has an answer, so every branch names one. The four that
    normally self-update reach this only when the in-app path is unavailable,
    and for them the answer is to run the installer again: it upgrades in place
    and the data directory sits outside the install, so nothing is lost.
    """
    if deployment_type == DeploymentType.DOCKER:
        return f"Run `docker compose pull && docker compose up -d` to update to v{version}."
    if deployment_type == DeploymentType.GIT_DEV:
        return f"Run `git pull` and rebuild to update to v{version}."
    if deployment_type == DeploymentType.WINDOWS_INSTALLER:
        return (
            f"Download OpenAVC-Setup-{version}.exe and run it. It upgrades this "
            "installation in place and leaves your projects, drivers and settings untouched."
        )
    if deployment_type == DeploymentType.MACOS_APP:
        return (
            f"Download OpenAVC-{version}-macos-<arch>.pkg (arm64 for Apple Silicon, "
            "x86_64 for Intel) and run it. It upgrades this installation in place and "
            "leaves your projects, drivers and settings untouched."
        )
    if deployment_type == DeploymentType.LINUX_PACKAGE:
        return (
            f"Download openavc-{version}-linux-<arch>.tar.gz and its .sig, copy both to "
            f"/var/lib/openavc, then stage and restart. Steps: {OFFLINE_GUIDE_URL}"
        )
    if deployment_type == DeploymentType.ANDROID_APPLIANCE:
        return (
            "Appliance updates are applied by the device supervisor. Connect the appliance "
            "to a network with internet access to update it."
        )
    return f"Update to v{version} is available. See {OFFLINE_GUIDE_URL}"


def offline_update_instructions(deployment_type: DeploymentType) -> str:
    """What to do when the update check itself can't reach the internet.

    An isolated system never gets as far as "an update is available", so the
    guidance can't name a version and can't hang off an update record. It is
    shown next to the failed check instead, because that error is the only
    place an integrator on an air-gapped network ever looks.
    """
    if deployment_type == DeploymentType.WINDOWS_INSTALLER:
        step = "download OpenAVC-Setup-<version>.exe on a machine that has internet, copy it here and run it"
    elif deployment_type == DeploymentType.MACOS_APP:
        step = "download the macOS .pkg on a machine that has internet, copy it here and run it"
    elif deployment_type == DeploymentType.LINUX_PACKAGE:
        step = (
            "download the Linux archive and its .sig on a machine that has internet, "
            "copy both to /var/lib/openavc and stage the update"
        )
    elif deployment_type == DeploymentType.DOCKER:
        step = "save the image on a machine that has internet, copy it here and load it"
    elif deployment_type == DeploymentType.ANDROID_APPLIANCE:
        return (
            "This system has no internet access. Appliance updates are applied by the "
            "device supervisor, so connect it to a network with internet access to "
            f"update it. More: {OFFLINE_GUIDE_URL}"
        )
    elif deployment_type == DeploymentType.GIT_DEV:
        step = "update the source with your normal git workflow"
    else:
        step = "download the release files on a machine that has internet and copy them here"
    return (
        f"This system can be updated without an internet connection: {step}. "
        f"Full steps: {OFFLINE_GUIDE_URL}"
    )
