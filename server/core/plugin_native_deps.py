"""Install the native (non-Python) libraries a plugin declares.

Some plugins wrap a system library or a shipped binary — hidapi, ffmpeg,
MediaMTX — which pip cannot provide. A plugin declares those in
``native_dependencies`` in its PLUGIN_INFO, and this module resolves them for
the running platform: check whether the thing is already present, and if not
install it either by pulling one file out of a release archive or by running
the platform's own package-manager command.

Everything lands in ``plugin_repo/.deps/``, the same directory
``plugin_wheels`` fills, and the directory is injected into the process's
DLL / shared-library search path afterwards so a just-installed library is
loadable without a restart.

Separate from ``plugin_wheels`` because they share nothing but that output
directory: no call crosses between them, and the substrates could hardly be
less alike (ctypes probes, the Windows registry, tar/zip sniffing, and
subprocess package managers on this side; PyPI metadata and wheel tags on
the other).
"""

import asyncio
import ctypes.util
import os
import platform as platform_mod
import subprocess
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

from server.core.plugin_artifacts import (
    _MAX_UNCOMPRESSED_BYTES,
    _download_capped,
    _validate_download_url,
)
from server.system_config import PLUGIN_REPO_DIR
from server.utils.logger import get_logger
from server.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

# Native-dep platform entries with one of these `type` values are downloaded
# and a single file extracted from them. The actual container format (zip vs.
# gzip/xz tarball) is sniffed from the URL and magic bytes, not this field —
# `"zip"` is the historical generic value and stays valid for any archive.
_ARCHIVE_DEP_TYPES = frozenset({
    "zip", "tar", "tar.gz", "tgz", "tar.xz", "txz", "archive",
})


async def install_native_deps(plugin_id: str, native_deps: list[dict]) -> None:
    """Install every declared native dep that isn't already present.

    Best-effort per dep: a failure is logged (loudly, if the dep is marked
    required) and the rest still run, because a missing optional library
    should not fail the whole plugin install.
    """
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

        # Check if already installed (to_thread: ctypes/registry/command
        # probes can block)
        if await asyncio.to_thread(_check_native_dep, dep):
            log.debug(f"Native dep '{dep_name}' already available")
            continue

        platform_info = platforms[platform_key]
        log.info(f"Installing native dep '{dep_name}' for plugin '{plugin_id}'")

        try:
            if platform_info.get("type") in _ARCHIVE_DEP_TYPES:
                await _install_native_dep_archive(dep_name, platform_info, deps_dir)
            elif platform_info.get("install_cmd"):
                # to_thread: runs a system command with a 60s timeout
                await asyncio.to_thread(
                    _install_native_dep_command, dep_name, platform_info
                )
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

    # Decompress + extract off the event loop (xz/gzip payloads are CPU-bound)
    await asyncio.to_thread(
        _extract_native_dep_file, dep_name, url, data, extract_path, deps_dir
    )


def _extract_native_dep_file(
    dep_name: str, url: str, data: bytes, extract_path: str, deps_dir: Path
) -> None:
    """Extract one file from a native-dep archive (sync; run via to_thread)."""
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
