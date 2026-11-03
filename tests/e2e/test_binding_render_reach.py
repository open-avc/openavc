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
    assert panel_page.get_by_role("button", name="ONLINE", exact=True).count() == 1


def test_a_label_draws_the_state_label_too(panel_page) -> None:
    """What the AI asked for, and now gets.

    This used to be the negative case: wired for both, a label drew the raw
    ``true`` its state carried and ignored the look entirely, so a panel asking
    for ONLINE showed ``true``. Nothing errored and the element rendered, so it
    read as a stale value rather than a binding that was never wired up.

    The state's label now wins over the bound value, which is the way round the
    author meant it -- naming the words for a state is a more specific
    instruction than "print whatever this key holds".
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
    assert "label_look" in result["registered"]
    assert result["text"] == "ONLINE"


def test_a_label_keeps_its_bound_value_when_the_state_names_no_words(
    panel_page,
) -> None:
    """Colour without words leaves the words alone.

    A state that sets only a colour must not blank the text or overwrite it
    with the element's own ``label`` field, which a label element never draws
    anyway. This is the half a button does differently -- it restores its
    default label there -- and getting it wrong would break every label that
    tints itself while showing a live value.
    """
    result = _probe(panel_page, "label", {
        "bindings": {
            "show": {
                "value": {"key": STATE_KEY, "format": "{value}"},
                "look": {"key": STATE_KEY, "states": {
                    "true": {"text_color": "#4CAF50"},
                }},
            },
        },
    })
    assert result["text"] == "true"


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


def test_the_types_that_draw_a_state_label_are_the_ones_recorded(panel_page) -> None:
    """Every type, asked at once: who can draw a state label and who cannot.

    This is the whole ``STATE_LABEL_TYPES`` claim, executed -- and asked of the
    rendered text rather than of which binding got registered, so it stays true
    however the panel is wired internally. A type that starts drawing state text
    -- or stops -- lands here rather than in a silently wrong warning six months
    later.
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
        if "ONLINE" in result["text"]:
            drawn.add(type_)
    assert drawn == set(STATE_LABEL_TYPES)


# --- An image's picture per state -------------------------------------------
#
# The pictures are inline SVG data URIs: the harness stubs `fetch`, and a
# data URI loads without a server, so a load or an error is the browser's own.

def _svg(fill: str) -> str:
    from urllib.parse import quote

    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'>"
        f"<rect width='4' height='4' fill='{fill}'/></svg>"
    )
    return "data:image/svg+xml," + quote(svg)


EVERYDAY, CHRISTMAS, EASTER = _svg("gray"), _svg("green"), _svg("yellow")
BROKEN = "data:image/png;base64,bm90IGEgcGljdHVyZQ=="

# Render one image, then step through a list of whole states, evaluating after
# each and reporting what the image shows. Async so a picture that fails or
# loads has had its event before the next step reads the result.
IMAGE_STEPS_JS = r"""
async ([element, steps]) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = {};
  const node = app.renderElement(Object.assign({ id: 'probe', type: 'image' }, element));
  box.appendChild(node);
  const seen = [];
  for (const state of steps) {
    app.state = state;
    app.evaluateAllBindings();
    await new Promise(r => setTimeout(r, 150));
    const imgs = node.querySelectorAll('img');
    seen.push({
      registered: app.bindings.map(b => b.type),
      imgs: imgs.length,
      src: imgs.length ? imgs[0].getAttribute('src') : null,
      shown: imgs.length ? imgs[0].style.display !== 'none' : false,
      notes: [...node.querySelectorAll('div')].filter(d => d.textContent === 'Image not found').length,
      bg: node.style.backgroundColor,
    });
  }
  return seen;
}
"""


def _image_steps(page, element: dict, steps: list[dict]) -> list[dict]:
    return page.evaluate(IMAGE_STEPS_JS, [element, steps])


def _seasons(key: str) -> dict:
    return {"bindings": {"show": {"look": {
        "key": key, "default_state": "everyday",
        "states": {
            "everyday": {},
            "christmas": {"button_image": CHRISTMAS, "bg_color": "rgb(0, 80, 0)"},
            "easter": {"button_image": EASTER},
        },
    }}}}


def test_an_image_changes_its_picture_with_the_state(panel_page) -> None:
    """The feature: a variable a script sets picks the picture, and back."""
    seen = _image_steps(panel_page, {"src": EVERYDAY, **_seasons("var.season")}, [
        {"var.season": "everyday"},
        {"var.season": "christmas"},
        {"var.season": "easter"},
        {"var.season": "everyday"},
    ])
    assert seen[0]["registered"] == ["image_look"]
    assert [s["src"] for s in seen] == [EVERYDAY, CHRISTMAS, EASTER, EVERYDAY]
    # One <img> throughout, swapped rather than stacked.
    assert {s["imgs"] for s in seen} == {1}
    # The state's background colour comes and goes with it.
    assert seen[1]["bg"] == "rgb(0, 80, 0)"
    assert seen[2]["bg"] == "" and seen[3]["bg"] == ""


def test_an_image_shows_its_own_picture_when_no_state_names_one(panel_page) -> None:
    """An unmatched value falls to the default state, which names no picture."""
    seen = _image_steps(panel_page, {"src": EVERYDAY, **_seasons("var.season")}, [
        {"var.season": "halloween"},
        {},
    ])
    assert [s["src"] for s in seen] == [EVERYDAY, EVERYDAY]


def test_an_image_claims_no_state_while_its_device_is_unreachable(panel_page) -> None:
    """Offline shows the image's own picture, as a button shows its own face."""
    key = "device.acme_widget.mode"
    seen = _image_steps(panel_page, {"src": EVERYDAY, **_seasons(key)}, [
        {key: "christmas", "device.acme_widget.connected": True},
        {key: "christmas", "device.acme_widget.connected": False},
        {key: "christmas", "device.acme_widget.connected": True},
    ])
    assert [s["src"] for s in seen] == [CHRISTMAS, EVERYDAY, CHRISTMAS]
    assert seen[1]["bg"] == ""


def test_an_image_whose_pictures_are_all_in_its_states(panel_page) -> None:
    """No `src`: nothing drawn until a state names a picture, and nothing after."""
    seen = _image_steps(panel_page, _seasons("var.season"), [
        {"var.season": "everyday"},
        {"var.season": "christmas"},
        {"var.season": "everyday"},
    ])
    assert [s["imgs"] for s in seen] == [0, 1, 0]
    assert seen[1]["src"] == CHRISTMAS


def test_a_missing_picture_says_so_once_and_a_good_one_clears_it(panel_page) -> None:
    """One "Image not found" however many pictures fail, gone when one loads."""
    look = {"bindings": {"show": {"look": {"key": "var.season", "states": {
        "a": {"button_image": BROKEN},
        "b": {"button_image": BROKEN + "QQ=="},
        "c": {"button_image": CHRISTMAS},
    }}}}}
    seen = _image_steps(panel_page, {"src": EVERYDAY, **look}, [
        {"var.season": "a"},
        {"var.season": "b"},
        {"var.season": "c"},
    ])
    assert [s["notes"] for s in seen] == [1, 1, 0]
    assert [s["shown"] for s in seen] == [False, False, True]
