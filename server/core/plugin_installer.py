"""
Plugin installer — download, install, update, and uninstall community plugins.

Mirrors the driver install system: fetches index.json from the community
repository, downloads plugin files, manages pip dependencies, handles
install/uninstall lifecycle.
"""

import ctypes.util
import os
import platform as platform_mod
import re
import shutil
import subprocess
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx

from server.core.plugin_loader import (
    _PLUGIN_CLASS_REGISTRY,
    register_plugin_class,
    unregister_plugin_class,
)
from server.utils.logger import get_logger

log = get_logger(__name__)

# Community plugin repository URL
COMMUNITY_REPO_URL = (
    "https://raw.githubusercontent.com/open-avc/openavc-plugins/main"
)

# Plugin repo directory
PLUGIN_REPO_DIR = Path(__file__).resolve().parent.parent.parent / "plugin_repo"


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    return re.sub(r"[^a-zA-Z0-9_\-.]", "", name)


# ──── Community Index Cache ────


class CommunityPluginCache:
    """Cached fetch of the community plugin index.json."""

    def __init__(self, ttl: float = 600.0):
        self._ttl = ttl
        self._data: list[dict[str, Any]] = []
        self._last_fetch: float = 0
        self._error: str | None = None

    async def get(self, force: bool = False) -> tuple[list[dict[str, Any]], str | None]:
        """Return (plugins_list, error_or_none). Never raises."""
        now = time.monotonic()
        if not force and self._data and (now - self._last_fetch) < self._ttl:
            return self._data, None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{COMMUNITY_REPO_URL}/index.json")
                resp.raise_for_status()
                data = resp.json()
                self._data = data.get("plugins", [])
                self._last_fetch = now
                self._error = None
                return self._data, None
        except (httpx.HTTPError, OSError, ValueError, KeyError) as e:
            # HTTPError: status/transport errors; OSError: network; ValueError/KeyError: JSON
            self._error = str(e)
            log.warning(f"Failed to fetch community plugin index: {e}")
            return self._data, self._error


_cache = CommunityPluginCache()


async def get_community_plugins(force: bool = False) -> tuple[list[dict], str | None]:
    """Get the community plugin catalog. Returns (plugins, error)."""
    return await _cache.get(force=force)


# ──── Install ────


async def install_plugin(plugin_id: str, file_url: str) -> dict[str, Any]:
    """
    Download and install a plugin from the community repository.

    Args:
        plugin_id: The plugin identifier.
        file_url: Full URL to the plugin file or directory zip.

    Returns:
        {"status": "installed", "plugin_id": plugin_id}
    """
    PLUGIN_REPO_DIR.mkdir(parents=True, exist_ok=True)
    plugin_dir = PLUGIN_REPO_DIR / plugin_id

    if plugin_dir.exists():
        raise ValueError(f"Plugin '{plugin_id}' is already installed")

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            content = resp.content

        # Determine if it's a single .py file or a zip/directory structure
        if file_url.endswith(".py"):
            # Single file plugin
            plugin_dir.mkdir(parents=True, exist_ok=True)
            filename = _sanitize_filename(Path(file_url).name)
            (plugin_dir / filename).write_bytes(content)
            log.info(f"Installed plugin '{plugin_id}' from {filename}")

        elif file_url.endswith(".zip"):
            # Zip archive
            plugin_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(BytesIO(content)) as zf:
                for name in zf.namelist():
                    # Strip leading directory if present
                    parts = name.split("/", 1)
                    if len(parts) > 1:
                        target = plugin_dir / parts[1]
                    else:
                        target = plugin_dir / name
                    if name.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(name))
            log.info(f"Installed plugin '{plugin_id}' from zip archive")

        else:
            # Assume single file, use plugin_id as dir name
            plugin_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{plugin_id}_plugin.py"
            (plugin_dir / filename).write_bytes(content)
            log.info(f"Installed plugin '{plugin_id}'")

        # Install pip dependencies if we can find them
        await _install_pip_deps(plugin_id, plugin_dir)

        # Install native dependencies (e.g. hidapi.dll for Stream Deck)
        await _install_native_deps(plugin_id, plugin_dir)

        # Try to register the plugin class immediately
        _register_installed_plugin(plugin_id, plugin_dir)

        return {"status": "installed", "plugin_id": plugin_id}

    except Exception:  # Catch-all: ensures cleanup of partial install before re-raising
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)
        raise


async def _install_pip_deps(plugin_id: str, plugin_dir: Path) -> None:
    """Install pip dependencies for a plugin into plugin_repo/.deps/."""
    # Try to find PLUGIN_INFO to get dependencies
    deps: list[str] = []

    for py_file in plugin_dir.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            if '"dependencies"' in content or "'dependencies'" in content:
                # Quick parse: look for dependencies list
                import ast
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Dict):
                        for key, value in zip(node.keys, node.values):
                            if (isinstance(key, ast.Constant) and
                                    key.value == "dependencies" and
                                    isinstance(value, ast.List)):
                                for elt in value.elts:
                                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                        deps.append(elt.value)
        except (SyntaxError, ValueError, OSError):
            pass  # AST parse errors, literal_eval errors, or file read errors

    if not deps:
        return

    deps_dir = PLUGIN_REPO_DIR / ".deps"
    deps_dir.mkdir(exist_ok=True)

    log.info(f"Installing pip dependencies for '{plugin_id}': {deps}")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--target", str(deps_dir)]
            + deps,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"pip install failed for '{plugin_id}': {e.stderr}")
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning(f"Could not install deps for '{plugin_id}': {e}")


