"""
OpenAVC Project Library — saved project file management.

All saved projects live in saved_projects/<id>/project.avc with optional scripts/.
Starter projects are seeded from server/templates/ on first run.
No distinction between bundled and user projects — all are equal.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from server import config
from server.core.project_loader import (
    GridConfig,
    ISCConfig,
    ProjectConfig,
    ProjectMeta,
    UIConfig,
    UIPage,
    UISettings,
    save_project,
)
from server.system_config import (
    DRIVER_REPO_DIR as _DRIVER_REPO_DIR,
    PLUGIN_REPO_DIR as _PLUGIN_REPO_DIR,
    SEED_TEMPLATES_DIR,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

_SEED_DIR = SEED_TEMPLATES_DIR
_MAX_IMPORT_SIZE = 10 * 1024 * 1024  # 10 MB


# --- Helpers ---


def sanitize_id(raw: str) -> str:
    """Sanitize a string to a valid project ID."""
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", raw.strip())
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "untitled"


def _lib_dir() -> Path:
    """Get the saved projects directory, creating it if needed."""
    d = config.SAVED_PROJECTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_avc(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically via temp + rename."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_scripts_dir(scripts_dir: Path) -> dict[str, str]:
    """Read all .py files from a scripts directory."""
    result: dict[str, str] = {}
    if scripts_dir.exists():
        for f in sorted(scripts_dir.glob("*.py")):
            result[f.name] = f.read_text(encoding="utf-8")
    return result


def _project_meta(project_id: str, data: dict[str, Any],
                  scripts_dir: Path | None = None) -> dict[str, Any]:
    """Build a library listing entry from project data."""
    project = data.get("project", {})
    ui = data.get("ui", {})
    script_count = len(data.get("scripts", []))
    if scripts_dir and scripts_dir.exists():
        script_count = max(script_count, len(list(scripts_dir.glob("*.py"))))
    # Extract driver requirements
    driver_deps = data.get("driver_dependencies", [])
    required_drivers = [d.get("driver_id", "") for d in driver_deps if d.get("driver_id")]

    return {
        "id": project_id,
        "name": project.get("name", project_id),
        "description": project.get("description", ""),
        "device_count": len(data.get("devices", [])),
        "page_count": len(ui.get("pages", [])),
        "macro_count": len(data.get("macros", [])),
        "script_count": script_count,
        "required_drivers": required_drivers,
        "created": project.get("created", ""),
        "modified": project.get("modified", ""),
    }


# --- Startup Seeding ---


def _seed_zip_to_library(zip_path: Path, project_id: str, lib: Path) -> None:
    """Extract a .zip template into the library WITHOUT installing drivers.

    Only extracts project.avc, scripts, and assets. The .zip file itself
    is preserved alongside the project so drivers can be installed later
    when the user opens the project.
    """
    from server.core.project_migration import migrate_project

    project_dir = lib / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Extract and migrate the project file
        avc_names = [n for n in zf.namelist() if n.endswith(".avc")]
        if not avc_names:
            raise ValueError(f"No .avc file found in {zip_path.name}")

        data = json.loads(zf.read(avc_names[0]).decode("utf-8"))
        data, _ = migrate_project(data)
        (project_dir / "project.avc").write_text(
            json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
        )

        # Extract scripts
        for name in zf.namelist():
            if name.startswith("scripts/") and name.endswith(".py"):
                script_name = Path(name).name
                if script_name:
                    dest = project_dir / "scripts"
                    dest.mkdir(exist_ok=True)
                    (dest / script_name).write_bytes(zf.read(name))

        # Extract assets
        for name in zf.namelist():
            if name.startswith("assets/") and not name.endswith("/"):
                asset_name = Path(name).name
                if asset_name and not asset_name.startswith("."):
                    dest = project_dir / "assets"
                    dest.mkdir(exist_ok=True)
                    (dest / asset_name).write_bytes(zf.read(name))

    # Copy the original .zip alongside the project so bundled drivers
    # can be installed when the user opens it
    shutil.copy2(zip_path, project_dir / "bundle.zip")



def ensure_starter_projects() -> None:
    """Seed starter projects from server/templates/ on first run.

    Supports two formats:
    - .zip bundles (preferred): self-contained projects with drivers,
      plugins, scripts, and assets included. Uses the import path so
      bundled drivers are auto-installed to driver_repo/.
    - .avc files (legacy): plain project files. If a matching
      <stem>.scripts/ directory exists, scripts are copied too.
    """
    lib = _lib_dir()
    marker = lib / ".seeded"

    if marker.exists() or not _SEED_DIR.exists():
        return

    # Seed .zip bundles (just extract project.avc, scripts, and assets
    # into the library — drivers are NOT installed here. They get
    # installed when the user actually opens the project, because the
    # open_from_library flow handles bundled driver installation.)
    for zip_file in sorted(_SEED_DIR.glob("*.zip")):
        project_id = zip_file.stem
        if (lib / project_id / "project.avc").exists():
            continue
        try:
            _seed_zip_to_library(zip_file, project_id, lib)
            log.info("Seeded starter project: %s", project_id)
        except Exception as e:
            log.warning("Failed to seed starter project %s: %s", project_id, e)

    # Seed plain .avc files (skip if a .zip with the same stem was already seeded)
    for avc_file in sorted(_SEED_DIR.glob("*.avc")):
        project_dir = lib / avc_file.stem
        if project_dir.exists():
            continue
        project_dir.mkdir()
        shutil.copy2(avc_file, project_dir / "project.avc")
        scripts_seed = _SEED_DIR / f"{avc_file.stem}.scripts"
        if scripts_seed.is_dir():
            dest_scripts = project_dir / "scripts"
            dest_scripts.mkdir(exist_ok=True)
            for py_file in scripts_seed.glob("*.py"):
                shutil.copy2(py_file, dest_scripts / py_file.name)
        log.info("Seeded starter project: %s", avc_file.stem)

    marker.touch()
    log.info("Starter projects seeded")


# --- Public API ---


def list_projects() -> list[dict[str, Any]]:
    """List all saved projects."""
    projects: list[dict[str, Any]] = []
    lib = _lib_dir()

    for d in sorted(lib.iterdir()):
        if not d.is_dir():
            continue
        avc_path = d / "project.avc"
        if not avc_path.exists():
            continue
        try:
            data = _load_avc(avc_path)
            projects.append(_project_meta(d.name, data, d / "scripts"))
        except (OSError, ValueError, KeyError):  # File read, JSON parse, or data shape errors
            projects.append({
                "id": d.name, "name": d.name, "description": "",
                "device_count": 0, "page_count": 0, "macro_count": 0,
                "script_count": 0, "created": "", "modified": "",
            })

    return projects


def get_project(project_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Get a saved project's data and scripts.

    Returns (project_config_dict, {filename: source_code}).
    Raises FileNotFoundError if not found.
    """
    sid = sanitize_id(project_id)
    avc_path = _lib_dir() / sid / "project.avc"
    if not avc_path.exists():
        raise FileNotFoundError(f"Project '{project_id}' not found in library")
    data = _load_avc(avc_path)
    scripts = _read_scripts_dir(_lib_dir() / sid / "scripts")
    return data, scripts


