"""Contract-check one driver file from a terminal.

    python -m server.drivers.check path/to/driver.py
    python -m server.drivers.check path/to/driver.avcdriver
    python -m server.drivers.check path/to/drivers/        # whole directory

Works on any path. It needs no repo layout, no ``manufacturers.json`` and no
catalog, so a driver written for one job — or one that will never be
contributed — can be checked the same way a catalog driver is.

Why this exists: every gate that knows the driver contract used to sit
somewhere the author of a Python driver could not reach. ``build_index.py
--check`` lives in the community catalog repo and validates the whole catalog
against its manufacturer registry; ``simulator.validate`` takes any path but
checks driver-to-simulator parity, not the contract; the strict save-time
validation runs inside the Programmer IDE. A YAML author at least gets live
editor feedback from the ``# yaml-language-server:`` schema line — there is no
equivalent for a ``DRIVER_INFO`` dict, so the command line is the only place a
Python author can be told anything before runtime.

It defines no rules of its own. Every verdict comes from the same functions
the save doors, the loader and the catalog already call:

    .avcdriver   driver_loader.validate_driver_definition(..., strict=True)
                 — the whole rule set (unknown keys, cross-field rules, the
                 discovery block), exactly as the Builder's save runs it
    .py          python_info.extract_python_driver_info_full  ->
                 avcdriver_semantic.unknown_key_errors, plus
                 python_info.python_driver_info_issues (the loader's
                 structural rules)

Posture is the authoring one: unknown keys are errors here, as they are at
save and at catalog publish, not the warnings the runtime loader settles for.
The loader is lenient on purpose — a driver written for a newer platform must
not vanish at load and take its devices offline — but a checker has no device
to protect.

Coverage is reported, never implied. A file it cannot read is an error, not a
skip; a Python value it cannot evaluate is named; and the two structural
checks that need the loaded class are listed rather than silently passed.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from server.drivers.python_info import (
    CLASS_DEPENDENT_CHECKS,
    ExtractError,
    declares_driver_info,
    extract_python_driver_info_full,
    python_driver_info_issues,
    python_driver_reference_skips,
)

DRIVER_EXTENSIONS = (".avcdriver", ".py")

# Sibling files that live next to drivers but aren't drivers: discovery
# companions and Python simulators. Mirrors the runtime loader's filter.
COMPANION_SUFFIXES: tuple[str, ...] = ("_discovery.py", "_sim.py")


@dataclass
class FileCheckResult:
    """What the contract check found in one file."""

    path: Path
    kind: str  # "yaml" | "python" | "unknown"
    errors: list[str] = field(default_factory=list)
    # Notes are coverage statements, not defects: places the check could not
    # reach. They never change the exit code, and they are never omitted.
    notes: list[str] = field(default_factory=list)
    # False when the file could not be parsed far enough to check anything.
    readable: bool = True

    @property
    def ok(self) -> bool:
        return not self.errors


def check_driver_file(path: Path) -> FileCheckResult:
    """Run the driver contract over one file.

    The single entry point for every caller — the CLI below,
    ``simulator.validate``, and any other door that wants the contract verdict
    on a path. Callers decide how to print it and what to exit with; they do
    not re-derive it.
    """
    if path.suffix == ".avcdriver":
        return _check_yaml_driver(path)
    if path.suffix == ".py":
        return _check_python_driver(path)
    return FileCheckResult(
        path,
        "unknown",
        errors=[
            f"unsupported file type '{path.suffix or path.name}' — expected "
            f".avcdriver (YAML) or .py (Python)"
        ],
        readable=False,
    )


def _check_yaml_driver(path: Path) -> FileCheckResult:
    result = FileCheckResult(path, "yaml")
    try:
        driver_def = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        result.errors.append(f"could not read as YAML — {exc}")
        result.readable = False
        return result
    if not isinstance(driver_def, dict):
        result.errors.append("top-level YAML must be a mapping")
        result.readable = False
        return result

    # Imported here, not at module scope: the discovery-block check pulls in
    # the discovery engine, and this module stays cheap to import.
    from server.drivers.driver_loader import validate_driver_definition

    result.errors.extend(validate_driver_definition(driver_def, strict=True))
    return result


def _check_python_driver(path: Path) -> FileCheckResult:
    # Imported here so the module-level import stays stdlib + yaml.
    from server.drivers.avcdriver_semantic import unknown_key_errors

    result = FileCheckResult(path, "python")
    try:
        info, unevaluated = extract_python_driver_info_full(path)
    except (OSError, ExtractError) as exc:
        # Not a skip. A file the checker cannot read is a file the checker
        # cannot vouch for, and saying nothing about it reads as a pass.
        message = str(exc)
        prefix = f"{path.name}: "
        result.errors.append(
            message[len(prefix):] if message.startswith(prefix) else message
        )
        result.readable = False
        return result

    result.errors.extend(unknown_key_errors(info))
    result.errors.extend(python_driver_info_issues(info))

    for skip in python_driver_reference_skips(info):
        result.notes.append(f"cross-reference not checked — {skip}")

    if unevaluated:
        result.notes.append(
            f"{len(unevaluated)} value(s) could not be read from the source "
            f"(built from a constant, a call or a comprehension); keys nested "
            f"under them are unchecked: {', '.join(unevaluated)}"
        )
    skipped = _class_dependent_notes(info)
    if skipped:
        result.notes.append(
            f"{len(skipped)} check(s) skipped, they need the loaded driver "
            f"class: {'; '.join(skipped)}"
        )
    return result


def _class_dependent_notes(info: dict) -> list[str]:
    """Which class-dependent checks this driver would have been subject to.

    Reported only when the declaration that triggers one is present, so the
    note names a real gap rather than boilerplate on every file.
    """
    skipped: list[str] = []
    actions = info.get("actions")
    if isinstance(actions, list) and any(
        isinstance(a, dict) and a.get("kind") == "setup" for a in actions
    ):
        skipped.append(CLASS_DEPENDENT_CHECKS[0])
    if isinstance(info.get("device_settings"), dict) and info["device_settings"]:
        skipped.append(CLASS_DEPENDENT_CHECKS[1])
    return skipped


# Directories a driver never lives in. Without this a walk of a driver repo
# picks up its own test suite: a driver test defines a stub base class carrying
# `DRIVER_INFO: dict = {}`, which is a real declaration by any honest reading of
# the source — 55 of them in the community repo today.
NON_DRIVER_DIRS = frozenset(
    {"__pycache__", ".git", ".venv", "venv", "node_modules", "tests", "test"}
)


def _is_test_module(path: Path) -> bool:
    return path.name == "conftest.py" or path.name.startswith("test_")


@dataclass
class DirectoryScan:
    """What a directory walk found, and what it passed over."""

    files: list[Path] = field(default_factory=list)
    # Files that declare a DRIVER_INFO but sit where a driver never does.
    # Counted and reported rather than dropped: an unexplained skip and a
    # clean pass look identical from the outside.
    skipped: list[Path] = field(default_factory=list)


def scan_for_drivers(path: Path) -> DirectoryScan:
    """Driver files under a path (or the file itself).

    A file named explicitly is always checked, whatever is in it — naming a
    path and being told nothing about it is the failure mode this command
    exists to remove. A directory walk skips companions (``*_sim.py``,
    ``*_discovery.py``), underscore-prefixed helpers, test modules, and any
    ``.py`` that does not declare a ``DRIVER_INFO`` at all.
    """
    if path.is_file():
        return DirectoryScan(files=[path])

    scan = DirectoryScan()
    for candidate in sorted(path.rglob("*.avcdriver")):
        if NON_DRIVER_DIRS.isdisjoint(candidate.parts):
            scan.files.append(candidate)
        else:
            scan.skipped.append(candidate)
    for candidate in sorted(path.rglob("*.py")):
        if candidate.name.startswith("_"):
            continue
        if candidate.name.endswith(COMPANION_SUFFIXES):
            continue
        if not declares_driver_info(candidate):
            continue
        if NON_DRIVER_DIRS.isdisjoint(candidate.parts) and not _is_test_module(
            candidate
        ):
            scan.files.append(candidate)
        else:
            scan.skipped.append(candidate)
    scan.files.sort()
    scan.skipped.sort()
    return scan


def iter_driver_files(path: Path) -> list[Path]:
    """The driver files under a path — the checkable half of a scan."""
    return scan_for_drivers(path).files


def _display_path(path: Path, root: Path) -> str:
    """The path as the caller would type it — relative where that is shorter.

    Native separators, so a Windows author gets ``drivers\\my_driver.py`` and
    can paste it back into their own shell. ``as_posix()`` would print
    ``C:/Users/...``, which no Windows tool emits and an editor's problem
    matcher will not recognise — and the point of this output format is that
    the terminal, CI and the IDE all say the same thing.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m server.drivers.check",
        description=(
            "Check a driver file against the OpenAVC driver contract. Works "
            "on .avcdriver (YAML) and .py (Python) drivers, at any path."
        ),
        epilog=(
            "Examples:\n"
            "  python -m server.drivers.check my_projector.py\n"
            "  python -m server.drivers.check data/driver_repo/\n"
            "\n"
            "Exits 1 when anything is wrong, 0 when the contract is clean.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="Driver file or directory to check",
    )
    args = parser.parse_args(argv)

    targets: list[Path] = []
    skipped: list[Path] = []
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"{raw}: error: no such file or directory", file=sys.stderr)
            return 1
        scan = scan_for_drivers(path)
        if not scan.files:
            print(f"{raw}: error: no driver files found", file=sys.stderr)
            return 1
        targets.extend(scan.files)
        skipped.extend(scan.skipped)

    cwd = Path.cwd()
    results = [check_driver_file(p) for p in targets]

    for result in results:
        where = _display_path(result.path, cwd)
        for message in result.errors:
            print(f"{where}: error: {message}", file=sys.stderr)
        for note in result.notes:
            print(f"{where}: note: {note}")

    failed = [r for r in results if not r.ok]
    unreadable = [r for r in results if not r.readable]

    # A single clean file says nothing and exits 0. Anything else prints the
    # coverage line, because "no findings" and "did not look" are the same
    # output otherwise — and the second one is the dangerous one. A skip counts
    # as "did not look", so it prints the line too.
    if len(results) > 1 or failed or skipped:
        summary = (
            f"\n{len(results)} file(s) checked, {len(failed)} with errors, "
            f"{len(unreadable)} unreadable"
        )
        if skipped:
            summary += (
                f"; {len(skipped)} file(s) declaring a DRIVER_INFO were "
                f"skipped as tests or helpers"
            )
        print(f"{summary}.", file=sys.stderr if failed else sys.stdout)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
