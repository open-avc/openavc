"""Regression test for asset URL encoding in the icon/image renderers.

An asset reference is stored as ``assets://<filename>`` and resolved to
``/api/projects/default/assets/<filename>`` at render time. The server's asset
FILENAME_PATTERN allows legal-but-special names (spaces, for example), so the
filename must be percent-encoded to build a working URL — exactly what the
programmer's shared ``getAssetUrl`` helper does with ``encodeURIComponent``.

Two renderers hand-built the URL WITHOUT encoding:
  * the Programmer IDE's ``ElementIcon`` (built the <img src> inline), and
  * the runtime panel's ``resolveAssetUrl`` (plus a duplicate inline build for
    the page background image).
A name with a space produced a broken URL in both.

The fix routes ElementIcon through the shared ``getAssetUrl`` and encodes the
name in the panel's single ``resolveAssetUrl`` helper (which every panel asset
build now uses). There is no vitest/jest harness in web/programmer, so — like
the other frontend regression tests — this pins the source to the fixed shape.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_asset_url_encoding.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
ELEMENT_ICON_TSX = (
    OPENAVC_ROOT / "web" / "programmer" / "src"
    / "components" / "ui-builder" / "ElementIcon.tsx"
)
PANEL_JS = OPENAVC_ROOT / "web" / "panel" / "panel.js"


def test_element_icon_uses_shared_getAssetUrl() -> None:
    src = ELEMENT_ICON_TSX.read_text(encoding="utf-8")
    assert "getAssetUrl(" in src, (
        "ElementIcon.tsx must build its asset <img src> via the shared "
        "getAssetUrl helper (which encodeURIComponent's the filename)"
    )
    # The old hand-built, un-encoded URL must not come back.
    assert "/api/projects/default/assets/" not in src, (
        "ElementIcon.tsx must not hand-build the asset URL (un-encoded); route "
        "it through getAssetUrl"
    )


def test_panel_resolveAssetUrl_encodes_name() -> None:
    src = PANEL_JS.read_text(encoding="utf-8")
    # resolveAssetUrl is the single asset-URL builder; it must encode the name.
    assert re.search(r"encodeURIComponent\(\s*ref\.slice\('assets://'\.length\)\s*\)", src), (
        "panel.js resolveAssetUrl must encodeURIComponent the asset filename"
    )
    # No panel code path may build the assets URL by slicing/replacing the
    # assets:// prefix without encoding (the old duplicate inline builds).
    unencoded = re.findall(
        r"/api/projects/default/assets/\$\{[^}]*(?:\.slice\(|\.replace\()[^}]*\}",
        src,
    )
    assert not unencoded, (
        f"panel.js still builds an un-encoded asset URL: {unencoded}"
    )
