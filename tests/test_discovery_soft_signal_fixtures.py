"""Discovery soft-signal coverage for representative real drivers.

Phase 8 (Tasks 8.1, 8.3, 8.4, 8.5) widened the matcher so any soft
signal — OUI, hostname pattern, open AV port, or SNMP PEN — is enough
to surface a device as ``possible``. The synthetic-driver tests in
``test_discovery_tier_matcher.py`` and ``test_discovery_hints_schema.py``
exercise the matcher contract using ``_drv()``-built drivers; this
file pins a small set of *real* community drivers so that the
catalog we ship actually participates in matching as expected.

If a driver here ever stops surfacing as ``possible`` for its declared
soft signal, the regression is in either the catalog file (signal
removed/changed) or the matcher path — both are worth catching at
test time rather than during a live scan.
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
# Picked to cover the three currently-populated soft-signal kinds (OUI single,
# OUI multi, hostname pattern) across audio, lighting, switching, camera, and
# streaming categories.
_FIXTURES: list[tuple[str, str, list[str], list[str]]] = [
    (
        "audio/shure_network.avcdriver",
        "shure_network",
        ["00:0e:dd:11:22:33"],
        [],
    ),
    (
        "audio/yamaha_mtx_mrx.avcdriver",
        "yamaha_mtx_mrx",
        ["00:a0:de:11:22:33"],
        [],
    ),
    (
        "lighting/etc_eos.avcdriver",
        "etc_eos",
        ["00:c0:16:aa:bb:cc"],
        [],
    ),
    (
        "lighting/philips_hue.avcdriver",
        "philips_hue",
        # Two OUI prefixes; both should resolve.
        ["00:17:88:11:22:33", "ec:b5:fa:11:22:33"],
        [],
    ),
    (
        "switchers/atlona_ome_ps62.avcdriver",
        "atlona_ome_ps62",
        ["b8:98:b0:01:23:45"],
        [],
    ),
    (
        "cameras/panasonic_awhe.avcdriver",
        "panasonic_awhe",
        ["00:80:45:11:22:33", "70:1d:7c:11:22:33"],
        [],
    ),
    (
        "streaming/barco_clickshare_cx.avcdriver",
        "barco_clickshare_cx",
        ["00:04:a5:de:ad:be"],
        ["clickshare-meeting-1"],
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
def test_catalog_soft_signals_produce_possible(
    rel_path: str,
    driver_id: str,
    oui_macs: list[str],
    hostnames: list[str],
) -> None:
    """A real catalog driver's declared soft signals produce ``possible``
    for a synthetic device that carries the matching evidence."""
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


def test_open_port_soft_signal_pinned_via_synthetic_driver() -> None:
    """No catalog driver currently declares ``open_ports:`` — Phase 8 added
    the schema field but no community driver has backfilled it yet.

    Until catalog adoption catches up, pin the open-port soft-signal path
    with a synthetic driver so the wiring stays covered.
    """
    synthetic = {
        "id": "synthetic_avport_widget",
        "name": "Synthetic AV Port Widget",
        "discovery": {
            "open_ports": [9876],
        },
    }
    hint = parse_driver_discovery(synthetic)
    assert hint is not None
    idx = build_signal_index([hint])
    matcher = TierMatcher(idx)

    result = matcher.match([evidence_open_port(9876)])
    assert result.state == DeviceState.POSSIBLE
    assert "synthetic_avport_widget" in result.candidates
