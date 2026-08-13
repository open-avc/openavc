"""The matrix control must send what was asked for, and show what is routed.

Every assertion here failed before the fixes it guards. The control routed
correctly the whole time, which is why a bench test that asks "does it switch"
passed on it for months -- what it got wrong was how MANY commands one touch
sent, whether a scroll counted as a touch, and what it drew afterwards.

A real browser is required, and not as a nicety: the double-send came out of
the ordering of pointerup against click, the scroll-drag out of a real scroll
container, and the label clipping out of flexbox overflow. jsdom has no layout
engine and no event ordering worth the name, so it answers none of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip-gate only: the browser itself comes from pytest-playwright's session
# fixtures, never from a second sync_playwright() of our own (see the note in
# test_control_minimums.py -- calling it here breaks every test in the file).
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800
ROUTE_KEY = "device.mx.output.*.input"
AUDIO_KEY = "device.mx.output.*.audio_input"


def _page_html() -> str:
    """The panel's own CSS and JS, with every outbound message captured.

    The @import in panel.css is resolved by hand so the two stylesheets can be
    inlined, matching test_control_minimums.py and tests/fixtures/panel_harness.cjs.
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
  #box {{ position: absolute; left: 40px; top: 40px; }}
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
<script>
  // panel.js builds its app on DOMContentLoaded, so resolve it at call time.
  window.__sent = [];
  const APP = () => {{
    const a = window.__openavcPanel;
    if (!a.__patched) {{ a.send = (m) => window.__sent.push(m); a.__patched = true; }}
    return a;
  }};
  window.__mount = (element, w, h) => {{
    const app = APP();
    window.__sent = [];
    const box = document.getElementById('box');
    box.innerHTML = '';
    box.style.width = w + 'px';
    box.style.height = h + 'px';
    app.bindings = [];
    app.elementMap = {{}};
    app.state = {{}};
    const node = app.renderElement(element);
    node.classList.add('panel-element');
    node.style.position = 'absolute';
    node.style.left = '0px'; node.style.top = '0px';
    node.style.width = '100%'; node.style.height = '100%';
    box.appendChild(node);
    void node.offsetWidth;
  }};
  window.__setState = (obj) => {{
    const app = APP();
    app.state = Object.assign({{}}, obj);
    app.evaluateAllBindings(null);
  }};
