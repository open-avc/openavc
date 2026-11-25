"""The generated firing-context table must match the one it is rendered from.

``openavc/core/value_resolver.py`` lists what each producer of a macro run
puts in the run's firing context (``FIRING_CONTEXTS``);
``python -m openavc.core.firing_context_gen`` renders it into
``firingContext.gen.ts``, which the IDE's state-key picker and trigger editor
read. This re-renders and compares byte for byte, so a field changed without
regenerating, or the file edited by hand, fails CI instead of the IDE offering
a field the runtime never hands over.

Same arrangement as tests/test_theme_values_generated.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openavc.core.firing_context_gen import ARTIFACTS, render

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("artifact", sorted(ARTIFACTS))
def test_each_committed_artifact_matches_the_table(artifact: str) -> None:
    path = REPO / artifact
    assert path.is_file(), f"{artifact} is missing -- run 'python -m openavc.core.firing_context_gen'"
    committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == render(artifact), (
        f"{artifact} does not match FIRING_CONTEXTS -- regenerate with "
        f"'python -m openavc.core.firing_context_gen' (never edit it by hand)"
    )
