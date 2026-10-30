"""A slider or fader being dragged sends at its rate, not when it stops.

The Builder offers "Send: While dragging" with a "Rate (ms)" field on both
controls: at most one command per that many milliseconds while the control
moves. These tests drag the real controls in Chromium on a controlled clock,
with moves arriving every 16 ms (one frame, about what a touch screen
delivers), and read what the panel put on the socket and when.

What they pin:

- the first movement sends at once, and a continuous drag keeps sending, one
  command per rate interval, each carrying where the control was at that moment;
- the value the control stopped on always arrives, and it arrives last: the
  release sends it, and nothing stale is sent after it;
- a rate of 0 sends every move, and "On release only" sends exactly one command;
- a tap, a key press, or a double tap is one command per action, never a copy
  of the one just sent and never a value the finger was not on.

A browser is required for the slider: its 'input' and 'change' events come from
the native range control's own drag and keyboard handling, which jsdom does not
implement at all. The clock is Playwright's, so the spacing is exact and the run
takes no wall time.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Skip-gate only: the browser itself comes from pytest-playwright's session
# fixtures, never from a second sync_playwright() of our own (see the note in
# test_control_minimums.py -- calling it here breaks every test in the file).
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800
FRAME_MS = 16
LEVEL_KEY = "device.acme_amp.level"
CONNECTED_KEY = "device.acme_amp.connected"


def _page_html() -> str:
    """The panel's own CSS and JS, with every frame it sends recorded and timed.

    The socket is a stand-in whose send() records the frame, the page clock,
    and what the control showed at that instant, so what is captured is what
    the panel would have put on the wire and where the control was when it did.
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
  /* Pinned over the panel's own start-up blocks, which would otherwise push
     the control below the viewport where no pointer can reach it. */
  #stage {{ position: fixed; left: 0; top: 0; z-index: 10000; width: {REF_W}px; height: {REF_H}px; }}
  #box {{ position: absolute; left: 40px; top: 40px; }}
</style></head>
<body>
  <div id="panel-root"></div><div id="connection-status"></div>
  <div id="offline-overlay"></div><div id="loading-state"></div>
  <div id="stage"><div id="box"></div></div>
<script>
  window.fetch = async () => ({{ ok: false, json: async () => ({{}}) }});
  window.__wire = [];
  class FakeWS {{
    constructor() {{ this.readyState = 1; }}
    send(data) {{
      window.__wire.push({{ t: performance.now(), msg: JSON.parse(data), shown: window.__shown() }});
    }}
    close() {{}}
  }}
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
</script>
<script>{panel_js}</script>
<script>
  window.__mount = (element, w, h, state) => {{
    const app = window.__openavcPanel;
    app.editMode = false;
    app.ws = new FakeWS();
    window.__wire = [];
    const box = document.getElementById('box');
    box.innerHTML = '';
    box.style.width = w + 'px';
    box.style.height = h + 'px';
    app.bindings = [];
    app.elementMap = {{}};
    app.state = Object.assign({{}}, state || {{}});
    const node = app.renderElement(element);
    node.classList.add('panel-element');
    node.style.position = 'absolute';
    node.style.left = '0px'; node.style.top = '0px';
    node.style.width = '100%'; node.style.height = '100%';
    box.appendChild(node);
    app.elementMap = {{ [element.id]: {{ el: node, def: element }} }};
    void node.offsetWidth;
    app.evaluateAllBindings(null);
  }};
  // The value each control currently shows, read the way the operator sees it.
  window.__shown = () => {{
    const handle = document.querySelector('#box .fader-handle');
    if (handle) return Number(handle.getAttribute('aria-valuenow'));
    return Number(document.querySelector('#box input[type=range]').value);
  }};
  window.__sends = () => window.__wire
    .filter((f) => f.msg.type === 'ui.change' && f.msg.element_id === 'ctl')
    .map((f) => ({{ t: f.t, value: f.msg.value, shown: f.shown }}));