</script>
</body></html>
"""


@pytest.fixture(scope="module")
def panel_page(browser):
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong, not the matrix"
    )
    yield page
    context.close()


def _matrix(**config):
    """A matrix element wired the way the Builder writes one."""
    cfg = {"input_count": 4, "output_count": 4, "route_key_pattern": ROUTE_KEY}
    cfg.update(config)
    return {
        "id": "mx1", "type": "matrix", "label": "Routing",
        "matrix_config": cfg,
        "bindings": {"do": {"video_route": [{"action": "device.command"}]}},
    }


def _mount(page, element, w=600, h=400):
    page.evaluate("([e, w, h]) => window.__mount(e, w, h)", [element, w, h])


def _sent(page):
    return page.evaluate("() => window.__sent")


def _crosspoint(page, inp: int, out: int):
    """The cell at one crosspoint, addressed by what it routes.

    Deliberately not `.matrix-cell` by index: the per-output lock and mute
    buttons are `.matrix-cell` too, so an index walks off by one per row.
    """
    return page.locator(f'.matrix-crosspoint[data-input="{inp}"][data-output="{out}"]')


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------

def test_a_tap_sends_exactly_one_route(panel_page) -> None:
    """The cell carried a click handler AND a drag handler that both routed."""
    _mount(panel_page, _matrix())
    _crosspoint(panel_page, 2, 3).click()
    sent = _sent(panel_page)
    assert len(sent) == 1, (
        f"one tap sent {len(sent)} messages: {json.dumps(sent)}"
    )
    assert sent[0] == {
        "type": "ui.route", "element_id": "mx1", "input": 2, "output": 3,
    }


def test_a_tap_with_audio_follow_sends_exactly_two(panel_page) -> None:
    """One video route and one audio route, not two of each."""
    element = _matrix(audio_follow_video=True, audio_route_key_pattern=AUDIO_KEY)
    element["bindings"]["do"]["audio_route"] = [{"action": "device.command"}]
    _mount(panel_page, element)
    _crosspoint(panel_page, 2, 3).click()
    sent = _sent(panel_page)
    assert len(sent) == 2, f"one tap sent {len(sent)}: {json.dumps(sent)}"
    assert [m.get("audio", False) for m in sent] == [False, True]


def test_scrolling_an_oversized_grid_routes_nothing(panel_page) -> None:
    """The gesture that lets you SEE a large matrix must not change it.

    A 16x16 does not fit its box, so it is scrolled by dragging -- and the drag
    handler routed to whatever crosspoint the finger was released over.
    """
    _mount(panel_page, _matrix(input_count=16, output_count=16), 420, 300)
    overflow = panel_page.evaluate("""() => {
        const s = document.querySelector('.matrix-scroll');
        return s.scrollWidth - s.clientWidth;
    }""")
    assert overflow > 100, "this matrix is meant to overflow its box; it does not"

    box = _crosspoint(panel_page, 1, 1).bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    panel_page.mouse.move(cx, cy)
    panel_page.mouse.down()
    panel_page.mouse.wheel(0, 220)          # the grid moves under the finger
    panel_page.mouse.move(cx + 120, cy + 90, steps=12)
    panel_page.mouse.up()

    moved = panel_page.evaluate("() => document.querySelector('.matrix-scroll').scrollTop")
    assert moved > 0, "the grid did not actually scroll, so this proves nothing"
    sent = _sent(panel_page)
    assert sent == [], f"a scroll gesture routed: {json.dumps(sent)}"


def test_a_gesture_the_browser_takes_for_a_pan_routes_nothing(panel_page) -> None:
    """On a touch panel the browser claims the gesture and fires pointercancel.

    That is the signal, and it arrives before any scroll position has changed,
    so it has to be handled on its own rather than left to the scroll check.
    """
    _mount(panel_page, _matrix(input_count=16, output_count=16), 420, 300)
    box = _crosspoint(panel_page, 1, 1).bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    panel_page.mouse.move(cx, cy)
    panel_page.mouse.down()
    panel_page.mouse.move(cx + 60, cy + 40, steps=6)
    _crosspoint(panel_page, 1, 1).dispatch_event("pointercancel")
    panel_page.mouse.up()
    sent = _sent(panel_page)
    assert sent == [], f"a cancelled gesture routed: {json.dumps(sent)}"
    assert panel_page.evaluate(
        "() => document.querySelectorAll('.matrix-drag-line').length") == 0, (
        "the drag line outlived the cancelled gesture"
    )


def test_dragging_across_a_grid_that_fits_still_routes(panel_page) -> None:
    """Drag-to-route survives the fix -- it is only the scroll case that stops."""
    _mount(panel_page, _matrix(), 600, 400)
    start = _crosspoint(panel_page, 1, 1).bounding_box()
    end = _crosspoint(panel_page, 1, 3).bounding_box()
    panel_page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    panel_page.mouse.down()
    panel_page.mouse.move(end["x"] + end["width"] / 2, end["y"] + end["height"] / 2, steps=10)
    panel_page.mouse.up()
    sent = _sent(panel_page)
    assert len(sent) == 1, f"a drag sent {len(sent)}: {json.dumps(sent)}"
    assert sent[0]["input"] == 1 and sent[0]["output"] == 3


# ---------------------------------------------------------------------------
# What gets shown
# ---------------------------------------------------------------------------

def _lit(page) -> int:
    return page.evaluate("() => document.querySelectorAll('.matrix-crosspoint.active').length")


@pytest.mark.parametrize(("reported", "expected"), [
    (2, 4),          # the happy path, and the only one that used to work
    ("2", 4),        # an integer that arrived as text
    ("IN2", 4),      # a switcher that labels its ports
    ("HDMI 2", 4),   # ...with a space in it
    ("Laptop", 0),   # a source NAME matches no numbered crosspoint: stay dark
    ("1080p60", 0),  # two digit runs: refuse to guess
    (0, 0),          # nothing routed
])
def test_crosspoints_light_for_what_the_device_actually_reports(
    panel_page, reported, expected: int,
) -> None:
    """parseInt() decided this, so anything but a bare integer lit nothing."""
    _mount(panel_page, _matrix())
    panel_page.evaluate(
        "(s) => window.__setState(s)",
        {f"device.mx.output.{o}.input": reported for o in range(1, 5)},
    )
    assert _lit(panel_page) == expected, (
        f"a device reporting {reported!r} lit {_lit(panel_page)} crosspoints, "
        f"expected {expected}"
    )


def _badges_lit(page) -> list[int]:
    vis = page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-route-mismatch')).map(b => !b.hidden)""")
    return [i + 1 for i, v in enumerate(vis) if v]