def save_to_library(
    project_id: str,
    project: ProjectConfig,
    scripts_dir: Path,
    name: str,
    description: str,
) -> None:
    """Save the current project to the library."""
    sid = sanitize_id(project_id)
    project_dir = _lib_dir() / sid
    if project_dir.exists() and (project_dir / "project.avc").exists():
        raise ValueError(f"Project '{sid}' already exists in library")

    project_dir.mkdir(parents=True, exist_ok=True)

    data = project.model_dump(mode="json")
    now = datetime.now().isoformat()
    data["project"]["id"] = sid
    data["project"]["name"] = name
    data["project"]["description"] = description
    data["project"]["created"] = now
    data["project"]["modified"] = now

    avc_path = project_dir / "project.avc"
    _atomic_write_text(avc_path, json.dumps(data, indent=4, ensure_ascii=False))

    if scripts_dir.exists():
        dest_scripts = project_dir / "scripts"
        dest_scripts.mkdir(exist_ok=True)
        for f in scripts_dir.glob("*.py"):
            shutil.copy2(f, dest_scripts / f.name)

    log.info(f"Saved project '{sid}' to library")


def delete_project(project_id: str) -> bool:
    """Delete a project from the library. Returns True if deleted."""
    sid = sanitize_id(project_id)
    project_dir = _lib_dir() / sid
    if not project_dir.exists():
        return False
    shutil.rmtree(project_dir)
    log.info(f"Deleted project '{sid}' from library")
    return True


