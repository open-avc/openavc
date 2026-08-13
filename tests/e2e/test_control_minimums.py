"""The recorded per-control minimums must still be true, and still be tight.

``openavc/ui/control_minimums.py`` says how small each control can be drawn.
Those numbers were measured rather than derived, which means nothing about the
source code keeps them honest -- edit a padding value in ``panel-elements.css``
and they are quietly wrong, with no test anywhere going red. That is exactly
how the table this replaced came to contain numbers that were never true.

So this renders every control at its recorded minimum, in a real browser, with
the real ``panel.js`` and the real stylesheets, and checks two things:

  fits    at the recorded size, every fixed internal still holds its size and
          stays inside the box. If this fails the minimum is too small and the
          control is being drawn broken.
  tight   at one pixel less, on each axis, something fails. If this fails the
          minimum is too big -- which sounds harmless and is not: an inflated
          floor makes the Builder and the AI reject layouts that would have
          rendered perfectly well, and there is no signal anywhere that the
          number drifted.

A real browser is required. jsdom cannot answer this at all: it has no layout
engine, so every box measures zero and every assertion below passes vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openavc.ui.control_minimums import TYPES_WITH_MINIMUMS, minimum_box

# Skip-gate only: the browser itself comes from pytest-playwright's session
# fixtures, never from a second sync_playwright() of our own.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

# A representative element per type. Deliberately minimal: this measures the
# control's own fixed parts, not how long a caption someone typed.
SPECIMENS: dict[str, dict] = {
    "status_led": {"label": "On"},
    "fader": {"label": "Ch", "min": -80, "max": 10},
    "slider": {"label": "Vol", "min": 0, "max": 100},
    "keypad": {"label": "PIN"},
    # input_count / output_count, not inputs / outputs. The keys renderMatrix
    # reads: the pair that used to be here were ignored, so the "2x2" specimen
    # was silently a 4x4 and the floor it recorded belonged to a grid nobody
    # meant to measure.
    "matrix": {"label": "Route", "matrix_config": {"input_count": 4, "output_count": 4}},
    "list": {"label": "Presets", "options": [{"value": "a", "label": "A"}]},
    "select": {"label": "Src", "options": [{"value": "a", "label": "A"}]},
    "text_input": {"label": "Name"},
    "level_meter": {"label": "L", "min": -60, "max": 0},
}

# Per-type assertions. Each names the parts that must keep their size.
#
# There is no generic version of this and two were tried. Asking the root
# whether it overflowed cannot work: the flex items carry `min-height: 0`
# (panel-elements.css:676, :849), so a starved control COLLAPSES rather than
# overflowing and reports scrollHeight == clientHeight while being unusable.
# Checking every child against its own parent cannot work either: a zero-width
# slider fill at value 0, an <option> with no box, and SVG internals are all
# legitimately zero, and a 44px thumb on a 6px track legitimately overhangs.
ASSERT_JS = r"""
([type, extra, w, h]) => {
  const TOL = 0.01;
  const app = window.__openavcPanel;
  const box = document.getElementById('box');
  box.innerHTML = '';
  box.style.width = w + 'px';
  box.style.height = h + 'px';
  let node;
  try { node = app.renderElement(Object.assign({ id: 'probe', type }, extra)); }
  catch (e) { return { ok: false, reason: 'render threw: ' + e }; }
  if (!node) return { ok: false, reason: 'renderElement returned null' };
  node.classList.add('panel-element');
  node.style.position = 'absolute';
  node.style.left = '0px'; node.style.top = '0px';
  node.style.width = '100%'; node.style.height = '100%';
  box.appendChild(node);
  void node.offsetWidth;

  const R = (el) => el.getBoundingClientRect();
  const q = (s) => node.querySelector(s);
  const qa = (s) => Array.from(node.querySelectorAll(s));
  const outer = R(node);
  const inside = (el) => {
    const r = R(el);
    return r.left >= outer.left - TOL && r.top >= outer.top - TOL &&
           r.right <= outer.right + TOL && r.bottom <= outer.bottom + TOL;
  };
  const bad = (reason) => ({ ok: false, reason });

  switch (type) {
    case 'status_led': {
      const dot = q('.led-dot');
      if (!dot) return bad('no dot');
      const r = R(dot);
      if (r.width < 20 - TOL || r.height < 20 - TOL)
        return bad(`dot shrank to ${r.width.toFixed(1)}x${r.height.toFixed(1)}`);
      if (!inside(dot)) return bad('dot pushed outside the box');
      // The caption having NO room is the original defect: the dot is
      // flex-shrink:0, so it survives and the text is squeezed to nothing
      // while the control still looks structurally fine.
      const lab = q('.led-label');
      if (lab && (lab.textContent || '').trim() && R(lab).width < 1)
        return bad('caption crushed to zero width');
      break;
    }
    case 'fader': {
      const handle = q('.fader-handle'), wrap = q('.fader-track-wrap'), scale = q('.fader-scale');
      if (!handle || !wrap) return bad('missing handle/track');
      const hr = R(handle), wr = R(wrap);
      if (hr.width < 44 - TOL || hr.height < 44 - TOL)
        return bad(`handle shrank to ${hr.width.toFixed(1)}x${hr.height.toFixed(1)}`);
      if (wr.height < hr.height - TOL) return bad('track shorter than the handle');
      if (scale) {
        const sr = R(scale);
        if (sr.width < 28 - TOL) return bad(`scale column shrank to ${sr.width.toFixed(1)}`);
        if (sr.height < 1) return bad('scale column crushed to zero height');
      }
      if (!inside(handle)) return bad('handle outside the box');
      break;
    }
    case 'slider': {
      const input = q('input[type="range"]');
      if (!input) return bad('no range input');
      // The thumb is a ::-webkit-slider-thumb pseudo-element: there is no node
      // to measure, so it is read from the custom property panel.js sets.
      const thumb = parseFloat(getComputedStyle(input).getPropertyValue('--thumb-size')) * 14;
      const wrap = q('.slider-track-wrapper') || input;
      const wr = R(wrap);
      if (wr.width < thumb - TOL) return bad('track narrower than the thumb');
      if (wr.height < thumb - TOL) return bad('track shorter than the thumb');
      if (!inside(input)) return bad('range input outside the box');
      break;
    }
    case 'keypad': {
      const keys = qa('.keypad-key');
      if (!keys.length) return bad('no keys');
      for (const k of keys) {
        const r = R(k);
        if (r.height < 20 - TOL) return bad(`keys crushed to ${r.height.toFixed(1)}px tall`);
        if (r.width < 20 - TOL) return bad(`keys crushed to ${r.width.toFixed(1)}px wide`);
        if (!inside(k)) return bad('a key sits outside the box');
      }
      break;
    }
    case 'matrix': {
      // VISIBLE, not merely laid out. .matrix-scroll clips, so a cell can sit
      // inside the element's own rect and still be behind a scrollbar -- which
      // is how a 16x16 in a 278px box counted as "holding" under the constant
      // floor this replaced, while showing a corner of itself.
      const scroll = q('.matrix-scroll');
      // The lock and mute buttons are .matrix-cell too, and they are NOT
      // crosspoints: they keep the touch floor whatever cell_size authors.
      const cells = qa('.matrix-cell:not(.matrix-toggle)');
      const toggles = qa('.matrix-cell.matrix-toggle');
      const rows = qa('.matrix-list-row');
      const cellFloor = extra.style && extra.style.cell_size
        ? extra.style.cell_size * 14 : 44;
      if (cells.length) {
        for (const c of cells) {
          const r = R(c);
          if (r.width < cellFloor - TOL || r.height < cellFloor - TOL)
            return bad(`cell shrank to ${r.width.toFixed(1)}x${r.height.toFixed(1)}`);
        }
        for (const t of toggles) {
          const r = R(t);
          if (r.width < 44 - TOL)
            return bad(`a lock/mute button crushed to ${r.width.toFixed(1)}px wide`);
        }
        // The column numbers are the only thing saying which source a column
        // is. .matrix-header sets overflow:hidden, which lets a grid track
        // compress below its own line box, so this row halved the digits
        // silently before it had a declared minimum.
        for (const h of qa('.matrix-input-header')) {
          if (R(h).height < 24.9)
            return bad(`the column numbers are cut to ${R(h).height.toFixed(1)}px tall`);
        }
      } else if (rows.length) {
        for (const r0 of rows) {
          if (R(r0).height < 28 - TOL)
            return bad(`a list row crushed to ${R(r0).height.toFixed(1)}px`);
          const sel = r0.querySelector('.matrix-list-select');
          if (!sel) return bad('a row lost its dropdown');
          if (R(sel).width < 44 - TOL)
            return bad(`a dropdown crushed to ${R(sel).width.toFixed(1)}px wide`);
        }
      } else return bad('no cells or rows');
      if (scroll.scrollWidth > scroll.clientWidth + 1)
        return bad(`${scroll.scrollWidth - scroll.clientWidth}px of it is off to the side`);
      if (scroll.scrollHeight > scroll.clientHeight + 1)
        return bad(`${scroll.scrollHeight - scroll.clientHeight}px of it is below the fold`);
      const legend = q('.matrix-legend');
      if (legend && !inside(legend)) return bad('the source legend is clipped');
      break;
    }
    case 'list': {
      const items = qa('.list-item');
      if (!items.length) return bad('no rows');
      for (const it of items) {
        if (R(it).height < 44 - TOL) return bad(`row shrank to ${R(it).height.toFixed(1)}`);
        if (!inside(it)) return bad('a row sits outside the box');
      }
      break;
    }
    case 'select':
    case 'text_input': {
      const ctl = q('select, input');
      if (!ctl) return bad('no control');
      const r = R(ctl);
      if (r.height < 20 - TOL) return bad(`control ${r.height.toFixed(1)}px tall`);
      if (r.width < 20 - TOL) return bad(`control ${r.width.toFixed(1)}px wide`);
      if (!inside(ctl)) return bad('control outside the box');
      break;
    }
    case 'level_meter': {
      const bar = q('.meter-bar');
      if (!bar) return bad('no meter bar');
      const br = R(bar);
      if (br.width < 1 || br.height < 1)
        return bad(`meter bar collapsed to ${br.width.toFixed(1)}x${br.height.toFixed(1)}`);
      for (const s of qa('.meter-segment')) {
        if (R(s).height < 2 - TOL)
          return bad(`segments crushed to ${R(s).height.toFixed(2)}px`);
      }
      if (!inside(bar)) return bad('meter bar outside the box');
      break;
    }
    default:
      return bad('no assertion defined for ' + type);
  }
  return { ok: true };
}
"""


def _page_html() -> str:
    """The panel's own CSS and JS, in a page with one absolutely-sized box.

    The @import in panel.css is resolved by hand so the two stylesheets can be
    inlined, matching what tests/fixtures/panel_harness.cjs does for jsdom.
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
  #box {{ position: absolute; left: 0; top: 0; }}
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
    """A page on the plugin's browser -- never a second Playwright of our own.

    pytest-playwright keeps ONE Playwright open for the whole session, started
    the first time any test asks for `page` or `browser`. Calling
    `sync_playwright()` here as well raises "Sync API inside the asyncio loop"
    for every test in this file, because that first one is still running. It
    only looked fine in isolation: alone, nothing had started the plugin's
    instance yet, so the whole file passed and `pytest tests/e2e` errored.
    """
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.set_content(_page_html(), wait_until="load")
    assert page.evaluate("() => !!window.__openavcPanel"), (
        "panel.js did not initialise -- the harness page is wrong, not the minimums"
    )
    yield page
    context.close()


