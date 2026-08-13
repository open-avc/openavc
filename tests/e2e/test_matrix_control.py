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

from openavc.core.project_migration import (
    _migrate_matrix_config_0_9_to_0_10 as migrate_matrix_config,
)
from openavc.ui.matrix_model import resolve_matrix_config

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
    """A matrix element as the SERVER hands it to the panel: two resolved lists.

    Written in the shorthand an author still types -- counts, labels, a key with
    a `*` in it -- and expanded here by the real resolver, through the real
    migration, because that is exactly the path a project written before format
    0.10.0 takes to reach this renderer. The panel has no expander of its own
    (matrix plan D6), so handing it the shorthand would draw an empty box.
    """
    cfg = {"input_count": 4, "output_count": 4, "route_key_pattern": ROUTE_KEY}
    cfg.update(config)
    return {
        "id": "mx1", "type": "matrix", "label": "Routing",
        "matrix_config": resolve_matrix_config(migrate_matrix_config(cfg)),
        "bindings": {"do": {"video_route": [{"action": "device.command"}]}},
    }


def _resolved(**config):
    """A matrix authored in the 0.10.0 form directly, entry by entry.

    The half the shorthand above cannot reach: per-destination keys, opaque
    values, a subset of a frame's ports.
    """
    cfg = {"sources": [], "destinations": []}
    cfg.update(config)
    return {
        "id": "mx1", "type": "matrix", "label": "Routing",
        "matrix_config": resolve_matrix_config(cfg),
        "bindings": {"do": {"video_route": [{"action": "device.command"}]}},
    }


def _mount(page, element, w=600, h=400):
    page.evaluate("([e, w, h]) => window.__mount(e, w, h)", [element, w, h])


def _sent(page):
    return page.evaluate("() => window.__sent")


def _crosspoint(page, inp, out):
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
    """Which destinations are naming an audio source separate from their video.

    It was an 'A\u2260V' badge until Phase 6; what it says changed, and WHEN it
    says it did not, which is what these cases are about.
    """
    vis = page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-audio-source')).map(b => !b.hidden)""")
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
def test_a_separate_audio_source_is_named_only_when_there_IS_one(
    panel_page, video, audio, expect_lit: bool,
) -> None:
    _mount(panel_page, _matrix(audio_route_key_pattern=AUDIO_KEY))
    state = {f"device.mx.output.{o}.input": video for o in range(1, 5)}
    if audio is not None:
        state.update({f"device.mx.output.{o}.audio_input": audio for o in range(1, 5)})
    panel_page.evaluate("(s) => window.__setState(s)", state)
    lit = _badges_lit(panel_page)
    assert bool(lit) is expect_lit, (
        f"video={video!r} audio={audio!r} named a separate audio source on "
        f"{lit or 'nothing'}"
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
        "() => document.querySelector('.matrix-list-select')"
        ".querySelectorAll('option[data-source-idx]').length")
    panel_page.evaluate("(s) => window.__setState(s)", {
        f"device.mx.output.{o}.name": n for o, n in
        enumerate(["Main LCD", "Left Proj", "Right Proj", "Confidence"], 1)
    })
    after = panel_page.evaluate("""() => ({
        options: document.querySelector('.matrix-list-select')
            .querySelectorAll('option[data-source-idx]').length,
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
        document.querySelector('.matrix-list-select')
            .querySelectorAll('option[data-source-idx]')).map(o => o.textContent)""")
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


DESTINATIONS = ["Main LCD", "Left Proj", "Right Proj", "Confidence",
                "Lobby TV", "Stream", "Overflow", "Record"]


def _label_column_px(page) -> float:
    return page.evaluate("""() => document.querySelector(
        '.matrix-output-header[data-output-idx="0"]').getBoundingClientRect().width""")


def test_destination_names_are_not_starved_by_the_cells(panel_page) -> None:
    """The names must not lose to the dots they label.

    A grid shares its spare room out EQUALLY between the tracks that can take
    it. Once the cells could grow, the one name column was getting a ninth of
    that room and starting from nothing (`.matrix-header` is `overflow: hidden`,
    which makes a grid track's min-content zero) -- so an 8x8 with 563px to
    spend drew "Main LCD" as "M". Nothing measured caught it: the floor
    deliberately does not size text, so the column was free to collapse.
    """
    _mount(panel_page, _matrix(input_count=8, output_count=8,
                               output_labels=DESTINATIONS), 563, 592)
    assert _label_column_px(panel_page) >= 80, (
        f"the destination column is {_label_column_px(panel_page):.0f}px wide in a "
        f"563px box, so the names are being starved by the crosspoints"
    )
    truncated = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]'))
        .filter(s => s.scrollWidth > s.clientWidth + 0.5).map(s => s.textContent)""")
    assert not truncated, (
        f"a matrix with room to spare still truncates {truncated}"
    )


def test_the_destination_column_keeps_its_room_at_the_floor(panel_page) -> None:
    """Declared, not content-derived, so the floor can state it.

    The column is 80px whatever the names are -- the same number the list
    style's own label already declares. That is what lets control_minimums.py
    publish a width at all: a column sized to its content is a column whose
    width is whatever somebody typed.
    """
    element = _matrix(input_count=8, output_count=8, output_labels=DESTINATIONS)
    _mount(panel_page, element, 455 + 45, 446)
    assert _label_column_px(panel_page) >= 80


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


# ---------------------------------------------------------------------------
# The model (project format 0.10.0)
#
# Each of these is a shape the pattern form could not express at all. They are
# not refinements of the grid -- they are the reason the grid was rewritten:
# one glob pattern plus two counts can only describe a rectangular frame with
# contiguous ports numbered from one, on one device, reporting plain integers,
# and the driver corpus is not that.
# ---------------------------------------------------------------------------

# An 8x8 frame with six ports patched, which is what a room actually has. The
# gap is the point: rows 5 and 6 are outputs 7 and 8.
PATCHED = [
    {"value": v, "label": lbl, "route_key": f"device.mx.output.{v}.input"}
    for v, lbl in ((1, "Main LCD"), (2, "Left Proj"), (3, "Right Proj"),
                   (4, "Confidence"), (7, "Lobby TV"), (8, "Record"))
]
TWO_SOURCES = [{"value": 1, "label": "Apple TV"}, {"value": 2, "label": "Room PC"}]


def test_a_matrix_draws_the_entries_it_has_not_a_rectangle(panel_page) -> None:
    """Six destinations out of an eight-output frame is six rows.

    'Leave out unused inputs and outputs' turns out not to be a feature at all
    -- it is a shorter list. The counts form had nowhere to put a gap, so an
    8x8 frame with two ports patched drew eight rows, two of which routed
    signal nowhere.
    """
    _mount(panel_page, _resolved(sources=TWO_SOURCES, destinations=PATCHED), 700, 600)
    rows = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]')).map(s => s.textContent)""")
    assert rows == ["Main LCD", "Left Proj", "Right Proj",
                    "Confidence", "Lobby TV", "Record"]
    assert panel_page.locator(".matrix-crosspoint").count() == 12, (
        "six destinations by two sources is twelve crosspoints"
    )


