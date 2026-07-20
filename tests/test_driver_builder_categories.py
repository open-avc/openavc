"""Regression test for the driver-authoring category dropdowns.

The Driver Builder's category ``<select>`` used to offer four values the
community catalog rejects — ``scaler``, ``recorder``, ``relay``, ``other`` — and
omit the valid ``streaming``. A driver authored with an invalid category saves,
loads, and runs locally with no warning, then is rejected by build_index.py's
Pydantic validator only at catalog-submission CI, far from the authoring
surface. The Python-driver ``CreateDriverDialog`` had the mirror-image gap: an
all-valid list that silently dropped ``streaming`` and ``power``.

The fix routes every authoring dropdown through one shared canonical list
(``driverCategories.ts``) so the surfaces can't drift from the catalog again.
There is no vitest/jest harness in web/programmer, so — like the other frontend
regression tests — this pins the source to the fixed shape: the invalid values
can't quietly come back, and both dropdowns must consume the shared list.

The ten values must stay in lockstep with build_index.py's DRIVER_CATEGORIES
(openavc-drivers) and avcdriver.schema.json's category enum.
"""

from __future__ import annotations

import re
from pathlib import Path

from server.drivers.spec import CATEGORIES

# Repo root = openavc/ (this file is openavc/tests/test_driver_builder_categories.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
PROGRAMMER_SRC = OPENAVC_ROOT / "web" / "programmer" / "src"
CATEGORIES_TS = PROGRAMMER_SRC / "components" / "driver-builder" / "driverCategories.ts"
DRIVER_EDITOR_TSX = PROGRAMMER_SRC / "components" / "driver-builder" / "DriverEditor.tsx"
CREATE_DRIVER_DIALOG_TSX = PROGRAMMER_SRC / "components" / "scripts" / "CreateDriverDialog.tsx"

# The catalog-valid categories, straight from the contract tables.
CANONICAL_CATEGORIES = set(CATEGORIES)
# Values the old DriverEditor dropdown offered that the catalog rejects.
CATALOG_INVALID = {"scaler", "recorder", "relay", "other"}


def _shared_category_values() -> set[str]:
    src = CATEGORIES_TS.read_text(encoding="utf-8")
    return set(re.findall(r'value:\s*"([^"]+)"', src))


def test_shared_list_is_exactly_the_canonical_ten() -> None:
    values = _shared_category_values()
    assert values == CANONICAL_CATEGORIES, (
        "driverCategories.ts must offer exactly the catalog's ten categories "
        f"(got {sorted(values)})"
    )


def test_shared_list_offers_no_catalog_invalid_values() -> None:
    values = _shared_category_values()
    leaked = values & CATALOG_INVALID
    assert not leaked, f"driverCategories.ts still offers catalog-invalid categories: {sorted(leaked)}"


def test_driver_editor_uses_the_shared_list() -> None:
    src = DRIVER_EDITOR_TSX.read_text(encoding="utf-8")
    assert 'from "./driverCategories"' in src and "DRIVER_CATEGORIES" in src, (
        "DriverEditor.tsx must render its category options from the shared list"
    )
    for bad in CATALOG_INVALID:
        assert f'value="{bad}"' not in src, (
            f"DriverEditor.tsx must not hardcode the catalog-invalid '{bad}' option"
        )


def test_create_driver_dialog_uses_the_shared_list() -> None:
    src = CREATE_DRIVER_DIALOG_TSX.read_text(encoding="utf-8")
    assert "driverCategories" in src and "DRIVER_CATEGORIES" in src, (
        "CreateDriverDialog.tsx must render its category options from the shared "
        "list so it can't drop valid categories (streaming/power)"
    )
    assert "const CATEGORIES" not in src, (
        "CreateDriverDialog.tsx must not keep its own drifting category list"
    )
