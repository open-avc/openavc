"""Regression test for the Programmer tab-close guard.

The app warns before the browser tab closes or reloads with unsaved work.
That guard originally checked only the project store's ``dirty`` flag, so a
half-authored driver in the Driver Builder — a module-global draft that only
persists to disk on Save and stays dirty even after navigating away from the
Driver Builder view — was lost on tab close/reload with no native prompt,
unlike the project, script editor, and UI builder which all warn.

The fix extends the global ``beforeunload`` guard in App.tsx to also check the
driver-builder store, matching the other unsaved-work guards. There is no pure
helper to unit-test here (the guard is a one-line boolean read of two stores
inside a browser event handler), so this pins App.tsx to the fixed shape the
same way the other frontend regression tests pin their editors: the old
project-store-only guard can't quietly come back.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_app_beforeunload_guard.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
APP_TSX = OPENAVC_ROOT / "web" / "programmer" / "src" / "App.tsx"


def _beforeunload_guard() -> str:
    """Return the source of App.tsx's BeforeUnloadEvent handler."""
    src = APP_TSX.read_text(encoding="utf-8")
    match = re.search(r"BeforeUnloadEvent.*?\n\s*\};", src, re.DOTALL)
    assert match, "App.tsx no longer has a BeforeUnloadEvent handler"
    return match.group(0)


def test_app_imports_driver_builder_store() -> None:
    src = APP_TSX.read_text(encoding="utf-8")
    assert "useDriverBuilderStore" in src, (
        "App.tsx must reference the driver-builder store so its global "
        "unsaved-work guard can see a dirty driver draft"
    )


def test_beforeunload_guard_covers_driver_builder_draft() -> None:
    guard = _beforeunload_guard()
    assert "useProjectStore.getState().dirty" in guard, (
        "the tab-close guard must still warn on an unsaved project"
    )
    assert "useDriverBuilderStore.getState().dirty" in guard, (
        "the tab-close guard must also warn on an unsaved driver-builder draft"
    )
