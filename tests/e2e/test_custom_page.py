"""A whole page the integrator wrote, in a real browser.

The three things this proves cannot be proved anywhere else. Stacking is
computed by the browser, so "the offline notice draws over the author's page"
is a claim about paint order that jsdom has no layout engine to answer. Focus
crosses into a sandboxed frame through the browser and nothing else -- a
synthetic event cannot put it there, and the whole idle-timer guard keys off
it. And a click inside an opaque-origin frame is the exact gesture the panel
cannot see, which is why a person using a custom page was invisible to the idle
timer in the first place.

Every device and control here is invented. This tests a platform capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

PANEL_URL = "http://openavc.invalid/panel/"
PAGE_URL = "http://openavc.invalid/api/projects/default/ui/room_map/index.html"

#: The author's page. It fills its frame and records what the panel told it.
PAGE_HTML = """<!DOCTYPE html>
<html><body style="margin:0">
  <div id="room" style="width:100%;height:100vh;background:#123;color:#fff">Room map</div>
  <script>
    window.__init = null;
    window.__ask = (msg) => parent.postMessage(msg, '*');
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'openavc:init') window.__init = e.data;
    });
  </script>
</body></html>
"""


def _panel_html() -> str:
    """The real panel at real size, rendering into its own `#panel-root`.

    Unlike the control tests there is no stage box: a custom page IS the panel,
    so it has to be measured against the whole screen.
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
  html, body {{ margin: 0; height: 100%; }}
  #panel-root {{ position: relative; width: {REF_W}px; height: {REF_H}px; }}
</style></head>
<body>
  <div id="connection-status"></div>
  <div id="offline-overlay">
    <div class="offline-content"><div class="offline-title">System Offline</div></div>
  </div>
  <div id="loading-state"></div>
  <div id="panel-root"></div>