def update_project_meta(project_id: str, name: str | None, description: str | None) -> None:
    """Update a saved project's name and/or description."""
    sid = sanitize_id(project_id)
    avc_path = _lib_dir() / sid / "project.avc"
    if not avc_path.exists():
        raise FileNotFoundError(f"Project '{project_id}' not found in library")

    data = _load_avc(avc_path)
    if name is not None:
        data["project"]["name"] = name
    if description is not None:
        data["project"]["description"] = description
    data["project"]["modified"] = datetime.now().isoformat()

    _atomic_write_text(avc_path, json.dumps(data, indent=4, ensure_ascii=False))
    log.info(f"Updated project '{sid}' metadata")


def duplicate_project(source_id: str, new_id: str, new_name: str) -> None:
    """Duplicate a saved project."""
    new_sid = sanitize_id(new_id)
    if (_lib_dir() / new_sid / "project.avc").exists():
        raise ValueError(f"Project '{new_sid}' already exists in library")

    data, scripts = get_project(source_id)

    now = datetime.now().isoformat()
    data["project"]["id"] = new_sid
    data["project"]["name"] = new_name
    data["project"]["created"] = now
    data["project"]["modified"] = now

    project_dir = _lib_dir() / new_sid
    project_dir.mkdir(parents=True, exist_ok=True)

    avc_path = project_dir / "project.avc"
    _atomic_write_text(avc_path, json.dumps(data, indent=4, ensure_ascii=False))

    if scripts:
        dest_scripts = project_dir / "scripts"
        dest_scripts.mkdir(exist_ok=True)
        for fname, source_code in scripts.items():
            (dest_scripts / fname).write_text(source_code, encoding="utf-8")

    log.info(f"Duplicated project '{source_id}' -> '{new_sid}'")


def create_blank_project(project_id: str, project_name: str) -> ProjectConfig:
    """Create a minimal empty project."""
    now = datetime.now().isoformat()
    return ProjectConfig(
        openavc_version="0.4.0",
        project=ProjectMeta(
            id=project_id,
            name=project_name,
            description="",
            created=now,
            modified=now,
        ),
        devices=[],
        variables=[],
        macros=[],
        ui=UIConfig(
            settings=UISettings(),
            pages=[
                UIPage(id="main", name="Main", grid=GridConfig(), elements=[]),
            ],
        ),
        scripts=[],
        isc=ISCConfig(),
    )


def open_from_library(
    project_id: str,
    project_path: Path,
    scripts_dir: Path,
    new_project_id: str,
    new_project_name: str,
) -> ProjectConfig:
    """Load a saved project as the active project.

    Backs up the existing project, clears scripts, copies library scripts.
    If the project has a bundle.zip with drivers/plugins, installs them.
    Returns the new ProjectConfig (caller should reload the engine).
    """
    data, scripts = get_project(project_id)

    # Install bundled drivers/plugins from the original zip if present
    _install_bundled_from_library(project_id)

    now = datetime.now().isoformat()
    data["project"]["id"] = new_project_id
    data["project"]["name"] = new_project_name
    data["project"]["created"] = now
    data["project"]["modified"] = now

    project = ProjectConfig(**data)
    save_project(project_path, project)

    replace_scripts(scripts_dir, scripts)

    log.info(f"Opened project '{project_id}' as '{new_project_name}'")
    return project