def _holds(
    page, type_: str, w: float, h: float, extra: dict | None = None,
) -> tuple[bool, str]:
    result = page.evaluate(ASSERT_JS, [type_, extra or SPECIMENS[type_], w, h])
    return result.get("ok", False), result.get("reason", "")


def _smallest_holding(page, type_: str, w: float, h: float, *, limit: int = 40) -> str:
    """The nearest size at or above (w, h) that renders whole, as a sentence.

    A floor that is too small is reported by the machine that noticed, and the
    machine that noticed is usually not the one that can be asked interactively
    -- these numbers differ per font stack (see TIGHTNESS_SLACK_PX), so the CI
    log is frequently the only place the real answer exists. Saying "needs 237"
    instead of "is too small" is the difference between a one-line edit and
    another round trip.
    """
    for grow in range(1, limit + 1):
        if _holds(page, type_, w, h + grow)[0]:
            return f"{w:.0f}x{h + grow:.0f} holds here (+{grow}px tall)"
    for grow in range(1, limit + 1):
        if _holds(page, type_, w + grow, h)[0]:
            return f"{w + grow:.0f}x{h:.0f} holds here (+{grow}px wide)"
    return f"nothing within +{limit}px on either axis holds here"


@pytest.mark.parametrize("type_", TYPES_WITH_MINIMUMS)
def test_control_is_whole_at_its_recorded_minimum(panel_page, type_: str) -> None:
    box = minimum_box({"type": type_, **SPECIMENS[type_]})
    assert box is not None, f"{type_} is in TYPES_WITH_MINIMUMS but has no minimum_box"
    ok, reason = _holds(panel_page, type_, box.width_px, box.height_px)
    assert ok, (
        f"{type_} is drawn broken at its recorded minimum "
        f"{box.width_px:.0f}x{box.height_px:.0f}: {reason}. "
        f"The recorded minimum is too small -- "
        f"{_smallest_holding(panel_page, type_, box.width_px, box.height_px)}."
    )


