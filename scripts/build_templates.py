"""Rebuild the starter-template .zip bundles from their canonical sources.

Every starter template ships twice in ``openavc/templates/``: a loose
``<name>.avc`` project file, an optional ``<name>.scripts/`` directory beside
it, and a ``<name>.zip`` bundle. The loose pair is what a person edits. The
bundle is what a fresh install actually seeds into the project library, and it
is the only thing that carries the device drivers the template needs. That
matters because AV control networks are frequently isolated: a starter project
that had to reach the internet before it would open is a poor first run, so the
bundle brings its drivers with it.

Keeping two copies in sync by hand is what let them rot. The committed bundles
drifted away from the loose files beside them and ended up carrying Python
whose UTF-8 had been double-encoded, so an em dash in a projector's "check
password" log message reached real installs as garbage. This script rebuilds a
bundle from one source of truth instead, and ``tests/test_template_bundles.py``
turns any hand edit back into a failing test.

A driver is copied in exactly as the community catalog publishes it: the driver
file plus the companions an install of that driver would fetch. That way a
template installs the same driver a person would get from Browse Drivers, right
down to the simulator, so a template can be tried out with no hardware.

Usage:
    python scripts/build_templates.py           # rewrite every bundle
    python scripts/build_templates.py --check   # report stale bundles, change nothing

The community driver library normally sits beside this repo in the workspace.
Set OPENAVC_DRIVERS_ROOT to point somewhere else.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "openavc" / "templates"
BUILTIN_DRIVER_DIR = REPO_ROOT / "openavc" / "drivers" / "definitions"

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or REPO_ROOT.parent / "openavc-drivers"
)

# Zip entries get a fixed timestamp so rebuilding an unchanged template
# produces the identical file. Without it every run would rewrite all four
# bundles with a new mtime and --check could never mean anything. 1980-01-01
# is the earliest a zip can store, and reads clearly as "not a real date".
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

# Regular file, mode 0644, in the high 16 bits where zip keeps POSIX mode.
FILE_ATTRS = (0o100644 << 16)


class BuildError(Exception):
    """Something the person running this needs to fix before it can finish."""


def builtin_driver_ids() -> set[str]:
    """Driver ids that ship inside the server, so no template needs to carry them."""
    ids: set[str] = set()
    for path in BUILTIN_DRIVER_DIR.glob("*.avcdriver"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("id:"):
                ids.add(line.split(":", 1)[1].strip())
                break
    return ids


def catalog_entries() -> dict[str, dict]:
    """The community catalog, keyed by driver id."""
    index_path = DRIVERS_ROOT / "index.json"
    if not index_path.is_file():
        raise BuildError(
            f"No community driver catalog at {index_path}.\n"
            "Clone open-avc/openavc-drivers beside this repo, or point "
            "OPENAVC_DRIVERS_ROOT at your checkout."
        )
    data = json.loads(index_path.read_text(encoding="utf-8"))
    return {e["id"]: e for e in data.get("drivers", []) if e.get("id")}


def driver_files(driver_id: str, catalog: dict[str, dict]) -> list[Path]:
    """Every file an install of ``driver_id`` puts in driver_repo/.

    The catalog's per-entry ``files`` map is the authority here -- it is the
    same list the platform hashes downloads against, and it already knows the
    difference between a Python driver (which brings a discovery companion and
    a simulator) and a YAML one (which does not).
    """
    entry = catalog.get(driver_id)
    if entry is None:
        raise BuildError(
            f"Driver '{driver_id}' is used by a starter template but is not in "
            f"the community catalog at {DRIVERS_ROOT / 'index.json'}. A template "
            "may only depend on a driver a person could also install themselves."
        )

    relpaths = sorted(entry.get("files") or {})
    if not relpaths:
        # An older catalog with no hash map. Fall back to the driver file plus
        # the documented companion suffixes so this still produces a usable
        # bundle instead of silently shipping a driver with no simulator.
        main = entry.get("file")
        if not main:
            raise BuildError(f"Catalog entry for '{driver_id}' has no 'file'.")
        relpaths = [main]
        if main.endswith(".py"):
            stem = main[: -len(".py")]
            relpaths += [f"{stem}_discovery.py", f"{stem}_sim.py"]

    resolved: list[Path] = []
    for rel in relpaths:
        path = DRIVERS_ROOT / rel
        if not path.is_file():
            if rel == entry.get("file"):
                raise BuildError(f"Driver file missing: {path}")
            continue  # An optional companion the catalog lists but this checkout lacks.
        resolved.append(path)
    return resolved


def template_drivers(project: dict, builtins: set[str]) -> list[str]:
    """Non-builtin driver ids the template's devices need, in a stable order."""
    used = {dev.get("driver", "") for dev in project.get("devices", [])}
    return sorted(d for d in used if d and d not in builtins)