def test_a_tap_sends_the_destinations_own_value_not_its_row(panel_page) -> None:
    """The sharpest consequence of a gap in the list.

    Row five of that patched frame is OUTPUT 7. The old renderer sent the row
    number, so on any matrix that was not a full contiguous rectangle it routed
    the wrong destination -- correctly, silently, and every time.
    """
    _mount(panel_page, _resolved(sources=TWO_SOURCES, destinations=PATCHED), 700, 600)
    _crosspoint(panel_page, 2, 7).click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 2, "output": 7}
    ]


def test_a_source_value_need_not_be_a_number(panel_page) -> None:
    """A Gefen frame's ids are strings and a Crestron NVX source is a URL.

    The value goes out as authored -- not coerced, not indexed -- because the
    device is the one that decides what a source is called.
    """
    element = _resolved(
        sources=[{"value": "HDMI_A", "label": "Laptop"},
                 {"value": "rtsp://10.0.0.9/live", "label": "Stream"}],
        destinations=[{"value": "out_a", "label": "Main LCD",
                       "route_key": "device.mx.out_a.source"}],
    )
    _mount(panel_page, element, 700, 400)
    _crosspoint(panel_page, "rtsp://10.0.0.9/live", "out_a").click()
    assert _sent(panel_page) == [{
        "type": "ui.route", "element_id": "mx1",
        "input": "rtsp://10.0.0.9/live", "output": "out_a",
    }]


def test_one_matrix_can_span_two_devices(panel_page) -> None:
    """Each destination owns its key, so nothing says they share a device.

    A single glob pattern made this unsayable: every row of a matrix had to be
    an output of one frame, which is why a page needing a switcher and an
    encoder needed two controls that could not show one picture.
    """
    element = _resolved(
        sources=TWO_SOURCES,
        destinations=[
            {"value": 1, "label": "Main LCD", "route_key": "device.mx.output.1.input"},
            {"value": "live", "label": "Stream", "route_key": "device.enc.source"},
        ],
    )
    _mount(panel_page, element, 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.output.1.input": 1,
        "device.enc.source": 2,
    })
    lit = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-crosspoint.active')).map(d => [d.dataset.input, d.dataset.output])""")
    assert lit == [["1", "1"], ["2", "live"]], (
        f"two devices' routes should light one crosspoint each; lit {lit}"
    )


def test_one_destination_can_watch_audio_while_the_others_do_not(panel_page) -> None:
    """The badge is per destination now, because the key is.

    A single audio_route_key_pattern put a badge on every row or on none, so a
    frame whose audio breakaway is wired on one output could not say so.
    """
    element = _resolved(
        sources=TWO_SOURCES,
        destinations=[
            {"value": 1, "label": "Main LCD", "route_key": "device.mx.output.1.input",
             "audio_route_key": "device.mx.output.1.audio"},
            {"value": 2, "label": "Left Proj", "route_key": "device.mx.output.2.input"},
        ],
    )
    _mount(panel_page, element, 700, 400)
    assert panel_page.locator(".matrix-audio-source").count() == 1, (
        "only the destination that names an audio key can name an audio source"
    )
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.output.1.input": 1,
        "device.mx.output.1.audio": 2,
        "device.mx.output.2.input": 1,
    })
    assert panel_page.locator(".matrix-audio-source:not([hidden])").count() == 1


def test_a_live_name_is_per_entry_not_per_axis(panel_page) -> None:
    """Two sources on two devices, each named by its own key."""
    element = _resolved(
        sources=[
            {"value": 1, "label": "Input 1", "label_key": "device.mx.input.1.name"},
            {"value": 2, "label": "Input 2", "label_key": "device.enc.name"},
        ],
        destinations=[{"value": 1, "label": "Main LCD",
                       "route_key": "device.mx.output.1.input"}],
    )
    _mount(panel_page, element, 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.input.1.name": "Apple TV",
        "device.enc.name": "Encoder A",
    })
    shown = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-legend-item [data-label-text]')).map(n => n.textContent)""")
    assert shown == ["Apple TV", "Encoder A"]