#: How much bigger than strictly necessary a recorded floor may be.
#:
#: These floors are text-driven -- a control's height is its label's line box
#: plus fixed furniture -- so the true answer moves with the font stack, and the
#: font stack moves with the machine. The same specimens measured in Chromium in
#: three places give three answers: taking the fader, 100px on the Windows dev
#: box where the table was recorded, 99 on the GitHub ubuntu runner, 102 in the
#: Playwright container. A test demanding the floor be exact to the pixel is
#: therefore asserting which machine ran it, and it duly failed CI on seven
#: controls at once the first time it was ever allowed to execute there.
#:
#: Four is the widest disagreement actually measured across those three, not a
#: round number: the matrix's width spans 274..278 and the fader's height 99..102.
#: The check still does its real job, which is catching a floor inflated far
#: enough to make the Builder and the AI reject layouts that would render fine.
TIGHTNESS_SLACK_PX = 4


@pytest.mark.parametrize("type_", TYPES_WITH_MINIMUMS)
def test_recorded_minimum_is_tight_not_merely_safe(panel_page, type_: str) -> None:
    """Comfortably under the floor, on either axis, must fail.

    An inflated floor is not a harmless safety margin: it makes the Builder and
    the AI reject layouts that render perfectly well, and nothing signals it.
    See TIGHTNESS_SLACK_PX for why this is not the exact one-pixel check it
    reads like it should be.
    """
    box = minimum_box({"type": type_, **SPECIMENS[type_]})
    assert box is not None

    narrow = box.width_px - 1 - TIGHTNESS_SLACK_PX
    short = box.height_px - 1 - TIGHTNESS_SLACK_PX
    narrow_ok, _ = _holds(panel_page, type_, narrow, box.height_px)
    short_ok, _ = _holds(panel_page, type_, box.width_px, short)
    assert not narrow_ok, (
        f"{type_} still renders whole at {narrow:.0f}px wide, more than "
        f"{TIGHTNESS_SLACK_PX}px under its recorded minimum width "
        f"{box.width_px:.0f}, so that floor is too big -- re-measure it."
    )
    assert not short_ok, (
        f"{type_} still renders whole at {short:.0f}px tall, more than "
        f"{TIGHTNESS_SLACK_PX}px under its recorded minimum height "
        f"{box.height_px:.0f}, so that floor is too big -- re-measure it."
    )


