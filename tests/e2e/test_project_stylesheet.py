"""A project stylesheet written as ordinary CSS actually restyles the control.

This is a cascade question, so it can only be answered by a browser. The panel
writes the theme's colors, corner radius, borders and shadows straight onto each
control as inline styles, and an inline style beats a stylesheet rule that does
not say ``!important``. So the first rule anybody writes --
``.brand { background: #8AB493 }`` -- did nothing at all, silently, which is the
worst possible introduction to a feature whose whole point is "make it look like
ours".

Requiring ``!important`` was never protecting anything: the properties it
applies to are exactly the properties the author is trying to change. So
``_raiseCustomCssPriority`` re-sets every declaration in the project stylesheet
as important, through the CSSOM, and these tests pin the three things that has
to mean:

1. plain CSS wins over what the theme drew,
2. an animation still runs (``!important`` inside ``@keyframes`` is ignored by
   the spec, and writing it there can drop the declaration outright, so a naive
   pass would break animations rather than boost them),
3. a rule the author already marked important is left exactly as it was.

Asserted through ``getComputedStyle`` on the real panel, because "the rule is in
the sheet" is not the same claim as "the control is that color" -- the first
version of this feature had the rule in the sheet and a grey button on screen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
PANEL_DIR = OPENAVC_ROOT / "openavc" / "web" / "panel"

REF_W, REF_H = 1280, 800

PARENT_URL = "http://openavc.invalid/builder"
PANEL_URL = "http://openavc.invalid/panel/"

#: A button with a theme-shaped inline look, and the class under test on it.
def _ui_def(custom_css: str) -> dict:
    return {
        "settings": {},
        "custom_css": custom_css,
        "master_elements": [],
        "pages": [
            {
                "id": "main",
                "name": "Main",
                "page_type": "page",
                "elements": [
                    {
                        "id": "btn_on",
                        "type": "button",
                        "label": "System On",
                        "css_class": "brand",
                        # What a theme default lands as: an inline background the
                        # author's rule has to get past.
                        "style": {"bg_color": "#424242", "border_radius": 0.5714},
                        "bindings": {},
                    },
                ],
                "layouts": [
                    {
                        "id": "landscape",
                        "orientation": "landscape",
                        "primary": True,
                        "placements": {"btn_on": {"x": 10, "y": 10, "w": 30, "h": 20}},
                        "hidden": [],
                    },
                ],
            },
        ],
    }


def _panel_html() -> str:
    """The panel document with its two stylesheets inlined (the @import by hand)."""
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


def _parent_html() -> str:
    """What Canvas.tsx is, reduced to the part that matters: an iframe it posts to."""
    return f"""<!DOCTYPE html>
<html><head><style>html,body{{margin:0}}
  iframe{{width:{REF_W}px;height:{REF_H}px;border:0;display:block}}</style></head>
<body><iframe id="preview" src="{PANEL_URL}?edit=1"></iframe>
<script>
  window.postToPanel = (msg) =>
    document.getElementById('preview').contentWindow.postMessage(msg, '*');
</script>
</body></html>
"""


@pytest.fixture
def panel(browser):
    """The panel embedded in edit mode, exactly as the design canvas embeds it.

    Driven through a real parent window rather than by calling the panel's
    methods, because the edit-mode message path only listens to its parent --
    posting to itself is a shape the Builder never produces.
    """
    context = browser.new_context(viewport={"width": REF_W, "height": REF_H})
    page = context.new_page()
    panel_body = _panel_html()
    page.route(f"{PANEL_URL}*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=panel_body,
    ))
    parent_body = _parent_html()
    page.route(f"{PARENT_URL}*", lambda route: route.fulfill(
        status=200, content_type="text/html", body=parent_body,
    ))
    page.goto(PARENT_URL)
    page.wait_for_function(
        "() => document.getElementById('preview').contentWindow.__openavcPanel"
    )
    yield page, page.frames[1]
    context.close()


def _render(panel, custom_css: str) -> None:
    """Hand the panel a project, the way the Builder's canvas does."""
    page, child = panel
    page.evaluate(
        "(ui) => window.postToPanel("
        "  {type: 'openavc:editor-project', project: {ui}, pageId: 'main', showGrid: false})",
        _ui_def(custom_css),
    )
    child.wait_for_selector('[data-element-id="btn_on"]', timeout=5000)


