"""Every control's text starts at one size, and an authored size reaches all of it.

The panel's text default is one CSS token, ``--panel-font-size`` on the panel
root in ``panel-elements.css``. ``.panel-element`` takes it, and every piece of
text inside a control is ``em`` -- a proportion of the control it sits in. Two
things follow, and this file measures both in a real browser:

* a plain control of ANY type is drawn at the token: no type is quietly left
  on the layout rem the way twelve of them were;
* an element's own ``font_size`` scales every text inside it by the same
  factor: a keypad's digits and a matrix tile's source name move with the
  label, where before they were ``rem`` and stayed where they were whatever
  the author typed.

A stylesheet read cannot keep either true. The last regression here was two
correct-looking rules disagreeing at runtime (``font: inherit`` on a button
resetting the size a class rule had set), and the one before it was a second
default bolted onto five classes. The cascade is the thing under test, so the
cascade is what is measured.

One deliberate exception: a gauge's readout is SVG text scaled with the dial.
A numeral on a dial belongs to the dial's size, not to the label's, and it is
named here so the exception is a decision rather than a gap.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

#: Twice the default, so the scaling check has a factor worth measuring. In
#: rem, because the project is written at the current format version.
AUTHORED_REM = 4.0


def _matrix(style: str) -> dict[str, Any]:
    return {
        "label": "Video Routing",
        "matrix_config": {
            "sources": {"from": {"count": 4, "labels": ["In 1", "In 2", "In 3", "In 4"]}},
            "destinations": {"from": {"count": 4, "labels": ["Out 1", "Out 2", "Out 3", "Out 4"]}},
        },
        "matrix_style": style,
    }


#: One of each control the palette can create, as createDefaultElement shapes
#: it, at the size the Builder drops it. The matrix is here three times because
#: its three styles share nothing but a label.
SPECIMENS: list[tuple[str, str, dict[str, Any], tuple[float, float]]] = [
    ("button", "button", {"label": "Button"}, (25, 25)),
    ("label", "label", {"text": "Label"}, (25, 12.5)),
    ("status_led", "status_led", {"label": "Status"}, (16.6667, 12.5)),
    ("slider", "slider", {"label": "Slider", "min": 0, "max": 100, "step": 1,
                          "style": {"show_value": True}}, (33.3333, 12.5)),
    ("page_nav", "page_nav", {"label": "Next Page", "target_page": ""}, (16.6667, 12.5)),
    ("select", "select", {"label": "Select", "options": [
        {"label": "Option 1", "value": "option_1"}]}, (25, 12.5)),
    ("text_input", "text_input", {"label": "Input", "placeholder": "Type here..."}, (25, 12.5)),
    ("camera_preset", "camera_preset", {"label": "Preset", "preset_number": 1}, (16.6667, 25)),
    ("gauge", "gauge", {"label": "Gauge", "min": 0, "max": 100, "unit": "%",
                        "style": {"show_value": True}}, (25, 37.5)),
    ("level_meter", "level_meter", {"label": "Level", "min": -60, "max": 0,
                                    "orientation": "vertical"}, (8.3333, 50)),
    ("fader", "fader", {"label": "Fader", "min": 0, "max": 100, "unit": "%",
                        "orientation": "vertical",
                        "style": {"show_value": True, "show_scale": True}}, (16.6667, 62.5)),
    ("group", "group", {"label": "Container", "label_position": "top-left"}, (50, 50)),
    ("clock", "clock", {"clock_mode": "time"}, (25, 12.5)),
    ("keypad", "keypad", {"label": "Keypad", "digits": 4, "keypad_style": "numeric",
                          "show_display": True}, (25, 62.5)),
    ("list", "list", {"label": "Sources", "list_style": "selectable", "item_height": 44 / 14,
                      "items": [{"label": "Item 1", "value": "1"}]}, (25, 50)),
    ("matrix_tiles", "matrix", _matrix("tiles"), (33.3333, 37.5)),
    ("matrix_crosspoint", "matrix", _matrix("crosspoint"), (33.3333, 37.5)),
    ("matrix_list", "matrix", _matrix("list"), (33.3333, 37.5)),
]


def _pages(prefix: str, font_size_rem: float | None) -> list[dict[str, Any]]:
    """The specimens row-packed onto as many pages as they need."""
    pages: list[dict[str, Any]] = []
    x = y = row_h = 0.0
    placements: dict[str, Any] = {}
    elements: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal placements, elements, x, y, row_h
        if elements:
            pid = f"{prefix}{len(pages) + 1}"
            pages.append({"id": pid, "name": pid, "page_type": "page",
                          "layouts": [{"id": "d", "placements": placements}],
                          "elements": elements})
        placements, elements, x, y, row_h = {}, [], 0.0, 0.0, 0.0

    for eid, type_, fields, (w, h) in SPECIMENS:
        if x + w > 100.0001:
            x, y, row_h = 0.0, y + row_h, 0.0
        if y + h > 100.0001:
            flush()
        element: dict[str, Any] = {"id": f"{prefix}_{eid}", "type": type_, **fields}
        if font_size_rem is not None:
            element["style"] = {**element.get("style", {}), "font_size": font_size_rem}
        placements[element["id"]] = {"x": x, "y": y, "w": w, "h": h}
        elements.append(element)
        x += w
        row_h = max(row_h, h)
    flush()
    return pages


#: The font size of the control and of every text-bearing thing inside it, by
#: the class that carries the text. Native controls (select, input) are their
#: own text; SVG text is left out on purpose (see the module docstring). The
#: probe is a bare .panel-element, so tokenPx is what --panel-font-size
#: resolves to on this page with nothing else in the way.
MEASURE_JS = r"""
() => {
  const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const cls = (e) => (e.className && e.className.baseVal !== undefined) ? e.className.baseVal : (e.className || '');
  const probe = document.createElement('div');
  probe.className = 'panel-element';
  document.body.appendChild(probe);
  const tokenPx = parseFloat(getComputedStyle(probe).fontSize);
  probe.remove();
  const out = {};
  for (const el of document.querySelectorAll('[data-element-id]')) {
    const parts = {};
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (!n.textContent.trim()) continue;
      const p = n.parentElement;
      if (!p || p instanceof SVGElement || p.tagName === 'OPTION') continue;
      const cs = getComputedStyle(p);
      if (cs.display === 'none') continue;
      const key = (cls(p).split(/\s+/).filter(Boolean).pop() || p.tagName.toLowerCase());
      if (!(key in parts)) parts[key] = parseFloat(cs.fontSize);
    }
    for (const c of el.querySelectorAll('select, input:not([type=range])')) {
      parts[c.tagName.toLowerCase()] = parseFloat(getComputedStyle(c).fontSize);
    }
    out[el.dataset.elementId] = { px: parseFloat(getComputedStyle(el).fontSize), parts };
  }
  return { rootPx, tokenPx, elements: out };
}
"""


# One server and one page for the whole file: every test here reads the same
# thirty-six renders, and a server per test would boot the panel eighteen
# times over to answer one question each.
@pytest.fixture(scope="module")
def server_factory_module(tmp_path_factory, _install_test_driver):
    from tests.e2e.conftest import _start_server

    gens = []

    def _make(*, project_overrides=None):
        gen = _start_server(tmp_path_factory.mktemp("srv"), initial_children=0,
                            project_overrides=project_overrides)
        gens.append(gen)
        return next(gen)

    yield _make
    for gen in gens:
        try:
            next(gen)
        except StopIteration:
            pass


@pytest.fixture(scope="module")
def page_module(browser) -> Page:
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    yield page
    context.close()


@pytest.fixture(scope="module")
def rendered(server_factory_module, page_module: Page) -> dict[str, Any]:
    """Every specimen, plain and authored, measured on the served panel."""
    plain = _pages("p", None)
    authored = _pages("a", AUTHORED_REM)
    handle = server_factory_module(project_overrides={
        "openavc_version": "0.13.0", "devices": [],
        "ui": {"settings": {"theme_id": "dark-default"}, "master_elements": [],
               "page_groups": [], "pages": plain + authored},
    })
    page = page_module
    result: dict[str, Any] = {"elements": {}}
    for p in plain + authored:
        page.goto(f"{handle.base_url}/panel/?page={p['id']}", wait_until="domcontentloaded")
        page.locator("[data-element-id]").first.wait_for(state="visible", timeout=20_000)
        page.wait_for_timeout(600)
        measured = page.evaluate(MEASURE_JS)
        result["rootPx"] = measured["rootPx"]
        result["tokenPx"] = measured["tokenPx"]
        result["elements"].update(measured["elements"])
    return result


@pytest.mark.parametrize("eid", [s[0] for s in SPECIMENS])
def test_every_control_starts_at_the_panel_text_default(rendered, eid: str) -> None:
    """The token, for every type. Twelve types used to sit on the layout rem
    while six read a rule of their own, and no test said which was which."""
    el = rendered["elements"][f"p_{eid}"]
    assert el["px"] == pytest.approx(rendered["tokenPx"], abs=0.5), (
        f"{eid} is drawn at {el['px']}px; a bare .panel-element on the same page "
        f"draws at {rendered['tokenPx']}px (--panel-font-size). Some rule with more "
        f"specificity is setting font-size on this type -- look in panel.css, which "
        f"loads after the element stylesheet, and at anything panel.js sets inline."
    )


def test_the_default_is_readable_across_a_room(rendered) -> None:
    """Not a pin on the number, which is a design call made in the stylesheet,
    but a floor under it. The old default was the layout rem itself: 14px on
    the 1280x800 reference, a 1.7mm cap height on a 10-inch panel. This is what
    stops the token drifting back there through a change that looks unrelated."""
    assert rendered["tokenPx"] >= 1.5 * rendered["rootPx"], (
        f"--panel-font-size resolves to {rendered['tokenPx']}px against a "
        f"{rendered['rootPx']}px rem; the panel's text default is back down where "
        f"nobody could read it"
    )


@pytest.mark.parametrize("eid", [s[0] for s in SPECIMENS])
def test_an_authored_size_wins(rendered, eid: str) -> None:
    """The Builder writes ``style.font_size`` and the renderer puts it inline;
    if any class rule ever beat that, every hand-set size in every project
    would silently revert."""
    el = rendered["elements"][f"a_{eid}"]
    want = AUTHORED_REM * rendered["rootPx"]
    assert el["px"] == pytest.approx(want, abs=0.5), (
        f"{eid} with font_size {AUTHORED_REM} draws at {el['px']}px, not {want}px"
    )


@pytest.mark.parametrize("eid", [s[0] for s in SPECIMENS])
def test_an_authored_size_reaches_every_text_inside_the_control(rendered, eid: str) -> None:
    """Every text inside a control is em of the control. Rem inside a control
    is the bug this catches: it looked fine at the default and ignored the
    author's Font Size, which is how a keypad's digits stayed 18px under a
    28px label."""
    plain = rendered["elements"][f"p_{eid}"]
    authored = rendered["elements"][f"a_{eid}"]
    factor = authored["px"] / plain["px"]
    assert factor > 1.5, f"the authored specimen of {eid} is not bigger than the plain one"
    assert plain["parts"], f"{eid} rendered no text to measure"
    stuck = {
        key: (px, authored["parts"].get(key))
        for key, px in plain["parts"].items()
        if key in authored["parts"]
        and authored["parts"][key] != pytest.approx(px * factor, rel=0.02)
    }
    assert not stuck, (
        f"{eid}: font_size scaled the control by {factor:.2f}x but these texts did "
        f"not follow (plain px, authored px): {stuck}. A font-size inside a control "
        f"must be em (or inherit), never rem or px -- see the header of "
        f"panel-elements.css."
    )