def test_an_unlabelled_status_led_is_just_the_dot(panel_page) -> None:
    """The caption changes the floor, so both cases have to be pinned.

    A labelled LED needs the dot plus a gap plus a sliver of text; an
    unlabelled one needs only the dot. Recording one number for both would
    either starve the labelled case or inflate the unlabelled one -- and the
    unlabelled case is the one a dense status column is built from.
    """
    bare = minimum_box({"type": "status_led"})
    assert bare is not None
    assert (bare.width_px, bare.height_px) == (20, 20)

    SPECIMENS["status_led"] = {}
    try:
        ok, reason = _holds(panel_page, "status_led", bare.width_px, bare.height_px)
        assert ok, f"an unlabelled LED is broken at 20x20: {reason}"
        narrow_ok, _ = _holds(panel_page, "status_led", bare.width_px - 1, bare.height_px)
        assert not narrow_ok, "an unlabelled LED still fits at 19px -- 20 is too big"
    finally:
        SPECIMENS["status_led"] = {"label": "On"}


#: Matrices whose floor the model has to predict, not just the default one.
#:
#: The counts are the point: an 8x8 and a 16x16 are the sizes that were being
#: silently accepted in a box that showed a third of them, and the off-diagonal
#: pairs are what prove the two axes are independent rather than one number
#: fitted to the square cases. The list style is here because it is the case a
#: single recorded constant got most wrong -- sixteen sources are sixteen
#: options in one dropdown, not sixteen columns, so its width does not move at
#: all.
MATRIX_GRIDS: list[tuple[str, dict]] = [
    ("2x2", {"matrix_config": {"input_count": 2, "output_count": 2}}),
    ("8x8", {"matrix_config": {"input_count": 8, "output_count": 8}}),
    ("12x12", {"matrix_config": {"input_count": 12, "output_count": 12}}),
    ("16x4", {"matrix_config": {"input_count": 16, "output_count": 4}}),
    ("4x16", {"matrix_config": {"input_count": 4, "output_count": 16}}),
    ("8x8 no lock", {"matrix_config": {
        "input_count": 8, "output_count": 8, "show_lock": False}}),
    ("4x4 with presets", {"matrix_config": {
        "input_count": 4, "output_count": 4,
        "presets": [{"name": "All to 1", "macro": "m"}]}}),
    ("8x8 cell 60", {"style": {"cell_size": 60 / 14},
                     "matrix_config": {"input_count": 8, "output_count": 8}}),
    ("list 8 out", {"matrix_style": "list",
                    "matrix_config": {"input_count": 4, "output_count": 8}}),
    ("list 16 in", {"matrix_style": "list",
                    "matrix_config": {"input_count": 16, "output_count": 4}}),
    ("list 16 out", {"matrix_style": "list",
                     "matrix_config": {"input_count": 4, "output_count": 16}}),
]


