"""
Plugin installer — download, install, update, and uninstall community plugins.

Mirrors the driver install system: fetches index.json from the community
repository, downloads plugin files, and handles the install/uninstall
lifecycle.

Dependency *installation* is not here. This module reads what a plugin
declares — the `dependencies` and `native_dependencies` entries in its
PLUGIN_INFO, parsed straight out of the source without importing it — and
hands each list to the module that knows how to install that kind of thing:
`plugin_wheels` for Python requirements, `plugin_native_deps` for system
libraries. Both sit on `plugin_artifacts`, which bounds what any downloaded
byte stream can do to us.
"""

import asyncio
import os
import re
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from openavc.core.plugin_loader import (
    _PLUGIN_CLASS_REGISTRY,
    _exec_plugin_in_package,
    _purge_plugin_modules,
    register_plugin_class,
    unregister_plugin_class,
)
from openavc.core.plugin_artifacts import (
    _DownloadBudget,
    _check_zip_bomb,
    _download_capped,
)
from openavc.core.plugin_native_deps import install_native_deps
from openavc.core.plugin_wheels import install_requirements
from openavc.system_config import PLUGIN_DATA_DIR, PLUGIN_REPO_DIR
from openavc.utils.community_integrity import (
    PLUGINS_OWNER_REPO,
    ArtifactHashes,
    CommunityArtifactError,
    catalog_relpath,
    validate_catalog_url,
)
from openavc.utils.logger import get_logger
from openavc.utils.paths import safe_path_within

log = get_logger(__name__)

# Community plugin repository URLs
COMMUNITY_REPO_URL = (
    "https://raw.githubusercontent.com/open-avc/openavc-plugins/main"
)
COMMUNITY_API_URL = (
    "https://api.github.com/repos/open-avc/openavc-plugins/contents"
)


def _sanitize_filename(name: str) -> str:
    """Remove unsafe characters from a filename."""
    return re.sub(r"[^a-zA-Z0-9_\-.]", "", name)



# Official community plugin catalog. Plugin *code* must come from this exact
# repo — a hostname-only "is it GitHub?" check gives false assurance: any
# attacker-controlled GitHub repo passes it, yet plugin code runs in-process.
# The per-host path rules live with the shared validator.
_CATALOG_OWNER_REPO = PLUGINS_OWNER_REPO


def _validate_catalog_url(url: str) -> None:
    """Reject a plugin URL that isn't under the official community catalog repo.

    A hostname allowlist alone gives false assurance of a trusted source: any
    attacker-controlled GitHub repo passes a ``*.githubusercontent.com`` check,
    yet plugin code is executed in-process. So we require the URL to point at
    the curated, human-reviewed catalog repo (``open-avc/openavc-plugins``),
    not merely "some GitHub URL". https-only.

    The rule itself lives in ``server.utils.community_integrity`` so drivers —
    which are equally arbitrary in-process code — are pinned by the same one.
    ``ValueError`` is preserved for callers that already handle it.
    """
    try:
        validate_catalog_url(url, owner_repo=_CATALOG_OWNER_REPO)
    except CommunityArtifactError as e:
        raise ValueError(str(e).replace("Community artifact URL", "Plugin URL"))



def _is_safe_entry_name(name: str) -> bool:
    """True if a GitHub Contents API entry name is a plain filename.

    The API returns basenames, so anything with a path separator, a '.'/'..'
    component, or a NUL is anomalous (a tampered/MITM'd listing) and is skipped
    before it can redirect a write outside the plugin directory.
    """
    return bool(name) and name not in (".", "..") and not (
        "/" in name or "\\" in name or "\x00" in name
    )




# Per-plugin async lock serializing install/update/uninstall of the SAME id so
# two concurrent requests can't interleave dir creation, dep installs, and the
# failure-cleanup rmtree (which could otherwise wipe a sibling's in-progress
# install). Created lazily; the dict get/set is await-free so the event loop
# can't switch mid-check (no guard lock needed).
_plugin_op_locks: dict[str, asyncio.Lock] = {}


