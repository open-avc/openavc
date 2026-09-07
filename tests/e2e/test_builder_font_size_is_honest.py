"""What the Builder SAYS a control's text size is, versus what the panel DRAWS.

Select a control, read the Font Size box, and the number in it describes the
text on the canvas. That is the whole contract, and it did not hold: the panel
drew a fresh button at 28px while the Style panel advertised 14, so typing the
number it was already showing halved the text. Both halves "worked". They were
reading different sources -- the panel its stylesheet, the Builder a baseline
hard-coded in UIBuilderView -- and nothing compared them.

The Builder no longer has a source of its own. The panel canvas reports what
its cascade resolved each element to (``openavc:editor-text-defaults``, posted
after every render) and the Style panel shows that. So this test is not a
check that two copies of a number agree; it is a check that there is one
number, by measuring the only two places a person can see it.

Two consequences are pinned, per control type:

* the placeholder equals the pixels on the canvas (converted to the reference
  panel's pixels, which is the unit the box speaks);
* typing the placeholder back changes nothing on the canvas.

And one that proves the source is the cascade rather than a table: an element
carrying a project-stylesheet class with its own font-size shows THAT size,
which no per-type table could know.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

from openavc.ui.control_minimums import REM_BASE_PX  # noqa: E402

RAIL_TIMEOUT = 20_000

#: Every type the palette can create, plus the matrix in each of its styles.
TYPES: list[tuple[str, str, dict[str, Any]]] = [
    ("button", "button", {"label": "Button"}),
    ("label", "label", {"text": "Label"}),
    ("status_led", "status_led", {"label": "Status"}),
    ("slider", "slider", {"label": "Slider", "min": 0, "max": 100}),
    ("page_nav", "page_nav", {"label": "Next", "target_page": ""}),
    ("select", "select", {"label": "Select", "options": [{"label": "A", "value": "a"}]}),
    ("text_input", "text_input", {"label": "Input"}),
    ("camera_preset", "camera_preset", {"label": "Preset", "preset_number": 1}),
    ("gauge", "gauge", {"label": "Gauge", "min": 0, "max": 100}),
    ("level_meter", "level_meter", {"label": "Level", "min": -60, "max": 0}),
    ("fader", "fader", {"label": "Fader", "min": 0, "max": 100}),
    ("group", "group", {"label": "Group"}),
    ("clock", "clock", {"clock_mode": "time"}),
    ("keypad", "keypad", {"label": "Keypad", "digits": 4}),
    ("list", "list", {"label": "List", "items": [{"label": "One", "value": "1"}]}),
    ("matrix_tiles", "matrix", {"label": "Tiles", "matrix_style": "tiles"}),
    ("matrix_crosspoint", "matrix", {"label": "Grid", "matrix_style": "crosspoint"}),
    ("matrix_list", "matrix", {"label": "List", "matrix_style": "list"}),
]

#: A button whose size comes from the project stylesheet, not from any default.
STYLED_ID = "btn_styled"
STYLED_REM = 3.0


def _project() -> dict[str, Any]:
    placements: dict[str, Any] = {}
    elements: list[dict[str, Any]] = []
    grid = {"sources": {"from": {"count": 2}}, "destinations": {"from": {"count": 2}}}
    for i, (eid, type_, fields) in enumerate(TYPES + [(STYLED_ID, "button", {"label": "Styled"})]):
        placements[eid] = {"x": 1 + (i % 5) * 19.5, "y": 1 + (i // 5) * 24, "w": 18, "h": 22}
        element: dict[str, Any] = {"id": eid, "type": type_, **fields}
        if type_ == "matrix":
            element["matrix_config"] = grid
        if eid == STYLED_ID:
            element["css_class"] = "big"
        elements.append(element)
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "custom_css": f".big {{ font-size: {STYLED_REM}rem; }}",
            "master_elements": [], "page_groups": [],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": placements}],
                "elements": elements,
            }],
        },
    }


@pytest.fixture
def builder(server_factory, page: Page) -> Page:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.evaluate(
        """() => document.querySelector('nav button[aria-label="UI Builder"]')?.click()"""
    )
    page.locator("[data-canvas-element]").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    # The placeholder is filled by the canvas's first render report; the
    # canvas elements above are drawn by the IDE, not the iframe, so give the
    # iframe its moment rather than racing it.
    page.wait_for_timeout(2500)
    return page


def _font_size_box(page: Page):
    """The Style panel's Font Size input: the one number box bounded 8..72."""
    return page.locator('input[type=number][min="8"][max="72"]')


