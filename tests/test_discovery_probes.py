"""Generic fixture-driven test for driver-declared discovery probes.

Each ``.avcdriver`` may declare a ``tcp_probe`` or ``udp_probe``
fingerprint. This test loops over every loaded driver, looks for a
captured-response fixture in ``tests/fixtures/discovery/<driver_id>.bin``
(or ``.txt``), and replays it through ``probe_runner._matches`` and
``_apply_extract`` to confirm:

1. The declared response matcher matches the captured payload.
2. Each ``extract`` rule with a static value or matching regex
   produces a non-empty value.
3. Reserved extract keys (``manufacturer`` / ``make``) — the keys the
   ``extract_vendor_strings`` path lifts to drive peer-driver
   manufacturer_alias narrowing — appear when declared.

Adding a new probe-supporting driver is a two-step PR (declare in
openavc-drivers, drop a fixture here); no per-driver test code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from server.discovery.hints import (
    CustomProbeSpec,
    parse_driver_discovery,
)
from server.discovery.probe_runner import _apply_extract, _matches

# Repo paths.
TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = TESTS_DIR / "fixtures" / "discovery"
OPENAVC_ROOT = TESTS_DIR.parent
WORKSPACE_ROOT = OPENAVC_ROOT.parent
BUILTIN_DEFINITIONS_DIR = OPENAVC_ROOT / "server" / "drivers" / "definitions"
COMMUNITY_DRIVERS_DIR = WORKSPACE_ROOT / "openavc-drivers"
COMMUNITY_INDEX = COMMUNITY_DRIVERS_DIR / "index.json"


def _load_driver_files(directories: list[Path]) -> list[dict[str, Any]]:
    """Recursively load every ``*.avcdriver`` from the given directories,
    plus parse ``openavc-drivers/index.json`` for Python drivers.

    Python drivers carry ``DRIVER_INFO`` in code, so they don't show up
    in a YAML scan. ``build_index.py`` already extracts the discovery
    block from each Python driver into ``index.json``, so reading the
    catalog index gives us all drivers regardless of format.
    """
    driver_defs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for filepath in sorted(directory.rglob("*.avcdriver")):
            try:
                data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            driver_id = data.get("id")
            if not isinstance(driver_id, str) or not driver_id:
                continue
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)
            driver_defs.append(data)

    if COMMUNITY_INDEX.exists():
        try:
            catalog = json.loads(COMMUNITY_INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            catalog = {}
        for entry in catalog.get("drivers") or []:
            if not isinstance(entry, dict):
                continue
            driver_id = entry.get("id")
            if not isinstance(driver_id, str) or not driver_id:
                continue
            if driver_id in seen_ids:
                continue
            seen_ids.add(driver_id)
            driver_defs.append(entry)
    return driver_defs


def _collect_probe_specs() -> list[tuple[str, str, CustomProbeSpec, list[str]]]:
    """Collect (driver_id, kind, spec, manufacturer_aliases) for every declared probe.

    ``kind`` is ``"udp_probe"`` or ``"tcp_probe"`` so test ids in
    pytest output stay readable.
    """
    out: list[tuple[str, str, CustomProbeSpec, list[str]]] = []
    drivers = _load_driver_files([BUILTIN_DEFINITIONS_DIR, COMMUNITY_DRIVERS_DIR])
    for driver_def in drivers:
        try:
            hint = parse_driver_discovery(driver_def)
        except Exception:
            # parse failures are covered by test_discovery_hints_schema.
            continue
        if hint is None:
            continue
        aliases = list(hint.manufacturer_alias)
        if hint.udp_probe is not None:
            out.append((
                hint.driver_id, "udp_probe", hint.udp_probe, aliases,
            ))
        if hint.tcp_probe is not None:
            out.append((
                hint.driver_id, "tcp_probe", hint.tcp_probe, aliases,
            ))
    return out


def _fixture_for(driver_id: str) -> Path | None:
    for ext in (".bin", ".txt"):
        candidate = FIXTURE_DIR / f"{driver_id}{ext}"
        if candidate.exists():
            return candidate
    return None


_PROBE_SPECS = _collect_probe_specs()


@pytest.mark.skipif(
    not _PROBE_SPECS,
    reason="No drivers declare tcp_probe or udp_probe yet",
)
def test_every_declared_probe_has_a_fixture():
    """Every declared probe needs a captured-response fixture.

    Lands as a single failing test as soon as a Phase 10 PR adds a
    probe declaration without committing the corresponding fixture —
    that's the contract that makes the rest of this file a meaningful
    regression test instead of a silent no-op.
    """
    missing = [
        f"{driver_id} ({kind})"
        for driver_id, kind, _spec, _aliases in _PROBE_SPECS
        if _fixture_for(driver_id) is None
    ]
    assert not missing, (
        "Drivers declaring a probe must ship a captured response at "
        f"tests/fixtures/discovery/<driver_id>.bin (or .txt). Missing: {missing}"
    )


@pytest.mark.parametrize(
    ("driver_id", "kind", "spec", "vendor_aliases"),
    [
        pytest.param(driver_id, kind, spec, aliases, id=f"{driver_id}-{kind}")
        for driver_id, kind, spec, aliases in _PROBE_SPECS
        if _fixture_for(driver_id) is not None
    ],
)
def test_fixture_replays_through_probe_runner(
    driver_id: str,
    kind: str,
    spec: CustomProbeSpec,
    vendor_aliases: list[str],
):
    """Captured response must satisfy the matcher + every extract rule.

    Uses the same ``_matches`` and ``_apply_extract`` helpers the live
    runner calls per packet; if those return mismatched / missing
    values the deterministic matcher will too.
    """
    fixture = _fixture_for(driver_id)
    assert fixture is not None  # gated by the skipif filter above
    payload = fixture.read_bytes()

    assert _matches(payload, spec.response_match), (
        f"{driver_id}: declared expect / expect_regex / expect_hex did "
        f"not match captured fixture {fixture.name!r}."
    )

    reserved, extracted = _apply_extract(payload, spec.extract)
    expected_fields = {rule.field_name for rule in spec.extract}
    actual_fields = set(reserved) | set(extracted)
    missing_fields = expected_fields - actual_fields
    assert not missing_fields, (
        f"{driver_id}: declared extract field(s) produced no value "
        f"against captured fixture: {sorted(missing_fields)}"
    )

    # Manufacturer narrowing contract: if the driver claims peers via
    # manufacturer_alias, the manufacturer/make extract result has to
    # match one of those aliases (case-insensitive). Otherwise the
    # cross-vendor demotion can't pick a vendor-specific peer.
    vendor_value = reserved.get("manufacturer") or reserved.get("make")
    if vendor_aliases and vendor_value:
        normalized = vendor_value.strip().lower()
        normalized_aliases = {a.strip().lower() for a in vendor_aliases}
        assert normalized in normalized_aliases, (
            f"{driver_id}: extracted vendor {vendor_value!r} does not "
            f"appear in manufacturer_alias {vendor_aliases}; peer-driver "
            "narrowing won't fire for this device."
        )
