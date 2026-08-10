"""A dialog previewed in the UI Builder behaves like a dialog, not like a page.

The bug this pins was invisible in the place people look. A confirm dialog with
a Cancel button worked perfectly on real glass and looked broken in the
Builder's Preview -- which is the surface used to check panel runtime, so the
one tool for answering "does this work" was the one saying no.

Cause was a single missing hop. At runtime a dialog arrives through
``navigateToPage``, the only place ``page_type`` is read: it pushes the overlay
stack and calls ``renderOverlay``. The embedder's page messages instead assigned
``currentPage`` and called ``renderCurrentPage``, which reads no ``page_type``
and opens by tearing every overlay down. So the dialog drew flat with no
backdrop, and its Cancel (``$back``) found an empty overlay stack and silently
did nothing.

Driven through a REAL parent window posting REAL editor messages into a REAL
iframe, because the defect was in that wiring rather than in either end of it.
Calling the panel's method directly would pass just as happily with the
handlers reverted, which is the regression most worth catching.

Both halves are asserted: the dialog renders AS an overlay over the page behind
it, and ``$back`` closes it and leaves that page on screen. Edit mode keeps the
flat render on purpose -- authoring a dialog means seeing its contents -- so
that is asserted too, or the fix would just have moved the lie to the other
surface.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

#: Served through Playwright routing rather than set_content: the panel reads
#: `?edit=1` off its own URL to tell the design canvas from a preview, and
#: about:blank has no query string to read.
PARENT_URL = "http://openavc.invalid/builder"
PANEL_URL = "http://openavc.invalid/panel/"

#: A project with a page to stand behind the dialog, and the dialog.
UI_DEF = {
    "settings": {},
    "master_elements": [],
    "pages": [
        {
            "id": "system", "name": "System", "page_type": "page",
            "elements": [{"id": "sys_title", "type": "label", "text": "System"}],
            "layouts": [{
                "id": "landscape", "orientation": "landscape", "primary": True,
                "placements": {"sys_title": {"x": 5, "y": 5, "w": 40, "h": 10}},
                "hidden": [],
            }],
        },
        {
            "id": "confirm_reboot", "name": "Confirm", "page_type": "overlay",
            "overlay": {
                "width": 44, "height": 34, "position": "center",
                "backdrop": "dim", "dismiss_on_backdrop": True,
            },
            "elements": [
                {"id": "cr_title", "type": "label", "text": "Reboot?"},
                {"id": "cr_cancel", "type": "page_nav", "label": "Cancel",
                 "target_page": "$back"},
            ],
            "layouts": [{
                "id": "landscape", "orientation": "landscape", "primary": True,
                "placements": {
                    "cr_title": {"x": 8, "y": 15, "w": 84, "h": 20},
                    "cr_cancel": {"x": 8, "y": 55, "w": 40, "h": 30},
                },
                "hidden": [],
            }],
        },
    ],
}


def _panel_html() -> str:
    """The panel document, with its own two stylesheets inlined.

    The @import in panel.css is resolved by hand so both can be inlined, the
    same way tests/e2e/test_control_minimums.py does it.
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
<style>html {{ font-size: 14px; }} body {{ margin: 0; }}</style></head>
<body>
  <div id="panel-root"></div><div id="connection-status"></div>
  <div id="offline-overlay"></div><div id="loading-state"></div>
<script>
  window.fetch = async () => ({{ ok: false, json: async () => ({{}}) }});
  class FakeWS {{ constructor() {{ this.readyState = 1; }} send() {{}} close() {{}} }}
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
</script>
<script>{panel_js}</script>
</body></html>
"""


def _parent_html(edit_mode: bool) -> str:
    """What Canvas.tsx is, reduced to the part that matters: an iframe it posts to."""
    suffix = "?edit=1" if edit_mode else "?page=system"
    return f"""<!DOCTYPE html>
<html><head><style>html,body{{margin:0}}
  iframe{{width:{REF_W}px;height:{REF_H}px;border:0;display:block}}</style></head>
<body><iframe id="preview" src="{PANEL_URL}{suffix}"></iframe>
<script>
  window.postToPanel = (msg) =>
    document.getElementById('preview').contentWindow.postMessage(msg, '*');