def test_a_list_dropdown_sends_the_sources_typed_value(panel_page) -> None:
    """A DOM option value is always a string; a device that wants 3 wants 3."""
    element = _resolved(
        sources=[{"value": 3, "label": "Doc Cam"}, {"value": "HDMI_A", "label": "Laptop"}],
        destinations=[{"value": 6, "label": "Confidence",
                       "route_key": "device.mx.output.6.input"}],
    )
    element["matrix_style"] = "list"
    _mount(panel_page, element, 400, 300)
    # Index 0 is the "nothing routed" placeholder, which is disabled and cannot
    # be chosen -- that is the point of it. The sources start at 1.
    panel_page.select_option(".matrix-list-select", index=1)
    panel_page.select_option(".matrix-list-select", index=2)
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 3, "output": 6},
        {"type": "ui.route", "element_id": "mx1", "input": "HDMI_A", "output": 6},
    ]


def test_a_list_row_follows_a_route_reported_as_a_name(panel_page) -> None:
    """The dropdown moves to the option the device's report names, whatever the
    device chose to call it."""
    element = _resolved(
        sources=[{"value": "HDMI_A", "label": "Laptop"},
                 {"value": "HDMI_B", "label": "Room PC"}],
        destinations=[{"value": 1, "label": "Main LCD",
                       "route_key": "device.mx.output.1.input"}],
    )
    element["matrix_style"] = "list"
    _mount(panel_page, element, 400, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.1.input": "HDMI_B"})
    assert panel_page.locator(".matrix-list-select").input_value() == "HDMI_B"


def test_a_matrix_that_says_nothing_draws_nothing(panel_page) -> None:
    """Rather than the phantom 4x4 the old default invented.

    Four rows of dots that can never light look like a working control that the
    device is not answering, which is the most expensive thing a panel can look
    like. An empty box is visible, and the page review names it.
    """
    _mount(panel_page, _resolved(), 400, 300)
    assert panel_page.locator(".matrix-crosspoint").count() == 0
    assert panel_page.locator(".matrix-output-header").count() == 0


def test_a_matrix_that_reached_the_panel_unexpanded_draws_nothing(panel_page) -> None:
    """The panel has no expander, and must not half-invent one.

    Resolution happens once, on the server (D6). A generator arriving here means
    something upstream did not run, and the honest answer is an empty box rather
    than a guess at what `count: 8` meant.
    """
    element = _resolved()
    element["matrix_config"] = {
        "sources": {"from": {"count": 8}},
        "destinations": {"from": {"count": 8, "route_key": ROUTE_KEY}},
    }
    _mount(panel_page, element, 700, 600)
    assert panel_page.locator(".matrix-crosspoint").count() == 0


# ---------------------------------------------------------------------------
# What the device picker writes, drawn by the real renderer
#
# Phase 4 changed no renderer code, so these do not guard a rendering defect.
# They guard the JOIN: the inference produces route keys, opaque values and an
# action list, and the only thing that can say those are usable is the renderer
# reading them. Every field below comes out of `propose_matrices` -- nothing in
# these fixtures is hand-written, which is the point, because a hand-written
# fixture proves the test author agrees with themselves.
# ---------------------------------------------------------------------------

#: An invented decoder carrying two independent routing planes, chosen by a
#: parameter on one command. The shape is the AVoIP one from the plan's §2.1a
#: (a decoder routes video, audio, USB and more separately); the names are not
#: any product's.
ACME_AVOIP = {
    "child_entity_types": {
        "decoder": {
            "label": "Decoder", "label_plural": "Decoders",
            "id_format": {"type": "integer", "min": 1, "max": 3},
            "state_variables": {
                "source_video": {"type": "integer", "label": "Video Source"},
                "source_audio": {"type": "integer", "label": "Audio Source"},
                "name": {"type": "string"},
            },
        },
        "encoder": {
            "label": "Encoder", "label_plural": "Encoders",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"name": {"type": "string"}},
        },
    },
    "commands": {
        "route": {"params": {
            "decoder_id": {"type": "child_id", "child_type": "decoder"},
            "encoder_id": {"type": "child_id", "child_type": "encoder"},
            "stream": {"type": "enum", "required": True, "values": ["VIDEO", "AUDIO"]},
        }},
    },
}


def _from_driver(plane: str, roster=None) -> dict:
    """A matrix element built the way the picker builds one.

    The picker writes the axes out entry by entry (matrix plan D5), so this is
    the resolved form already -- there is nothing left to expand.
    """
    from openavc.ui.matrix_inference import propose_matrices

    proposal = next(
        p for p in propose_matrices("mx", ACME_AVOIP, roster) if p["id"] == plane
    )
    return {
        "id": "mx1", "type": "matrix", "label": "Routing",
        "matrix_config": {
            "sources": proposal["sources"],
            "destinations": proposal["destinations"],
        },
        "bindings": {"do": {"route": proposal["route"]}},
    }, proposal


def test_a_matrix_read_off_a_driver_draws_what_the_driver_declares(panel_page) -> None:
    """Three decoders and two encoders, with nothing typed by hand."""
    element, _ = _from_driver("decoder.source_video")
    _mount(panel_page, element, 700, 500)
    assert panel_page.locator(".matrix-crosspoint").count() == 6
    rows = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]')).map(s => s.textContent)""")
    assert rows == ["Decoder 1", "Decoder 2", "Decoder 3"]