def _get_plugin_lock(plugin_id: str) -> asyncio.Lock:
    lock = _plugin_op_locks.get(plugin_id)
    if lock is None:
        lock = asyncio.Lock()
        _plugin_op_locks[plugin_id] = lock
    return lock


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


async def _fetch_plugin_hashes(plugin_id: str) -> ArtifactHashes:
    """The catalog's per-file hashes for one plugin, read server-side.

    A plugin installs as a directory whose file list comes from the GitHub
    Contents API — attacker-supplied in the threat model this defends against —
    so a per-file manifest is the only form of hash that means anything here.
    Fetched fresh per install rather than cached: it is one small request beside
    a multi-file download, and a stale copy would reject a legitimately updated
    plugin.
    """
    files = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{COMMUNITY_REPO_URL}/manifest.json")
            resp.raise_for_status()
            data = resp.json()
        entry = (data.get("plugins") or {}).get(plugin_id)
        if isinstance(entry, dict) and isinstance(entry.get("files"), dict):
            files = entry["files"]
    except (httpx.HTTPError, OSError, ValueError, AttributeError) as e:
        # Treated as "no hashes published", the same as a catalog that predates
        # the manifest. See community_integrity's module docstring: this control
        # rides the same channel as the artifact, so a fetch an attacker could
        # suppress is one they could also rewrite.
        log.warning("Could not read the plugin manifest for '%s': %s", plugin_id, e)

    return ArtifactHashes(
        f"Plugin '{plugin_id}'", files, source="the community plugin manifest"
    )


_SAFE_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _validate_plugin_id(plugin_id: str) -> None:
    if not plugin_id or not _SAFE_ID_RE.match(plugin_id):
        raise ValueError(
            f"Invalid plugin ID '{plugin_id}': must be lowercase letters, "
            "numbers, and underscores only"
        )


# ──── Install ────


def _extract_plugin_zip(content: bytes, plugin_dir: Path) -> None:
    """Extract a plugin zip into plugin_dir (sync; run via to_thread)."""
    with zipfile.ZipFile(BytesIO(content)) as zf:
        _check_zip_bomb(zf, label="Plugin archive")
        for name in zf.namelist():
            parts = name.split("/", 1)
            relative = parts[1] if len(parts) > 1 else name
            target = safe_path_within(plugin_dir, relative)
            if target is None:
                log.warning(f"Skipping zip entry with unsafe path: {name}")
                continue
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))


async def install_plugin(plugin_id: str, file_url: str) -> dict[str, Any]:
    """
    Download and install a plugin from the community repository.

    Args:
        plugin_id: The plugin identifier.
        file_url: Full URL to the plugin file or directory zip.

    Returns:
        {"status": "installed", "plugin_id": plugin_id}
    """
    _validate_plugin_id(plugin_id)
    async with _get_plugin_lock(plugin_id):
        return await _do_install(plugin_id, file_url)


