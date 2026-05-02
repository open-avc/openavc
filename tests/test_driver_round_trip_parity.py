"""
Driver round-trip parity test.

For every `.avcdriver` file in the community driver repo, load the YAML,
push it through `DriverDefinitionRequest` (the same Pydantic gate the API
uses on save), and assert the round-trip is semantically equivalent.

This catches the failure mode that bit Phase 0: when a new top-level field
is added to the runtime but the API request model still uses the default
`extra='ignore'`, every save silently drops the field. The Driver Builder
renders the field, the user fills it in, the live YAML preview shows it,
and then save strips it. The fix in P0 was `model_config = ConfigDict(extra="allow")`,
and this test makes sure that contract stays in place.

It also catches type coercions that would corrupt a driver — for example,
if the request model declared a field as `str` when the runtime accepts
`str | None`, the round-trip would substitute an empty string and the
diff would catch it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.api.models import DriverDefinitionRequest

# openavc-drivers/ is a sibling of openavc/ in the workspace.
DRIVERS_ROOT = Path(__file__).resolve().parent.parent.parent / "openavc-drivers"


def _discover_driver_files() -> list[Path]:
    """Walk the community driver repo and return every .avcdriver file."""
    if not DRIVERS_ROOT.exists():
        return []
    return sorted(DRIVERS_ROOT.rglob("*.avcdriver"))


DRIVER_FILES = _discover_driver_files()

# Treat a missing sibling repo as a skip, not a hard failure — devs may
# only have the openavc/ repo cloned. CI clones both.
pytestmark = pytest.mark.skipif(
    not DRIVER_FILES,
    reason=f"No community drivers found at {DRIVERS_ROOT}",
)


@pytest.mark.parametrize("driver_path", DRIVER_FILES, ids=lambda p: p.name)
def test_round_trip_preserves_every_field(driver_path: Path) -> None:
    """Every top-level key in the YAML survives a Pydantic round-trip.

    A diff in either direction means the API request model is silently
    dropping or mutating fields the runtime supports — the exact bug
    `model_config = ConfigDict(extra="allow")` on `DriverDefinitionRequest`
    was added to prevent. Treat any diff as a regression: either declare
    the new field on the model, or extend the runtime contract intentionally.
    """
    src = yaml.safe_load(driver_path.read_text(encoding="utf-8"))
    assert isinstance(src, dict), f"{driver_path.name} is not a YAML mapping"

    req = DriverDefinitionRequest(**src)
    out = req.model_dump(exclude_none=True, exclude_unset=True)

    missing = [k for k in src if k not in out]
    added = [k for k in out if k not in src]
    mutated = [
        k for k in src if k in out and src[k] != out[k]
    ]

    assert not missing, (
        f"{driver_path.name}: round-trip dropped fields {missing}. "
        f"Add them to DriverDefinitionRequest or confirm the runtime "
        f"actually ignores them."
    )
    assert not added, (
        f"{driver_path.name}: round-trip introduced fields {added}. "
        f"DriverDefinitionRequest is injecting defaults the source file "
        f"doesn't have — this changes byte-equality on save."
    )
    assert not mutated, (
        f"{driver_path.name}: round-trip mutated fields {mutated}. "
        f"For each: src={ {k: src[k] for k in mutated} }, "
        f"out={ {k: out[k] for k in mutated} }"
    )