def test_the_crosspoints_light_off_the_keys_the_driver_was_read_for(panel_page) -> None:
    """The inference's route_key and the renderer's state lookup must be one key.

    A key that is one segment out lights nothing, and looks exactly like a
    device that is not reporting -- which is the defect this whole plan started
    from, arrived at from the other end.
    """
    element, proposal = _from_driver("decoder.source_video")
    _mount(panel_page, element, 700, 500)
    panel_page.evaluate("(s) => window.__setState(s)", {
        proposal["destinations"][0]["route_key"]: 2,
        proposal["destinations"][2]["route_key"]: 1,
    })
    lit = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-crosspoint.active')
    ).map(c => `${c.dataset.input}->${c.dataset.output}`).sort()""")
    assert lit == ["1->3", "2->1"]


def test_the_two_planes_of_one_decoder_watch_different_keys(panel_page) -> None:
    """One element covers one plane, and the plane is part of the key.

    Six routing planes needed no new machinery at all (matrix plan §3.1), and
    this is that claim tested rather than asserted: the audio matrix must not
    light for a video route.
    """
    video, video_proposal = _from_driver("decoder.source_video")
    _mount(panel_page, video, 700, 500)
    panel_page.evaluate("(s) => window.__setState(s)", {
        video_proposal["destinations"][0]["route_key"]: 2,
        "device.mx.decoder.1.source_audio": 1,
    })
    lit = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-crosspoint.active')
    ).map(c => `${c.dataset.input}->${c.dataset.output}`)""")
    assert lit == ["2->1"], "the video matrix lit for the audio route"

    audio, audio_proposal = _from_driver("decoder.source_audio")
    assert audio_proposal["destinations"][0]["route_key"].endswith("source_audio")
    _mount(panel_page, audio, 700, 500)
    panel_page.evaluate("(s) => window.__setState(s)", {
        video_proposal["destinations"][0]["route_key"]: 2,
        "device.mx.decoder.1.source_audio": 1,
    })
    lit = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-crosspoint.active')
    ).map(c => `${c.dataset.input}->${c.dataset.output}`)""")
    assert lit == ["1->1"]


def test_a_tap_sends_the_values_the_driver_named(panel_page) -> None:
    """And the route action carries the plane, so the audio matrix routes audio."""
    element, proposal = _from_driver("decoder.source_audio")
    _mount(panel_page, element, 700, 500)
    _crosspoint(panel_page, 2, 3).click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 2, "output": 3}
    ]
    assert proposal["route"][0]["params"] == {
        "decoder_id": "$output", "encoder_id": "$input", "stream": "AUDIO",
    }


def test_a_patched_frame_read_live_draws_only_the_ports_that_are_there(panel_page) -> None:
    """The declared range says three decoders; this unit has two, numbered 1 and 3.

    Reading the live roster rather than the file is what makes the difference
    visible, and the gap in the numbering is what the pattern form could never
    say.
    """
    roster = {
        "decoder": [
            {"local_id": 1, "local_id_padded": "1", "label": "Lobby"},
            {"local_id": 3, "local_id_padded": "3", "label": "Boardroom"},
        ],
        "encoder": [{"local_id": 2, "local_id_padded": "2", "label": "Laptop"}],
    }
    element, _ = _from_driver("decoder.source_video", roster)
    _mount(panel_page, element, 700, 500)
    rows = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]')).map(s => s.textContent)""")
    assert rows == ["Lobby", "Boardroom"]
    _crosspoint(panel_page, 2, 3).click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 2, "output": 3}
    ]


def test_a_live_port_name_from_the_driver_reaches_the_label(panel_page) -> None:
    """The picker binds a label_key only where the DRIVER declares a name.

    The platform puts a `label` on every child, but that one is the project's
    own name for the port -- which is what the author is editing in the picker,
    so binding it would make renaming do nothing.
    """
    element, proposal = _from_driver("decoder.source_video")
    assert proposal["destinations"][0]["label_key"] == "device.mx.decoder.1.name"
    _mount(panel_page, element, 700, 500)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.decoder.1.name": "Boardroom",
    })
    rows = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]')).map(s => s.textContent)""")
    assert rows[0] == "Boardroom"


# ---------------------------------------------------------------------------
# What a driver that DECLARES its routing produces, drawn by the real renderer
#
# The guess is structural, so there are shapes it cannot reach: a routing
# command needing a value no property name supplies, and a device that routes
# itself and has no ports to enumerate. Both are in the shipped corpus, and
# both are silent -- the first sends a command the device refuses, the second
# offers nothing at all. These mount what a declaration produces and check the
# renderer can use it.
# ---------------------------------------------------------------------------

#: An invented decoder whose one routing command covers several signals and is
#: told which by a parameter. The routed property is a plain `source`, so
#: nothing in its name says which signal -- which is exactly when the guess
#: cannot fill the parameter in.
ACME_UNNAMEABLE = {
    "child_entity_types": {
        "decoder": {
            "label": "Decoder", "label_plural": "Decoders",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"source": {"type": "integer", "label": "Source"}},
        },
        "encoder": {
            "label": "Encoder", "label_plural": "Encoders",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"name": {"type": "string"}},
        },
    },
    "commands": {
        "switch": {"params": {
            "decoder_id": {"type": "child_id", "child_type": "decoder"},
            "encoder_id": {"type": "child_id", "child_type": "encoder"},
            "signal": {"type": "enum", "required": True,
                       "values": ["ALL", "VIDEO", "IR"]},
        }},
    },
    "routing": {
        "destination_child_type": "decoder",
        "source_child_type": "encoder",
        "command": "switch",
        "planes": [{"label": "Source", "route_property": "source",
                    "params": {"signal": "ALL"}}],
    },
}

