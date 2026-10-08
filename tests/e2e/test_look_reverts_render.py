"""A look that goes inactive gives the element its own colour back, in a real browser.

The jsdom scenarios in ``tests/fixtures/panel_harness.cjs`` assert that
``panel.js`` drops the inline colour a look's active state wrote once the
condition clears. That is half the claim. A button takes its resting colour
from ``panel-elements.css`` (``.panel-button``), so an empty inline value only
means "back to normal" if the cascade actually lets the stylesheet through --
and jsdom has no cascade worth the name. Most other element types take their
theme colour inline instead, so for them the colour has to be written back.

The failure this guards against was seen on a customer's panel: an input
picker with several inputs lit at once, and a Blank All button that stayed
orange after the screens were restored. So this renders the real controls with
the real stylesheets and asks Chromium what colour is on screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip-gate only: the browser itself comes from pytest-playwright's session
# fixtures, never from a second sync_playwright() of our own.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800
KEY = "var.selected"

# Render one element, then walk it through the given values of KEY, reading the
# computed background after each. Returned rather than asserted in JS so a
# failure names the colour that was on screen.
PROBE_JS = r"""
([type, extra, values, themeDefaults]) => {
  const app = window.__openavcPanel;
  app.editMode = false;
  app.themeElementDefaults = themeDefaults || {};
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.elementMap = {};
  app.state = {};
  let node;
  try { node = app.renderElement(Object.assign({ id: 'probe', type }, extra)); }
  catch (e) { return { error: 'render threw: ' + e }; }
  if (!node) return { error: 'renderElement returned null' };
  node.classList.add('panel-element');
  node.style.position = 'absolute';
  node.style.left = '0px'; node.style.top = '0px';
  node.style.width = '100%'; node.style.height = '100%';
  box.appendChild(node);
  const seen = [];
  for (const v of values) {
    app.state['__KEY__'] = v;
    try { app.evaluateAllBindings(['__KEY__']); }
    catch (e) { return { error: 'evaluate threw: ' + e }; }
    void node.offsetWidth;
    seen.push(getComputedStyle(node).backgroundColor);
  }
  return { seen };
}
""".replace("__KEY__", KEY)


def _page_html() -> str:
    """The panel's own CSS and JS, in a page with one absolutely-sized box.

    The @import in panel.css is resolved by hand so the two stylesheets can be
    inlined, the same way tests/e2e/test_control_minimums.py does.
    """
    elements_css = (PANEL_DIR / "panel-elements.css").read_text(encoding="utf-8")
    panel_css = "\n".join(
        line
        for line in (PANEL_DIR / "panel.css").read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("@import")
    )
    panel_js = (PANEL_DIR / "panel.js").read_text(encoding="utf-8")
    return f"""<!DOCTYPE html>
<html><head><style>{elements_css}
{panel_css}</style>
<style>
  html {{ font-size: 14px; }}
  #stage {{ position: relative; width: {REF_W}px; height: {REF_H}px; }}
  #box {{ position: absolute; left: 0; top: 0; width: 160px; height: 80px; }}
  /* Elements animate colour changes; a computed style read straight after
     the change would return the colour the animation starts from. This is
     about where the colour comes to rest, so animation is switched off. */
  #box, #box * {{ transition: none !important; }}
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
<script>{panel_js}</script>
</body></html>
"""


@pytest.fixture(scope="module")
def panel_page(browser):
    """A page on the plugin's browser -- see the note in test_control_minimums."""
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong, not the CSS"
    )
    yield page
    context.close()


def _walk(page, type_: str, extra: dict, values: list, theme_defaults=None) -> list[str]:
    result = page.evaluate(PROBE_JS, [type_, extra, values, theme_defaults or {}])
    assert "error" not in result, f"{type_}: {result['error']}"
    return result["seen"]


ACTIVE = "rgb(255, 152, 0)"


def _look_button(on_value) -> dict:
    """The Blank All shape: a look with an active colour and no inactive one."""
    return {
        "label": "Blank All",
        "bindings": {"show": {"look": {
            "key": KEY,
            "condition": {"equals": on_value},
            "style_active": {"bg_color": "#ff9800"},
        }}},
    }


def test_a_button_returns_to_the_stylesheet_colour(panel_page) -> None:
    """The resting colour is whatever the stylesheet draws for a plain button,
    read from Chromium rather than written down here, so a theme change cannot
    make this pass or fail on its own."""
    resting = _walk(panel_page, "button", {"label": "Plain"}, [None])[0]
    assert resting != ACTIVE, "the plain button already draws the active colour"

    seen = _walk(panel_page, "button", _look_button(True), [False, True, False])
    assert seen[0] == resting, f"before: {seen[0]} (resting {resting})"
    assert seen[1] == ACTIVE, f"active: {seen[1]}"
    assert seen[2] == resting, (
        f"after the condition cleared the button kept {seen[2]}, not its own {resting}"
    )


def test_an_input_picker_lights_one_button_at_a_time(panel_page) -> None:
    """One button per input on one key. Each is walked through the same
    sequence; at every step only the button for the current input is lit."""
    sequence = [1, 2, 3, 1]
    lit_at = {n: _walk(panel_page, "button", _look_button(n), sequence) for n in (1, 2, 3)}
    for step, current in enumerate(sequence):
        lit = [n for n in (1, 2, 3) if lit_at[n][step] == ACTIVE]
        assert lit == [current], f"input {current} selected, lit: {lit}"


def test_a_themed_element_returns_to_its_theme_colour(panel_page) -> None:
    """A camera preset takes its theme colour inline, not from the stylesheet,
    so dropping the state's colour alone would leave it the wrong colour."""
    theme = {"camera_preset": {"bg_color": "#2a2a4a", "text_color": "#e0e0e0"}}
    extra = {
        "label": "Wide", "preset_number": 1,
        "bindings": {"show": {"look": {
            "key": KEY, "condition": {"equals": 1},
            "style_active": {"bg_color": "#ff0000"},
        }}},
    }
    seen = _walk(panel_page, "camera_preset", extra, [1, 2], theme)
    assert seen[0] == "rgb(255, 0, 0)", f"active: {seen[0]}"
    assert seen[1] == "rgb(42, 42, 74)", f"inactive: {seen[1]}, not the theme's colour"