<script>
  // Let the ui/ tree through so the panel's own is-the-file-there check
  // succeeds; stub everything else it asks for at startup.
  const realFetch = window.fetch.bind(window);
  window.fetch = async (url, opts) => (
    String(url).includes('/api/projects/default/ui/')
      ? realFetch(url, opts)
      : {{ ok: false, json: async () => ({{}}) }}
  );
  class FakeWS {{ constructor() {{ this.readyState = 1; }} send() {{}} close() {{}} }}
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
</script>
<script>{panel_js}</script>
</body></html>
"""


def _project(page_extra: dict | None = None, masters: list | None = None) -> dict:
    page = {
        "id": "main", "name": "Main", "page_type": "page",
        "render_mode": "custom", "custom_file": "room_map/index.html",
        "custom_config": {"room": "204"},
        "elements": [{"id": "hidden_btn", "type": "button", "label": "Never drawn"}],
        "layouts": [{
            "id": "landscape", "orientation": "landscape", "primary": True,
            "placements": {"hidden_btn": {"x": 40, "y": 40, "w": 20, "h": 10}},
            "hidden": [],
        }],
        **(page_extra or {}),
    }
    return {
        "settings": {},
        "master_elements": masters or [],
        "pages": [page, {
            "id": "dlg", "name": "Dialog", "page_type": "overlay",
            "elements": [{"id": "dlg_btn", "type": "button", "label": "Close"}],
            "layouts": [{
                "id": "landscape", "orientation": "landscape", "primary": True,
                "placements": {"dlg_btn": {"x": 10, "y": 40, "w": 80, "h": 20}},
                "hidden": [],
            }],
        }],
    }


@pytest.fixture
def panel(browser):
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.route(PANEL_URL + "*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=_panel_html(),
    ))
    page.route(PAGE_URL, lambda route: route.fulfill(
        status=200, content_type="text/html", body=PAGE_HTML,
    ))
    page.goto(PANEL_URL)
    assert page.evaluate("() => !!window.__openavcPanel"), "panel.js did not initialise"
    yield page
    context.close()


def _render(page, ui: dict) -> None:
    page.evaluate(
        """(ui) => {
            const app = window.__openavcPanel;
            app.uiDef = ui;
            app.uiSettings = ui.settings || {};
            app.currentPage = ui.pages[0].id;
            app.snapshotReceived = true;
            app.state = {'device.acme_widget.power': 'on', 'var.volume': 30};
            window.__sent = [];
            app.ws = { readyState: 1, send: (m) => window.__sent.push(JSON.parse(m)) };
            app.renderCurrentPage();
        }""",
        ui,
    )
    page.wait_for_selector('#panel-root .panel-custom iframe')


#: The layers that can be on top of each other at one point on the glass, so a
#: hit lands on the LAYER rather than on whichever inner div drew the pixel.
_LAYERS = "#offline-overlay, #lock-overlay, .panel-overlay, .panel-page, #panel-root"


def _at_centre(page) -> str:
    """Which layer the browser says is on top at the middle of the screen."""
    return page.evaluate(
        """(layers) => {
            const hit = document.elementFromPoint(
                window.innerWidth / 2, window.innerHeight / 2);
            if (!hit) return 'nothing';
            const layer = hit.closest(layers);
            return layer ? (layer.id || layer.className) : (hit.id || hit.className);
        }""",
        _LAYERS,
    )


def test_a_custom_page_fills_the_screen_and_draws_no_controls(panel) -> None:
    _render(panel, _project())

    box = panel.evaluate(
        """() => {
            const el = document.querySelector('#panel-root .panel-custom');
            const r = el.getBoundingClientRect();
            return {w: Math.round(r.width), h: Math.round(r.height),
                    x: Math.round(r.left), y: Math.round(r.top)};
        }"""
    )
    assert box == {"x": 0, "y": 0, "w": REF_W, "h": REF_H}
    # The author's page really loaded into it, across the sandbox boundary.
    frame = panel.frame_locator('#panel-root .panel-custom iframe')
    frame.locator("#room").wait_for(timeout=5000)
    assert panel.frames[1].evaluate("() => window.__init")["config"] == {"room": "204"}
    # ...and the control still in the project is nowhere on screen.
    assert panel.locator('[data-element-id="hidden_btn"]').count() == 0


def test_a_master_element_draws_over_a_custom_page(panel) -> None:
    """A nav bar is how somebody gets off a custom page. Equal z-index means
    paint order decides, so this is a claim only the browser can settle."""
    _render(panel, _project(masters=[{
        "id": "home", "type": "button", "label": "Home", "pages": "*",
        "placements": {"landscape": {"x": 40, "y": 40, "w": 20, "h": 20}},
    }]))
    panel.frame_locator('#panel-root .panel-custom iframe').locator("#room").wait_for(timeout=5000)

    # Dead centre is inside both the frame and the master element's box.
    on_top = panel.evaluate(
        """() => {
            const el = document.elementFromPoint(window.innerWidth / 2, window.innerHeight / 2);
            return el.closest('[data-element-id]')?.dataset.elementId ?? 'nothing';
        }"""
    )
    assert on_top == "home", "the master element is buried under the author's page"


def test_the_offline_notice_draws_over_a_custom_page(panel) -> None:
    """The one promise a custom page must not be able to break."""
    _render(panel, _project())
    panel.frame_locator('#panel-root .panel-custom iframe').locator("#room").wait_for(timeout=5000)

    panel.evaluate("() => window.__openavcPanel.setConnectionStatus(false)")
    assert _at_centre(panel) == "offline-overlay"
    assert panel.locator("#offline-overlay").is_visible()
    # ...and the page stops taking input while the room is unreachable.
    assert panel.evaluate(
        "() => getComputedStyle(document.getElementById('panel-root')).pointerEvents"
    ) == "none"


def test_an_open_dialog_cannot_hide_the_offline_notice(panel) -> None:
    """A dialog left open when the room drops used to draw straight over
    "System Offline": the panel was dead and the screen said nothing."""
    _render(panel, _project())
    panel.evaluate("() => window.__openavcPanel.navigateToPage('dlg')")
    panel.wait_for_selector('.panel-overlay[data-page-id="dlg"]')

    panel.evaluate("() => window.__openavcPanel.setConnectionStatus(false)")
    assert _at_centre(panel) == "offline-overlay"


def test_touching_a_custom_page_keeps_the_panel_awake(panel) -> None:
    """A click inside an opaque-origin frame reaches nothing out here.

    That is the whole finding: the panel's idle listeners never fire, so a
    person working a custom page is invisible to the timer and the panel
    navigates away and re-locks under their hands. The browser is the only
    thing that can say the frame now has focus, which is what the reset keys
    off -- so this cannot be checked anywhere but here.
    """
    _render(panel, _project())
    frame = panel.frame_locator('#panel-root .panel-custom iframe')
    frame.locator("#room").wait_for(timeout=5000)

    panel.evaluate("""() => {
        const app = window.__openavcPanel;
        window.__resets = 0;
        app.resetIdleTimer = () => { window.__resets++; };
    }""")

    # A real click, the way a finger lands on the page.
    frame.locator("#room").click()
    assert panel.evaluate(
        "() => window.__openavcPanel._frameHasFocus("
        "document.querySelector('#panel-root .panel-custom iframe'))"
    ) is True, "clicking into the frame did not move focus to it"

    panel.frames[1].evaluate("() => window.__ask({type: 'openavc:activity'})")
    panel.wait_for_timeout(50)
    assert panel.evaluate("() => window.__resets") >= 1, (
        "the panel would have idled out under somebody using it"
    )


def test_a_frame_nobody_is_touching_cannot_hold_the_panel_awake(panel) -> None:
    """The other half of the same guard, and the reason it is a guard.

    Without the focus test a control could post activity in a loop and keep a
    wall panel unlocked all night, which would make the PIN lock defeatable by
    a file somebody dropped into the project.
    """
    _render(panel, _project())
    panel.frame_locator('#panel-root .panel-custom iframe').locator("#room").wait_for(timeout=5000)

    # Focus somewhere in the panel document, not the frame.
    panel.evaluate("""() => {
        document.body.tabIndex = -1;
        document.body.focus();
        window.__resets = 0;
        window.__openavcPanel.resetIdleTimer = () => { window.__resets++; };
    }""")
    panel.frames[1].evaluate("() => window.__ask({type: 'openavc:activity'})")
    panel.wait_for_timeout(50)
    assert panel.evaluate("() => window.__resets") == 0, (
        "a frame nobody is in kept the panel awake"
    )