async def _do_install(plugin_id: str, file_url: str) -> dict[str, Any]:
    """Install body, run while holding the plugin lock (see install_plugin /
    update_plugin). Assumes plugin_id is already validated."""
    _validate_catalog_url(file_url)
    PLUGIN_REPO_DIR.mkdir(parents=True, exist_ok=True)
    plugin_dir = PLUGIN_REPO_DIR / plugin_id

    if plugin_dir.exists():
        raise ValueError(f"Plugin '{plugin_id}' is already installed")

    # Fetched before anything is downloaded, so a plugin whose bytes don't match
    # the catalog is refused with nothing yet written to plugin_repo/.
    hashes = await _fetch_plugin_hashes(plugin_id)

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if file_url.endswith(".py"):
                # Single file plugin
                content = await _download_capped(
                    client, file_url, label="plugin file"
                )
                hashes.check(
                    catalog_relpath(file_url, owner_repo=_CATALOG_OWNER_REPO), content
                )
                plugin_dir.mkdir(parents=True, exist_ok=True)
                filename = _sanitize_filename(Path(urlparse(file_url).path).name)
                target = safe_path_within(plugin_dir, filename)
                if target is None:
                    raise ValueError(f"Unsafe plugin filename: {filename!r}")
                target.write_bytes(content)
                log.info(f"Installed plugin '{plugin_id}' from {filename}")

            elif file_url.endswith(".zip"):
                # Zip archive — extract off the event loop. The archive is
                # checked as one file, before extraction: an archive verified
                # afterwards has already written its members to disk.
                content = await _download_capped(
                    client, file_url, label="plugin archive"
                )
                hashes.check(
                    catalog_relpath(file_url, owner_repo=_CATALOG_OWNER_REPO), content
                )
                plugin_dir.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(_extract_plugin_zip, content, plugin_dir)
                log.info(f"Installed plugin '{plugin_id}' from zip archive")

            else:
                # Directory — download all files via GitHub Contents API
                plugin_dir.mkdir(parents=True, exist_ok=True)
                # Extract the path relative to the repo from the raw URL
                repo_path = file_url.replace(COMMUNITY_REPO_URL + "/", "")
                await _download_github_directory(
                    client, repo_path, plugin_dir, hashes=hashes
                )
                # The listing that drove that walk came from the network, so
                # check it delivered the whole manifest and not a subset —
                # per-file hashes say nothing about a file that was dropped.
                hashes.require_complete()
                log.info(f"Installed plugin '{plugin_id}' from directory")

        # Reject the install up front if the plugin needs a newer OpenAVC.
        # Without this, the plugin's pip deps would be installed first and
        # the incompatibility wouldn't surface until enable.
        _check_min_openavc_version(plugin_id, plugin_dir)

        # Install pip dependencies if we can find them
        await _install_pip_deps(plugin_id, plugin_dir)

        # Install native dependencies (e.g. hidapi.dll for Stream Deck)
        await _install_native_deps(plugin_id, plugin_dir)

        # Try to register the plugin class immediately. On failure, write
        # an .install-error sidecar so list_installed_plugins() can surface
        # `status: "load_failed"` to the UI (A60). We don't raise here
        # because the plugin's files ARE on disk — uninstall/update will
        # still work, and the user can read the diagnostic.
        register_error = _register_installed_plugin(plugin_id, plugin_dir)
        sidecar = plugin_dir / ".install-error"
        if register_error:
            try:
                sidecar.write_text(register_error, encoding="utf-8")
            except OSError:
                pass  # Sidecar is best-effort; log warning already emitted
            return {
                "status": "load_failed",
                "plugin_id": plugin_id,
                "error": register_error,
            }
        # Clean up any sidecar from a previous failed install
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

        return {"status": "installed", "plugin_id": plugin_id}

    except Exception:  # Catch-all: ensures cleanup of partial install before re-raising
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir, ignore_errors=True)
        raise


async def _download_github_directory(
    client: httpx.AsyncClient, repo_path: str, dest_dir: Path,
    *, budget: "_DownloadBudget | None" = None, hashes: ArtifactHashes | None = None,
    _depth: int = 0, _max_depth: int = 5,
) -> None:
    """Recursively download a directory from GitHub using the Contents API."""
    if budget is None:
        budget = _DownloadBudget()
    if _depth >= _max_depth:
        log.warning(f"Skipping directory at depth {_depth} (max {_max_depth}): {repo_path}")
        return

    api_url = f"{COMMUNITY_API_URL}/{repo_path}?ref=main"
    # Re-validate: repo_path is built from a (now-validated) file_url and from
    # nested entry names; this catches a '..' that would walk the api path off
    # the catalog repo (defense in depth against a tampered listing).
    _validate_catalog_url(api_url)
    resp = await client.get(api_url)
    resp.raise_for_status()
    entries = resp.json()

    if not isinstance(entries, list):
        raise ValueError(f"Expected directory listing, got: {type(entries)}")

    for entry in entries:
        name = entry.get("name", "")
        entry_type = entry.get("type", "")
        if not _is_safe_entry_name(name):
            log.warning(f"Skipping directory entry with unsafe name: {name!r}")
            continue
        safe_name = _sanitize_filename(name)
        if not safe_name:
            log.warning(f"Skipping file with unsafe name: {name!r}")
            continue
        # is_relative_to guard (parity with the zip path) so a sanitized name
        # can never resolve outside the plugin dir.
        target = safe_path_within(dest_dir, safe_name)
        if target is None:
            log.warning(f"Skipping entry that escapes the plugin dir: {name!r}")
            continue
        if entry_type == "file":
            download_url = entry.get("download_url", "")
            if download_url:
                # The download_url is harvested from a network-controlled JSON
                # body — re-validate it against the catalog before fetching
                # (the top-level file_url check doesn't cover nested URLs).
                _validate_catalog_url(download_url)
                content = await _download_capped(
                    client, download_url, label=f"plugin file {safe_name}"
                )
                budget.add_file(len(content))
                # Checked before the write. The manifest is keyed by the file's
                # real repo path, not the sanitized local name, so a listing
                # that renames a file on the way in cannot find a hash to match.
                if hashes is not None:
                    hashes.check(
                        catalog_relpath(download_url, owner_repo=_CATALOG_OWNER_REPO),
                        content,
                    )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        elif entry_type == "dir":
            target.mkdir(parents=True, exist_ok=True)
            await _download_github_directory(
                client, f"{repo_path}/{name}", target,
                budget=budget, hashes=hashes,
                _depth=_depth + 1, _max_depth=_max_depth,
            )


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

    # plugin_wheels owns the install itself, including the safety gate on these
    # untrusted specifier strings and the frozen-vs-pip strategy choice.
    await install_requirements(deps, deps_dir, plugin_id)