async def _install_native_deps(plugin_id: str, plugin_dir: Path) -> None:
    """Check and install native dependencies declared in PLUGIN_INFO."""
    # Parse native_dependencies from the plugin source
    native_deps = _parse_native_deps(plugin_dir)
    if not native_deps:
        return

    from server.core.plugin_loader import get_platform_id

    current_platform = get_platform_id()
    deps_dir = PLUGIN_REPO_DIR / ".deps"
    deps_dir.mkdir(exist_ok=True)

    for dep in native_deps:
        dep_id = dep.get("id", "unknown")
        dep_name = dep.get("name", dep_id)

        # Check if the platform has install info for this dep
        platforms = dep.get("platforms", {})
        platform_key = current_platform
        if platform_key not in platforms:
            if not dep.get("required", False):
                continue
            log.warning(
                f"Native dep '{dep_name}' for plugin '{plugin_id}' has no install "
                f"info for platform '{current_platform}'"
            )
            continue

        # Check if already installed
        if _check_native_dep(dep):
            log.debug(f"Native dep '{dep_name}' already available")
            continue

        platform_info = platforms[platform_key]
        log.info(f"Installing native dep '{dep_name}' for plugin '{plugin_id}'")

        try:
            if platform_info.get("type") == "zip":
                await _install_native_dep_zip(dep_name, platform_info, deps_dir)
            elif platform_info.get("install_cmd"):
                _install_native_dep_command(dep_name, platform_info)
            else:
                log.warning(f"No install method for native dep '{dep_name}'")
        except (OSError, ValueError, httpx.HTTPError) as e:
            log.warning(f"Could not install native dep '{dep_name}': {e}")
            if dep.get("required", False):
                log.error(
                    f"Required native dep '{dep_name}' could not be installed. "
                    f"The plugin may not work. See plugin README for manual install steps."
                )

    # After installing native deps, inject .deps/ into DLL search paths
    # immediately (scan_plugins does this at startup, but runtime installs
    # need it too).
    _inject_native_lib_paths(deps_dir)


def _inject_native_lib_paths(deps_dir: Path) -> None:
    """Add .deps/ to DLL/shared-library search paths for the current process."""
    deps_str = str(deps_dir)
    system = platform_mod.system().lower()

    if system == "windows":
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(deps_str)
            if deps_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = deps_str + os.pathsep + os.environ.get("PATH", "")
            log.debug(f"Injected {deps_str} into Windows DLL search paths")
        except OSError as e:
            log.debug(f"Could not inject DLL paths: {e}")

    elif system == "linux":
        current_ld = os.environ.get("LD_LIBRARY_PATH", "")
        if deps_str not in current_ld:
            os.environ["LD_LIBRARY_PATH"] = deps_str + (":" + current_ld if current_ld else "")
            log.debug(f"Injected {deps_str} into LD_LIBRARY_PATH")


def _check_native_dep(dep: dict) -> bool:
    """Check if a native dependency is already available."""
    check = dep.get("check", {})
    check_type = check.get("type", "")

    if check_type == "env_var":
        return bool(os.environ.get(check.get("key", ""), ""))

    elif check_type == "file_exists":
        return os.path.exists(check.get("path", ""))

    elif check_type == "library_load":
        # Check if the library can be found by ctypes
        system = platform_mod.system()
        names = check.get("names", {})
        lib_name = names.get(system, "")
        if not lib_name:
            return False
        # Check in .deps first
        deps_dir = PLUGIN_REPO_DIR / ".deps"
        if (deps_dir / lib_name).exists():
            return True
        # Check system paths
        base = os.path.splitext(lib_name)[0]
        return ctypes.util.find_library(base) is not None

    return False


async def _install_native_dep_zip(
    dep_name: str, platform_info: dict, deps_dir: Path
) -> None:
    """Download a zip and extract the specified file to .deps/."""
    url = platform_info.get("url", "")
    extract_path = platform_info.get("extract", "")
    if not url or not extract_path:
        raise ValueError(f"Missing url or extract path for '{dep_name}'")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        # Extract just the specified file
        target_filename = Path(extract_path).name
        try:
            data = zf.read(extract_path)
            target = deps_dir / target_filename
            target.write_bytes(data)
            log.info(f"Extracted {target_filename} ({len(data)} bytes) to {deps_dir}")
        except KeyError:
            raise ValueError(
                f"File '{extract_path}' not found in zip for '{dep_name}'"
            )