def read_source(path: Path) -> bytes:
    """Read one source file exactly as it should appear inside a bundle.

    THE definition of "the bytes this source contributes". Anything comparing a
    bundle against its sources has to ask here too, or the two answers drift --
    which is the shape of bug this whole script exists to prevent.

    Two normalizations, both deliberate:

    UTF-8 in and UTF-8 straight back out, so the round trip cannot re-encode
    text that was already encoded. That is the exact defect this replaces --
    Python read as cp1252 and written back as UTF-8 turned every em dash into
    three garbage characters.

    Line endings collapse to LF, because ``read_text`` reads in universal-
    newline mode. A bundle is a shipped artifact extracted verbatim onto a
    user's disk, so its contents must not depend on the platform it was built
    on. Git hands a Windows checkout CRLF for these same files; without this,
    building on Windows would rewrite all four bundles with CRLF and the
    committed artifacts would flip back and forth by whose machine ran last.
    """
    return path.read_text(encoding="utf-8").encode("utf-8")


def build_bundle(stem: str, catalog: dict[str, dict], builtins: set[str]) -> bytes:
    """Assemble one template bundle and return its bytes."""
    avc_path = TEMPLATE_DIR / f"{stem}.avc"
    project = json.loads(avc_path.read_text(encoding="utf-8"))

    members: list[tuple[str, bytes]] = [("project.avc", read_source(avc_path))]

    scripts_dir = TEMPLATE_DIR / f"{stem}.scripts"
    if scripts_dir.is_dir():
        for script in sorted(scripts_dir.glob("*.py")):
            members.append((f"scripts/{script.name}", read_source(script)))

    for driver_id in template_drivers(project, builtins):
        for path in driver_files(driver_id, catalog):
            members.append((f"drivers/{path.name}", read_source(path)))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in members:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FILE_ATTRS
            info.create_system = 3  # Unix, so the mode above is meaningful.
            zf.writestr(info, payload)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report bundles that differ from their sources; write nothing.",
    )
    args = parser.parse_args(argv)

    try:
        catalog = catalog_entries()
    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    builtins = builtin_driver_ids()

    stale: list[str] = []
    for avc_path in sorted(TEMPLATE_DIR.glob("*.avc")):
        stem = avc_path.stem
        zip_path = TEMPLATE_DIR / f"{stem}.zip"
        try:
            payload = build_bundle(stem, catalog, builtins)
        except BuildError as e:
            print(f"error: {stem}: {e}", file=sys.stderr)
            return 2

        current = zip_path.read_bytes() if zip_path.is_file() else None
        if current == payload:
            print(f"  ok       {zip_path.name}")
            continue

        stale.append(zip_path.name)
        if args.check:
            print(f"  STALE    {zip_path.name}")
        else:
            zip_path.write_bytes(payload)
            print(f"  rebuilt  {zip_path.name}")

    if args.check and stale:
        print(
            f"\n{len(stale)} bundle(s) out of date: {', '.join(stale)}\n"
            "Run: python scripts/build_templates.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