#: An invented endpoint that IS one end of a route: it shows one thing at a
#: time, has no destination child, and its command addresses the device.
ACME_ENDPOINT = {
    "state_variables": {
        "video_source": {"type": "enum", "label": "Video Source",
                         "values": ["None", "Input1", "Input2", "Stream"]},
    },
    "commands": {
        "set_video_source": {"params": {
            "source": {"type": "enum", "required": True,
                       "values": ["None", "Input1", "Input2", "Stream"]},
        }},
    },
    "routing": {"planes": [
        {"label": "Video", "route_property": "video_source",
         "command": "set_video_source", "source_param": "source"},
    ]},
}

#: A channel carrying a real source selector alongside one that only READS
#: like a route: it picks between Dante and analogue, not between sources.
#: Nothing about its shape says so, so only the driver can.
ACME_NOISY = {
    "child_entity_types": {
        "channel": {
            "label": "Channel", "label_plural": "Channels",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "primary_source": {"type": "string", "label": "Primary Source"},
                "dante_audio_source": {"type": "enum", "label": "Dante Audio Source",
                                       "values": ["DANTE", "NATIVE"]},
            },
        },
    },
    "commands": {
        "set_primary_source": {"params": {
            "channel": {"type": "child_id", "child_type": "channel"},
            "source": {"type": "enum", "values": ["Analog 1", "Analog 2"]},
        }},
        "set_dante_audio_source": {"params": {
            "channel": {"type": "child_id", "child_type": "channel"},
            "source": {"type": "enum", "values": ["DANTE", "NATIVE"]},
        }},
    },
    "routing": {"planes": [
        {"label": "Source", "destination_child_type": "channel",
         "route_property": "primary_source", "command": "set_primary_source"},
    ]},
}


def _declared(driver: dict, device_id: str = "mx", index: int = 0):
    """A matrix element built from a DECLARED driver, the way the picker builds one."""
    from openavc.ui.matrix_inference import propose_matrices

    proposals = propose_matrices(device_id, driver, None)
    assert len(proposals) > index, (
        f"this driver declares its routing and {len(proposals)} plane(s) came "
        f"back, so there is no matrix to draw"
    )
    proposal = proposals[index]
    return {
        "id": "mx1", "type": "matrix", "label": "Routing",
        "matrix_config": {
            "sources": proposal["sources"],
            "destinations": proposal["destinations"],
        },
        "bindings": {"do": {"route": proposal["route"]}},
    }, proposal, proposals


def test_a_declared_plane_routes_with_the_value_its_command_requires(panel_page) -> None:
    """The guess leaves `signal` out, so the route is refused on the wire.

    Nothing about that is visible on the panel -- the tap looks identical --
    which is why it survived: the crosspoint lights from feedback that never
    arrives because the command never ran.
    """
    element, proposal, _ = _declared(ACME_UNNAMEABLE)
    assert proposal["route"][0]["params"] == {
        "decoder_id": "$output", "encoder_id": "$input", "signal": "ALL",
    }
    _mount(panel_page, element, 700, 500)
    _crosspoint(panel_page, 2, 1).click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 2, "output": 1}
    ]


def test_a_device_that_routes_itself_draws_a_row(panel_page) -> None:
    """No destination child at all, so the guess offers nothing to draw."""
    _, proposal, proposals = _declared(ACME_ENDPOINT, device_id="nvx")
    assert len(proposals) == 1, "a self-routing endpoint proposed nothing"
    element = _declared(ACME_ENDPOINT, device_id="nvx")[0]
    _mount(panel_page, element, 700, 500)
    rows = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-output-header [data-label-text]')).map(s => s.textContent)""")
    assert rows == ["nvx"]
    assert panel_page.locator(".matrix-crosspoint").count() == 4


