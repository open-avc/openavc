"""
Plugin installer — download, install, update, and uninstall community plugins.

Mirrors the driver install system: fetches index.json from the community
repository, downloads plugin files, manages pip dependencies, handles
install/uninstall lifecycle.
"""

import asyncio
import ctypes.util
import ipaddress
import os
import platform as platform_mod
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from server.core.plugin_loader import (
    _PLUGIN_CLASS_REGISTRY,
    _exec_plugin_in_package,
    _purge_plugin_modules,
    register_plugin_class,
    unregister_plugin_class,
)
from server.system_config import PLUGIN_DATA_DIR, PLUGIN_REPO_DIR
from server.utils.logger import get_logger
from server.utils.spawn import CREATE_NO_WINDOW

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


def _safe_zip_target(base_dir: Path, relative_path: str) -> Path | None:
    """Resolve a zip entry path safely, rejecting path traversal.

    Returns the resolved target path if it's inside base_dir, or None if
    the path would escape (e.g. via '../').
    """
    target = (base_dir / relative_path).resolve()
    if not target.is_relative_to(base_dir.resolve()):
        return None
    return target


# Official community plugin catalog. Plugin *code* must come from this exact
# repo — a hostname-only "is it GitHub?" check gives false assurance: any
# attacker-controlled GitHub repo passes it, yet plugin code runs in-process.
_CATALOG_OWNER_REPO = "open-avc/openavc-plugins"

# For a URL to count as "from the official catalog", its host must be one of
# these and its path must sit under the required prefix for that host.
_CATALOG_URL_PREFIXES = {
    "raw.githubusercontent.com": f"/{_CATALOG_OWNER_REPO}/",
    "github.com": f"/{_CATALOG_OWNER_REPO}/",
    "api.github.com": f"/repos/{_CATALOG_OWNER_REPO}/",
}

# Download size guards (DoS defense). Generous ceilings sized for real native
# deps (a full ffmpeg build is ~100 MB compressed / ~250 MB extracted), not
# artificial limits — the point is to stop multi-GB downloads and zip bombs.
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024        # any single fetched file (compressed)
_MAX_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024   # total extracted size of an archive
_MAX_ARCHIVE_MEMBERS = 20_000                  # entries in a zip / wheel
_MAX_DIRECTORY_FILES = 5_000                   # files in a directory-style install


