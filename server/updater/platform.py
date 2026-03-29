"""
Platform detection for the update system.

Detects how OpenAVC was installed to determine update behavior:
- windows_installer: Silent installer re-run
- linux_package: Archive extraction + systemd restart
- docker: Notification only (containers are immutable)
- git_dev: Notification only (developer manages source)
- unknown: Notification only
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path


class DeploymentType(str, Enum):
    WINDOWS_INSTALLER = "windows_installer"
    LINUX_PACKAGE = "linux_package"
    DOCKER = "docker"
    GIT_DEV = "git_dev"
    UNKNOWN = "unknown"


def _is_docker() -> bool:
    """Detect if running inside a Docker container."""
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.exists():
            text = cgroup.read_text(encoding="utf-8", errors="ignore")
            if "docker" in text or "containerd" in text:
                return True
    except OSError:
        pass
    return False


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


def detect_deployment_type(app_dir: Path | None = None) -> DeploymentType:
    """Detect how OpenAVC was deployed.

    Order matters: Docker is checked first (it could also have a venv),
    then installer-specific markers, then git, then unknown.
    """
    if app_dir is None:
        app_dir = Path(__file__).resolve().parent.parent.parent

    if _is_docker():
        return DeploymentType.DOCKER

    if _is_windows_installer(app_dir):
        return DeploymentType.WINDOWS_INSTALLER

    if _is_linux_package(app_dir):
        return DeploymentType.LINUX_PACKAGE

    if _is_git_checkout(app_dir):
        return DeploymentType.GIT_DEV

    return DeploymentType.UNKNOWN


def can_self_update(deployment_type: DeploymentType) -> bool:
    """Whether this deployment type supports in-app self-update."""
    return deployment_type in (
        DeploymentType.WINDOWS_INSTALLER,
        DeploymentType.LINUX_PACKAGE,
    )


def update_instructions(deployment_type: DeploymentType, version: str) -> str:
    """Human-readable update instructions for notification-only deployments."""
    if deployment_type == DeploymentType.DOCKER:
        return f"Run `docker compose pull && docker compose up -d` to update to v{version}."
    if deployment_type == DeploymentType.GIT_DEV:
        return f"Run `git pull` and rebuild to update to v{version}."
    return f"Update to v{version} is available."