def test_a_self_routing_device_lights_off_its_own_key(panel_page) -> None:
    """The route key has no child segment, and the renderer must read it whole."""
    element, proposal, _ = _declared(ACME_ENDPOINT, device_id="nvx")
    assert proposal["destinations"][0]["route_key"] == "device.nvx.video_source"
    _mount(panel_page, element, 700, 500)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.nvx.video_source": "Input2"})
    lit = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-crosspoint.active')
    ).map(c => `${c.dataset.input}->${c.dataset.output}`)""")
    assert lit == ["Input2->nvx"]


def test_a_self_routing_tap_sends_the_source_and_the_device(panel_page) -> None:
    element, _, _ = _declared(ACME_ENDPOINT, device_id="nvx")
    _mount(panel_page, element, 700, 500)
    _crosspoint(panel_page, "Input1", "nvx").click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": "Input1", "output": "nvx"}
    ]


def test_a_declaration_leaves_only_the_planes_it_declared(panel_page) -> None:
    """The selector that only reads like a route must not be offered.

    A matrix built from it would draw two convincing crosspoints per channel
    that switch the audio backend rather than the source.
    """
    element, proposal, proposals = _declared(ACME_NOISY, device_id="amp")
    assert [p["route_property"] for p in proposals] == ["primary_source"]
    _mount(panel_page, element, 700, 500)
    assert panel_page.locator(".matrix-crosspoint").count() == 4
    assert proposal["destinations"][0]["route_key"] == (
        "device.amp.channel.1.primary_source"
    )


# ---------------------------------------------------------------------------
# Destination-first: the tile wall
# ---------------------------------------------------------------------------

def _tiles(**config):
    element = _resolved(**config)
    element["matrix_style"] = "tiles"
    return element


def _wall(destinations=4, sources=3, **extra):
    return _tiles(
        sources=[{"value": i, "label": f"Src {i}"} for i in range(1, sources + 1)],
        destinations=[
            {"value": i, "label": f"Dest {i}",
             "route_key": f"device.mx.output.{i}.input"}
            for i in range(1, destinations + 1)
        ],
        **extra,
    )


def test_a_tile_wall_draws_one_card_per_destination(panel_page) -> None:
    """And no crosspoints at all: the sources are not on the wall.

    That is the whole shape of the style. A crosspoint grid is inputs times
    outputs of dots; a wall is one card per destination saying what is on it,
    and the sources live in the chooser a card opens.
    """
    _mount(panel_page, _wall(destinations=6, sources=12), 800, 500)
    assert panel_page.locator(".matrix-tile").count() == 6
    assert panel_page.locator(".matrix-crosspoint").count() == 0
    names = panel_page.evaluate("""() => Array.from(document.querySelectorAll(
        '.matrix-tile-dest [data-label-text]')).map(n => n.textContent)""")
    assert names == [f"Dest {i}" for i in range(1, 7)]


def test_a_tile_wall_draws_the_shape_its_floor_is_stated_for(panel_page) -> None:
    """Eight destinations are four across and two down, on both sides of the mirror.

    The floor in control_minimums.py is a rectangle only because the renderer
    commits to a shape rather than reflowing to whatever width it is given. If
    the two ever disagree, the published minimum is for a layout nothing draws
    -- and the way that shows up in a room is tiles below the fold at exactly
    the size the Builder called big enough.
    """
    _mount(panel_page, _wall(destinations=8), 900, 500)
    rows = panel_page.evaluate("""() => {
        const tops = Array.from(document.querySelectorAll('.matrix-tile'))
            .map(t => Math.round(t.getBoundingClientRect().top));
        return [...new Set(tops)].length;
    }""")
    columns = panel_page.evaluate("""() => {
        const lefts = Array.from(document.querySelectorAll('.matrix-tile'))
            .map(t => Math.round(t.getBoundingClientRect().left));
        return [...new Set(lefts)].length;
    }""")
    assert (columns, rows) == (4, 2)


def test_a_tile_names_what_is_routed_to_it(panel_page) -> None:
    _mount(panel_page, _wall(), 800, 500)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.2.input": 3})
    shown = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-tile-source')).map(n => n.textContent)""")
    assert shown == ["—", "Src 3", "—", "—"]


def test_a_tile_tap_opens_the_chooser_and_routes_once(panel_page) -> None:
    """One card, one chooser, one message. Never a route on the way in."""
    _mount(panel_page, _wall(), 800, 500)
    panel_page.locator('.matrix-tile[data-dest-idx="2"]').click()
    assert panel_page.locator(".matrix-chooser").count() == 1
    assert _sent(panel_page) == [], "opening a chooser must not route anything"
    panel_page.locator('.matrix-chooser-source[data-source-idx="1"]').click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": 2, "output": 3},
    ]
    assert panel_page.locator(".matrix-chooser").count() == 0


def test_a_chooser_is_headed_with_the_name_its_tile_is_showing(panel_page) -> None:
    """A destination's live name reaches its chooser, not only its card.

    Both halves of the same row: a wall drawn from a device shows the names the
    device reports, so a tile reading `CH 2` opening a sheet headed `Input 2`
    -- the name somebody typed months earlier -- is two names for one output.
    """
    wall = _wall(destinations=2)
    for i, dest in enumerate(wall["matrix_config"]["destinations"], start=1):
        dest["label_key"] = f"device.mx.output.{i}.name"
    _mount(panel_page, wall, 800, 500)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.2.name": "CH 2"})
    panel_page.locator('.matrix-tile[data-dest-idx="1"]').click()
    assert panel_page.locator(".matrix-chooser-title").text_content() == "CH 2"
    # The chooser is a fixed overlay on document.body, so it outlives the element
    # a re-mount replaces -- leaving one open is a stray overlay in the next test.
    panel_page.locator(".matrix-chooser-cancel").click()


def test_a_chooser_falls_back_to_the_authored_name(panel_page) -> None:
    """A destination with no live name, or one that has not arrived yet."""
    _mount(panel_page, _wall(destinations=2), 800, 500)
    panel_page.locator('.matrix-tile[data-dest-idx="1"]').click()
    assert panel_page.locator(".matrix-chooser-title").text_content() == "Dest 2"
    panel_page.locator(".matrix-chooser-cancel").click()


def test_a_tile_chooser_cancels_without_routing(panel_page) -> None:
    _mount(panel_page, _wall(), 800, 500)
    panel_page.locator('.matrix-tile[data-dest-idx="0"]').click()
    panel_page.locator(".matrix-chooser-cancel").click()
    assert panel_page.locator(".matrix-chooser").count() == 0
    assert _sent(panel_page) == []


# ---------------------------------------------------------------------------
# D8 -- the audio source by name, where a badge used to say they disagree
# ---------------------------------------------------------------------------

