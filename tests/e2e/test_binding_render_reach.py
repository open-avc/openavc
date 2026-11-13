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
from tests.e2e._panel_modules import panel_script_tag, serve_panel_scripts

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
    """A page on the plugin's browser -- see the note in test_control_minimums.

    One Playwright per session. Opening a second with `sync_playwright()` fails
    outright once the plugin has started its own.
    """
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    serve_panel_scripts(page)
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
        # A page nav with nowhere to go is drawn disabled and reads nothing.
        "page_nav": {"target_page": "main"},
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


# --- A page nav: lit while its page is showing, and a button's Appearance -----

# Render one page nav on page `main`, then walk steps: a page change, an overlay
# opened or closed, or new state. Each step reports what the nav draws.
NAV_STEPS_JS = r"""
([element, steps]) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = {};
  app.overlayStack = [];
  app.currentPage = 'main';
  const node = app.renderElement(Object.assign({ id: 'probe', type: 'page_nav' }, element));
  box.appendChild(node);
  const btn = node.matches('button') ? node : node.querySelector('button');
  const seen = [];
  for (const step of steps) {
    if (step.state) app.state = step.state;
    if (step.page) app.currentPage = step.page;
    if (step.open) { app.overlayStack.push(step.open); app.evaluateAllBindings(); }
    else if (step.close) app.dismissOverlay();
    else app.evaluateAllBindings();
    seen.push({
      registered: app.bindings.map(b => b.type),
      lit: btn.classList.contains('nav-current'),
      current: btn.getAttribute('aria-current'),
      bg: btn.style.backgroundColor,
      text: (btn.textContent || '').trim(),
    });
  }
  return seen;
}
"""

ACCENT = "rgb(33, 150, 243)"  # the panel's default accent, with no theme loaded


def _nav_steps(page, element: dict, steps: list[dict]) -> list[dict]:
    return page.evaluate(NAV_STEPS_JS, [element, steps])


def test_a_page_nav_lights_while_its_page_is_showing(panel_page) -> None:
    seen = _nav_steps(panel_page, {
        "label": "Video", "target_page": "video", "highlight_current": True,
    }, [{"page": "main"}, {"page": "video"}, {"page": "main"}])
    assert seen[0]["registered"] == ["nav_look"]
    assert [s["lit"] for s in seen] == [False, True, False]
    assert [s["current"] for s in seen] == [None, "page", None]
    assert seen[1]["bg"] == ACCENT
    assert seen[2]["bg"] == ""


def test_a_page_nav_to_an_overlay_is_lit_while_it_is_open(panel_page) -> None:
    """Closing an overlay re-renders nothing under it, so the nav is redrawn."""
    seen = _nav_steps(panel_page, {
        "label": "Help", "target_page": "help", "highlight_current": True,
    }, [{}, {"open": "help"}, {"close": True}])
    assert [s["lit"] for s in seen] == [False, True, False]


def test_a_page_nav_lights_only_when_asked_to(panel_page) -> None:
    """Off unless set, so a nav saved before the setting existed is unchanged."""
    seen = _nav_steps(panel_page, {"label": "Main", "target_page": "main"}, [{}])
    assert seen[0]["registered"] == []
    assert seen[0]["lit"] is False


def test_a_back_nav_never_lights(panel_page) -> None:
    seen = _nav_steps(panel_page, {
        "label": "Back", "target_page": "$back", "highlight_current": True,
    }, [{}, {"open": "help"}])
    assert [s["lit"] for s in seen] == [False, False]


def test_a_page_nav_takes_its_appearance_and_lit_draws_over_the_colour(panel_page) -> None:
    """The state's words stay while lit; the state's colour shows when not lit."""
    seen = _nav_steps(panel_page, {
        "label": "Mode", "target_page": "main", "highlight_current": True,
        "bindings": {"show": {"look": {"key": "var.mode", "states": {
            "a": {"label": "ALPHA", "bg_color": "rgb(255, 0, 0)"},
        }}}},
    }, [
        {"state": {"var.mode": "a"}},
        {"page": "other"},
        {"state": {"var.mode": "b"}},
    ])
    assert [s["text"] for s in seen] == ["ALPHA", "ALPHA", "Mode"]
    assert seen[0]["lit"] and seen[0]["bg"] == ACCENT
    assert not seen[1]["lit"] and seen[1]["bg"] == "rgb(255, 0, 0)"
    assert seen[2]["bg"] == ""


# --- A container: a colour or a background picture per state ----------------

