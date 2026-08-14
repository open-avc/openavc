"""A custom control really loads and really gets its opening message.

jsdom can tell you the panel built an iframe with the right attributes. It
cannot tell you the browser then fetched that file, ran the author's script
inside a sandbox with an opaque origin, and let a message across that boundary
-- and those three are the whole feature. A control that renders as a blank
rectangle because the sandbox blocked something is a failure with no error
message anywhere, so it gets a real browser.

The grant is checked here too, on both sides of the boundary: what a control is
handed at startup, and what the panel does with what it sends back. The jsdom
harness pins the same rules against a fake window; this proves them where the
sandbox is real and the messages actually have to cross an opaque origin.
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
CONTROL_URL = "http://openavc.invalid/api/projects/default/ui/room_map/index.html"

#: The author's control: it draws, and it records what the panel told it.
CONTROL_HTML = """<!DOCTYPE html>
<html><body style="margin:0">
  <div id="room">Room map</div>
  <script>
    window.__init = null;
    window.__state = [];
    // What the author calls to act on the room. The test drives it directly so
    // the assertion is about the panel's answer, not about timing.
    window.__ask = (msg) => parent.postMessage(msg, '*');
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'openavc:init') {
        window.__init = e.data;
        document.getElementById('room').textContent =
          'Room ' + (e.data.config.room || '?');
      }
      if (e.data && e.data.type === 'openavc:state') window.__state.push(e.data.key);
    });
  </script>
</body></html>
"""


def _panel_html() -> str:
    """The panel's own CSS and JS, with one absolutely-sized box to render into.

    The @import in panel.css is resolved by hand so both sheets can be inlined,
    the same way the other panel e2e files do it.
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
  #box {{ position: absolute; left: 0; top: 0; width: 400px; height: 300px; }}
</style></head>
<body>
  <div id="panel-root"></div><div id="connection-status"></div>
  <div id="offline-overlay"></div><div id="loading-state"></div>
  <div id="stage"><div id="box"></div></div>
<script>
  // Stub the project fetch the panel does at startup, but let requests for the
  // ui/ tree through: the panel asks whether a control's file is really there,
  // and a stub that answers "no" for everything would put a failure strip on
  // every control in every scenario here.
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


@pytest.fixture
def panel(browser):
    """The real panel at a real URL, with the ui/ route served.

    A routed URL rather than ``set_content``: the iframe's src is relative on
    purpose (that is what survives the cloud tunnel), so it only resolves if
    the page has an address to resolve against.
    """
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.route(PANEL_URL + "*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=_panel_html(),
    ))
    page.route(CONTROL_URL, lambda route: route.fulfill(
        status=200, content_type="text/html", body=CONTROL_HTML,
    ))
    page.goto(PANEL_URL)
    assert page.evaluate("() => !!window.__openavcPanel"), "panel.js did not initialise"
    yield page
    context.close()


def _render_control(page, element: dict) -> None:
    page.evaluate(
        """(el) => {
            const app = window.__openavcPanel;
            const box = document.getElementById('box');
            box.innerHTML = '';
            app.state = {'device.acme_widget.power': 'on', 'var.volume': 30};
            // Every frame the panel writes to the room, so a refusal is the
            // absence of an entry rather than something nobody looked at.
            window.__sent = [];
            app.ws = { readyState: 1, send: (m) => window.__sent.push(JSON.parse(m)) };
            // Navigation never touches the socket, so watching only __sent
            // would report "nothing reached the room" while the panel walked
            // off the page somebody was using.
            window.__navigated = [];
            app.navigateToPage = (p) => window.__navigated.push(p);
            box.appendChild(app.renderElement(el));
        }""",
        element,
    )


def _ask(page, message: dict) -> list:
    """Have the control send one message, and return what reached the room."""
    page.frames[1].evaluate("(m) => window.__ask(m)", message)
    page.wait_for_timeout(50)  # one postMessage hop, cross-origin
    return page.evaluate("() => window.__sent")


def test_the_control_loads_and_receives_its_opening_message(panel) -> None:
    _render_control(panel, {
        "id": "map", "type": "custom",
        "custom_file": "room_map/index.html",
        "custom_config": {"room": "204"},
    })

    frame = panel.frame_locator('[data-element-id="map"] iframe')
    # It drew, from the file the panel asked for.
    frame.locator("#room").wait_for(timeout=5000)
    # ...and it redrew from the config the panel handed it, which means the
    # message crossed the sandbox boundary.
    assert frame.locator("#room").inner_text() == "Room 204"

    init = panel.frames[1].evaluate("() => window.__init")
    assert init["elementId"] == "map"
    assert init["config"] == {"room": "204"}
    # The full theme palette, so a control can follow a theme switch.
    assert init["theme"]["--panel-bg"]
    assert len(init["theme"]) == 12
    # And nothing it was not granted: the room's live state is not in there.
    assert init["state"] == {}