</script>
</body></html>
"""


CLOCK_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _open(context):
    """The harness page on a paused fake clock: time moves only by run_for()."""
    page = context.new_page()
    page.clock.install(time=CLOCK_START)
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong, not the control"
    )
    # Installed, the clock still runs at wall speed until it is paused.
    page.clock.pause_at(CLOCK_START + timedelta(seconds=30))
    # The spacing assertions below read performance.now(). Prove the fake clock
    # owns it, or every number they compare is wall time.
    before = page.evaluate("() => performance.now()")
    page.clock.run_for(1000)
    after = page.evaluate("() => performance.now()")
    assert after - before == 1000, f"the page clock is not under test control ({after - before} ms)"
    return page


@pytest.fixture
def panel_page(browser):
    """A fresh page per test on a fake clock, so no test inherits another's timers."""
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    yield _open(context)
    context.close()


def _fader(**extra) -> dict:
    return {
        "id": "ctl", "type": "fader", "min": 0, "max": 100, "step": 1,
        "bindings": {"show": {"value": {"key": LEVEL_KEY}}}, **extra,
    }


def _slider(**extra) -> dict:
    return {
        "id": "ctl", "type": "slider", "min": 0, "max": 100, "step": 1,
        "bindings": {"show": {"value": {"key": LEVEL_KEY}}}, **extra,
    }


def _mount(page, element: dict, level=50) -> dict:
    """Mount one control and return the box it drags in, in page pixels.

    The default level sits mid-travel, away from where a drag starts, so the
    press itself moves the control (a range input fires nothing for a press
    that lands on its own value).
    """
    w, h = (120, 340) if element["type"] == "fader" else (420, 80)
    page.evaluate(
        "([el, w, h, state]) => window.__mount(el, w, h, state)",
        [element, w, h, {CONNECTED_KEY: True, LEVEL_KEY: level}],
    )
    sel = ".fader-track-wrap" if element["type"] == "fader" else "input[type=range]"
    box = page.locator(f"#box {sel}").bounding_box()
    assert box and box["width"] > 0 and box["height"] > 0, f"no {sel} on screen"
    return box


def _drag_points(element: dict, box: dict, moves: int) -> list[tuple[float, float]]:
    """Where the pointer goes, from 10% to 90% of the travel in equal steps."""
    points = []
    for i in range(moves + 1):
        frac = 0.1 + 0.8 * i / moves
        if element["type"] == "fader":
            # A vertical fader: up is louder.
            points.append((box["x"] + box["width"] / 2, box["y"] + box["height"] * (1 - frac)))
        else:
            points.append((box["x"] + box["width"] * frac, box["y"] + box["height"] / 2))
    return points


def _sends(page) -> list[dict]:
    return page.evaluate("() => window.__sends()")


def _shown(page) -> float:
    return page.evaluate("() => window.__shown()")


def _now(page) -> float:
    return page.evaluate("() => performance.now()")


def _mouse_drag(page, element: dict, box: dict, moves: int) -> dict:
    """Press, move once per frame, and stop with the pointer still down.

    Returns the press time and what the control showed after every move.
    """
    points = _drag_points(element, box, moves)
    page.mouse.move(*points[0])
    t0 = _now(page)
    page.mouse.down()
    shown = [(t0, _shown(page))]
    for x, y in points[1:]:
        page.clock.run_for(FRAME_MS)
        page.mouse.move(x, y)
        shown.append((_now(page), _shown(page)))
    return {"t0": t0, "shown": shown}


def _assert_streams_at_rate(sends: list[dict], drag: dict, rate: int) -> None:
    """The live half: sends start at once, keep going, are spaced at the rate,
    and each one carries where the control was when it went out."""
    span = drag["shown"][-1][0] - drag["t0"]
    assert sends, "nothing was sent while the control was being dragged"
    assert sends[0]["t"] == drag["t0"], (
        f"the first movement waited {sends[0]['t'] - drag['t0']} ms instead of sending at once"
    )
    expected = math.floor(span / rate)
    assert len(sends) >= expected, (
        f"a {span:.0f} ms drag at {rate} ms sent {len(sends)} commands, expected at least "
        f"{expected}: the control is waiting for the finger to stop"
    )
    gaps = [b["t"] - a["t"] for a, b in zip(sends, sends[1:])]
    assert all(rate <= g <= rate + FRAME_MS for g in gaps), (
        f"sends are not spaced at the {rate} ms rate: gaps {gaps}"
    )
    for s in sends:
        assert s["value"] == s["shown"], (
            f"the send at {s['t']} carried {s['value']} while the control showed "
            f"{s['shown']}: a held value went out stale"
        )


