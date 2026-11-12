"""The picker on the Theme Studio's preview names the section a click opens.

Hovering a control in the Theme Studio's preview outlines it and shows a name
tag; clicking it opens that kind of control's section in the editor. The tag is
drawn by the panel (`THEME_STUDIO_NAMES` in `web/panel/modules/theme.js`), the
sections by the Builder (`ELEMENT_SECTIONS` in `elementControls.ts`): two
hand-written lists of the same names, in two languages. A section renamed on
one side only would tag a control "Status LED" and open "Status Light".

Read with a regex rather than executed, like `test_ui_override_properties_mirror.py`:
the point is to catch one side being edited without the other.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
THEME_JS = REPO / "openavc" / "web" / "panel" / "modules" / "theme.js"
SECTIONS_TS = (
    REPO / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "elementControls.ts"
)

#: `button: 'Button',` inside the THEME_STUDIO_NAMES object.
_PANEL_NAME = re.compile(r"""\b([a-z_]+):\s*'([^']+)'""")

#: `type: "button",` followed by `label: "Button",` in one ELEMENT_SECTIONS entry.
_SECTION = re.compile(r"""type:\s*"([a-z_]+)",\s*\n\s*label:\s*"([^"]+)\"""")


def _panel_names() -> dict[str, str]:
    src = THEME_JS.read_text(encoding="utf-8")
    start = src.index("export const THEME_STUDIO_NAMES = {")
    body = src[start:src.index("};", start)]
    found = dict(_PANEL_NAME.findall(body))
    assert found, "found no names in THEME_STUDIO_NAMES -- the regex is stale"
    return found


def _section_names() -> dict[str, str]:
    src = SECTIONS_TS.read_text(encoding="utf-8")
    start = src.index("export const ELEMENT_SECTIONS")
    found = dict(_SECTION.findall(src[start:]))
    assert found, "found no sections in ELEMENT_SECTIONS -- the regex is stale"
    return found


def test_the_picker_names_each_control_after_its_section():
    panel = _panel_names()
    sections = _section_names()
    assert panel == sections, (
        "the Theme Studio picker's names and the Builder's sections have drifted.\n"
        f"  named by the picker only: {sorted(set(panel.items()) - set(sections.items())) or 'none'}\n"
        f"  a section the picker names otherwise: {sorted(set(sections.items()) - set(panel.items())) or 'none'}\n"
        "Update both."
    )