@pytest.mark.parametrize(("name", "extra"), MATRIX_GRIDS, ids=[n for n, _ in MATRIX_GRIDS])
def test_the_matrix_floor_predicts_every_grid_not_just_the_default(
    panel_page, name: str, extra: dict,
) -> None:
    """The floor is a line now, and a line is only as good as its other points.

    A constant that happened to be right for a 4x4 is what this replaced, and
    nothing could tell -- the recorded number was checked against one grid and
    asserted to hold for all of them. So each of these is measured both ways:
    it must draw whole at the computed floor, and must not still draw whole
    comfortably under it, or the model is inventing room the control does not
    need and the Builder will reject layouts that would have rendered.
    """
    element = {"type": "matrix", "label": "Route", **extra}
    box = minimum_box(element)
    assert box is not None
    ok, reason = _holds(panel_page, "matrix", box.width_px, box.height_px, element)
    assert ok, (
        f"a {name} matrix is drawn broken at its computed floor "
        f"{box.width_px:.0f}x{box.height_px:.0f}: {reason}"
    )
    narrow = box.width_px - 1 - TIGHTNESS_SLACK_PX
    short = box.height_px - 1 - TIGHTNESS_SLACK_PX
    narrow_ok, _ = _holds(panel_page, "matrix", narrow, box.height_px, element)
    short_ok, _ = _holds(panel_page, "matrix", box.width_px, short, element)
    assert not narrow_ok, (
        f"a {name} matrix still draws whole at {narrow:.0f}px wide, more than "
        f"{TIGHTNESS_SLACK_PX}px under its computed {box.width_px:.0f} -- the "
        f"model overstates the width"
    )
    assert not short_ok, (
        f"a {name} matrix still draws whole at {short:.0f}px tall, more than "
        f"{TIGHTNESS_SLACK_PX}px under its computed {box.height_px:.0f} -- the "
        f"model overstates the height"
    )