def _with_audio(style):
    element = _resolved(
        sources=[{"value": 1, "label": "Apple TV"}, {"value": 2, "label": "Room PC"}],
        destinations=[{"value": 1, "label": "Main LCD",
                       "route_key": "device.mx.output.1.input",
                       "audio_route_key": "device.mx.output.1.audio"}],
    )
    element["matrix_style"] = style
    return element


@pytest.mark.parametrize("style", ["crosspoint", "list", "tiles"])
def test_a_diverging_audio_route_is_named_rather_than_flagged(panel_page, style) -> None:
    """'A≠V' said two things disagree and put what they were in a tooltip.

    A touch panel has no pointer to hover a tooltip with, so the one place the
    meaning lived was the one place nobody standing at the panel could reach.
    """
    _mount(panel_page, _with_audio(style), 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.output.1.input": 1,
        "device.mx.output.1.audio": 2,
    })
    node = panel_page.locator(".matrix-audio-source")
    assert node.count() == 1
    assert node.is_visible()
    assert node.text_content() == "Audio: Room PC"


@pytest.mark.parametrize("style", ["crosspoint", "list", "tiles"])
def test_an_agreeing_audio_route_says_nothing(panel_page, style) -> None:
    _mount(panel_page, _with_audio(style), 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.output.1.input": 1,
        "device.mx.output.1.audio": 1,
    })
    assert not panel_page.locator(".matrix-audio-source").is_visible()


def test_a_diverging_audio_route_uses_the_live_source_name(panel_page) -> None:
    """The name a device reports, not the one that was typed at build time."""
    element = _with_audio("tiles")
    element["matrix_config"]["sources"][1]["label_key"] = "device.mx.input.2.name"
    _mount(panel_page, element, 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {
        "device.mx.output.1.input": 1,
        "device.mx.output.1.audio": 2,
        "device.mx.input.2.name": "Lectern PC",
    })
    assert panel_page.locator(".matrix-audio-source").text_content() == "Audio: Lectern PC"


# ---------------------------------------------------------------------------
# D8a -- routed to nothing and routed to something unlisted are different facts
# ---------------------------------------------------------------------------

def _subset(style):
    """A patched frame: the device is on port 3 and port 3 is not in the list."""
    element = _resolved(
        sources=[{"value": 1, "label": "Apple TV"}, {"value": 2, "label": "Room PC"}],
        destinations=[{"value": 1, "label": "Main LCD",
                       "route_key": "device.mx.output.1.input"}],
    )
    element["matrix_style"] = style
    return element


def test_a_list_row_routed_to_nothing_does_not_claim_the_first_source(panel_page) -> None:
    """Older than this plan and just as wrong: a <select> shows its first option
    when nothing has selected one, so four rows read 'Main LCD -> Apple TV' in
    one screenshot with nothing routed to any of them."""
    _mount(panel_page, _subset("list"), 500, 300)
    select = panel_page.locator(".matrix-list-select")
    assert select.input_value() == ""
    assert select.evaluate("s => s.selectedOptions[0].textContent") == "—"