def _install_native_dep_command(dep_name: str, platform_info: dict) -> None:
    """Run a system command to install a native dependency."""
    cmd = platform_info.get("install_cmd", "")
    if not cmd:
        return

    # Split command string into list for safe execution (no shell injection)
    import shlex
    try:
        cmd_list = shlex.split(cmd)
    except ValueError as e:
        log.warning(f"Invalid install command for '{dep_name}': {e}")
        return

    log.info(f"Running: {cmd_list}")
    try:
        result = subprocess.run(
            cmd_list, shell=False, capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            log.info(f"Installed native dep '{dep_name}' via system command")
        else:
            log.warning(
                f"Command failed for '{dep_name}' (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning(f"Could not run install command for '{dep_name}': {e}")


def _parse_native_deps(plugin_dir: Path) -> list[dict]:
    """Parse native_dependencies from a plugin's source files."""
    import ast

    for py_file in plugin_dir.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            if "native_dependencies" not in content:
                continue
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if (
                            isinstance(key, ast.Constant)
                            and key.value == "native_dependencies"
                            and isinstance(value, ast.List)
                        ):
                            # Evaluate the list literal
                            return ast.literal_eval(value)
        except (SyntaxError, ValueError, OSError):
            pass  # AST parse errors, literal_eval errors, or file read errors
    return []


def _register_installed_plugin(plugin_id: str, plugin_dir: Path) -> bool:
    """Try to import and register a newly installed plugin."""
    import importlib.util

    # Add .deps to sys.path
    deps_path = str(PLUGIN_REPO_DIR / ".deps")
    if os.path.isdir(deps_path) and deps_path not in sys.path:
        sys.path.insert(0, deps_path)

    # Look for plugin file
    candidates = [
        plugin_dir / "__init__.py",
        plugin_dir / f"{plugin_id}_plugin.py",
    ]
    candidates.extend(sorted(plugin_dir.glob("*.py")))

    for filepath in candidates:
        if not filepath.exists() or filepath.name.startswith("_"):
            if filepath.name != "__init__.py":
                continue

        try:
            dir_str = str(plugin_dir)
            if dir_str not in sys.path:
                sys.path.insert(0, dir_str)

            spec = importlib.util.spec_from_file_location(
                f"plugin_{plugin_id}", filepath
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        hasattr(attr, "PLUGIN_INFO") and
                        isinstance(attr.PLUGIN_INFO, dict) and
                        attr.PLUGIN_INFO.get("id") == plugin_id):
                    register_plugin_class(attr)
                    log.info(f"Registered plugin class '{plugin_id}' from {filepath.name}")
                    return True

            if dir_str in sys.path:
                sys.path.remove(dir_str)

        except Exception as e:  # Catch-all: exec_module runs arbitrary plugin code
            log.debug(f"Could not load {filepath.name}: {e}")

    return False


# ──── Uninstall ────


async def uninstall_plugin(plugin_id: str, project_plugins: dict | None = None) -> dict[str, Any]:
    """
    Uninstall a plugin. Checks that it's not in use by the current project.

    Args:
        plugin_id: Plugin to uninstall.
        project_plugins: Current project's plugins dict (for safety check).
    """
    plugin_dir = PLUGIN_REPO_DIR / plugin_id

    if not plugin_dir.exists():
        raise ValueError(f"Plugin '{plugin_id}' is not installed")

    # Safety check: is the plugin enabled in the current project?
    if project_plugins and plugin_id in project_plugins:
        entry = project_plugins[plugin_id]
        enabled = entry.enabled if hasattr(entry, "enabled") else entry.get("enabled", False)
        if enabled:
            raise ValueError(
                f"Plugin '{plugin_id}' is currently enabled in the project. "
                f"Disable it before uninstalling."
            )

    # Remove files
    shutil.rmtree(plugin_dir, ignore_errors=True)
    unregister_plugin_class(plugin_id)
    log.info(f"Uninstalled plugin '{plugin_id}'")

    return {"status": "uninstalled", "plugin_id": plugin_id}


# ──── Update ────


async def update_plugin(plugin_id: str, file_url: str) -> dict[str, Any]:
    """
    Update a plugin by removing the old version and installing the new one.
    """
    plugin_dir = PLUGIN_REPO_DIR / plugin_id

    if not plugin_dir.exists():
        raise ValueError(f"Plugin '{plugin_id}' is not installed")

    # Remove old version
    shutil.rmtree(plugin_dir, ignore_errors=True)
    unregister_plugin_class(plugin_id)

    # Install new version
    return await install_plugin(plugin_id, file_url)


# ──── List Installed ────


def list_installed_plugins() -> list[dict[str, Any]]:
    """List all plugins installed in plugin_repo/."""
    if not PLUGIN_REPO_DIR.is_dir():
        return []

    installed = []
    for entry in sorted(PLUGIN_REPO_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(entry.name)
        if plugin_class:
            info = plugin_class.PLUGIN_INFO
            installed.append({
                "id": entry.name,
                "name": info.get("name", entry.name),
                "version": info.get("version", ""),
                "source": "community",
            })
        else:
            installed.append({
                "id": entry.name,
                "name": entry.name,
                "version": "",
                "source": "community",
            })

    return installed