def _install_bundled_from_library(project_id: str) -> None:
    """Install drivers and plugins from a project's bundle.zip if it exists."""
    sid = sanitize_id(project_id)
    bundle_path = _lib_dir() / sid / "bundle.zip"
    if not bundle_path.exists():
        return

    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            installed_drivers = _install_bundled_drivers(zf)
            installed_plugins = _install_bundled_plugins(zf)
            if installed_drivers:
                log.info("Installed bundled drivers for '%s': %s",
                         project_id, ", ".join(installed_drivers))
            if installed_plugins:
                log.info("Installed bundled plugins for '%s': %s",
                         project_id, ", ".join(installed_plugins))
    except Exception as e:
        log.warning("Failed to install bundled drivers/plugins for '%s': %s",
                    project_id, e)


def replace_scripts(scripts_dir: Path, scripts: dict[str, str]) -> None:
    """Clear existing .py files from scripts dir, write new ones."""
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for f in scripts_dir.glob("*.py"):
        f.unlink()
    for fname, source in scripts.items():
        (scripts_dir / fname).write_text(source, encoding="utf-8")


def _find_driver_files(driver_deps: list[dict]) -> list[tuple[str, Path]]:
    """Find driver files on disk for non-builtin drivers.

    Returns list of (filename, filepath) tuples.
    """
    driver_repo = _DRIVER_REPO_DIR
    if not driver_repo.exists():
        return []

    files = []
    for dep in driver_deps:
        if dep.get("source") == "builtin":
            continue
        driver_id = dep.get("driver_id", "")
        # Search driver_repo for matching files
        for ext in ("*.avcdriver", "*.py"):
            for f in driver_repo.glob(ext):
                if f.name.startswith("_"):
                    continue
                if f.stem == driver_id or f.stem.replace("-", "_") == driver_id:
                    files.append((f.name, f))
                    break
    return files


def _find_plugin_files(plugin_deps: list[dict]) -> list[tuple[str, Path]]:
    """Find plugin directories on disk for non-builtin plugins.

    Returns list of (archive_path, filepath) tuples for all plugin files.
    """
    plugin_repo = _PLUGIN_REPO_DIR
    if not plugin_repo.exists():
        return []

    files = []
    for dep in plugin_deps:
        plugin_id = dep.get("plugin_id", "")
        plugin_dir = plugin_repo / plugin_id
        if not plugin_dir.is_dir():
            continue
        # Bundle all files in the plugin directory
        for f in plugin_dir.rglob("*"):
            if f.is_file() and not f.name.startswith("."):
                rel = f.relative_to(plugin_repo)
                files.append((f"plugins/{rel.as_posix()}", f))
    return files


def export_project(project_id: str) -> tuple[bytes, str, str]:
    """Export a saved project as a .zip bundle.

    Returns (content_bytes, filename, content_type).
    Always produces a .zip containing project.avc, scripts/, drivers/, and plugins/.
    """
    data, scripts = get_project(project_id)
    sid = sanitize_id(project_id)

    # Find non-builtin driver files to bundle
    driver_deps = data.get("driver_dependencies", [])
    driver_files = _find_driver_files(driver_deps)

    # Find plugin files to bundle
    plugin_deps = data.get("plugin_dependencies", [])
    plugin_files = _find_plugin_files(plugin_deps)

    # Find asset files to bundle
    project_dir = _lib_dir() / sid
    assets_dir = project_dir / "assets"
    asset_files: list[tuple[str, Path]] = []
    if assets_dir.exists():
        for f in assets_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(assets_dir)
                asset_files.append((f"assets/{rel.as_posix()}", f))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.avc", json.dumps(data, indent=4, ensure_ascii=False))
        for fname, source in scripts.items():
            zf.writestr(f"scripts/{fname}", source)
        for fname, fpath in driver_files:
            zf.writestr(f"drivers/{fname}", fpath.read_text(encoding="utf-8"))
        for archive_path, fpath in plugin_files:
            zf.writestr(archive_path, fpath.read_bytes())
        for archive_path, fpath in asset_files:
            zf.writestr(archive_path, fpath.read_bytes())
    return buf.getvalue(), f"{sid}.zip", "application/zip"


