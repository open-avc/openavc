"""A button's gradient survives a button image, run rather than read.

A gradient is a background-image, and so is a button's artwork. The renderer
wrote the gradient through the `background` shorthand and then wrote the image
to `backgroundImage`, which replaced it -- and because the shorthand had
already reset `background-color`, there was nothing behind the image either.
A button with a transparent image and a gradient drew with no background at
all, while the same button with a flat colour was fine. That asymmetry is why
it read as "colour settings stop working once you use an image".

Asserted on computed style rather than a screenshot, because the failure is
exactly a property being overwritten. The Builder is no help here: it draws
its preview through an iframe of this same renderer, so it showed the same
empty button, and nothing in the authored project was wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.e2e._panel_modules import panel_script_tag, serve_panel_scripts

pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

GRADIENT = {"type": "linear", "angle": 180, "from": "#e74c3c", "to": "#8e44ad"}

#: A 1x1 transparent PNG, so "the image covers the background" can't be
#: confused with "the image is the background".
PIXEL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

PROBE_JS = r"""
(element) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = {};
  const node = app.renderElement(element);
  if (!node) return { error: 'renderElement returned null' };
  box.appendChild(node);
  const cs = getComputedStyle(node);
  return {
    backgroundImage: cs.backgroundImage,
    backgroundColor: cs.backgroundColor,
    backgroundSize: cs.backgroundSize,
  };
}
"""


def _page_html() -> str:
    elements_css = (PANEL_DIR / "panel-elements.css").read_text(encoding="utf-8")
    panel_css = "\n".join(
        line
        for line in (PANEL_DIR / "panel.css").read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("@import")
    )
    return f"""<!DOCTYPE html>
<html><head><style>{elements_css}
{panel_css}</style>
<style>
  html {{ font-size: 14px; }}
  #stage {{ position: relative; width: {REF_W}px; height: {REF_H}px; }}
  #box {{ position: absolute; left: 0; top: 0; width: 300px; height: 60px; }}
</style></head>
<body>
  <div id="panel-root"></div><div id="connection-status"></div>
  <div id="offline-overlay"></div><div id="loading-state"></div>
  <div id="stage"><div id="box"></div></div>
<script>
  window.fetch = async () => ({{ ok: false, json: async () => ({{}}) }});
  class FakeWS {{ constructor() {{ this.readyState = 1; }} send() {{}} close() {{}} }}
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
</script>
{panel_script_tag()}
</body></html>
"""


@pytest.fixture(scope="module")
def panel_page(browser):
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    serve_panel_scripts(page)
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong"
    )
    yield page
    context.close()


def _render(page, **element) -> dict:
    result = page.evaluate(PROBE_JS, {"id": "probe", "type": "button", **element})
    assert "error" not in result, result.get("error")
    return result


def _with_image(**extra) -> dict:
    return {"button_image": PIXEL, "display_mode": "image_text", **extra}


# --- The reported failure --------------------------------------------------


def test_a_gradient_survives_a_button_image(panel_page) -> None:
    """Both layers are drawn: the artwork over the gradient."""
    style = _render(
        panel_page,
        label="Power",
        style={"background_gradient": GRADIENT},
        **_with_image(),
    )
    assert "linear-gradient" in style["backgroundImage"], (
        "the button image replaced the gradient instead of drawing over it"
    )
    assert "url(" in style["backgroundImage"]
    # One size per layer, or the browser drops the second one.
    assert style["backgroundSize"].count(",") == 1


@pytest.mark.parametrize("extra, why", [
    ({"image_opacity": 0.5}, "opacity moves the image to its own layer"),
    ({"image_blend_mode": "mask"}, "mask moves the image to its own layer"),
    ({"image_blend_mode": "multiply"}, "a blend moves the image to its own layer"),
])
def test_a_gradient_survives_an_image_on_an_effect_layer(
    panel_page, extra, why,
) -> None:
    """The effect-layer path put the artwork on a child and then cleared the
    button's own background-image outright, which took the gradient with it."""
    style = _render(
        panel_page,
        label="Power",
        style={"background_gradient": GRADIENT},
        **_with_image(**extra),
    )
    assert "linear-gradient" in style["backgroundImage"], why


# --- The cases that already worked, so the fix can't quietly break them ----


def test_a_flat_colour_still_survives_a_button_image(panel_page) -> None:
    style = _render(
        panel_page, label="Power", style={"bg_color": "#27ae60"}, **_with_image(),
    )
    assert style["backgroundColor"] == "rgb(39, 174, 96)"