@pytest.mark.parametrize(("video", "audio", "expect_lit"), [
    ("IN1", "IN1", False),   # identical text: parseInt made NaN != NaN, lighting all
    (2, 2, False),           # identical numbers
    (2, "2", False),         # same source, different spelling
    ("HDMI 2", "IN2", False),  # same port number, different labels
    (1, 0, False),           # audio port idle: an absence, not a disagreement
    (1, None, False),        # device publishes no audio route at all
    (1, 3, True),            # a real mismatch, which is the whole point
])
def test_the_audio_mismatch_badge_means_a_mismatch(
    panel_page, video, audio, expect_lit: bool,
) -> None:
    _mount(panel_page, _matrix(audio_route_key_pattern=AUDIO_KEY))
    state = {f"device.mx.output.{o}.input": video for o in range(1, 5)}
    if audio is not None:
        state.update({f"device.mx.output.{o}.audio_input": audio for o in range(1, 5)})
    panel_page.evaluate("(s) => window.__setState(s)", state)
    lit = _badges_lit(panel_page)
    assert bool(lit) is expect_lit, (
        f"video={video!r} audio={audio!r} lit the mismatch badge on {lit or 'nothing'}"
    )


def test_live_output_names_do_not_empty_the_dropdowns(panel_page) -> None:
    """The row label and the <select> shared data-output-idx.

    So the label updater set textContent on the select, which destroys every
    option in it. The control went blank the moment the device reported names.
    """
    element = _matrix(output_key_pattern="device.mx.output.*.name")
    element["matrix_style"] = "list"
    _mount(panel_page, element)
    before = panel_page.evaluate(
        "() => document.querySelector('.matrix-list-select').options.length")
    panel_page.evaluate("(s) => window.__setState(s)", {
        f"device.mx.output.{o}.name": n for o, n in
        enumerate(["Main LCD", "Left Proj", "Right Proj", "Confidence"], 1)
    })
    after = panel_page.evaluate("""() => ({
        options: document.querySelector('.matrix-list-select').options.length,
        label: document.querySelector('.matrix-list-label').textContent,
    })""")
    assert after["options"] == before == 4, (
        f"live output names took the dropdown from {before} options to {after['options']}"
    )
    assert after["label"] == "Main LCD", "the row label should still take the live name"


def test_live_input_names_reach_the_list_dropdown(panel_page) -> None:
    """input_key_pattern only updated nodes tagged data-input-idx, which list
    style has none of, so its options kept their authored captions forever."""
    element = _matrix(input_key_pattern="device.mx.input.*.name")
    element["matrix_style"] = "list"
    _mount(panel_page, element)
    names = ["Apple TV", "Laptop", "Camera", "Signage"]
    panel_page.evaluate("(s) => window.__setState(s)", {
        f"device.mx.input.{i}.name": n for i, n in enumerate(names, 1)
    })
    options = panel_page.evaluate("""() => Array.from(
        document.querySelector('.matrix-list-select').options).map(o => o.textContent)""")
    assert options == names, f"dropdown options are {options}"


# ---------------------------------------------------------------------------
# How it fits the box
# ---------------------------------------------------------------------------

def _cell_size(page) -> tuple[float, float]:
    box = page.locator('.matrix-cell:not(.matrix-toggle)').first.bounding_box()
    return round(box["width"], 1), round(box["height"], 1)


def _scroll_overflow(page) -> dict:
    return page.evaluate("""() => {
        const s = document.querySelector('.matrix-scroll');
        return { x: s.scrollWidth - s.clientWidth, y: s.scrollHeight - s.clientHeight };
    }""")