def import_project(
    file_content: bytes, filename: str, override_id: str | None = None
) -> dict[str, Any]:
    """Import a .avc or .zip file into the library.

    Returns dict with: id, installed_drivers, missing_drivers, warnings.
    """
    if len(file_content) > _MAX_IMPORT_SIZE:
        raise ValueError("File too large (max 10 MB)")

    if filename.endswith(".zip"):
        return _import_zip(file_content, override_id)
    elif filename.endswith((".avc", ".json")):
        return _import_avc(file_content, override_id)
    else:
        raise ValueError("File must be .avc, .json, or .zip")


def _check_missing_drivers(data: dict) -> list[dict[str, Any]]:
    """Check which drivers are missing for a project's devices."""
    from server.core.device_manager import _DRIVER_REGISTRY

    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for device in data.get("devices", []):
        driver_id = device.get("driver", "")
        if driver_id in seen or driver_id in _DRIVER_REGISTRY:
            continue
        seen.add(driver_id)
        affected = [d.get("id", "") for d in data.get("devices", []) if d.get("driver") == driver_id]
        # Try to find the name from driver_dependencies
        dep_name = ""
        for dep in data.get("driver_dependencies", []):
            if dep.get("driver_id") == driver_id:
                dep_name = dep.get("driver_name", "")
                break
        missing.append({
            "driver_id": driver_id,
            "driver_name": dep_name,
            "affected_devices": affected,
        })
    return missing


def _import_avc(content: bytes, override_id: str | None) -> dict[str, Any]:
    from server.core.project_migration import migrate_project

    data = json.loads(content.decode("utf-8"))
    data, _ = migrate_project(data)
    ProjectConfig(**data)  # validate

    project = data.get("project", {})
    pid = sanitize_id(override_id or project.get("id", "imported"))

    if (_lib_dir() / pid / "project.avc").exists():
        raise ValueError(f"Project '{pid}' already exists in library")

    project_dir = _lib_dir() / pid
    project_dir.mkdir(parents=True, exist_ok=True)

    data["project"]["modified"] = datetime.now().isoformat()
    (project_dir / "project.avc").write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
    )

    missing = _check_missing_drivers(data)
    warnings = []
    for m in missing:
        names = ", ".join(m["affected_devices"])
        warnings.append(f"Driver '{m['driver_id']}' is not installed (used by: {names})")

    log.info(f"Imported project '{pid}' from .avc file")
    return {
        "id": pid,
        "installed_drivers": [],
        "missing_drivers": missing,
        "warnings": warnings,
    }


def _install_bundled_drivers(zf: zipfile.ZipFile) -> list[str]:
    """Install driver files bundled in a zip's drivers/ directory.

    Returns list of installed driver IDs.
    """
    from server.drivers.driver_loader import (
        load_driver_file,
        load_python_driver_file,
    )
    from server.core.device_manager import register_driver

    driver_repo = _DRIVER_REPO_DIR
    driver_repo.mkdir(exist_ok=True)
    installed: list[str] = []

    for name in zf.namelist():
        if not name.startswith("drivers/"):
            continue
        fname = Path(name).name
        if not fname or fname.startswith("_"):
            continue

        dest = driver_repo / fname
        if dest.exists():
            continue  # Don't overwrite existing drivers

        content = zf.read(name)
        dest.write_bytes(content)
        log.info(f"Installed bundled driver: {fname}")

        # Register the driver immediately
        try:
            if fname.endswith(".avcdriver"):
                driver_def = load_driver_file(dest)
                if driver_def:
                    from server.drivers.configurable import create_configurable_driver_class
                    driver_class = create_configurable_driver_class(driver_def)
                    register_driver(driver_class)
                    installed.append(driver_def.get("id", fname))
            elif fname.endswith(".py"):
                driver_class = load_python_driver_file(dest)
                if driver_class:
                    register_driver(driver_class)
                    installed.append(driver_class.DRIVER_INFO.get("id", fname))
        except Exception as e:  # Catch-all: driver loading can execute arbitrary Python
            log.warning(f"Could not register bundled driver {fname}: {e}")

    return installed