async def _install_native_deps(plugin_id: str, plugin_dir: Path) -> None:
    """Check and install native dependencies declared in PLUGIN_INFO."""
    native_deps = _parse_native_deps(plugin_dir)
    if not native_deps:
        return
    await install_native_deps(plugin_id, native_deps)


def _extract_min_openavc_version(plugin_dir: Path) -> str | None:
    """Find the plugin's declared min_openavc_version without importing it.

    Walks every .py file in plugin_dir, parses the AST, and returns the
    first `"min_openavc_version": "..."` value it finds inside any dict
    literal. The convention is that this lives in PLUGIN_INFO, but the
    scan is shape-tolerant so it also catches near-misses (e.g. devs who
    keep min_openavc_version on the class rather than in the dict).

    Returns None when the plugin file doesn't declare a minimum — those
    plugins install unconditionally.
    """
    import ast

    for py_file in plugin_dir.glob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            if "min_openavc_version" not in content:
                continue
            tree = ast.parse(content)
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and key.value == "min_openavc_version"
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                ):
                    return value.value
    return None


def _check_min_openavc_version(plugin_id: str, plugin_dir: Path) -> None:
    """Raise ValueError if the plugin requires a newer OpenAVC than running.

    Called during install (before pip deps install + class registration)
    so the user doesn't pay for a download that's going to fail to enable
    later anyway. Mirrors the runtime check in PluginLoader.validate_manifest.
    """
    min_version = _extract_min_openavc_version(plugin_dir)
    if not min_version:
        return
    from openavc.version import __version__
    try:
        from packaging.version import Version, InvalidVersion
    except ImportError:
        # packaging is a hard dependency; if it's missing something else
        # is very wrong — fail open rather than block the install.
        return
    try:
        if Version(__version__) < Version(min_version):
            raise ValueError(
                f"Plugin '{plugin_id}' requires OpenAVC v{min_version} or later "
                f"(current: v{__version__}). Upgrade OpenAVC, then reinstall."
            )
    except InvalidVersion:
        # Bad version string in PLUGIN_INFO is a plugin authoring bug,
        # not a runtime blocker. Let it through; validate_manifest will
        # surface it on enable.
        return


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