def test_crosspoint_cells_grow_into_the_room_they_are_given(panel_page) -> None:
    """A cell was a hardcoded 44px whatever the element's box was.

    So a matrix given half a page drew a postage stamp in the corner of it,
    with the rest of the box empty -- and the only way to make the crosspoints
    bigger was to know that `style.cell_size` existed and type a number.
    """
    _mount(panel_page, _matrix(), 1000, 700)
    assert _cell_size(panel_page) == (72, 72), (
        f"a 4x4 in a 1000x700 box drew {_cell_size(panel_page)} cells; with that "
        f"much room they should reach the 72px maximum"
    )


def test_cells_stop_at_a_comfortable_maximum(panel_page) -> None:
    """Growth is capped, not proportional. Past ~72px the dot inside is the
    same 16px and the grid is mostly gap, so a 2x2 on a whole page would
    otherwise draw two 500px squares."""
    _mount(panel_page, _matrix(input_count=2, output_count=2), 1200, 760)
    assert _cell_size(panel_page) == (72, 72)


def test_a_tighter_box_shrinks_the_cells_before_the_grid_scrolls(panel_page) -> None:
    """Between the maximum and the touch floor the cells give way, not the view.

    Scrolling is the last resort: the gesture that scrolls an oversized grid is
    the one a finger makes on a crosspoint, which is the whole of F2.
    """
    _mount(panel_page, _matrix(input_count=8, output_count=8), 520, 500)
    w, h = _cell_size(panel_page)
    assert 44 < w < 72 and 44 < h < 72, (
        f"an 8x8 in a 520x500 box drew {w}x{h} cells; it has room for more than "
        f"the 44px floor and less than the 72px maximum"
    )
    assert _scroll_overflow(panel_page) == {"x": 0, "y": 0}, (
        "it shrank AND scrolled; shrinking is what avoids the scroll"
    )


def test_cells_stop_at_the_touch_floor_and_the_grid_scrolls(panel_page) -> None:
    """Below 44px a crosspoint is not a touch target, so the grid scrolls
    instead. That is the boundary openavc/ui/control_minimums.py records."""
    _mount(panel_page, _matrix(input_count=16, output_count=16), 420, 300)
    assert _cell_size(panel_page) == (44, 44)
    overflow = _scroll_overflow(panel_page)
    assert overflow["x"] > 0 and overflow["y"] > 0, (
        f"a 16x16 in a 420x300 box should be scrolling, not fitting: {overflow}"
    )


def test_an_authored_cell_size_still_pins_the_cell(panel_page) -> None:
    """Fitting is what happens when nobody said otherwise.

    `style.cell_size` has always meant "this size", and a project that set it
    must keep getting it -- in a roomy box, where fitting would have grown the
    cell, and in a tight one, where fitting would have shrunk it.
    """
    element = _matrix()
    element["style"] = {"cell_size": 60 / 14}
    for w, h in ((1000, 700), (400, 340)):
        _mount(panel_page, element, w, h)
        assert _cell_size(panel_page) == (60, 60), (
            f"an authored 60px cell drew {_cell_size(panel_page)} in a {w}x{h} box"
        )


# ---------------------------------------------------------------------------
# Source names (F8)
# ---------------------------------------------------------------------------

SOURCES = ["Apple TV", "Room PC", "Laptop HDMI", "Doc Cam",
           "Blu-ray", "Camera 1", "Wireless", "Signage"]


def test_column_headers_are_numbers_not_clipped_source_names(panel_page) -> None:
    """Above four inputs the header rotated 45 degrees and capped at 4.2857rem
    of text, which clipped seven of these eight names; at four or fewer it did
    not rotate and they collided instead. A number always fits."""
    _mount(panel_page, _matrix(input_count=8, input_labels=SOURCES), 700, 500)
    headers = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-input-header')).map(h => h.textContent)""")
    assert headers == [str(i) for i in range(1, 9)], (
        f"column headers read {headers}"
    )
    assert panel_page.locator('.matrix-input-header.rotated').count() == 0, (
        "a column header is still rotated"
    )


