"""How big a control's label is, and which of the four sources decided it.

A control's text size can arrive by exactly four routes, and they are ranked:

1. inline, written by the renderer from ``getThemedStyle(type, element.style)``
   -- the element's own ``style.font_size`` (what the Builder's Font Size box
   writes), else the active theme's default for that type. Beats all CSS.
2. ``.panel-button, .panel-label, .panel-page-nav, .panel-clock, .panel-gauge``
   -- 2rem, the default for controls whose whole content is their label.
3. ``.panel-element`` -- 1rem, what every other control still takes.
4. ``:root`` -- what a rem IS: 1.75vmin, so 14px on a 1280x800 panel.

Nothing in the suite checked which of those actually won, and for `<button>`
the answer was none of them. ``panel.css`` carried
``button.panel-element { font: inherit }`` -- the `font` shorthand resets
font-size, the selector is (0,1,1), and panel.css loads after the
panel-elements.css it imports. So it beat both class rules and every button
took the root rem regardless of what any stylesheet said about buttons. Button
text being too small was the most visible symptom of it, and editing the
element stylesheet could not fix it.

A real browser is the only thing that can answer this: the bug WAS the cascade,
so any check that reasons about specificity instead of measuring it would have
had the same blind spot as the code.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

#: 1rem on the 1280x800 reference the e2e viewport uses.
REM_PX = 14.0
#: What a control whose content is only its label should end up at.
LABEL_DEFAULT_PX = 2 * REM_PX


def _project() -> dict[str, Any]:
    """One control per route, sized and unsized."""
    specs = [
        ("btn_plain", "button", "Plain Button", None),
        # In PIXELS, not rem: the e2e fixture builds projects at
        # openavc_version 0.5.0, so the 0.8.0 migration reads style
        # measurements as px and divides by REM_BASE_PX on the way in. Writing
        # a rem value here would arrive as 0.18rem and draw 2.6px. Authoring it
        # the way that format version means exercises the migration too.
        ("btn_sized", "button", "Sized Button", 36),
        ("lbl_plain", "label", "Plain Label", None),
        ("nav_plain", "page_nav", "Plain Nav", None),
        ("clock_plain", "clock", None, None),
        ("led_plain", "status_led", "LED", None),
    ]
    placements, elements = {}, []
    for i, (eid, type_, label, font) in enumerate(specs):
        placements[eid] = {"x": 2 + (i % 3) * 32, "y": 2 + (i // 3) * 30, "w": 30, "h": 26}
        el: dict[str, Any] = {"id": eid, "type": type_}
        if label:
            el["label"] = label
        if font:
            el["style"] = {"font_size": font}
        elements.append(el)
    return {"ui": {
        "settings": {"theme": "dark"},
        "master_elements": [], "page_groups": [],
        "pages": [{
            "id": "main", "name": "Main", "page_type": "page",
            "layouts": [{"id": "d", "placements": placements}],
            "elements": elements,
        }],
    }}


@pytest.fixture
def sizes(server_factory, page: Page) -> dict[str, float]:
    handle = server_factory(project_overrides=_project())
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    page.locator(".panel-element").first.wait_for(state="visible", timeout=20_000)
    page.wait_for_timeout(800)
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.panel-element')]
             .filter(e => e.dataset.elementId)
             .map(e => [e.dataset.elementId, parseFloat(getComputedStyle(e).fontSize)]))"""
    )


def test_a_button_reads_the_rule_written_for_buttons(sizes) -> None:
    """The regression itself. `font: inherit` on button.panel-element made this
    28 -> 14 with nothing else in the panel changing, and no test noticed."""
    assert sizes["btn_plain"] == pytest.approx(LABEL_DEFAULT_PX, abs=0.5), (
        f"a plain button drew at {sizes['btn_plain']}px, not the "
        f"{LABEL_DEFAULT_PX}px .panel-button asks for. Something with a higher "
        f"specificity is setting font-size or the `font` shorthand on buttons -- "
        f"check panel.css, which loads AFTER the panel-elements.css it imports."
    )


def test_every_label_only_control_gets_the_same_default(sizes) -> None:
    """Or the panel is a patchwork: two controls side by side, same text, one
    of them a <button> and therefore quietly smaller."""
    for eid in ("btn_plain", "lbl_plain", "nav_plain", "clock_plain"):
        assert sizes[eid] == pytest.approx(LABEL_DEFAULT_PX, abs=0.5), (
            f"{eid} drew at {sizes[eid]}px, not {LABEL_DEFAULT_PX}px"
        )


def test_an_authored_size_still_wins(sizes) -> None:
    """The Builder's Font Size box writes style.font_size, which the renderer
    puts inline. If the class rule ever beat it, every hand-set size in every
    project would silently revert to the default."""
    assert sizes["btn_sized"] == pytest.approx(36, abs=0.5), (
        f"an authored 36px drew at {sizes['btn_sized']}px"
    )


def test_a_control_with_fixed_internals_is_left_at_the_base(sizes) -> None:
    """The other half of the scoping, and the reason this is not one rule on
    .panel-element: a status LED's box is a percentage of the page but its dot
    is a fixed size, so raising the base starves it. Same for matrix cells,
    keypad keys, list rows and meter segments -- they keep the 1rem base and
    their floors in control_minimums.py stay true."""
    assert sizes["led_plain"] == pytest.approx(REM_PX, abs=0.5), (
        f"a status LED drew at {sizes['led_plain']}px, not the {REM_PX}px base. "
        f"If the label default has been widened to cover it, the floors in "
        f"control_minimums.py need re-measuring in the same change."
    )