def _computed(panel, prop: str) -> str:
    _page, child = panel
    return child.evaluate(
        "(prop) => getComputedStyle(document.querySelector('[data-element-id=\"btn_on\"]'))"
        ".getPropertyValue(prop)",
        prop,
    )


def _in_panel(panel, script: str):
    _page, child = panel
    return child.evaluate(script)


def test_plain_css_beats_the_inline_look(panel) -> None:
    """No !important, and the button is the author's color."""
    _render(panel, ".brand { background: rgb(138, 180, 147); border-radius: 32px; }")
    assert _computed(panel, "background-color") == "rgb(138, 180, 147)", (
        "the project stylesheet lost to the inline style the renderer wrote -- "
        "this is the whole reason _raiseCustomCssPriority exists"
    )
    assert _computed(panel, "border-radius") == "32px"


def test_a_shorthand_holding_a_custom_property_survives(panel) -> None:
    """`background: var(--brand)` is the shape that broke the first version.

    A shorthand carrying a var() cannot be expanded into longhands until the
    variable is substituted, so the browser lists background-color and friends
    and hands back an empty string for every one of them. A priority pass that
    walks properties writes those empty strings back and DELETES the
    declaration -- which is not a theoretical worry: it shipped, and the worked
    example in the docs rendered without its green.
    """
    _render(
        panel,
        ":root { --brand: rgb(47, 125, 79); }\n"
        ".brand { background: var(--brand); border: 2px solid var(--brand); }",
    )
    assert _computed(panel, "background-color") == "rgb(47, 125, 79)"
    assert _computed(panel, "border-top-color") == "rgb(47, 125, 79)"


def test_a_declaration_with_a_semicolon_inside_it_is_not_split(panel) -> None:
    """A data: URI carries a semicolon, and so can a quoted string."""
    _render(
        panel,
        '.brand { background-image: url("data:image/svg+xml;utf8,'
        '<svg xmlns=%22http://www.w3.org/2000/svg%22/>"); letter-spacing: 3px; }',
    )
    # The rule survived intact: the trailing declaration is still there, which
    # is what a bad split would have eaten.
    assert _computed(panel, "letter-spacing") == "3px"
    assert "data:image/svg+xml" in _computed(panel, "background-image")


def test_a_property_the_panel_never_writes_inline_still_works(panel) -> None:
    """The half that always worked keeps working -- nothing was traded for the fix."""
    _render(panel, ".brand { letter-spacing: 4px; text-transform: uppercase; }")
    assert _computed(panel, "letter-spacing") == "4px"
    assert _computed(panel, "text-transform") == "uppercase"


def test_keyframes_survive_the_priority_pass(panel) -> None:
    """!important inside a keyframe is ignored by spec, so it must be left alone.

    A pass that wrote it there can drop the declaration, which would turn a
    working animation into a still frame -- the opposite of the intent.
    """
    _render(
        panel,
        "@keyframes pulse { from { opacity: 0.25; } to { opacity: 0.75; } }\n"
        ".brand { animation: pulse 10s linear infinite; }",
    )
    kept = _in_panel(
        panel,
        """() => {
            const sheet = document.getElementById('panel-custom-css').sheet;
            for (const rule of sheet.cssRules) {
                if (rule.type === CSSRule.KEYFRAMES_RULE) {
                    return [...rule.cssRules].map(k => k.style.cssText);
                }
            }
            return null;
        }""",
    )
    assert kept == ["opacity: 0.25;", "opacity: 0.75;"], (
        f"the keyframe declarations did not survive intact: {kept}"
    )
    assert _computed(panel, "animation-name") == "pulse"


def test_an_authors_own_important_is_left_alone(panel) -> None:
    """Already important stays important, and stays exactly one declaration."""
    _render(panel, ".brand { background: rgb(1, 2, 3) !important; }")
    assert _computed(panel, "background-color") == "rgb(1, 2, 3)"
    text = _in_panel(
        panel,
        "() => document.getElementById('panel-custom-css').sheet.cssRules[0].cssText",
    )
    assert text.count("!important") == 1, f"declaration was doubled up: {text}"


def test_the_stored_sheet_is_the_authors_text_verbatim(panel) -> None:
    """The project keeps what the author wrote. Priority is applied, not saved.

    If the pass ever rewrote the text instead of the parsed sheet, an author
    would open their stylesheet to find !important sprayed through it.
    """
    css = ".brand { background: rgb(9, 9, 9); }"
    _render(panel, css)
    stored = _in_panel(
        panel,
        "() => document.getElementById('panel-custom-css').textContent",
    )
    assert stored == css
