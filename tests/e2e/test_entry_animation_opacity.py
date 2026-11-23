"""A control draws its own opacity once it has come in, under every entry animation.

Panel Settings can bring each control in with an entry animation (fade, fade
up, scale, or one of them staggered). A control can also be dimmed on purpose:
its style's opacity, a look's (a "disabled" state), or a script's
``ui.<id>.opacity`` override. When the animation ends the control has to show
that opacity, and a cleared override has to give back the control's own.

Which opacity is on screen is settled by the cascade: an animation's held
value beats an inline style, so only a browser can answer it. This renders the
real panel, with its real stylesheets, in Chromium.

Every control here is invented. This tests a platform capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.e2e._panel_modules import panel_script_tag, serve_panel_scripts

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800
STAGGER_MS = 10

#: Each control, the opacity it should show once in, and what gives it that.
CONTROLS = {
    "styled": 0.5,      # its style
    "looked": 0.4,      # a look, active from the start
    "overridden": 0.3,  # a script's override, set from the start
    "plain": 1.0,       # nothing: fully drawn
}

#: (element_entry, element_stagger_style): every way a control can come in.
ENTRIES = [
    ("fade", None),
    ("fade-up", None),
    ("scale", None),
    ("stagger", "fade"),
    ("stagger", "fade-up"),
    ("stagger", "scale"),
]


def _panel_html() -> str:
    """The real panel at real size, rendering into its own `#panel-root`."""
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
  html, body {{ margin: 0; height: 100%; }}
  #panel-root {{ position: relative; width: {REF_W}px; height: {REF_H}px; }}
  /* A control eases between colours and opacities as its state changes. This
     is about where the opacity comes to rest, so easing is switched off; the
     entry animation is a CSS animation, not a transition, and still runs. */
  #panel-root * {{ transition: none !important; }}
</style></head>
<body>
  <div id="connection-status"></div><div id="offline-overlay"></div>
  <div id="loading-state"></div>
  <div id="panel-root"></div>
<script>
  window.fetch = async () => ({{ ok: false, json: async () => ({{}}) }});
  class FakeWS {{ constructor() {{ this.readyState = 1; }} send() {{}} close() {{}} }}
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
</script>
{panel_script_tag()}
</body></html>
"""


def _project(entry: str, stagger_style: str | None) -> dict:
    elements = [
        {"id": "styled", "type": "button", "label": "Styled", "style": {"opacity": 0.5}},
        {"id": "looked", "type": "button", "label": "Looked", "bindings": {"show": {"look": {
            "key": "var.dim", "condition": {"equals": True},
            "style_active": {"opacity": 0.4},
        }}}},
        {"id": "overridden", "type": "button", "label": "Overridden"},
        {"id": "plain", "type": "button", "label": "Plain"},
        # Its own opacity, covered by an override from the start and then cleared.
        {"id": "uncovered", "type": "button", "label": "Uncovered", "style": {"opacity": 0.6}},
    ]
    settings = {"page_transition": "none", "element_entry": entry, "element_stagger_ms": STAGGER_MS}
    if stagger_style:
        settings["element_stagger_style"] = stagger_style
    return {
        "settings": settings,
        "master_elements": [],
        "pages": [{
            "id": "main", "name": "Main", "page_type": "page",
            "elements": elements,
            "layouts": [{
                "id": "landscape", "orientation": "landscape", "primary": True,
                "placements": {
                    e["id"]: {"x": 2 + 19 * i, "y": 40, "w": 16, "h": 12}
                    for i, e in enumerate(elements)
                },
                "hidden": [],
            }],
        }],
    }


@pytest.fixture(scope="module")
def panel(browser):
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    serve_panel_scripts(page)
    page.set_content(_panel_html(), wait_until="load")
    page.wait_for_function("() => !!window.__openavcPanel")
    yield page
    context.close()


def _render(page, ui: dict) -> None:
    """Draw the page as a panel does, with every state the controls read set first."""
    page.evaluate(
        """(ui) => {
            const app = window.__openavcPanel;
            app.editMode = false;
            app.root.textContent = '';
            app.uiDef = ui;
            app.uiSettings = ui.settings;
            app.currentPage = ui.pages[0].id;
            app.snapshotReceived = true;
            app.state = {
                'var.dim': true,
                'ui.overridden.opacity': 0.3,
                'ui.uncovered.opacity': 0.2,
            };
            app.ws = { readyState: 1, send: () => {} };
            app.renderCurrentPage();
        }""",
        ui,
    )
    # Every control's stagger step, then its animation, then some.
    page.wait_for_timeout(STAGGER_MS * len(ui["pages"][0]["elements"]) + 300 + 200)
    page.wait_for_function(
        "() => !document.querySelector('.element-entry-waiting')"
        " && document.getAnimations().every((a) => a.playState !== 'running')"
    )


def _opacities(page) -> dict:
    return page.evaluate(
        """(ids) => Object.fromEntries(ids.map((id) => {
            const el = document.querySelector(`#panel-root [data-element-id="${id}"]`);
            return [id, el ? Number(getComputedStyle(el).opacity) : null];
        }))""",
        [*CONTROLS, "uncovered"],
    )


def _set(page, key: str, value) -> None:
    """A state change reaching the panel: `None` clears the key."""
    page.evaluate(
        """([key, value]) => {
            const app = window.__openavcPanel;
            if (value === null) delete app.state[key]; else app.state[key] = value;
            app.evaluateAllBindings([key]);
        }""",
        [key, value],
    )


@pytest.mark.parametrize("entry,stagger_style", ENTRIES)
def test_each_control_draws_its_own_opacity_once_in(panel, entry, stagger_style) -> None:
    _render(panel, _project(entry, stagger_style))
    # The controls really came in by the animation asked for, so a panel that
    # stopped animating at all cannot pass this.
    animated = panel.evaluate(
        """(cls) => [...document.querySelectorAll('#panel-root [data-element-id]')]
            .filter((el) => !el.classList.contains(cls)).map((el) => el.dataset.elementId)""",
        f"element-entry-{stagger_style or entry}",
    )
    assert animated == [], f"{entry}/{stagger_style}: these did not animate in: {animated}"
    seen = _opacities(panel)
    for control, want in CONTROLS.items():
        assert seen[control] == pytest.approx(want), (
            f"{entry}/{stagger_style}: {control} draws at {seen[control]}, not its own {want}"
        )


@pytest.mark.parametrize("entry,stagger_style", ENTRIES)
def test_a_change_after_the_entry_draws(panel, entry, stagger_style) -> None:
    """A look that goes inactive and an override set later both reach the glass."""
    _render(panel, _project(entry, stagger_style))
    _set(panel, "var.dim", False)
    _set(panel, "ui.plain.opacity", 0.7)
    seen = _opacities(panel)
    assert seen["looked"] == pytest.approx(1.0), f"{entry}: the inactive look still draws {seen['looked']}"
    assert seen["plain"] == pytest.approx(0.7), f"{entry}: a later override draws {seen['plain']}"


@pytest.mark.parametrize("entry,stagger_style", ENTRIES)
def test_a_cleared_override_gives_back_the_controls_own_opacity(panel, entry, stagger_style) -> None:
    """The override was drawn while the control was still coming in; what it
    covered is the control's own 0.6, not a frame of the animation."""
    _render(panel, _project(entry, stagger_style))
    assert _opacities(panel)["uncovered"] == pytest.approx(0.2)
    _set(panel, "ui.uncovered.opacity", None)
    seen = _opacities(panel)["uncovered"]
    assert seen == pytest.approx(0.6), f"{entry}/{stagger_style}: cleared, it draws {seen}, not its own 0.6"