def _assert_release_lands_last(page, before_release: list[dict], rate: int) -> None:
    """The release sends where the control stopped, last, and nothing follows it."""
    final = _shown(page)
    after = _sends(page)
    assert len(after) == len(before_release) + 1, (
        f"the release sent {len(after) - len(before_release)} commands, expected 1"
    )
    assert after[-1]["value"] == final, (
        f"the last command carried {after[-1]['value']}, the control stopped on {final}"
    )
    page.clock.run_for(rate * 4)
    later = _sends(page)
    assert later == after, f"a command followed the release: {later[len(after):]}"


@pytest.mark.parametrize(
    "element, rate",
    [
        (_fader(), 50),
        (_slider(), 100),
        (_fader(send_throttle_ms=200), 200),
        (_slider(send_throttle_ms=30), 30),
    ],
    ids=["fader-default", "slider-default", "fader-200ms", "slider-30ms"],
)
def test_a_drag_sends_at_its_rate_and_lands_where_it_stopped(panel_page, element, rate):
    box = _mount(panel_page, element)
    # 41 frames is 656 ms, which no rate here divides evenly, so the last move
    # is always held when the finger lifts: the release has to send it and
    # nothing may follow.
    drag = _mouse_drag(panel_page, element, box, moves=41)
    during = _sends(panel_page)
    _assert_streams_at_rate(during, drag, rate)
    assert during[-1]["value"] != _shown(panel_page), (
        "the drag was meant to end with a move still held; the release check below proves nothing"
    )
    panel_page.mouse.up()
    _assert_release_lands_last(panel_page, during, rate)


@pytest.mark.parametrize("element", [_fader(), _slider()], ids=["fader", "slider"])
def test_a_drag_that_pauses_sends_the_held_value_without_waiting_for_release(panel_page, element):
    """The finger stops but stays down: the last position goes out at the end of
    the interval, and the release then sends nothing new."""
    rate = 50 if element["type"] == "fader" else 100
    box = _mount(panel_page, element)
    _mouse_drag(panel_page, element, box, moves=9)
    panel_page.clock.run_for(rate * 3)
    held = _sends(panel_page)
    assert held[-1]["value"] == _shown(panel_page), (
        f"with the finger resting, {held[-1]['value']} went out, the control shows {_shown(panel_page)}"
    )
    panel_page.mouse.up()
    panel_page.clock.run_for(rate * 3)
    assert _sends(panel_page) == held, (
        "the release repeated a value the drag had already sent"
    )


@pytest.mark.parametrize("element", [_fader(send_throttle_ms=0), _slider(send_throttle_ms=0)],
                         ids=["fader", "slider"])
def test_a_rate_of_zero_sends_every_move(panel_page, element):
    box = _mount(panel_page, element)
    drag = _mouse_drag(panel_page, element, box, moves=12)
    sends = _sends(panel_page)
    values = [v for _, v in drag["shown"]]
    assert [s["value"] for s in sends] == values, (
        f"rate 0 sent {[s['value'] for s in sends]}, the control moved through {values}"
    )
    panel_page.mouse.up()
    panel_page.clock.run_for(200)
    assert len(_sends(panel_page)) == len(sends), "the release repeated the last move"


@pytest.mark.parametrize("element", [_fader(send_on_release=True), _slider(send_on_release=True)],
                         ids=["fader", "slider"])
def test_on_release_only_sends_one_command(panel_page, element):
    box = _mount(panel_page, element)
    _mouse_drag(panel_page, element, box, moves=20)
    panel_page.clock.run_for(500)
    assert _sends(panel_page) == [], "an On release only control sent while it was held"
    panel_page.mouse.up()
    panel_page.clock.run_for(500)
    sends = _sends(panel_page)
    assert [s["value"] for s in sends] == [_shown(panel_page)], (
        f"the release sent {sends}, expected exactly the value it stopped on"
    )


