"""A control with no reading loses its handle, in a real browser.

The jsdom scenarios in ``tests/fixtures/panel_harness.cjs`` assert that
``panel.js`` puts a ``no-reading`` class on a fader's handle and a slider's
range input when the bound key holds nothing. That is half the claim. The other
half lives in ``panel-elements.css``, and jsdom cannot see it at all: it has no
layout engine and no cascade worth the name, so a class that matched no rule --
or a rule that lost to a more specific one -- would leave every scenario green
with the handle still sitting at the bottom of its travel.

Which is the exact failure the treatment exists to prevent. A handle parked at
the floor of a -80..0 fader is a claim of full attenuation, and the panel was
making it for readings no device had ever sent.

So this renders the real controls with the real stylesheets and asks Chromium
what is actually visible. It also pins the distinction the CSS comment makes:
an unreported reading loses the handle but is NOT dimmed and dashed -- that
mark means "this control cannot be trusted", which a device that is answering
has not earned.
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

DEVICE = "acme_amp"
LEVEL_KEY = f"device.{DEVICE}.level_db"
CONNECTED_KEY = f"device.{DEVICE}.connected"

# Render one element, feed the panel a state snapshot, evaluate, and report what
# Chromium actually shows. Returned rather than asserted in JS so a failure can
# name the number that was on screen.
PROBE_JS = r"""
([type, extra, state]) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.elementMap = {};
  app.state = state;
  let node;
  try { node = app.renderElement(Object.assign({ id: 'probe', type }, extra)); }
  catch (e) { return { error: 'render threw: ' + e }; }
  if (!node) return { error: 'renderElement returned null' };
  node.classList.add('panel-element');
  node.style.position = 'absolute';
  node.style.left = '0px'; node.style.top = '0px';
  node.style.width = '100%'; node.style.height = '100%';
  box.appendChild(node);
  app.elementMap = { probe: { el: node, def: null } };
  void node.offsetWidth;
  try { app.evaluateAllBindings(); }
  catch (e) { return { error: 'evaluate threw: ' + e }; }

  const shown = (sel) => {
    const el = node.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      visibility: cs.visibility,
      display: cs.display,
      area: r.width * r.height,
    };
  };
  const cs = getComputedStyle(node);
  return {
    text: (node.textContent || '').trim(),
    handle: shown('.fader-handle'),
    // A range input's thumb is a pseudo-element with no node to query, so the
    // control's own computed style is what carries the rule.
    thumbRule: (() => {
      const input = node.querySelector('input[type="range"]');
      return input ? input.className : null;
    })(),
    unavailable: node.classList.contains('device-offline'),
    filter: cs.filter,
  };
}
"""


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
  #box {{ position: absolute; left: 0; top: 0; width: 120px; height: 300px; }}
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


FADER = {
    "type": "fader",
    "min": -80,
    "max": 0,
    "unit": "dB",
    "bindings": {"show": {"value": {"key": LEVEL_KEY}}},
}
SLIDER = {
    "type": "slider",
    "min": -80,
    "max": 0,
    "unit": "dB",
    # The readout is opt-in on a slider (`show_value === true`), and it is the
    # half that used to print the range floor as a value.
    "style": {"show_value": True},
    "bindings": {"show": {"value": {"key": LEVEL_KEY}}},
}


def _probe(page, spec: dict, level, connected=True) -> dict:
    extra = {k: v for k, v in spec.items() if k != "type"}
    state = {CONNECTED_KEY: connected, LEVEL_KEY: level}
    result = page.evaluate(PROBE_JS, [spec["type"], extra, state])
    assert "error" not in result, f"{spec['type']}: {result['error']}"
    return result


def test_a_reported_fader_draws_its_handle(panel_page) -> None:
    """The positive case, so the negative one below means something. Without it
    a panel.js that hid every handle would pass the rest of this file."""
    shown = _probe(panel_page, FADER, -6.0)
    assert shown["handle"] is not None, "the fader has no handle element at all"
    assert shown["handle"]["visibility"] == "visible", shown["handle"]
    assert shown["handle"]["area"] > 0, shown["handle"]
    assert "-6.0 dB" in shown["text"], shown["text"]


def test_an_unreported_fader_has_no_visible_handle(panel_page) -> None:
    """The device is connected and has simply not sent this reading. The
    readout says so, and the handle is gone rather than parked at the floor --
    which on this fader would read as fully attenuated."""
    shown = _probe(panel_page, FADER, None)
    assert "-- dB" in shown["text"], shown["text"]
    assert shown["handle"] is not None, "the handle element should exist, just be hidden"
    assert shown["handle"]["visibility"] == "hidden", (
        f"the no-reading class did not hide the handle: {shown['handle']}"
    )


def test_an_unreported_slider_carries_the_rule_that_hides_its_thumb(panel_page) -> None:
    """A range input's thumb is a ``::-webkit-slider-thumb`` pseudo-element with
    no node to measure, so what is checkable is that the class the rule keys on
    is on the input and the readout has stopped printing the range floor."""
    shown = _probe(panel_page, SLIDER, None)
    assert shown["thumbRule"] is not None, "no range input rendered"
    assert "no-reading" in shown["thumbRule"], shown["thumbRule"]
    assert "-- dB" in shown["text"], shown["text"]

    reported = _probe(panel_page, SLIDER, -6.0)
    assert "no-reading" not in (reported["thumbRule"] or ""), reported["thumbRule"]


def test_unreported_is_not_the_unavailable_mark(panel_page) -> None:
    """The two states must not look the same. Dimmed and dashed says "you
    cannot rely on this control"; a device that is answering has not earned it,
    and the CSS comment on that rule says as much."""
    unreported = _probe(panel_page, FADER, None, connected=True)
    assert unreported["unavailable"] is False, "a live device was marked unavailable"
    assert unreported["filter"] in ("none", ""), unreported["filter"]

    unreachable = _probe(panel_page, FADER, None, connected=False)
    assert unreachable["unavailable"] is True, "an unreachable device was not marked"
    assert unreachable["filter"] not in ("none", ""), (
        "the unavailable mark drew no dimming, so the two states look identical"
    )
