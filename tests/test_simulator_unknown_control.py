"""Regression test for the simulator's unknown-control-type rendering.

DynamicControls switches on control.type. The ControlDef union is compile-time
only — the actual control list comes from the API — so a driver's
simulator.controls can carry a type this build doesn't render (a typo like
"meter" for "meters", or a newer type). The old default branch was a bare
`return null`, so such a control vanished with no diagnostic, leaving the author
staring at a blank card. The default branch now console.warns and renders a
visible "Unknown control type" marker instead.

DynamicControls is a rendering component (a switch returning JSX, no pure seam
to unit-test), so this pins the source the same way the other frontend
regression tests pin their components.
"""
from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_simulator_unknown_control.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
COMPONENT = (
    OPENAVC_ROOT
    / "web" / "simulator" / "src" / "components" / "controls"
    / "DynamicControls.tsx"
)


def test_unknown_control_type_is_surfaced_not_dropped() -> None:
    src = COMPONENT.read_text(encoding="utf-8")

    # The silent-drop default (`default:` immediately returning null) must be gone.
    assert not re.search(r"default:\s*\n\s*return null;", src), (
        "the default branch must not silently return null for an unknown control type"
    )

    # An unknown type must now warn and render a visible fallback.
    assert "console.warn(" in src, "unknown control type should log a console.warn"
    assert "Unknown control type" in src, (
        "unknown control type should render a visible 'Unknown control type' marker"
    )