def _validate_catalog_url(url: str) -> None:
    """Reject a plugin URL that isn't under the official community catalog repo.

    A hostname allowlist alone gives false assurance of a trusted source: any
    attacker-controlled GitHub repo passes a ``*.githubusercontent.com`` check,
    yet plugin code is executed in-process. So we require the URL to point at
    the curated, human-reviewed catalog repo (``open-avc/openavc-plugins``),
    not merely "some GitHub URL". https-only.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Plugin URL must use https, got: {parsed.scheme or url!r}")
    prefix = _CATALOG_URL_PREFIXES.get(parsed.hostname or "")
    if prefix is None:
        raise ValueError(
            f"Plugin URL must come from the official catalog "
            f"({_CATALOG_OWNER_REPO}); host {parsed.hostname or url!r} is not allowed"
        )
    # Decode %2e%2e / %2f before the prefix + traversal check so an encoded path
    # can't masquerade as the catalog and then resolve elsewhere on the host.
    path = unquote(parsed.path)
    if not path.startswith(prefix) or ".." in path.split("/"):
        raise ValueError(
            f"Plugin URL must be a path under {_CATALOG_OWNER_REPO}; "
            f"path {parsed.path!r} is not"
        )


async def _validate_download_url(url: str) -> None:
    """SSRF guard for an arbitrary (non-catalog) download URL.

    Native-dependency archives legitimately come from public release hosts
    (GitHub releases, project mirrors), so they can't be pinned to the catalog
    repo like plugin code. Instead require https and reject any URL whose host
    resolves into private, loopback, link-local (cloud-metadata), multicast,
    reserved, or unspecified address space — closing the SSRF vector (e.g. a
    plugin pointing the server at 169.254.169.254). Loopback is allowed only on
    a dev checkout so local tests / mirrors work.

    Mirrors routes/system.py:_validate_cloud_api_url; kept local because the
    policy differs (private ranges are blocked here) and to avoid a route->core
    import dependency.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Download URL must use https, got: {parsed.scheme or url!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"Download URL is missing a host: {url!r}")
    port = parsed.port or 443
    try:
        infos = await asyncio.get_event_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
    except (OSError, socket.gaierror) as e:
        raise ValueError(f"Could not resolve download host {host!r}: {e}")

    from server.api.auth import _deployment_is_dev
    allow_loopback = _deployment_is_dev()
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback and allow_loopback:
            continue
        if (
            ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(
                f"Download URL resolves to a disallowed (non-public) address: {ip}"
            )


async def _download_capped(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
    label: str = "file",
) -> bytes:
    """Stream a URL into memory, aborting if it exceeds ``max_bytes``.

    Streamed (not ``client.get``) so a multi-GB or unbounded chunked response
    can't exhaust RAM before we notice. Honors an upfront Content-Length when
    present (fast reject) and re-checks the running total as chunks arrive.
    """
    buf = bytearray()
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise ValueError(
                f"{label} is too large: {int(declared)} bytes exceeds the "
                f"{max_bytes}-byte limit"
            )
        async for chunk in resp.aiter_bytes():
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(
                    f"{label} exceeded the {max_bytes}-byte download limit"
                )
    return bytes(buf)


def _check_zip_bomb(zf: zipfile.ZipFile, *, label: str = "Archive") -> None:
    """Reject an archive whose member count or total uncompressed size would
    make extraction a DoS (zip bomb), using the central-directory sizes."""
    infos = zf.infolist()
    if len(infos) > _MAX_ARCHIVE_MEMBERS:
        raise ValueError(f"{label} has too many entries (max {_MAX_ARCHIVE_MEMBERS}).")
    total = 0
    for info in infos:
        total += info.file_size
        if total > _MAX_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"{label} is too large uncompressed "
                f"(max {_MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MB)."
            )


def _is_safe_entry_name(name: str) -> bool:
    """True if a GitHub Contents API entry name is a plain filename.

    The API returns basenames, so anything with a path separator, a '.'/'..'
    component, or a NUL is anomalous (a tampered/MITM'd listing) and is skipped
    before it can redirect a write outside the plugin directory.
    """
    return bool(name) and name not in (".", "..") and not (
        "/" in name or "\\" in name or "\x00" in name
    )


_VCS_PREFIXES = ("git+", "hg+", "svn+", "bzr+")


def _is_safe_requirement(req: str) -> bool:
    """True if ``req`` is a plain name-based pip requirement.

    Rejects exactly the shapes that turn ``pip install <req>`` into something
    other than "install this named package from the default index":
      - a leading '-' / '.' / path char -> pip option injection
        ('--index-url=...', '-r file', '-e .') or a local path install
      - a URL ('http://...', 'file://...') -> arbitrary download/sdist exec
      - a VCS spec ('git+https://...') -> clone + setup.py exec
      - a PEP 508 direct reference ('name @ url')
    Extras ('pkg[extra]'), version specifiers, and environment markers
    ('pkg; sys_platform=="win32"') are PEP 508 features that stay one argv
    element under shell=False, so they're allowed.
    """
    s = req.strip()
    if not s or s[0] in "-./\\":
        return False
    low = s.lower()
    if "://" in low or "@" in s or any(p in low for p in _VCS_PREFIXES):
        return False
    # The leading token must be a valid PEP 503 distribution name.
    return bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", s))