def test_the_source_legend_names_every_input_in_full(panel_page) -> None:
    """The names have to go somewhere, and the legend is where they go.

    Legible means not truncated: each entry's own text has to fit the box it is
    drawn in, which is exactly what the rotated header could not do.
    """
    _mount(panel_page, _matrix(input_count=8, input_labels=SOURCES), 700, 500)
    entries = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-legend-item')).map(item => ({
            num: item.querySelector('.matrix-legend-num').textContent,
            name: item.querySelector('[data-label-text]').textContent,
            needed: item.querySelector('[data-label-text]').scrollWidth,
            available: item.querySelector('[data-label-text]').clientWidth,
        }))""")
    assert [e["name"] for e in entries] == SOURCES, "the legend lost a source"
    assert [e["num"] for e in entries] == [str(i) for i in range(1, 9)], (
        "the legend's numbers do not line up with the column numbers"
    )
    clipped = [e["name"] for e in entries if e["needed"] > e["available"] + 0.5]
    assert not clipped, f"the legend truncates {clipped}"


def test_live_input_names_reach_the_legend(panel_page) -> None:
    """`input_key_pattern` updates whatever carries data-input-idx, which moved
    from the (now numbered) column header onto the legend entry."""
    _mount(panel_page, _matrix(input_count=4,
                               input_key_pattern="device.mx.input.*.name"), 700, 500)
    live = ["Apple TV", "Laptop", "Camera", "Signage"]
    panel_page.evaluate("(s) => window.__setState(s)", {
        f"device.mx.input.{i}.name": n for i, n in enumerate(live, 1)
    })
    shown = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-legend-item [data-label-text]')).map(n => n.textContent)""")
    assert shown == live, f"the legend reads {shown}"
    headers = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-input-header')).map(h => h.textContent)""")
    assert headers == ["1", "2", "3", "4"], (
        f"live names overwrote the column numbers: {headers}"
    )


def test_the_legend_is_one_strip_and_cannot_eat_the_grid(panel_page) -> None:
    """Eight long names wrapped would be four rows, and every row of it comes
    out of the grid's height -- so the control would lose crosspoints to its
    own key, and no floor could be stated for it. It scrolls sideways instead.
    """
    long_names = [f"{n} in the Main Lecture Hall" for n in SOURCES]
    _mount(panel_page, _matrix(input_count=8, input_labels=long_names), 500, 460)
    legend = panel_page.evaluate("""() => {
        const l = document.querySelector('.matrix-legend');
        const items = l.querySelectorAll('.matrix-legend-item');
        return {
            rows: new Set(Array.from(items).map(
                i => Math.round(i.getBoundingClientRect().top))).size,
            wrapped: l.scrollHeight - l.clientHeight,
            reachable: l.scrollWidth > l.clientWidth,
        };
    }""")
    assert legend["rows"] == 1, f"the legend wrapped to {legend['rows']} rows"
    assert legend["wrapped"] == 0
    assert legend["reachable"], (
        "these names are meant to overflow the strip, so this proves nothing"
    )


def test_a_long_destination_name_loses_its_tail_not_its_head(panel_page) -> None:
    """A right-aligned flex box with overflow:hidden clips the START of a line,
    and text-overflow does not apply to it -- so "Main LCD" rendered as "ain
    LCD" with nothing to say it had been truncated. That reads as a typo."""
    element = _matrix(
        output_labels=["Main Lecture Hall Display", "B", "C", "D"],
        input_count=8,
    )
    _mount(panel_page, element, 380, 300)
    box = panel_page.evaluate("""() => {
        const span = document.querySelector(
            '.matrix-output-header[data-output-idx="0"] [data-label-text]');
        const h = span.parentElement;
        return {
            // Measured against the HEADER, not the span: before the fix the
            // span was never shrunk, so its own scrollWidth and clientWidth
            // agreed and a span-only check reported nothing to see.
            needed: span.scrollWidth,
            available: h.clientWidth,
            spanLeft: span.getBoundingClientRect().left,
            headerLeft: h.getBoundingClientRect().left,
        };
    }""")
    assert box["needed"] > box["available"], (
        "this label is meant to be too long for its column, but it fits "
        f"({box['needed']}px of {box['available']}px available)"
    )
    # Clipped from the tail means the text still STARTS inside its header.
    # Clipped from the head pushes the span's own left edge outside it.
    assert box["spanLeft"] >= box["headerLeft"] - 0.5, (
        "the label is being clipped from the front: its first characters sit "
        f"{box['headerLeft'] - box['spanLeft']:.1f}px outside the header"
    )