def _install_bundled_plugins(zf: zipfile.ZipFile) -> list[str]:
    """Install plugin files bundled in a zip's plugins/ directory.

    Returns list of installed plugin IDs.
    """

    plugin_repo = _PLUGIN_REPO_DIR
    plugin_repo.mkdir(exist_ok=True)
    installed: list[str] = []

    # Collect plugin directories from the zip
    plugin_dirs: set[str] = set()
    for name in zf.namelist():
        if not name.startswith("plugins/"):
            continue
        parts = name.split("/")
        if len(parts) >= 2 and parts[1]:
            plugin_dirs.add(parts[1])

    for plugin_id in sorted(plugin_dirs):
        dest_dir = plugin_repo / plugin_id
        if dest_dir.exists():
            continue  # Don't overwrite existing plugins

        # Extract all files for this plugin
        dest_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"plugins/{plugin_id}/"
        for name in zf.namelist():
            if not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel:
                continue
            dest_file = dest_dir / rel
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_bytes(zf.read(name))

        installed.append(plugin_id)
        log.info(f"Installed bundled plugin: {plugin_id}")

    return installed


def _check_missing_plugins(data: dict) -> list[str]:
    """Check which plugins referenced in the project are not installed."""
    from server.core.plugin_loader import get_plugin_registry

    registry = get_plugin_registry()
    missing = []
    for plugin_id in data.get("plugins", {}):
        if plugin_id not in registry:
            missing.append(plugin_id)
    return missing


def _import_zip(content: bytes, override_id: str | None) -> dict[str, Any]:
    from server.core.project_migration import migrate_project

    buf = io.BytesIO(content)
    with zipfile.ZipFile(buf, "r") as zf:
        avc_names = [n for n in zf.namelist() if n.endswith(".avc")]
        if not avc_names:
            raise ValueError("No .avc file found in zip")

        data = json.loads(zf.read(avc_names[0]).decode("utf-8"))
        data, _ = migrate_project(data)
        ProjectConfig(**data)  # validate

        project = data.get("project", {})
        pid = sanitize_id(override_id or project.get("id", "imported"))

        if (_lib_dir() / pid / "project.avc").exists():
            raise ValueError(f"Project '{pid}' already exists in library")

        # Install bundled drivers first
        installed_drivers = _install_bundled_drivers(zf)

        # Install bundled plugins
        installed_plugins = _install_bundled_plugins(zf)

        project_dir = _lib_dir() / pid
        project_dir.mkdir(parents=True, exist_ok=True)

        data["project"]["modified"] = datetime.now().isoformat()
        (project_dir / "project.avc").write_text(
            json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8"
        )

        for name in zf.namelist():
            if name.startswith("scripts/") and name.endswith(".py"):
                script_name = Path(name).name
                if "/" not in script_name and "\\" not in script_name:
                    dest = project_dir / "scripts"
                    dest.mkdir(exist_ok=True)
                    (dest / script_name).write_bytes(zf.read(name))

        # Extract bundled assets
        for name in zf.namelist():
            if name.startswith("assets/") and not name.endswith("/"):
                asset_name = Path(name).name
                if asset_name and not asset_name.startswith("."):
                    assets_dest = project_dir / "assets"
                    assets_dest.mkdir(exist_ok=True)
                    (assets_dest / asset_name).write_bytes(zf.read(name))

    # Check for still-missing drivers after installing bundled ones
    missing = _check_missing_drivers(data)
    warnings = []
    for m in missing:
        names = ", ".join(m["affected_devices"])
        warnings.append(f"Driver '{m['driver_id']}' is not installed (used by: {names})")

    # Check for missing plugins
    missing_plugins = _check_missing_plugins(data)
    for mp in missing_plugins:
        warnings.append(
            f"Plugin '{mp}' is not installed and was not bundled in the export. "
            f"Install it from the community repository."
        )

    log.info(f"Imported project '{pid}' from .zip bundle")
    return {
        "id": pid,
        "installed_drivers": installed_drivers,
        "installed_plugins": installed_plugins,
        "missing_drivers": missing,
        "missing_plugins": missing_plugins,
        "warnings": warnings,
    }
