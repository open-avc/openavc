"""Install a plugin's Python requirements into a target directory.

Two strategies behind one door (``install_requirements``): in a normal
checkout it shells out to pip, and in a frozen PyInstaller build — where
``sys.executable`` is the server exe and pip does not exist — it resolves and
downloads wheels from PyPI itself. Choosing between them is package-manager
knowledge, not plugin knowledge, so it lives here rather than in the caller.

The frozen path is a small pip: PEP 503 name normalization, PEP 508
requirement parsing, wheel-tag compatibility scoring against the running
interpreter and platform, version-specifier resolution, and a breadth-first
walk of transitive ``Requires-Dist`` entries read out of each wheel's
METADATA. It is deliberately simple — no dependency solver, no sdist builds,
no extras — because the plugins that need it declare a handful of pure or
wheel-shipped packages.

Requirement strings arrive from community plugin source and are untrusted:
``install_requirements`` runs every one through ``_is_safe_requirement``
before anything reaches pip's argv or a PyPI query.
"""

import asyncio
import platform as platform_mod
import re
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

from server.core.plugin_artifacts import _check_zip_bomb, _download_capped
from server.utils.logger import get_logger
from server.utils.paths import safe_path_within
from server.utils.spawn import CREATE_NO_WINDOW

log = get_logger(__name__)

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


async def install_requirements(
    requirements: list[str], deps_dir: Path, plugin_id: str
) -> None:
    """Install ``requirements`` into ``deps_dir``, by whichever route works here.

    The safety gate runs at this door rather than in the caller so it cannot be
    bypassed by reaching the installer directly — raising aborts the whole
    install, and the caller cleans up the partial directory or rolls back an
    update.
    """
    for req in requirements:
        if not _is_safe_requirement(req):
            raise ValueError(
                f"Unsafe dependency specifier in plugin '{plugin_id}': {req!r}. "
                "Dependencies must be plain 'package' or 'package>=x.y' names."
            )

    log.info(f"Installing pip dependencies for '{plugin_id}': {requirements}")

    if getattr(sys, "frozen", False):
        # Frozen (PyInstaller): sys.executable is the server exe, not Python.
        # Download wheels directly from PyPI instead.
        await _install_deps_from_pypi(requirements, deps_dir, plugin_id)
        return

    # Development: use pip normally. Async subprocess, not subprocess.run —
    # a pip resolve can take the full 120s and this runs on the event
    # loop of a live engine (device polling, WS, cloud heartbeats).
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install",
            "--target", str(deps_dir), *requirements,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            log.warning(
                f"Could not install deps for '{plugin_id}': "
                f"pip timed out after 120s"
            )
            return
        if proc.returncode != 0:
            log.warning(
                f"pip install failed for '{plugin_id}': "
                f"{stderr.decode('utf-8', errors='replace')}"
            )
    except OSError as e:
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

                # Wheels are zip files — extract into .deps/ off the loop
                new_deps = await asyncio.to_thread(
                    _extract_wheel, wheel_bytes, deps_dir
                )
                for dep in new_deps:
                    dep_name = _normalize_pkg_name(_parse_requirement(dep)[0])
                    if dep_name not in installed:
                        queue.append(dep)

                installed.add(normalized)
                log.info(f"[{plugin_id}] Installed {wheel_name} to .deps/")

            except (httpx.HTTPError, OSError, ValueError, zipfile.BadZipFile) as e:
                log.warning(f"[{plugin_id}] Failed to install '{req}': {e}")


def _extract_wheel(wheel_bytes: bytes, deps_dir: Path) -> list[str]:
    """Extract a wheel into .deps/ and return its transitive requirements
    (sync; run via to_thread)."""
    with zipfile.ZipFile(BytesIO(wheel_bytes)) as whl:
        _check_zip_bomb(whl, label="Wheel")
        for name in whl.namelist():
            # Skip .dist-info/RECORD (file hashes) — not needed
            target = safe_path_within(deps_dir, name)
            if target is None:
                log.warning(f"Skipping wheel entry with unsafe path: {name}")
                continue
            if name.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(whl.read(name))

        # Read transitive dependencies from METADATA
        return _read_wheel_deps(whl)


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