def test_a_matrix_at_its_floor_shows_every_crosspoint(panel_page) -> None:
    """The floor has to mean the whole grid, not a scrollable corner of it.

    This is the defect the constant hid, and it is not the same assertion as
    the one above: a cell can keep its 44px, sit inside the element's own
    rectangle, and still be behind .matrix-scroll's scrollbar. Under the old
    reading a 16x16 "held" at 278x236 -- 22 of its 256 crosspoints on screen.
    """
    element = {"type": "matrix", "label": "Route",
               "matrix_config": {"input_count": 16, "output_count": 16}}
    box = minimum_box(element)
    assert box is not None
    seen = panel_page.evaluate(
        """([e, w, h]) => {
            const app = window.__openavcPanel;
            const box = document.getElementById('box');
            box.innerHTML = ''; box.style.width = w+'px'; box.style.height = h+'px';
            const node = app.renderElement(e);
            node.classList.add('panel-element');
            node.style.position = 'absolute';
            node.style.left = '0px'; node.style.top = '0px';
            node.style.width = '100%'; node.style.height = '100%';
            box.appendChild(node); void node.offsetWidth;
            const s = node.querySelector('.matrix-scroll').getBoundingClientRect();
            return Array.from(node.querySelectorAll('.matrix-crosspoint')).filter(d => {
                const r = d.getBoundingClientRect();
                return r.left >= s.left - 0.01 && r.right <= s.right + 0.01 &&
                       r.top >= s.top - 0.01 && r.bottom <= s.bottom + 0.01;
            }).length;
        }""",
        [{"id": "probe", **element}, box.width_px, box.height_px],
    )
    assert seen == 256, (
        f"a 16x16 at its own floor {box.width_px:.0f}x{box.height_px:.0f} shows "
        f"{seen} of its 256 crosspoints"
    )


def test_the_overridable_internals_move_the_floor(panel_page) -> None:
    """A bigger thumb or row means a bigger minimum, and the model says by how much.

    slider and list are the two types whose internals are authored rather than
    fixed, so their floors are functions. Measured linear with slope exactly 1
    across thumb/row 44, 66 and 88 -- this pins that the model still predicts
    the real rendering rather than only the default case.
    """
    for type_, key, px in (("slider", "thumb_size", 88.0), ("list", "item_height", 88.0)):
        box = minimum_box({"type": type_, **SPECIMENS[type_], key: px / 14.0})
        assert box is not None
        # The specimen dict is what drives the render, so the override has to
        # go through it as well as through minimum_box.
        SPECIMENS[type_][key] = px / 14.0
        try:
            ok, reason = _holds(panel_page, type_, box.width_px, box.height_px)
            assert ok, (
                f"{type_} with {key}={px:.0f}px is broken at its computed minimum "
                f"{box.width_px:.0f}x{box.height_px:.0f}: {reason}. "
                f"{_smallest_holding(panel_page, type_, box.width_px, box.height_px)}"
            )
            # Same per-machine wobble as the recorded floors, so the same slack:
            # the scaled floor is checked for gross overstatement, not to the pixel.
            short = box.height_px - 1 - TIGHTNESS_SLACK_PX
            short_ok, _ = _holds(panel_page, type_, box.width_px, short)
            assert not short_ok, (
                f"{type_} with {key}={px:.0f}px still fits at {short:.0f}px tall, more "
                f"than {TIGHTNESS_SLACK_PX}px under its computed floor "
                f"{box.height_px:.0f} -- the scaling model overstates it"
            )
        finally:
            del SPECIMENS[type_][key]
