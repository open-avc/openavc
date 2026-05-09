"""Discovery hint coverage for representative real drivers.

Verifies that any hint — OUI, hostname pattern, observed open port,
manufacturer alias, SNMP PEN — is enough to surface a device as
``possible``. The synthetic-driver tests in
``test_discovery_tier_matcher.py`` and ``test_discovery_hints_schema.py``
exercise the matcher contract; this file pins a handful of *real*
community drivers so the catalog we ship participates in matching as
expected.

If a driver here ever stops surfacing as ``possible`` for its declared
hint, the regression is in either the catalog file (signal removed or
changed) or the matcher path — both worth catching at test time
rather than during a live scan.

NOTE: Most catalog drivers still carry the pre-rewrite schema. As
they migrate in lockstep with the rewrite, they get added here. For
now the suite covers ``shure_network`` (the Step 1 migrated sample)
plus a synthetic driver that exercises the open-port hint path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.discovery.hints import (
    build_signal_index,
    parse_driver_discovery,
)
from server.discovery.result import DeviceState
from server.discovery.tier_matcher import (
    TierMatcher,
    evidence_hostname,
    evidence_open_port,
    evidence_oui,
)

# openavc-drivers/ is a sibling of openavc/ in the workspace.
DRIVERS_ROOT = Path(__file__).resolve().parent.parent.parent / "openavc-drivers"

pytestmark = pytest.mark.skipif(
    not DRIVERS_ROOT.exists(),
    reason=f"openavc-drivers not found at {DRIVERS_ROOT}",
)


# (relative path, driver_id, OUI MACs that should match, hostnames that should match)
# Picked to cover the OUI + hostname hint paths against a real driver
# that has been migrated to the new discovery schema.
_FIXTURES: list[tuple[str, str, list[str], list[str]]] = [
    (
        "audio/shure_network.avcdriver",
        "shure_network",
        ["00:0e:dd:11:22:33", "d8:34:ee:11:22:33"],
        ["MXA920-AABBCC", "ANI4IN-1"],
    ),
]


def _load_driver(rel_path: str) -> dict:
    path = DRIVERS_ROOT / rel_path
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "rel_path,driver_id,oui_macs,hostnames",
    _FIXTURES,
    ids=[f[1] for f in _FIXTURES],
)
def test_catalog_hints_produce_possible(
    rel_path: str,
    driver_id: str,
    oui_macs: list[str],
    hostnames: list[str],
) -> None:
    """A real catalog driver's declared hints produce ``possible`` for
    a synthetic device that carries the matching evidence."""
    raw = _load_driver(rel_path)
    hint = parse_driver_discovery(raw)
    assert hint is not None, f"{driver_id} should produce a hint"
    assert hint.driver_id == driver_id

    idx = build_signal_index([hint])
    matcher = TierMatcher(idx)

    for mac in oui_macs:
        result = matcher.match([evidence_oui(mac)])
        assert result.state == DeviceState.POSSIBLE, (
            f"{driver_id}: MAC {mac} did not produce POSSIBLE "
            f"(got {result.state}, candidates={result.candidates})"
        )
        assert driver_id in result.candidates

    for host in hostnames:
        result = matcher.match([evidence_hostname(host)])
        assert result.state == DeviceState.POSSIBLE, (
            f"{driver_id}: hostname {host!r} did not produce POSSIBLE "
            f"(got {result.state}, candidates={result.candidates})"
        )
        assert driver_id in result.candidates


def test_open_port_hint_pinned_via_synthetic_driver() -> None:
    """Pin the open-port hint path with a synthetic driver so the
    wiring stays covered while catalog adoption catches up.
    """
    synthetic = {
        "id": "synthetic_avport_widget",
        "name": "Synthetic AV Port Widget",
        "discovery": {
            "port_open": [9876],
        },
    }
    hint = parse_driver_discovery(synthetic)
    assert hint is not None
    idx = build_signal_index([hint])
    matcher = TierMatcher(idx)

    result = matcher.match([evidence_open_port(9876)])
    assert result.state == DeviceState.POSSIBLE
    assert "synthetic_avport_widget" in result.candidates
