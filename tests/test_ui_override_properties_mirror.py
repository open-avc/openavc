"""The overrides the IDE offers are the ones the panel actually reads.

`ui.<element id>.<property>` lets a macro or script change a control while the
program runs. The panel implements that in `evaluateUiOverrides`; the IDE's key
picker offers it from `uiOverrideProperties.ts`. Those are two hand-written
lists of the same five names, in two languages, and the failure they invite is
silent both ways: a property the picker offers and the panel ignores is a
control an integrator sets and watches do nothing, and one the panel reads and
the picker never offers is the capability being invisible -- which is how this
got reported in the first place, as "all UI automation requires Python".

Read with a regex rather than executed, like `test_ui_page_review_mirrors.py`:
the point is to catch one side being edited without the other, and a mismatch
here means somebody has to look, not that the regex needs loosening.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PANEL_JS = REPO / "openavc" / "web" / "panel" / "panel.js"
PICKER_TS = (
    REPO / "openavc" / "web" / "programmer" / "src" / "components" / "shared"
    / "uiOverrideProperties.ts"
)

#: `this.state[prefix + 'label']` inside evaluateUiOverrides. The function
#: reads every override through that one prefix, so this finds all of them.
_PANEL_READ = re.compile(r"""this\.state\[\s*prefix\s*\+\s*['"]([a-z_]+)['"]\s*\]""")

#: `name: "label",` in the TS table.
_TS_NAME = re.compile(r"""^\s*name:\s*["']([a-z_]+)["']""", re.MULTILINE)


def _panel_override_properties() -> set[str]:
    src = PANEL_JS.read_text(encoding="utf-8")
    # The DEFINITION, not the call site a few hundred lines above it: both read
    # "evaluateUiOverrides()" and slicing from the call gets an unrelated body.
    start = src.index("\n    evaluateUiOverrides() {")
    # To the next method at the same indentation, so a later `prefix +` in an
    # unrelated function can't leak in.
    end = src.index("\n    evaluate", start + 1)
    body = src[start:end]
    found = set(_PANEL_READ.findall(body))
    assert found, "found no ui.* overrides in evaluateUiOverrides -- the regex is stale"
    return found


def _picker_override_properties() -> set[str]:
    found = set(_TS_NAME.findall(PICKER_TS.read_text(encoding="utf-8")))
    assert found, "found no properties in uiOverrideProperties.ts -- the regex is stale"
    return found


def test_the_picker_offers_exactly_what_the_panel_reads():
    panel = _panel_override_properties()
    picker = _picker_override_properties()
    assert picker == panel, (
        "the IDE's ui.* override list and the panel's have drifted.\n"
        f"  offered but never read: {sorted(picker - panel) or 'none'}\n"
        f"  read but never offered: {sorted(panel - picker) or 'none'}\n"
        "Update both, and the docs that list them."
    )


def test_the_documented_properties_are_the_same_five():
    """The scripting reference is where these were documented first, and was
    the only place they appeared when an integrator concluded macros couldn't
    reach the panel. It has to keep naming all of them."""
    doc = (REPO / "docs" / "scripting-api-reference.md").read_text(encoding="utf-8")
    for prop in _panel_override_properties():
        assert f"ui.<element_id>.{prop}" in doc, (
            f"ui.<element_id>.{prop} is read by the panel but not in "
            f"docs/scripting-api-reference.md"
        )
