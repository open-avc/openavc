"""Regression test for ISC manual-peer port validation.

The "add peer" field in ISCView validated an address with
``/^[\\w.-]+(:\\d{1,5})?$/`` and silently ``return``ed on any mismatch. A 1-5
digit port passes that regex even when out of range (``host:99999``,
``host:0``), so an unusable peer saved with no feedback and just failed to
connect at runtime.

The fix parses the optional ``:port`` suffix, rejects anything outside
1-65535, and surfaces an inline error (peerError) for every rejection — the
authoring surface, where the user can act on it, is the right validation point
(the runtime deliberately stores peers as free-form strings and fails-soft).

There is no vitest/jest harness in web/programmer, so — like the other frontend
regression tests — this pins the source to the fixed shape.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_isc_peer_port_validation.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
ISC_VIEW_TSX = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "ISCView.tsx"


def _source() -> str:
    return ISC_VIEW_TSX.read_text(encoding="utf-8")


def test_no_silent_regex_only_validation() -> None:
    src = _source()
    # The old permissive check silently dropped bad input with no port-range
    # test and no user feedback.
    assert "if (!/^[\\w.-]+(:\\d{1,5})?$/.test(addr)) return;" not in src, (
        "ISCView handleAddPeer must not fall back to the regex-only, silent-return "
        "validation (it accepts out-of-range ports and gives no feedback)"
    )


def test_port_range_is_enforced() -> None:
    src = _source()
    assert "65535" in src, (
        "ISCView must reject a peer port outside 1-65535"
    )
    # A numeric parse of the port suffix must exist (not just the digit-count regex).
    assert re.search(r"Number\(m\[2\]\)|parseInt", src), (
        "ISCView must numerically parse the :port suffix to range-check it"
    )


def test_rejections_surface_feedback() -> None:
    src = _source()
    assert "setPeerError(" in src and "peerError" in src, (
        "ISCView must surface an inline error (peerError) when a peer entry is "
        "rejected, instead of silently discarding it"
    )