def test_a_gradient_alone_still_draws_only_the_gradient(panel_page) -> None:
    style = _render(panel_page, label="Power", style={"background_gradient": GRADIENT})
    assert "linear-gradient" in style["backgroundImage"]
    assert "url(" not in style["backgroundImage"]


def test_a_flat_colour_alone_draws_no_image(panel_page) -> None:
    style = _render(panel_page, label="Power", style={"bg_color": "#27ae60"})
    assert style["backgroundImage"] == "none"
    assert style["backgroundColor"] == "rgb(39, 174, 96)"


def test_a_button_with_neither_draws_no_gradient(panel_page) -> None:
    style = _render(panel_page, label="Power", style={})
    assert "linear-gradient" not in style["backgroundImage"]


def test_a_gradient_survives_an_element_background_image(panel_page) -> None:
    """The same composition for `style.background_image`, three lines away in
    the same function and broken the same way: it overwrote background-image
    with the asset and the gradient went with it."""
    style = _render(
        panel_page,
        label="Power",
        style={"background_gradient": GRADIENT, "background_image": PIXEL},
    )
    assert "linear-gradient" in style["backgroundImage"], (
        "an element background image replaced the gradient instead of "
        "drawing over it"
    )
    assert "url(" in style["backgroundImage"]


# --- What jsdom cannot read, pinned as Chromium draws it -------------------
#
# The jsdom harness pins every other value the style writers set
# (tests/fixtures/panel_paint_pins.json); jsdom drops a background-position
# with more than one layer and the -webkit- mask longhands, so those are read
# here, inline, as the panel writes them.

INLINE_JS = r"""
(element) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = {};
  const node = app.renderElement(element);
  box.appendChild(node);
  const layer = node.querySelector(':scope > .panel-button-image-layer');
  const read = (s, names) => Object.fromEntries(names.map((n) => [n, s.getPropertyValue(n)]));
  return {
    node: read(node.style, ['background-image', 'background-size', 'background-position',
      'border-style', '-webkit-mask-image']),
    layer: layer ? read(layer.style, ['-webkit-mask-image', '-webkit-mask-size',
      '-webkit-mask-position', '-webkit-mask-repeat', 'mask-image']) : null,
  };
}
"""

PIXEL_URL = f'url("{PIXEL}")'
GRADIENT_CSS = "linear-gradient(rgb(231, 76, 60), rgb(142, 68, 173))"
NO_LAYER = None


def _node(image="", size="", position="", border="") -> dict:
    return {"background-image": image, "background-size": size, "background-position": position,
            "border-style": border, "-webkit-mask-image": ""}


#: Each case: the element, then what Chromium reads back inline.
INLINE_PINS = {
    "picture_over_gradient": (
        _with_image(label="P", style={"background_gradient": GRADIENT}),
        {"node": _node(f"{PIXEL_URL}, {GRADIENT_CSS}", "cover, cover", "center center, center center"),
         "layer": NO_LAYER},
    ),
    "background_picture_over_gradient": (
        {"label": "P", "style": {"background_gradient": GRADIENT, "background_image": PIXEL,
                                 "background_position": "top", "background_size": "contain"}},
        {"node": _node(f"{PIXEL_URL}, {GRADIENT_CSS}", "contain, cover", "center top, center center"),
         "layer": NO_LAYER},
    ),
    "background_picture_alone": (
        {"label": "P", "style": {"background_image": PIXEL, "background_position": "left"}},
        {"node": _node(PIXEL_URL, "cover", "left center"), "layer": NO_LAYER},
    ),
    "frameless": (
        {"label": "P", "frameless": True, "style": {"border_width": 0.2}},
        {"node": _node("none", border="none"), "layer": NO_LAYER},
    ),
    "mask_layer": (
        _with_image(label="P", image_blend_mode="mask"),
        {"node": _node("none"),
         "layer": {"-webkit-mask-image": PIXEL_URL, "-webkit-mask-size": "cover",
                   "-webkit-mask-position": "center center", "-webkit-mask-repeat": "no-repeat",
                   "mask-image": PIXEL_URL}},
    ),
}


@pytest.mark.parametrize("case", sorted(INLINE_PINS))
def test_what_jsdom_cannot_read_is_drawn_as_it_is_today(panel_page, case) -> None:
    element, expected = INLINE_PINS[case]
    got = panel_page.evaluate(INLINE_JS, {"id": "probe", "type": "button", **element})
    assert got == expected
