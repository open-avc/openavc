"""Every shipped driver opens in the Driver Builder without an error.

The rejection corpus (test_driver_builder_validate.py) proves the Builder
refuses what the driver loader refuses. Nothing proved the other direction:
that the Builder *accepts* what the loader accepts. That gap is not
theoretical — it is where both defects found on 2026-07-27 and 2026-07-28
came from, and it is structurally invisible to the rejection corpus, which
only ever feeds in definitions that are supposed to be flagged.

A rule that is stricter than the platform does real damage. The Builder
hard-blocks import on any error (``importBlockers``), so a driver the
platform loads, runs and ships cannot be opened, edited or re-saved — while
every gate stays green, because nothing replays a valid driver through the
validator.

The corpus of valid drafts that needs no hand-maintenance is the shipped
library itself: the built-in generics plus every driver the community
catalog publishes. Each one loads on the platform by definition, so each one
must raise zero Builder errors. When this fails, the driver named is one an
author cannot open — read the message before assuming the driver is at
fault, because the rule is the more likely defect.

Warnings are deliberately not asserted on. They are advice an author can
read and ignore ("Version is empty"), they do not block anything, and
pinning them here would turn every prose tweak into a test failure.

Resolves the library through OPENAVC_DRIVERS_ROOT, falling back to the
workspace sibling, and a run that promised the corpus fails instead of
collecting an empty sweep (see tests/gates.py).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

from tests import gates

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
BUILTIN_DEFINITIONS = OPENAVC_ROOT / "server" / "drivers" / "definitions"

DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT") or OPENAVC_ROOT.parent / "openavc-drivers"
)

HARNESS = (
    OPENAVC_ROOT / "tests" / "fixtures" / "driver_builder_reverse_corpus_harness.cjs"
)
VALIDATOR = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "validateDriver.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _discover_driver_files() -> list[Path]:
    """Every shipped .avcdriver: the built-in generics plus the library."""
    files = sorted(BUILTIN_DEFINITIONS.rglob("*.avcdriver"))
    if DRIVERS_ROOT.exists():
        files += sorted(DRIVERS_ROOT.rglob("*.avcdriver"))
    return files


DRIVER_FILES = _discover_driver_files()


def _corpus_reason() -> str | None:
    if len(DRIVER_FILES) <= 4:
        return f"no community drivers found at {DRIVERS_ROOT}"
    return None


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "driver builder reverse corpus harness missing"
    if not VALIDATOR.is_file():
        return "validateDriver.ts missing"
    return None


@pytest.fixture(scope="module")
def builder_verdicts() -> dict:
    """Run every shipped definition through the real validateDriver."""
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)

    definitions = {}
    for path in DRIVER_FILES:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(doc, dict), f"{path.name} is not a YAML mapping"
        definitions[path.name] = doc

    with tempfile.TemporaryDirectory() as tmp:
        payload = Path(tmp) / "drivers.json"
        payload.write_text(json.dumps(definitions), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(HARNESS), str(VALIDATOR), str(payload)],
            capture_output=True,
            text=True,
            cwd=str(OPENAVC_ROOT),
            env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
            timeout=120,
        )
    if proc.returncode != 0:
        raise AssertionError(
            f"reverse corpus harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
@pytest.mark.parametrize("driver_path", DRIVER_FILES, ids=lambda p: p.name)
def test_shipped_driver_has_no_builder_errors(
    builder_verdicts: dict, driver_path: Path
) -> None:
    """A driver the platform ships must open in the Builder cleanly.

    An error here means the Builder is stricter than the platform, and the
    driver named cannot be imported or edited at all.
    """
    verdict = builder_verdicts[driver_path.name]
    assert not verdict["errors"], (
        f"{driver_path.name} loads on the platform but the Driver Builder "
        f"reports errors, so it cannot be imported or edited:\n  "
        + "\n  ".join(verdict["errors"])
    )


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
@pytest.mark.parametrize("driver_path", DRIVER_FILES, ids=lambda p: p.name)
def test_shipped_driver_does_not_crash_the_validator(
    builder_verdicts: dict, driver_path: Path
) -> None:
    """A throw is worse than an error: it blanks the editor for that driver."""
    verdict = builder_verdicts[driver_path.name]
    assert verdict["threw"] is None, (
        f"{driver_path.name}: validateDriver threw instead of reporting an "
        f"issue: {verdict['threw']}"
    )


@gates.skipif_missing(gates.DRIVER_CORPUS, _corpus_reason())
def test_the_sweep_covers_the_whole_shipped_library(builder_verdicts: dict) -> None:
    """A silent drop would make this file report green over nothing."""
    missing = sorted({p.name for p in DRIVER_FILES} - set(builder_verdicts))
    assert not missing, f"harness did not replay: {missing}"
    assert len(DRIVER_FILES) > 4, "the community library did not resolve"
