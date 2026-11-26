"""The generated operator lists must match the ones they are rendered from.

``openavc/core/condition_eval.py`` names the operators a condition may use,
their other spellings, a State Change trigger's own ``any`` and each place a
condition sits; ``python -m openavc.core.condition_gen`` renders them into
``conditionOperators.gen.ts``, which the Programmer's operator pickers and its
copy of the comparison read. This re-renders and compares byte for byte, so an
operator changed without regenerating, or the file edited by hand, fails CI
instead of a picker offering an operator the runtime does not know, or showing
a stored spelling it does know as something else.

Same arrangement as tests/test_firing_context_generated.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openavc.core.condition_gen import ARTIFACTS, render

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("artifact", sorted(ARTIFACTS))
def test_each_committed_artifact_matches_the_lists(artifact: str) -> None:
    path = REPO / artifact
    assert path.is_file(), f"{artifact} is missing -- run 'python -m openavc.core.condition_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(artifact), (
        f"{artifact} does not match condition_eval's operator lists -- regenerate with "
        f"'python -m openavc.core.condition_gen' (never edit it by hand)"
    )