def test_the_frame_is_sandboxed_with_scripts_and_nothing_else(panel) -> None:
    """The sandbox attribute IS the isolation story -- no CSP backs it up."""
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "room_map/index.html",
    })
    sandbox = panel.get_attribute('[data-element-id="map"] iframe', "sandbox")
    assert sandbox == "allow-scripts"
    # An opaque origin is what that buys: the frame cannot reach the panel's
    # document, so it can only ask the panel to act on its behalf.
    assert panel.frames[1].evaluate(
        "() => { try { return !!parent.document; } catch (e) { return false; } }"
    ) is False


def test_a_granted_control_sees_that_device_and_commands_it(panel) -> None:
    """The whole point of the step, through a real sandbox boundary."""
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "room_map/index.html",
        "custom_config": {"room": "204"},
        "grant": {"devices": ["acme_widget"], "variables": [], "macros": False, "navigate": False},
    })
    panel.frame_locator('[data-element-id="map"] iframe').locator("#room").wait_for(timeout=5000)

    init = panel.frames[1].evaluate("() => window.__init")
    # It is told what it holds, and handed the state that grant covers.
    assert init["grant"]["devices"] == ["acme_widget"]
    assert init["state"] == {"device.acme_widget.power": "on"}
    assert "var.volume" not in init["state"]

    sent = _ask(panel, {"type": "openavc:action", "action": "device.command",
                        "device": "acme_widget", "command": "power_on", "params": {}})
    assert len(sent) == 1, sent
    assert sent[0]["type"] == "command" and sent[0]["device_id"] == "acme_widget"

    # ...and the device next to it on the same page is still out of reach.
    sent = _ask(panel, {"type": "openavc:action", "action": "device.command",
                        "device": "acme_projector", "command": "power_on", "params": {}})
    assert len(sent) == 1, f"an ungranted device was commanded anyway: {sent}"


def test_an_ungranted_control_reaches_nothing(panel) -> None:
    """Placed and not configured is the common case, and it must be inert."""
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "room_map/index.html",
    })
    panel.frame_locator('[data-element-id="map"] iframe').locator("#room").wait_for(timeout=5000)

    assert panel.frames[1].evaluate("() => window.__init")["state"] == {}
    for message in (
        {"type": "openavc:action", "action": "device.command",
         "device": "acme_widget", "command": "power_on", "params": {}},
        {"type": "openavc:action", "action": "state.set", "key": "var.volume", "value": 1},
        {"type": "openavc:action", "action": "macro.run", "macro": "system_on"},
        {"type": "openavc:navigate", "page": "admin"},
    ):
        assert _ask(panel, message) == [], f"{message['type']} reached the room ungranted"
    assert panel.evaluate("() => window.__navigated") == [], "it moved the panel ungranted"


def test_a_granted_control_moves_the_panel_and_runs_a_macro(panel) -> None:
    """The two switches, through the real boundary. Navigation was ungated
    before grants existed, so this is the half that used to let any iframe walk
    the panel off whatever page a person was standing at."""
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "room_map/index.html",
        "grant": {"devices": [], "variables": [], "macros": True, "navigate": True},
    })
    panel.frame_locator('[data-element-id="map"] iframe').locator("#room").wait_for(timeout=5000)

    sent = _ask(panel, {"type": "openavc:action", "action": "macro.run", "macro": "system_on"})
    assert len(sent) == 1 and sent[0]["type"] == "macro.execute"
    assert sent[0]["macro_id"] == "system_on"

    _ask(panel, {"type": "openavc:navigate", "page": "admin"})
    assert panel.evaluate("() => window.__navigated") == ["admin"]


MISSING_URL = "http://openavc.invalid/api/projects/default/ui/gone/index.html"


def test_a_missing_file_says_so_in_the_box(panel) -> None:
    """An iframe pointed at a 404 renders the server's JSON error as text.

    That reads as "this control is broken in some unknowable way" rather than
    "that file is not in the project", and on a wall panel there is no console
    to check.
    """
    panel.route(MISSING_URL, lambda route: route.fulfill(
        status=404, content_type="application/json", body='{"detail":"Not found"}',
    ))
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "gone/index.html",
    })
    fault = panel.locator('[data-element-id="map"] .panel-iframe-fault')
    fault.wait_for(timeout=5000)
    assert fault.inner_text() == "gone/index.html could not be loaded (404)"