def test_a_list_row_routed_to_an_unlisted_source_says_what_it_is(panel_page) -> None:
    """A subset is the normal way to author a matrix now, so 'the device is on a
    port you left out' stopped being an edge case the day format 0.10.0 landed."""
    _mount(panel_page, _subset("list"), 500, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.1.input": 3})
    shown = panel_page.locator(".matrix-list-select").evaluate(
        "s => s.selectedOptions[0].textContent")
    assert shown == "3 (not listed)"


def test_an_unlisted_source_cannot_be_re_sent_from_the_dropdown(panel_page) -> None:
    """It is what the device said, not a source this matrix can route to."""
    _mount(panel_page, _subset("list"), 500, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.1.input": 3})
    disabled = panel_page.locator(".matrix-list-select").evaluate(
        "s => s.selectedOptions[0].disabled")
    assert disabled is True
    assert _sent(panel_page) == []


def test_a_crosspoint_row_routed_to_an_unlisted_source_says_so(panel_page) -> None:
    """No dot lit is what a row routed to NOTHING looks like too."""
    _mount(panel_page, _subset("crosspoint"), 600, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.1.input": 3})
    node = panel_page.locator(".matrix-unlisted")
    assert node.is_visible()
    assert node.text_content() == "3"
    assert panel_page.locator(".matrix-crosspoint.active").count() == 0


def test_a_crosspoint_row_routed_to_nothing_stays_quiet(panel_page) -> None:
    """The other half of the same check: silence has to still mean something."""
    _mount(panel_page, _subset("crosspoint"), 600, 300)
    panel_page.evaluate("(s) => window.__setState(s)", {})
    assert not panel_page.locator(".matrix-unlisted").is_visible()


def test_a_tile_routed_to_an_unlisted_source_shows_the_raw_value(panel_page) -> None:
    _mount(panel_page, _subset("tiles"), 500, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.mx.output.1.input": "HDMI_9"})
    tile = panel_page.locator(".matrix-tile-source")
    assert tile.text_content() == "HDMI_9"
    assert "unlisted" in tile.get_attribute("class")


# ---------------------------------------------------------------------------
# D8a, the second case -- a device routed by one vocabulary, reporting another
# ---------------------------------------------------------------------------

def _two_vocabularies(style="crosspoint"):
    """at_atdm_0604a's shape: send "0", read back "Mic"."""
    element = _resolved(
        sources=[
            {"value": "0", "label": "Mic", "report_value": "Mic"},
            {"value": "1", "label": "Line", "report_value": "Line"},
        ],
        destinations=[{"value": 1, "label": "Channel 1",
                       "route_key": "device.dsp.input.1.source"}],
    )
    element["matrix_style"] = style
    return element


def test_a_source_lights_on_what_the_device_REPORTS(panel_page) -> None:
    """The crosspoint that could never light.

    Its route value is "0", and 0 is what every renderer reads as "nothing is
    routed" -- so the source that works is the one that looks dead. Both
    vocabularies are declared on the driver; what was missing was somewhere to
    put the second one.
    """
    _mount(panel_page, _two_vocabularies(), 600, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.dsp.input.1.source": "Mic"})
    lit = panel_page.evaluate("""() => Array.from(
        document.querySelectorAll('.matrix-crosspoint.active')
    ).map(c => c.dataset.input)""")
    assert lit == ["0"]


def test_a_source_still_SENDS_what_the_route_command_takes(panel_page) -> None:
    """Matching on one value must not change what goes on the wire."""
    _mount(panel_page, _two_vocabularies(), 600, 300)
    _crosspoint(panel_page, "0", 1).click()
    assert _sent(panel_page) == [
        {"type": "ui.route", "element_id": "mx1", "input": "0", "output": 1},
    ]


def test_a_reported_vocabulary_reaches_the_tile_and_the_dropdown(panel_page) -> None:
    _mount(panel_page, _two_vocabularies("tiles"), 500, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.dsp.input.1.source": "Line"})
    assert panel_page.locator(".matrix-tile-source").text_content() == "Line"
    _mount(panel_page, _two_vocabularies("list"), 500, 300)
    panel_page.evaluate("(s) => window.__setState(s)",
                        {"device.dsp.input.1.source": "Line"})
    assert panel_page.locator(".matrix-list-select").input_value() == "1"


# ---------------------------------------------------------------------------
# D9 / F10 -- the lock
# ---------------------------------------------------------------------------

def _locked(style, backed=True):
    element = _resolved(
        sources=[{"value": 1, "label": "Apple TV"}, {"value": 2, "label": "Room PC"}],
        destinations=[
            {"value": 1, "label": "Main LCD", "route_key": "device.mx.output.1.input",
             **({"lock_key": "var.mx_lock_1"} if backed else {})},
        ],
        show_lock=True,
    )
    element["matrix_style"] = style
    return element


def test_the_lock_is_off_unless_it_is_asked_for(panel_page) -> None:
    """It used to be on unless turned off, and cost every matrix ever authored a
    whole column for a button that sent nothing."""
    _mount(panel_page, _subset("crosspoint"), 600, 300)
    assert panel_page.locator(".matrix-lock-btn").count() == 0


@pytest.mark.parametrize("style", ["crosspoint", "list", "tiles"])
def test_pressing_a_backed_lock_writes_its_variable(panel_page, style) -> None:
    """Which is what makes it a lock: every panel reads that key."""
    _mount(panel_page, _locked(style), 700, 400)
    panel_page.locator(".matrix-lock-btn").click()
    assert _sent(panel_page) == [
        {"type": "state.set", "key": "var.mx_lock_1", "value": True},
    ]


def test_a_backed_lock_reflects_the_variable_rather_than_the_press(panel_page) -> None:
    """No optimistic flip: the state is what every other panel sees, so the one
    that pressed it should be showing the same thing they are."""
    _mount(panel_page, _locked("list"), 600, 300)
    panel_page.locator(".matrix-lock-btn").click()
    assert "locked" not in (panel_page.locator(".matrix-lock-btn").get_attribute("class"))
    panel_page.evaluate("(s) => window.__setState(s)", {"var.mx_lock_1": True})
    assert "locked" in (panel_page.locator(".matrix-lock-btn").get_attribute("class"))


def test_a_locked_destination_refuses_a_route(panel_page) -> None:
    _mount(panel_page, _locked("crosspoint"), 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {"var.mx_lock_1": True})
    _crosspoint(panel_page, 2, 1).click()
    assert _sent(panel_page) == []


def test_a_locked_tile_does_not_open_the_chooser(panel_page) -> None:
    _mount(panel_page, _locked("tiles"), 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {"var.mx_lock_1": True})
    panel_page.locator('.matrix-tile[data-dest-idx="0"]').click()
    assert panel_page.locator(".matrix-chooser").count() == 0


def test_a_backed_lock_survives_a_re_render(panel_page) -> None:
    """F10's fourth clause. A lock that is forgotten when the page redraws is
    not a lock, and the page redraws on every project save."""
    element = _locked("crosspoint")
    _mount(panel_page, element, 700, 400)
    panel_page.evaluate("(s) => window.__setState(s)", {"var.mx_lock_1": True})
    engaged = panel_page.evaluate("""([e, w, h]) => {
        const app = window.__openavcPanel;
        const kept = app.state;
        window.__mount(e, w, h);
        app.state = kept;
        app.evaluateAllBindings(null);
        return document.querySelector('.matrix-lock-btn').classList.contains('locked');
    }""", [element, 700, 400])
    assert engaged is True


def test_an_unbacked_lock_is_still_a_button_and_still_local(panel_page) -> None:
    """Losing the button would be worse than keeping the old behaviour; the page
    review is what says the lock is going nowhere."""
    _mount(panel_page, _locked("list", backed=False), 600, 300)
    panel_page.locator(".matrix-lock-btn").click()
    assert _sent(panel_page) == []
    assert "locked" in (panel_page.locator(".matrix-lock-btn").get_attribute("class"))