def test_a_touch_drag_on_a_fader_sends_at_its_rate(browser):
    """The same drag with a finger: touch events, not mouse, on a touch screen."""
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H}, has_touch=True)
    try:
        page = _open(context)
        element = _fader()
        box = _mount(page, element)
        cdp = context.new_cdp_session(page)
        points = _drag_points(element, box, moves=41)

        def touch(kind, point=None):
            pts = [{"x": point[0], "y": point[1]}] if point else []
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": pts})

        t0 = _now(page)
        touch("touchStart", points[0])
        shown = [(t0, _shown(page))]
        for p in points[1:]:
            page.clock.run_for(FRAME_MS)
            touch("touchMove", p)
            shown.append((_now(page), _shown(page)))
        during = _sends(page)
        _assert_streams_at_rate(during, {"t0": t0, "shown": shown}, 50)
        touch("touchEnd")
        _assert_release_lands_last(page, during, 50)
    finally:
        context.close()


def test_each_arrow_key_press_on_a_slider_is_one_command(panel_page):
    _mount(panel_page, _slider(), level=10)
    panel_page.locator("#box input[type=range]").focus()
    for _ in range(4):
        panel_page.keyboard.press("ArrowRight")
        panel_page.clock.run_for(150)
    sends = _sends(panel_page)
    assert [s["value"] for s in sends] == [11, 12, 13, 14], (
        f"four presses from 10 sent {[s['value'] for s in sends]}"
    )


def test_a_held_arrow_key_on_a_fader_streams_at_its_rate(panel_page):
    """Arrow keys on a fader step it; a run of presses faster than the rate is
    sent at the rate, and the last step always arrives."""
    _mount(panel_page, _fader(), level=10)
    panel_page.locator("#box .fader-handle").focus()
    t0 = _now(panel_page)
    for _ in range(15):
        panel_page.keyboard.press("ArrowUp")
        panel_page.clock.run_for(FRAME_MS)
    during = _sends(panel_page)
    assert during and during[0]["t"] == t0 and during[0]["value"] == 11, (
        f"the first press did not send at once: {during[:1]}"
    )
    assert len(during) >= 4, f"15 presses over 240 ms sent only {len(during)} commands"
    gaps = [b["t"] - a["t"] for a, b in zip(during, during[1:])]
    assert all(g >= 50 for g in gaps), f"key presses sent faster than the 50 ms rate: gaps {gaps}"
    panel_page.clock.run_for(200)
    sends = _sends(panel_page)
    assert sends[-1]["value"] == 25 == _shown(panel_page), (
        f"the last press lands: sent {sends[-1]['value']}, shown {_shown(panel_page)}"
    )


def test_a_double_tap_on_a_fader_sends_only_where_the_finger_is(panel_page):
    """Two quick taps on a -80..0 fader's handle must not send any other level.

    A double tap is two presses inside one rate interval of each other, so any
    value sent between them goes out at once; a level the finger was not on
    (0 dB here, the top of the fader) would reach the device.
    """
    element = _fader(min=-80, max=0)
    _mount(panel_page, element, level=-40)
    handle = panel_page.locator("#box .fader-handle").bounding_box()
    x, y = handle["x"] + handle["width"] / 2, handle["y"] + handle["height"] / 2
    panel_page.mouse.move(x, y)
    for _ in range(2):
        panel_page.mouse.down()
        panel_page.clock.run_for(60)
        panel_page.mouse.up()
        panel_page.clock.run_for(60)
    panel_page.clock.run_for(500)
    sends = [s["value"] for s in _sends(panel_page)]
    assert sends, "tapping the handle sent nothing"
    assert all(-50 <= v <= -30 for v in sends), (
        f"a double tap at -40 dB sent {sends}"
    )
    assert _shown(panel_page) == sends[-1], "the handle and the last command disagree"