# Render one group with a child inside it, stand in for placement (both get the
# inline `position: absolute` the layout engine gives them), then walk states.
GROUP_STEPS_JS = r"""
([element, steps, childLayer]) => {
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  app.bindings = [];
  app.state = {};
  const node = app.renderElement(Object.assign({ id: 'probe', type: 'group' }, element));
  node.style.position = 'absolute';
  const child = document.createElement('div');
  child.style.position = 'absolute';
  child.textContent = 'child';
  if (childLayer) {
    const own = document.createElement('div');
    own.className = 'panel-bg-image-layer';
    own.style.backgroundImage = 'url("child.png")';
    child.prepend(own);
  }
  node.appendChild(child);
  box.appendChild(node);
  const seen = [];
  for (const state of steps) {
    app.state = state;
    app.evaluateAllBindings();
    seen.push({
      registered: app.bindings.map(b => b.type),
      bg: node.style.backgroundColor,
      image: node.style.backgroundImage,
      layers: node.querySelectorAll(':scope > .panel-bg-image-layer').length,
      layerImage: node.querySelector(':scope > .panel-bg-image-layer')?.style.backgroundImage || '',
      position: node.style.position,
      childPosition: child.style.position,
      childLayers: [...child.querySelectorAll(':scope > .panel-bg-image-layer')].map(l => l.style.backgroundImage),
      offline: node.classList.contains('device-offline'),
    });
  }
  return seen;
}
"""


def _group_steps(page, element: dict, steps: list[dict], child_layer: bool = False) -> list[dict]:
    return page.evaluate(GROUP_STEPS_JS, [element, steps, child_layer])


def _modes(key: str) -> dict:
    return {"bindings": {"show": {"look": {"key": key, "states": {
        "combined": {"bg_color": "rgb(0, 80, 0)", "button_image": CHRISTMAS},
        "alarm": {"bg_color": "rgb(200, 0, 0)"},
    }}}}}


def test_a_container_takes_a_colour_and_a_picture_per_state(panel_page) -> None:
    seen = _group_steps(panel_page, {"label": "Room", **_modes("var.mode")}, [
        {"var.mode": "combined"},
        {"var.mode": "alarm"},
        {"var.mode": "split"},
    ])
    assert seen[0]["registered"] == ["group_look"]
    assert seen[0]["bg"] == "rgb(0, 80, 0)" and CHRISTMAS[:20] in seen[0]["image"]
    assert seen[1]["bg"] == "rgb(200, 0, 0)" and seen[1]["image"] == ""
    assert seen[2]["bg"] == "" and seen[2]["image"] == ""


def test_a_faded_state_picture_is_one_layer_and_moves_nothing(panel_page) -> None:
    """The faded picture is a layer of its own: one, reused, gone with the
    picture, and drawing it neither unplaces the container nor its controls."""
    element = {"label": "Room", "style": {"background_opacity": 0.5}, **_modes("var.mode")}
    seen = _group_steps(panel_page, element, [
        {"var.mode": "combined"},
        {"var.mode": "alarm"},
        {"var.mode": "combined"},
        {"var.mode": "combined"},
    ])
    assert [s["layers"] for s in seen] == [1, 0, 1, 1]
    assert CHRISTMAS[:20] in seen[0]["layerImage"]
    assert {s["position"] for s in seen} == {"absolute"}
    assert {s["childPosition"] for s in seen} == {"absolute"}


def test_a_container_look_leaves_a_controls_own_faded_picture_alone(panel_page) -> None:
    """The faded layer a Container's look draws and takes away is its own,
    never the one a control inside it draws for its own faded picture: a
    state with no picture leaves that one, and a faded state picture is drawn
    on a layer of the Container's rather than into the control's."""
    child = ['url("child.png")']
    for style in ({}, {"background_opacity": 0.5}):
        element = {"label": "Room", "style": style, **_modes("var.mode")}
        seen = _group_steps(panel_page, element, [
            {"var.mode": "combined"},
            {"var.mode": "alarm"},
            {"var.mode": "split"},
            {"var.mode": "combined"},
        ], child_layer=True)
        assert [s["childLayers"] for s in seen] == [child] * 4, style
        own = [1, 0, 0, 1] if style else [0, 0, 0, 0]
        assert [s["layers"] for s in seen] == own, style


def test_a_container_claims_no_state_and_drains_nothing_while_away(panel_page) -> None:
    """Back to its own look while the device is away, without the unavailable
    mark: on a container that mark would grey every control inside it."""
    key = "device.acme_widget.mode"
    seen = _group_steps(panel_page, {"label": "Room", **_modes(key)}, [
        {key: "alarm", "device.acme_widget.connected": True},
        {key: "alarm", "device.acme_widget.connected": False},
    ])
    assert seen[0]["bg"] == "rgb(200, 0, 0)"
    assert seen[1]["bg"] == ""
    assert not any(s["offline"] for s in seen)
