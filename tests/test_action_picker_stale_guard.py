"""Regression test for the ActionPicker device-info fetch race.

The ``device.command`` action picker fetches the selected device's command list
and param schema in a ``useEffect`` keyed on the chosen device. The effect used
to call ``api.getDevice(selectedDevice).then(setDeviceInfo)`` with no cleanup,
so if the integrator switched devices before an earlier request resolved, the
stale response could land last and overwrite ``deviceInfo`` for the
newly-selected device — briefly offering commands that don't exist on it.

The fix adds the standard stale-response guard: a ``cancelled`` flag flipped in
the effect's cleanup return, checked before every ``setDeviceInfo``.

There is no vitest/jest harness in web/programmer, so — like the other frontend
regression tests — this pins the source to the fixed shape: the unguarded form
can't quietly come back, and the guarded form must be present.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_action_picker_stale_guard.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
ACTION_PICKER_TSX = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src"
    / "components" / "ui-builder" / "BindingEditor" / "ActionPicker.tsx"
)


def _source() -> str:
    return ACTION_PICKER_TSX.read_text(encoding="utf-8")


def test_no_unguarded_device_fetch() -> None:
    src = _source()
    assert "api.getDevice(selectedDevice).then(setDeviceInfo)" not in src, (
        "ActionPicker.tsx must not fetch device info without a stale-response "
        "guard — an out-of-order response can overwrite the selected device's "
        "command list"
    )


def test_device_effect_has_cancellation_guard() -> None:
    src = _source()
    # The effect that fetches device info must flip a cancelled flag in cleanup
    # and check it before applying the result.
    assert "let cancelled = false" in src, (
        "ActionPicker.tsx device-info effect must declare a cancelled guard"
    )
    assert "cancelled = true" in src, (
        "ActionPicker.tsx device-info effect must flip the guard in a cleanup return"
    )
    # setDeviceInfo(info) from the async resolve must be gated on the guard.
    assert re.search(r"if \(!cancelled\)\s*setDeviceInfo", src), (
        "ActionPicker.tsx must gate setDeviceInfo on the cancelled guard so a "
        "stale getDevice() response can't overwrite the current device"
    )