def test_a_control_that_throws_says_so_in_the_box(panel) -> None:
    """Nothing outside a sandboxed frame can see a script error inside it, so
    the control reports its own with the one line the guide gives it."""
    _render_control(panel, {
        "id": "map", "type": "custom", "custom_file": "room_map/index.html",
    })
    panel.frame_locator('[data-element-id="map"] iframe').locator("#room").wait_for(timeout=5000)
    panel.frames[1].evaluate(
        "() => window.__ask({type: 'openavc:error', message: 'ReferenceError: lights is not defined'})"
    )
    fault = panel.locator('[data-element-id="map"] .panel-iframe-fault')
    fault.wait_for(timeout=5000)
    assert "ReferenceError" in fault.inner_text()


# --- A file that changes under a running panel -----------------------------


def test_a_changed_file_redraws_a_panel_that_is_already_showing_it(browser) -> None:
    """The panel re-fetches on ui.files, which is the whole point of the push.

    A custom control is a file, so replacing one changes nothing the project
    push carries and the panel had no reason to look again: it kept drawing the
    old control until somebody navigated away and back. Nothing short of a real
    browser proves the fix -- the assertion is that the iframe went and got the
    new bytes, which is a fetch, a cache decision and a re-render.

    Its own fixture rather than the shared one: this test needs to change what
    the control URL serves half way through.
    """
    def _version(word: str) -> str:
        # Its own markup rather than CONTROL_HTML: that one rewrites its
        # visible text when the opening message arrives, which would overwrite
        # the very thing this test reads.
        return f'<!DOCTYPE html><html><body style="margin:0">' \
               f'<div id="ver">{word}</div></body></html>'

    served = {"body": _version("VERSION ONE")}
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    page.route(PANEL_URL + "*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=_panel_html(),
    ))
    # Matches the cache-busted URL too -- the version rides as a query string.
    page.route(CONTROL_URL + "*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=served["body"],
    ))
    page.goto(PANEL_URL)

    try:
        page.evaluate(
            """() => {
                const app = window.__openavcPanel;
                app.root = document.getElementById('box');
                app.snapshotReceived = true;
                app.currentPage = 'main';
                app.uiDef = {pages: [{
                    id: 'main', name: 'Main',
                    elements: [{id: 'map', type: 'custom',
                                custom_file: 'room_map/index.html', custom_config: {}}],
                    layouts: [{id: 'landscape', primary: true,
                               placements: {map: {x: 0, y: 0, w: 50, h: 50}}}],
                }]};
                app.renderCurrentPage();
            }"""
        )
        frame = page.frame_locator('[data-element-id="map"] iframe')
        frame.locator("#ver").wait_for(timeout=5000)
        assert frame.locator("#ver").inner_text() == "VERSION ONE"

        # The author saves. Nothing about the project moved.
        served["body"] = _version("VERSION TWO")
        page.evaluate(
            "() => window.__openavcPanel.handleMessage("
            "{type: 'ui.files', version: '1755180000000', path: 'room_map/index.html'})"
        )

        frame = page.frame_locator('[data-element-id="map"] iframe')
        frame.locator("#ver").wait_for(timeout=5000)
        assert frame.locator("#ver").inner_text() == "VERSION TWO"
    finally:
        context.close()


def test_a_page_with_no_author_markup_on_it_does_not_redraw(panel) -> None:
    """A redraw is cheap but visible -- it restarts page-enter animations and
    drops whatever transient state a control was holding. So a panel sitting on
    a page built entirely from shipped controls stays put."""
    panel.evaluate(
        """() => {
            const app = window.__openavcPanel;
            app.root = document.getElementById('box');
            app.snapshotReceived = true;
            app.currentPage = 'main';
            app.uiDef = {pages: [{
                id: 'main', name: 'Main',
                elements: [{id: 'btn', type: 'button', label: 'On'}],
                layouts: [{id: 'landscape', primary: true,
                           placements: {btn: {x: 0, y: 0, w: 20, h: 10}}}],
            }]};
            window.__renders = 0;
            const real = app.renderCurrentPage.bind(app);
            app.renderCurrentPage = () => { window.__renders++; return real(); };
            app.handleMessage({type: 'ui.files', version: '1755180000001', path: 'x.html'});
        }"""
    )
    assert panel.evaluate("() => window.__renders") == 0
    # The version still lands, so the next redraw for any other reason carries it.
    assert panel.evaluate("() => window.__openavcPanel._uiFilesVersion") == "1755180000001"
