"""Regression test for the Device Setup dialog's queue-failure result rendering.

When some setup settings apply online but the device is disconnected for others,
handleApply queues the disconnected ones via storePendingSettings. If that POST
itself failed, the handler set a top-level "Failed to queue settings" banner and
returned BEFORE setResults(newResults), so the per-field Saved/Queued indicators
were discarded — the dialog under-reported what actually happened. The fix moves
setResults(newResults) before the early return.

The handler is inline React with useState setters and api calls (no pure seam to
unit-test), so this pins the source to the fixed ordering the same way the other
frontend regression tests pin their components.
"""
from __future__ import annotations

from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_device_settings_setup_dialog.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
DIALOG = (
    OPENAVC_ROOT
    / "web" / "programmer" / "src" / "components" / "shared"
    / "DeviceSettingsSetupDialog.tsx"
)


def test_queue_failure_surfaces_per_field_results() -> None:
    src = DIALOG.read_text(encoding="utf-8")
    idx = src.find("Failed to queue settings")
    assert idx != -1, "could not find the queue-failure banner in the dialog"
    # setResults(newResults) must run just before the banner, so the online
    # Saved/Queued per-field indicators still render on a queue failure.
    preceding = src[max(0, idx - 200):idx]
    assert "setResults(newResults)" in preceding, (
        "setResults(newResults) must run before the 'Failed to queue settings' "
        "banner/return so per-field results aren't discarded"
    )