class _DownloadBudget:
    """Cumulative file-count + byte caps for a directory-style install."""

    def __init__(
        self,
        max_files: int = _MAX_DIRECTORY_FILES,
        max_bytes: int = _MAX_UNCOMPRESSED_BYTES,
    ):
        self.max_files = max_files
        self.max_bytes = max_bytes
        self.files = 0
        self.bytes = 0

    def add_file(self, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.files > self.max_files:
            raise ValueError(f"Plugin has too many files (max {self.max_files}).")
        if self.bytes > self.max_bytes:
            raise ValueError("Plugin directory total size exceeds the limit.")


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


_SAFE_ID_RE = re.compile(r"^[a-z0-9_]+$")


def _validate_plugin_id(plugin_id: str) -> None:
    if not plugin_id or not _SAFE_ID_RE.match(plugin_id):
        raise ValueError(
            f"Invalid plugin ID '{plugin_id}': must be lowercase letters, "
            "numbers, and underscores only"
        )


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

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if file_url.endswith(".py"):
                # Single file plugin
                content = await _download_capped(
                    client, file_url, label="plugin file"
                )
                plugin_dir.mkdir(parents=True, exist_ok=True)
                filename = _sanitize_filename(Path(urlparse(file_url).path).name)
                target = _safe_zip_target(plugin_dir, filename)
                if target is None:
                    raise ValueError(f"Unsafe plugin filename: {filename!r}")
                target.write_bytes(content)
                log.info(f"Installed plugin '{plugin_id}' from {filename}")

            elif file_url.endswith(".zip"):
                # Zip archive
                content = await _download_capped(
                    client, file_url, label="plugin archive"
                )
                plugin_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(BytesIO(content)) as zf:
                    _check_zip_bomb(zf, label="Plugin archive")
                    for name in zf.namelist():
                        parts = name.split("/", 1)
                        relative = parts[1] if len(parts) > 1 else name
                        target = _safe_zip_target(plugin_dir, relative)
                        if target is None:
                            log.warning(f"Skipping zip entry with unsafe path: {name}")
                            continue
                        if name.endswith("/"):
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(zf.read(name))
                log.info(f"Installed plugin '{plugin_id}' from zip archive")

            else:
                # Directory — download all files via GitHub Contents API
                plugin_dir.mkdir(parents=True, exist_ok=True)
                # Extract the path relative to the repo from the raw URL
                repo_path = file_url.replace(COMMUNITY_REPO_URL + "/", "")
                await _download_github_directory(
                    client, repo_path, plugin_dir
                )
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
    *, budget: "_DownloadBudget | None" = None, _depth: int = 0, _max_depth: int = 5,
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
        target = _safe_zip_target(dest_dir, safe_name)
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
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        elif entry_type == "dir":
            target.mkdir(parents=True, exist_ok=True)
            await _download_github_directory(
                client, f"{repo_path}/{name}", target,
                budget=budget, _depth=_depth + 1, _max_depth=_max_depth,
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

    # A community plugin's dependency strings are untrusted. Reject anything
    # that isn't a plain PEP 508 requirement BEFORE it reaches pip's argv (dev/
    # Linux/Docker) or a PyPI query (frozen): a leading '-' (--index-url=...), a
    # 'git+'/URL install, or a 'name @ url' direct reference would otherwise run
    # install-time code from an attacker-chosen index/repo. Raising aborts the
    # install (the caller cleans up the partial dir / rolls back an update).
    for dep in deps:
        if not _is_safe_requirement(dep):
            raise ValueError(
                f"Unsafe dependency specifier in plugin '{plugin_id}': {dep!r}. "
                "Dependencies must be plain 'package' or 'package>=x.y' names."
            )

    deps_dir = PLUGIN_REPO_DIR / ".deps"
    deps_dir.mkdir(exist_ok=True)

    log.info(f"Installing pip dependencies for '{plugin_id}': {deps}")

    if getattr(sys, "frozen", False):
        # Frozen (PyInstaller): sys.executable is the server exe, not Python.
        # Download wheels directly from PyPI instead.
        await _install_deps_from_pypi(deps, deps_dir, plugin_id)
    else:
        # Development: use pip normally
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--target", str(deps_dir)]
                + deps,
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.CalledProcessError as e:
            log.warning(f"pip install failed for '{plugin_id}': {e.stderr}")
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning(f"Could not install deps for '{plugin_id}': {e}")


async def _install_deps_from_pypi(
    requirements: list[str], deps_dir: Path, plugin_id: str
) -> None:
    """Download and extract wheel files directly from PyPI.

    Used in frozen (PyInstaller) environments where pip is not available.
    Handles transitive dependencies by reading wheel METADATA.
    """
    installed: set[str] = set()  # Track what we've installed to avoid loops
    queue = list(requirements)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while queue:
            req = queue.pop(0)
            pkg_name, version_spec = _parse_requirement(req)
            normalized = _normalize_pkg_name(pkg_name)

            if normalized in installed:
                continue
            # Skip packages already in the deps dir or bundled with the app
            if _package_already_available(normalized, deps_dir):
                installed.add(normalized)
                continue

            try:
                wheel_url, wheel_name = await _find_best_wheel(
                    client, pkg_name, version_spec
                )
                if not wheel_url:
                    log.warning(
                        f"[{plugin_id}] No compatible wheel found for '{req}'"
                    )
                    continue

                log.info(f"[{plugin_id}] Downloading {wheel_name}")
                wheel_bytes = await _download_capped(
                    client, wheel_url, label=f"wheel {wheel_name}"
                )

                # Wheels are zip files — extract into .deps/
                with zipfile.ZipFile(BytesIO(wheel_bytes)) as whl:
                    _check_zip_bomb(whl, label="Wheel")
                    for name in whl.namelist():
                        # Skip .dist-info/RECORD (file hashes) — not needed
                        if name.endswith("/"):
                            target = _safe_zip_target(deps_dir, name)
                            if target is None:
                                log.warning(f"Skipping wheel entry with unsafe path: {name}")
                                continue
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target = _safe_zip_target(deps_dir, name)
                            if target is None:
                                log.warning(f"Skipping wheel entry with unsafe path: {name}")
                                continue
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(whl.read(name))

                    # Read transitive dependencies from METADATA
                    new_deps = _read_wheel_deps(whl)
                    for dep in new_deps:
                        dep_name = _normalize_pkg_name(_parse_requirement(dep)[0])
                        if dep_name not in installed:
                            queue.append(dep)

                installed.add(normalized)
                log.info(f"[{plugin_id}] Installed {wheel_name} to .deps/")

            except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as e:
                log.warning(f"[{plugin_id}] Failed to install '{req}': {e}")


def _normalize_pkg_name(name: str) -> str:
    """Normalize a package name per PEP 503 (lowercase, hyphens to underscores)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(req: str) -> tuple[str, str]:
    """Split 'package>=1.0' into ('package', '>=1.0'). Returns ('package', '') if no version."""
    m = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)", req.strip())
    if m:
        return m.group(1), m.group(2).strip()
    return req.strip(), ""


def _package_already_available(normalized_name: str, deps_dir: Path) -> bool:
    """Check if a package is already installed in .deps/ or available in sys.path."""
    # Check .deps/ for a directory matching the package name
    pkg_dir_name = normalized_name.replace("-", "_")
    if (deps_dir / pkg_dir_name).is_dir():
        return True
    # Check for single-file module
    if (deps_dir / f"{pkg_dir_name}.py").exists():
        return True
    # Check if importable (bundled with app or stdlib)
    try:
        import importlib.util
        spec = importlib.util.find_spec(pkg_dir_name)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _get_platform_tags() -> list[str]:
    """Get compatible platform tags for wheel matching, ordered by preference."""
    arch = platform_mod.machine().lower()
    system = platform_mod.system().lower()

    if system == "windows":
        if arch in ("amd64", "x86_64"):
            return ["win_amd64", "any"]
        elif arch == "arm64":
            return ["win_arm64", "any"]
        return ["win32", "any"]
    elif system == "linux":
        # manylinux tags — accept a range of glibc versions
        if arch in ("x86_64", "amd64"):
            plat = "x86_64"
        elif arch in ("aarch64", "arm64"):
            plat = "aarch64"
        else:
            plat = arch
        tags = []
        # Common manylinux tags (newer to older)
        for ml in [
            "manylinux_2_35", "manylinux_2_34", "manylinux_2_31",
            "manylinux_2_28", "manylinux_2_27", "manylinux_2_24",
            "manylinux_2_17", "manylinux2014", "manylinux2010", "manylinux1",
        ]:
            tags.append(f"{ml}_{plat}")
        tags.append(f"linux_{plat}")
        tags.append("any")
        return tags

    return ["any"]


def _get_python_tags() -> tuple[str, str]:
    """Get the Python version tag (e.g., 'cp312') and ABI tag."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    # For frozen builds, read the version from the bundled DLL name
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        for dll in meipass.glob("python3*.dll"):
            m = re.match(r"python(\d)(\d+)\.dll", dll.name)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                break
    cp_tag = f"cp{major}{minor}"
    return cp_tag, cp_tag


async def _find_best_wheel(
    client: httpx.AsyncClient, package: str, version_spec: str
) -> tuple[str | None, str | None]:
    """Query PyPI and find the best compatible wheel URL.

    Returns (url, filename) or (None, None) if nothing matches.
    """
    # Get package info from PyPI
    normalized = _normalize_pkg_name(package)
    url = f"https://pypi.org/pypi/{normalized}/json"
    resp = await client.get(url)
    if resp.status_code == 404:
        return None, None
    resp.raise_for_status()
    data = resp.json()

    # Determine which version to use
    if version_spec:
        version = _resolve_version(data.get("releases", {}), version_spec)
    else:
        version = data.get("info", {}).get("version", "")

    if not version:
        return None, None

    # Get files for this version
    releases = data.get("releases", {})
    files = releases.get(version, [])
    if not files:
        # Fall back to urls from the info endpoint for latest version
        files = data.get("urls", [])

    cp_tag, abi_tag = _get_python_tags()
    platform_tags = _get_platform_tags()

    # Score each wheel by compatibility (lower is better)
    best_url = None
    best_name = None
    best_score = 999

    for f in files:
        if f.get("packagetype") != "bdist_wheel":
            continue
        filename = f.get("filename", "")
        if not filename.endswith(".whl"):
            continue

        # Parse wheel filename: name-ver(-build)?-pytag-abitag-plattag.whl
        parts = filename[:-4].split("-")
        if len(parts) < 5:
            continue

        whl_py_tag = parts[-3]
        whl_abi_tag = parts[-2]
        whl_plat_tag = parts[-1]

        # Check Python compatibility
        py_compat = False
        for py in whl_py_tag.split("."):
            if py in (cp_tag, f"cp{sys.version_info.major}", "py3",
                      f"py{sys.version_info.major}{sys.version_info.minor}",
                      f"py{sys.version_info.major}"):
                py_compat = True
                break
        if not py_compat:
            continue

        # Check ABI compatibility
        if whl_abi_tag not in (abi_tag, "none", "abi3",
                               f"abi{sys.version_info.major}"):
            continue

        # Check platform compatibility
        plat_score = 999
        for whl_plat in whl_plat_tag.split("."):
            for i, accepted in enumerate(platform_tags):
                if whl_plat == accepted:
                    plat_score = min(plat_score, i)
                    break
        if plat_score == 999:
            continue

        # Prefer native wheels (lower plat_score) over pure-python
        score = plat_score
        if whl_py_tag.startswith("cp"):
            score -= 100  # Prefer cpython wheels

        if score < best_score:
            best_score = score
            best_url = f.get("url")
            best_name = filename

    return best_url, best_name


def _resolve_version(
    releases: dict[str, list], version_spec: str
) -> str | None:
    """Find the latest version matching the version spec (e.g., '>=10.0')."""
    if not version_spec:
        return None

    m = re.match(r"(>=|==|<=|~=|!=|>|<)\s*([\d.]+)", version_spec)
    if not m:
        return None

    op, target = m.group(1), m.group(2)
    target_parts = _version_tuple(target)

    candidates = []
    for ver in releases:
        if not releases[ver]:  # Skip versions with no files
            continue
        # Skip pre-releases
        if re.search(r"(a|b|rc|dev|alpha|beta)", ver, re.IGNORECASE):
            continue
        ver_parts = _version_tuple(ver)
        if ver_parts is None:
            continue

        if op == ">=" and ver_parts >= target_parts:
            candidates.append((ver_parts, ver))
        elif op == "==" and ver_parts == target_parts:
            candidates.append((ver_parts, ver))
        elif op == "<=" and ver_parts <= target_parts:
            candidates.append((ver_parts, ver))
        elif op == ">" and ver_parts > target_parts:
            candidates.append((ver_parts, ver))
        elif op == "<" and ver_parts < target_parts:
            candidates.append((ver_parts, ver))
        elif op == "~=" and ver_parts >= target_parts and ver_parts[:len(target_parts) - 1] == target_parts[:len(target_parts) - 1]:
            candidates.append((ver_parts, ver))
        elif op == "!=" and ver_parts != target_parts:
            candidates.append((ver_parts, ver))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _version_tuple(v: str) -> tuple[int, ...] | None:
    """Parse '1.2.3' into (1, 2, 3). Returns None if unparseable."""
    try:
        return tuple(int(x) for x in v.split(".") if x.isdigit())
    except (ValueError, AttributeError):
        return None


def _read_wheel_deps(whl: zipfile.ZipFile) -> list[str]:
    """Read Requires-Dist entries from a wheel's METADATA file.

    Only returns unconditional dependencies (no extras, no markers that
    restrict to specific platforms we're not on).
    """
    for name in whl.namelist():
        if name.endswith(".dist-info/METADATA"):
            try:
                metadata = whl.read(name).decode("utf-8", errors="replace")
            except (KeyError, OSError):
                continue

            deps = []
            for line in metadata.splitlines():
                if not line.startswith("Requires-Dist:"):
                    continue
                value = line[len("Requires-Dist:"):].strip()
                # Skip conditional dependencies (extras, env markers)
                if "extra ==" in value:
                    continue
                # Strip markers after ';' — we take the base package
                if ";" in value:
                    # Only include if the marker is not platform-restrictive
                    # or applies to our platform. Simplified: skip complex markers.
                    marker = value.split(";", 1)[1].strip()
                    # Accept os_name == 'nt' on Windows, 'posix' on Linux
                    current_os = "nt" if sys.platform == "win32" else "posix"
                    if "os_name" in marker and f'"{current_os}"' not in marker and f"'{current_os}'" not in marker:
                        continue
                    if "sys_platform" in marker and f'"{sys.platform}"' not in marker and f"'{sys.platform}'" not in marker:
                        continue
                    value = value.split(";", 1)[0].strip()
                deps.append(value)
            return deps
    return []


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
            if platform_info.get("type") in _ARCHIVE_DEP_TYPES:
                await _install_native_dep_archive(dep_name, platform_info, deps_dir)
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
        # Check system paths — find_library expects name without 'lib' prefix
        # and extension (e.g. "hidapi-libusb" not "libhidapi-libusb.so")
        base = os.path.splitext(lib_name)[0]
        if base.startswith("lib"):
            base = base[3:]
        return ctypes.util.find_library(base) is not None

    elif check_type == "registry":
        if platform_mod.system() != "Windows":
            return False
        key_path = check.get("key", "")
        if not key_path:
            return False
        try:
            import winreg
            hive_map = {
                "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCU": winreg.HKEY_CURRENT_USER,
            }
            parts = key_path.replace("/", "\\").split("\\", 1)
            hive = hive_map.get(parts[0].upper())
            if hive is None or len(parts) < 2:
                return False
            with winreg.OpenKey(hive, parts[1]):
                return True
        except (OSError, ImportError):
            return False

    elif check_type == "command":
        cmd = check.get("command", "")
        if not cmd:
            return False
        try:
            import shlex
            result = subprocess.run(
                shlex.split(cmd), capture_output=True, timeout=10, shell=False,
                creationflags=CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    return False


# Native-dep platform entries with one of these `type` values are downloaded
# and a single file extracted from them. The actual container format (zip vs.
# gzip/xz tarball) is sniffed from the URL and magic bytes, not this field —
# `"zip"` is the historical generic value and stays valid for any archive.
_ARCHIVE_DEP_TYPES = frozenset({
    "zip", "tar", "tar.gz", "tgz", "tar.xz", "txz", "archive",
})


def _detect_archive_format(url: str, data: bytes) -> str:
    """Classify an archive payload as 'zip', 'tar.gz', or 'tar.xz'.

    Prefers the URL extension (authoritative for GitHub release assets), then
    falls back to magic bytes for URLs that hide the extension behind a
    redirect or query string.
    """
    low = url.lower().split("?", 1)[0]
    if low.endswith((".tar.gz", ".tgz")):
        return "tar.gz"
    if low.endswith((".tar.xz", ".txz")):
        return "tar.xz"
    if low.endswith(".zip"):
        return "zip"
    if data[:4] == b"PK\x03\x04":
        return "zip"
    if data[:2] == b"\x1f\x8b":
        return "tar.gz"
    if data[:6] == b"\xfd7zXZ\x00":
        return "tar.xz"
    # Default to the historical behavior so a misnamed zip still works.
    return "zip"


async def _install_native_dep_archive(
    dep_name: str, platform_info: dict, deps_dir: Path
) -> None:
    """Download an archive and extract one file from it into .deps/.

    Handles .zip (zipfile) plus .tar.gz / .tar.xz (tarfile). MediaMTX ships
    its Linux/ARM builds as .tar.gz and BtbN's ffmpeg builds as .tar.xz, so a
    zip-only extractor can't install either on Linux. The `extract` field is
    the path of the file *inside* the archive; the file lands in .deps/ under
    its basename. Tar member mode bits (notably the executable bit) are
    preserved; the zip format can't carry them, so callers that need +x on a
    zip-sourced binary must chmod at use time.
    """
    url = platform_info.get("url", "")
    extract_path = platform_info.get("extract", "")
    if not url or not extract_path:
        raise ValueError(f"Missing url or extract path for '{dep_name}'")

    # SSRF guard: the URL comes from plugin-declared native_dependencies and
    # points at an arbitrary host (not the catalog), so reject anything that
    # resolves to internal/metadata address space before fetching.
    await _validate_download_url(url)

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        data = await _download_capped(
            client, url, label=f"native dependency {dep_name}"
        )

    target_filename = Path(extract_path).name
    target = deps_dir / target_filename
    fmt = _detect_archive_format(url, data)

    if fmt == "zip":
        with zipfile.ZipFile(BytesIO(data)) as zf:
            try:
                info = zf.getinfo(extract_path)
            except KeyError:
                raise ValueError(
                    f"File '{extract_path}' not found in zip for '{dep_name}'"
                )
            if info.file_size > _MAX_UNCOMPRESSED_BYTES:
                raise ValueError(
                    f"'{extract_path}' is too large to extract for '{dep_name}'"
                )
            payload = zf.read(extract_path)
        target.write_bytes(payload)
    else:
        mode = "r:gz" if fmt == "tar.gz" else "r:xz"
        try:
            with tarfile.open(fileobj=BytesIO(data), mode=mode) as tf:
                try:
                    member = tf.getmember(extract_path)
                except KeyError:
                    raise ValueError(
                        f"File '{extract_path}' not found in archive for '{dep_name}'"
                    )
                if member.size > _MAX_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        f"'{extract_path}' is too large to extract for '{dep_name}'"
                    )
                src = tf.extractfile(member)
                if src is None:
                    raise ValueError(
                        f"'{extract_path}' is not a regular file in archive "
                        f"for '{dep_name}'"
                    )
                payload = src.read()
        except tarfile.TarError as e:
            raise ValueError(f"Could not read archive for '{dep_name}': {e}")
        target.write_bytes(payload)
        # Tar carries Unix mode bits; preserve them so an extracted binary
        # stays executable without the consumer having to re-chmod.
        mode_bits = member.mode & 0o777
        if mode_bits:
            try:
                target.chmod(mode_bits)
            except OSError:
                pass

    log.info(f"Extracted {target_filename} ({len(payload)} bytes) to {deps_dir}")


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
            cmd_list, shell=False, capture_output=True, text=True, timeout=60,
            creationflags=CREATE_NO_WINDOW,
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
    from server.version import __version__
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