</script>
</body></html>
"""


@pytest.fixture
def builder(browser, request):
    """A parent window with the panel embedded, in preview or edit mode."""
    edit_mode = getattr(request, "param", False)
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H + 40})
    page = context.new_page()
    panel_html = _panel_html()
    page.route(f"{PANEL_URL}*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=panel_html,
    ))
    parent_html = _parent_html(edit_mode)
    page.route(f"{PARENT_URL}*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=parent_html,
    ))
    page.goto(PARENT_URL)
    frame = page.frame_locator("#preview")
    # The panel has to be alive before the parent posts to it, exactly as the
    # builder waits for the iframe's load event.
    page.wait_for_function(
        "() => document.getElementById('preview').contentWindow.__openavcPanel"
    )
    child = page.frames[1]
    assert child.evaluate("() => window.__openavcPanel.editMode") is edit_mode
    assert child.evaluate("() => window.__openavcPanel.embedded") is True
    yield page, frame, child
    context.close()


def _post_project(page, page_id: str) -> None:
    """The message Canvas.tsx sends on every edit, and on first load."""
    page.evaluate(
        "([ui, pageId]) => window.postToPanel({"
        "  type: 'openavc:editor-project', project: {ui}, pageId, showGrid: false"
        "})",
        [UI_DEF, page_id],
    )


def _state(child) -> dict:
    return child.evaluate("""() => ({
        overlays: document.querySelectorAll('.panel-overlay').length,
        backdrops: document.querySelectorAll('.overlay-backdrop-dim').length,
        stack: window.__openavcPanel.overlayStack.slice(),
        behind: !!document.querySelector('[data-element-id="sys_title"]'),
        dialogText: (document.querySelector('.panel-overlay [data-element-id="cr_title"]')
                     || {}).textContent || null,
    })""")


def test_preview_draws_a_dialog_as_a_dialog(builder) -> None:
    """The overlay renders, over the page it was opened from."""
    page, _frame, child = builder
    _post_project(page, "system")
    _post_project(page, "confirm_reboot")
    child.wait_for_selector(".panel-overlay", timeout=5000)
    result = _state(child)
    assert result["overlays"] == 1, "the dialog did not render as an overlay"
    assert result["backdrops"] == 1, "no backdrop -- it reads as a plain page"
    assert result["stack"] == ["confirm_reboot"]
    assert result["behind"], "nothing was drawn behind the dialog"
    assert result["dialogText"] == "Reboot?"


def test_cancel_closes_the_dialog_in_preview(builder) -> None:
    """The half that was silently dead: `$back` against an empty overlay stack."""
    page, frame, child = builder
    _post_project(page, "system")
    _post_project(page, "confirm_reboot")
    child.wait_for_selector(".panel-overlay", timeout=5000)
    frame.locator('.panel-overlay [data-element-id="cr_cancel"]').click()
    # The overlay leaves on a transition, so its node outlives the click.
    # Waiting for it to detach is the assertion a person makes -- "it closed".
    child.wait_for_selector(".panel-overlay", state="detached", timeout=5000)
    result = _state(child)
    assert result["stack"] == []
    assert result["behind"], "closing the dialog should leave the page behind it"


def test_an_open_dialog_survives_a_vmin_message(builder) -> None:
    """The builder re-posts vmin on its own schedule, and it must not close it.

    `renderCurrentPage` opens by dismissing every overlay, so any re-render
    reason -- a vmin nudge, an unrelated edit -- used to close an open dialog
    out from under whoever was looking at it. Found by this test rather than by
    reading: the first fix routed only PAGE CHANGES through the runtime path and
    left every other re-render on the tear-down one.
    """
    page, _frame, child = builder
    _post_project(page, "system")
    _post_project(page, "confirm_reboot")
    child.wait_for_selector(".panel-overlay", timeout=5000)
    page.evaluate(
        "() => window.postToPanel({type: 'openavc:editor-page',"
        " pageId: 'confirm_reboot', vmin: 8})"
    )
    result = _state(child)
    assert result["stack"] == ["confirm_reboot"], "a vmin message closed the dialog"
    assert result["overlays"] == 1
    assert result["behind"], "the page behind it should still be drawn"


@pytest.mark.parametrize("builder", [True], indirect=True)
def test_edit_mode_still_draws_the_dialog_flat(builder) -> None:
    """Authoring a dialog means seeing its contents, not a backdrop over it.

    Without this the fix would have moved the problem rather than solved it:
    the design canvas sizes itself TO the dialog, so an overlay rendered there
    would put a full-screen backdrop inside a dialog-sized box.
    """
    page, _frame, child = builder
    _post_project(page, "confirm_reboot")
    child.wait_for_selector('[data-element-id="cr_title"]', timeout=5000)
    result = _state(child)
    assert result["overlays"] == 0, "edit mode should draw the dialog flat"
    assert result["stack"] == []
