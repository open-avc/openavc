"""Which bindings an element type's renderer actually reaches, run rather than read.

``openavc/ui/page_review.py`` warns the AI when it wires a binding an element
type does not render -- a label carrying ONLINE / OFFLINE state text, say, where
the panel has no code to draw one. That warning is only worth having if the
table behind it is true, and the table was written by reading JavaScript.

``tests/test_ui_page_review_mirrors.py`` re-derives it from that same JavaScript
with a regex, which catches drift but shares the reading's blind spots: a slot
mentioned in a comment, a binding registered through a helper, an evaluator that
turns out to write somewhere else. This file settles it by execution instead --
real browser, real ``panel.js``, real stylesheets -- and asks the panel what it
registered and what it drew.

The two halves are deliberately opposite. One proves a binding reaches the
screen; the other proves the identical binding on a different type does not. A
checker that only ever confirmed the positive would happily warn about
everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openavc.ui.page_review import HONORED_SHOW_SLOTS, STATE_LABEL_TYPES

# Skip-gate only: the browser itself comes from pytest-playwright's session
# fixtures, never from a second sync_playwright() of our own.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

STATE_KEY = "device.acme_widget.connected"

# Render one element with a state-driven look, evaluate everything the render
# registered, and report what the panel did with it. Returned rather than
# asserted in JS so a failure names the actual text on screen.
PROBE_JS = r"""
([type, extra, stateKey, stateValue]) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = Object.fromEntries([[stateKey, stateValue]]);
  let node;
  try { node = app.renderElement(Object.assign({ id: 'probe', type }, extra)); }
  catch (e) { return { error: 'render threw: ' + e }; }
  if (!node) return { error: 'renderElement returned null' };
  box.appendChild(node);
  const registered = app.bindings.map(b => b.type);
  try { app.evaluateAllBindings(); }
  catch (e) { return { error: 'evaluate threw: ' + e, registered }; }
  return { registered, text: (node.textContent || '').trim() };
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
<script>{panel_js}</script>
</body></html>
"""


@pytest.fixture(scope="module")
def panel_page(browser):
    """A page on the plugin's browser -- see the note in test_control_minimums.

    One Playwright per session. Opening a second with `sync_playwright()` fails
    outright once the plugin has started its own.
    """
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong, not the table"
    )
    yield page
    context.close()


def _probe(page, type_: str, extra: dict, value: str = "true") -> dict:
    result = page.evaluate(PROBE_JS, [type_, extra, STATE_KEY, value])
    assert "error" not in result, f"{type_}: {result['error']}"
    return result


def _look(**states) -> dict:
    return {"bindings": {"show": {"look": {"key": STATE_KEY, "states": states}}}}


def test_a_button_draws_the_state_label_it_was_given(panel_page) -> None:
    """The positive case, so the negative one below means something."""
    result = _probe(panel_page, "button", {"label": "Link", **_look(
        true={"label": "ONLINE"}, false={"label": "OFFLINE"},
    )})
    assert "feedback" in result["registered"]
    assert result["text"] == "ONLINE"


def test_a_label_draws_nothing_from_the_same_state_labels(panel_page) -> None:
    """The defect the AI shipped and nothing anywhere reported.

    Wired for both, a label draws its bound value and ignores the look entirely
    -- so the panel shows the raw ``true`` the state carries, where the author
    asked for ONLINE. Nothing errors, nothing logs, and the element renders, so
    it reads as a stale value rather than a binding that was never wired up.
    """
    result = _probe(panel_page, "label", {
        "bindings": {
            "show": {
                "value": {"key": STATE_KEY, "format": "{value}"},
                "look": {"key": STATE_KEY, "states": {
                    "true": {"label": "ONLINE"}, "false": {"label": "OFFLINE"},
                }},
            },
        },
    })
    assert "feedback" not in result["registered"]
    assert result["text"] == "true"
    assert "ONLINE" not in result["text"]


def test_a_status_led_takes_the_colour_and_leaves_the_label(panel_page) -> None:
    """Honored for one thing and not the other, which no schema can express.

    The LED's look binding is real -- it tints the dot -- so a check that only
    asked "is look honored here" would call this fine. The text is what is lost.
    """
    result = _probe(panel_page, "status_led", _look(
        true={"color": "#4CAF50", "label": "ONLINE"},
    ))
    assert result["registered"] == ["color"]
    assert "ONLINE" not in result["text"]


def test_the_types_that_register_feedback_are_the_ones_recorded(panel_page) -> None:
    """Every type, asked at once: who can draw a state label and who cannot.

    This is the whole ``STATE_LABEL_TYPES`` claim, executed. A type that starts
    registering a feedback binding -- or stops -- lands here rather than in a
    silently wrong warning six months later.
    """
    extras = {
        "list": {"options": [{"value": "a", "label": "A"}]},
        "select": {"options": [{"value": "a", "label": "A"}]},
        "matrix": {"matrix_config": {"inputs": 2, "outputs": 2}},
    }
    drawn = set()
    for type_ in sorted(HONORED_SHOW_SLOTS):
        if type_ == "plugin":  # an iframe; it has no text of its own to check
            continue
        result = _probe(panel_page, type_, {
            **extras.get(type_, {}), **_look(true={"label": "ONLINE"}),
        })
        if "feedback" in result["registered"]:
            drawn.add(type_)
    assert drawn == set(STATE_LABEL_TYPES)