def _register_installed_plugin(plugin_id: str, plugin_dir: Path) -> str | None:
    """Try to import and register a newly installed plugin.

    Returns None on success, or an error message string on failure. The
    caller (install_plugin) uses the message to surface diagnostics to
    the UI — silently swallowing here would leave the user staring at a
    green "Installed" check with nothing in the Installed tab (A60).

    The plugin file is executed inside a per-plugin package namespace (shared
    with the loader via ``_exec_plugin_in_package``), so a multi-file plugin's
    helper modules import via relative imports and can't collide with another
    plugin's same-named helpers in sys.modules (A388).
    """
    # Add .deps to sys.path. Append (don't insert at 0) so a bundled plugin
    # dependency can't shadow a stdlib or first-party module — .deps holds
    # extra packages plugins need, not overrides of ours.
    deps_path = str(PLUGIN_REPO_DIR / ".deps")
    if os.path.isdir(deps_path) and deps_path not in sys.path:
        sys.path.append(deps_path)

    # Look for plugin file
    candidates = [
        plugin_dir / "__init__.py",
        plugin_dir / f"{plugin_id}_plugin.py",
    ]
    candidates.extend(sorted(plugin_dir.glob("*.py")))

    last_error: str | None = None
    candidates_tried = 0

    for filepath in candidates:
        # Skip files that don't exist on disk. The original code special-
        # cased `__init__.py` to always proceed, which caused a phantom
        # FileNotFoundError to be logged when the plugin shipped without
        # one. Skip outright and let the next candidate try.
        if not filepath.exists():
            continue
        # Skip "private" files (starting with "_") except __init__.py,
        # which is the canonical Python package entry point.
        if filepath.name.startswith("_") and filepath.name != "__init__.py":
            continue

        candidates_tried += 1
        try:
            module = _exec_plugin_in_package(filepath, plugin_dir)
            if module is None:
                continue

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and
                        hasattr(attr, "PLUGIN_INFO") and
                        isinstance(attr.PLUGIN_INFO, dict) and
                        attr.PLUGIN_INFO.get("id") == plugin_id):
                    register_plugin_class(attr)
                    log.info(f"Registered plugin class '{plugin_id}' from {filepath.name}")
                    return None

            # No matching class in this file — drop the modules it loaded so a
            # later candidate (or install) starts clean.
            _purge_plugin_modules(f"plugin_{plugin_dir.name}")

        except Exception as e:  # Catch-all: exec_module runs arbitrary plugin code
            last_error = f"{filepath.name}: {type(e).__name__}: {e}"
            # Bump from debug to warning so failed installs are visible in
            # server logs without a debug flag (A60).
            log.warning(
                f"Plugin '{plugin_id}' failed to load from {filepath.name}: "
                f"{type(e).__name__}: {e}"
            )

    if candidates_tried == 0:
        return f"no plugin module found in {plugin_dir.name}/"
    if last_error:
        return last_error
    return f"no class with PLUGIN_INFO.id == '{plugin_id}' in {plugin_dir.name}/"


# ──── Uninstall ────


async def uninstall_plugin(
    plugin_id: str,
    project_plugins: dict | None = None,
    *,
    remove_data: bool = False,
) -> dict[str, Any]:
    """
    Uninstall a plugin. Checks that it's not in use by the current project.

    Args:
        plugin_id: Plugin to uninstall.
        project_plugins: Current project's plugins dict (for safety check).
        remove_data: If True, also delete the plugin's persistent data
            directory (PLUGIN_DATA_DIR/<plugin_id>). Default False — the
            data dir is kept so a future reinstall of the same plugin can
            pick up cached binaries, downloaded models, etc., without
            re-downloading. Users opt in via the IDE uninstall dialog or
            the REST endpoint's `?remove_data=true` query parameter.
    """
    _validate_plugin_id(plugin_id)
    async with _get_plugin_lock(plugin_id):
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

        # Remove code
        shutil.rmtree(plugin_dir, ignore_errors=True)
        unregister_plugin_class(plugin_id)

        # Optionally remove data
        data_removed = False
        if remove_data:
            data_dir = PLUGIN_DATA_DIR / plugin_id
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
                data_removed = True

        log.info(
            "Uninstalled plugin '%s'%s",
            plugin_id,
            " (data discarded)" if data_removed else "",
        )

        return {"status": "uninstalled", "plugin_id": plugin_id, "data_removed": data_removed}