def _advertised_px(page: Page) -> float | None:
    """The number a person reads in the box: its value if the element has one,
    else the placeholder, which is the inherited default."""
    box = _font_size_box(page)
    shown = box.input_value() or box.get_attribute("placeholder") or ""
    try:
        return float(shown)
    except ValueError:
        return None


def _drawn(page: Page, element_id: str) -> dict[str, float] | None:
    """What the panel iframe renders that element's text at, in its own px and
    in the reference panel's px (the unit the Style panel speaks)."""
    return page.evaluate(
        """([id, remBase]) => {
            const f = document.querySelector('iframe');
            const doc = f && f.contentDocument;
            if (!doc) return null;
            const el = doc.querySelector(`[data-element-id="${id}"]`);
            if (!el) return null;
            const px = parseFloat(getComputedStyle(el).fontSize);
            const rootPx = parseFloat(getComputedStyle(doc.documentElement).fontSize);
            return { px, referencePx: px / rootPx * remBase };
        }""",
        [element_id, REM_BASE_PX],
    )


def _select(page: Page, element_id: str) -> None:
    page.click(f'[data-canvas-element="{element_id}"]')
    page.wait_for_timeout(400)


@pytest.mark.parametrize("eid", [t[0] for t in TYPES])
def test_the_box_describes_the_text_on_the_canvas(builder: Page, eid: str) -> None:
    page = builder
    _select(page, eid)
    advertised = _advertised_px(page)
    drawn = _drawn(page, eid)
    assert drawn is not None, f"the panel iframe never rendered {eid}"
    assert advertised is not None, (
        f"{eid}: the Font Size box shows nothing. The canvas did not report this "
        f"element's text default (openavc:editor-text-defaults), or the Style panel "
        f"is not reading uiBuilderStore.textDefaultsRem."
    )
    assert advertised == pytest.approx(drawn["referencePx"], abs=0.6), (
        f"{eid}: the Style panel says {advertised} but the panel draws "
        f"{drawn['referencePx']:.1f}px (reference panel). Typing the number already "
        f"shown would change the size of text that is already on screen."
    )


@pytest.mark.parametrize("eid", [t[0] for t in TYPES])
def test_typing_the_number_already_shown_changes_nothing(builder: Page, eid: str) -> None:
    """The user-facing version of the same fact, which is the one that was
    actually noticed: read the box, type what it says, watch the text jump."""
    page = builder
    _select(page, eid)
    before = _drawn(page, eid)
    advertised = _advertised_px(page)
    assert before is not None and advertised is not None
    _font_size_box(page).fill(str(int(advertised)))
    page.wait_for_timeout(700)
    after = _drawn(page, eid)
    assert after is not None
    assert after["px"] == pytest.approx(before["px"], abs=0.5), (
        f"{eid}: typing the {advertised} the box already showed moved the text from "
        f"{before['px']}px to {after['px']}px on the canvas"
    )


def test_the_box_follows_the_stylesheet_not_a_table(builder: Page) -> None:
    """A project class with its own font-size is something no per-type table
    could know about. The box shows it because the box shows what was drawn."""
    page = builder
    _select(page, "button")
    plain = _advertised_px(page)
    _select(page, STYLED_ID)
    styled = _advertised_px(page)
    drawn = _drawn(page, STYLED_ID)
    assert drawn is not None
    assert styled == pytest.approx(STYLED_REM * REM_BASE_PX, abs=0.6), (
        f"a button carrying .big {{ font-size: {STYLED_REM}rem }} advertises {styled}, "
        f"not {STYLED_REM * REM_BASE_PX}; the Builder is not reading the canvas"
    )
    assert styled == pytest.approx(drawn["referencePx"], abs=0.6)
    assert plain is not None and styled != pytest.approx(plain, abs=0.6), (
        "the styled and the plain button advertise the same size, so the class "
        "is not reaching the canvas and this test is proving nothing"
    )
