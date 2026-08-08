"""The generated control-minimum types must match the rules.

``openavc/ui/control_minimums.py`` holds the rules; ``python -m
openavc.ui.minimums_gen`` renders them into the Programmer IDE's
``uiMinimums.gen.ts``. This re-renders and compares byte-for-byte, so editing
the rules without regenerating -- or hand-editing the artifact -- fails CI
instead of letting the two surfaces quietly hold different numbers.

Same arrangement as the driver contract (tests/test_driver_contract_generated.py).
"""

from __future__ import annotations

from pathlib import Path

from openavc.ui.control_minimums import RULES
from openavc.ui.minimums_gen import ARTIFACT, render

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_committed_artifact_matches_the_rules() -> None:
    path = REPO_ROOT / ARTIFACT
    assert path.is_file(), f"{ARTIFACT} is missing -- run 'python -m openavc.ui.minimums_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(), (
        f"{ARTIFACT} does not match the rules -- regenerate with "
        f"'python -m openavc.ui.minimums_gen' (never edit it by hand)"
    )


def test_every_rule_reaches_the_artifact() -> None:
    """A type added to the rules must appear, not silently stay Python-only."""
    rendered = render()
    for type_ in RULES:
        assert f'"{type_}"' in rendered, f"{type_} is in RULES but not in the generated types"


def test_every_number_says_where_it_came_from() -> None:
    """A minimum with no provenance is one nobody can check or safely change.

    The table this replaced had numbers whose origin nobody could reconstruct,
    and several of them turned out to be wrong.
    """
    for type_, rule in RULES.items():
        for internal in rule.internals:
            assert internal.source, f"{type_}: {internal.part} has no source"
            assert internal.origin in ("declared", "font-driven"), (
                f"{type_}: {internal.part} has an unknown origin {internal.origin!r}"
            )
        if rule.scales_with:
            assert rule.scales_with.source, f"{type_}: scaling internal has no source"