def get_plugin_data_info(plugin_id: str) -> dict[str, Any]:
    """Report whether a plugin has a persistent data directory and its size.

    Used by the IDE to show a "discard X MB of plugin data?" prompt when
    the user uninstalls a plugin. Returns size 0 with exists=False when
    the plugin has never written to its data dir.
    """
    _validate_plugin_id(plugin_id)
    data_dir = PLUGIN_DATA_DIR / plugin_id

    if not data_dir.exists():
        return {"plugin_id": plugin_id, "exists": False, "size_bytes": 0}

    size = 0
    for entry in data_dir.rglob("*"):
        if entry.is_file():
            try:
                size += entry.stat().st_size
            except OSError:
                pass
    return {"plugin_id": plugin_id, "exists": True, "size_bytes": size}


# ──── Update ────


async def update_plugin(plugin_id: str, file_url: str) -> dict[str, Any]:
    """Update a plugin, rolling back to the working version if the reinstall
    fails.

    Stages the new version: the existing dir is moved aside, the new version is
    installed fresh, and only on success is the backup dropped. A transient
    failure (network/GitHub drop, min-version gate) or a new version that won't
    load restores the old dir and re-registers it instead of leaving the user
    with no plugin at all. Returns ``{"status": "update_failed",
    "rolled_back": True, "error": ...}`` on a rolled-back update; the caller
    (REST endpoint) restarts the restored plugin if it was running.
    """
    _validate_plugin_id(plugin_id)
    async with _get_plugin_lock(plugin_id):
        plugin_dir = PLUGIN_REPO_DIR / plugin_id
        if not plugin_dir.exists():
            raise ValueError(f"Plugin '{plugin_id}' is not installed")

        # Move the working copy aside (hidden name so list_installed_plugins
        # skips it). os.replace is an atomic same-dir rename.
        backup_dir = PLUGIN_REPO_DIR / f".{plugin_id}.update-bak"
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        os.replace(plugin_dir, backup_dir)
        unregister_plugin_class(plugin_id)

        def _rollback() -> None:
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir, ignore_errors=True)
            os.replace(backup_dir, plugin_dir)
            _register_installed_plugin(plugin_id, plugin_dir)

        try:
            result = await _do_install(plugin_id, file_url)
        except Exception as e:
            _rollback()
            log.warning(f"Plugin '{plugin_id}' update failed, rolled back: {e}")
            return {
                "status": "update_failed",
                "plugin_id": plugin_id,
                "error": str(e),
                "rolled_back": True,
            }

        if result.get("status") != "installed":
            # New files are on disk but the class won't load — keep the working
            # version rather than swapping in a broken one.
            new_error = result.get("error")
            _rollback()
            log.warning(
                f"Plugin '{plugin_id}' new version failed to load, "
                f"rolled back: {new_error}"
            )
            return {
                "status": "update_failed",
                "plugin_id": plugin_id,
                "error": new_error,
                "rolled_back": True,
            }

        # Success — drop the staged backup.
        shutil.rmtree(backup_dir, ignore_errors=True)
        return result


# ──── List Installed ────


def list_installed_plugins() -> list[dict[str, Any]]:
    """List all plugins installed in plugin_repo/.

    Plugins whose registration failed at install time carry an
    ``.install-error`` sidecar. We surface those as
    ``status: "load_failed"`` with the captured error message so the UI
    has a diagnostic path instead of showing a phantom green check (A60).
    """
    if not PLUGIN_REPO_DIR.is_dir():
        return []

    installed = []
    for entry in sorted(PLUGIN_REPO_DIR.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue

        # Detect load-failure sidecar
        sidecar = entry / ".install-error"
        load_error: str | None = None
        if sidecar.exists():
            try:
                load_error = sidecar.read_text(encoding="utf-8").strip() or None
            except OSError:
                load_error = "registration failed"

        plugin_class = _PLUGIN_CLASS_REGISTRY.get(entry.name)
        if plugin_class:
            info = plugin_class.PLUGIN_INFO
            item: dict[str, Any] = {
                "id": entry.name,
                "name": info.get("name", entry.name),
                "version": info.get("version", ""),
                "source": "community",
            }
        else:
            item = {
                "id": entry.name,
                "name": entry.name,
                "version": "",
                "source": "community",
            }

        if load_error:
            item["status"] = "load_failed"
            item["error"] = load_error
        else:
            item["status"] = "loaded"

        installed.append(item)

    return installed
