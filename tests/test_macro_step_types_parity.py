"""The macro editor offers exactly the steps the runtime runs.

``STEP_TYPES`` in the IDE's ``macroHelpers.ts`` is the list both Add Step
menus draw from, and ``macro_validation.BUILTIN_STEP_ACTIONS`` is the list the
runtime, the validator and the AI accept. They are two copies of one fact. A
step the runtime has and the editor lacks can only be written by hand, and a
step the editor offers that the runtime lacks saves and then fails on every run.

Reading TypeScript with a regex is coarse on purpose: it fails loud when the
array's shape changes, and the fix is then to teach the parse.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.macro_validation import BUILTIN_STEP_ACTIONS

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
MACRO_HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "macros"
    / "macroHelpers.ts"
)

_ARRAY = re.compile(r"export const STEP_TYPES: StepTypeInfo\[\] = \[(.*?)\n\];", re.S)
# An entry's own `action:` sits at four spaces; a `defaults` object repeats it
# deeper inside the same entry.
_ACTION = re.compile(r'^    action: "([^"]+)",$', re.M)


def _editor_step_actions() -> list[str]:
    match = _ARRAY.search(MACRO_HELPERS.read_text(encoding="utf-8"))
    assert match, "STEP_TYPES array not found in macroHelpers.ts; teach the parse"
    actions = _ACTION.findall(match.group(1))
    assert actions, "no entries parsed out of STEP_TYPES; teach the parse"
    return actions


def test_the_editor_offers_every_step_the_runtime_runs() -> None:
    actions = _editor_step_actions()
    assert len(actions) == len(set(actions)), f"a step is listed twice: {actions}"
    assert set(actions) == set(BUILTIN_STEP_ACTIONS), (
        f"editor only: {sorted(set(actions) - BUILTIN_STEP_ACTIONS)}; "
        f"runtime only: {sorted(BUILTIN_STEP_ACTIONS - set(actions))}"
    )
