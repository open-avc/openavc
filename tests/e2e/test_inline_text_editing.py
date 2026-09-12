"""Double-click text on the design canvas and edit it where it sits.

The rule this pins: **what opens is the authored source string that produced
the text on screen, not the text on screen.** A label drawing "Amp draw: 0.076
A" opens as the literal ``Amp draw: {value} A``; a four-state look showing
"ON AIR" edits that state's own label and no other; a button whose words a
script is setting refuses, because there is nothing authored to write.

This can only be tested here. The canvas is the real panel in an iframe and
the text node being edited belongs to that document, so nothing short of a
browser can say whether a double-click put a caret in the right place or
whether the committed string reached the right field. Two bugs in this file's
history were invisible to every unit test:

* a text-only button holds its words in the ``<button>`` itself, and a
  button's own answer to the space bar is "press me" -- so "Mic Mute"
  committed as "MicMute", losing the space with no sign it had been dropped;
* an element whose words come from a runtime ``ui.<id>.label`` override was
  filtered out by the overlay before the panel could explain itself, so a
  double-click on it did nothing at all.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

RAIL_TIMEOUT = 20_000

#: The state the page's bindings read. Seeded before the Builder opens so the
#: canvas draws with values in it, exactly as it does against a live room.
SEED_STATE = {
    "var.amp_draw": 0.076,
    "var.power": "on",
    "var.temp": 31.4,
    "ui.btn_scripted.label": "SET BY A SCRIPT",
}


def _project() -> dict[str, Any]:
    elements = [
        # The plain case: words in the element's own field.
        {"id": "btn_plain", "type": "button", "label": "Mute"},
        # A label element, whose field is `text` rather than `label`.
        {"id": "lbl_plain", "type": "label", "text": "Room 101"},
        # A format string with a token in it. THE case: what is drawn is
        # "Amp draw: 0.076 A" and what opens is the format.
        {
            "id": "lbl_bound", "type": "label", "text": "",
            "bindings": {"show": {"value": {
                "key": "var.amp_draw", "format": "Amp draw: {value} A",
            }}},
        },
        # Two halves of a conditional. Only the one on screen is editable here.
        {
            "id": "lbl_cond", "type": "label", "text": "",
            "bindings": {"show": {"value": {
                "key": "var.power", "condition": {"equals": "on"},
                "text_true": "Live", "text_false": "Standby",
            }}},
        },
        # A four-state look. The showing state's label is the edit target, and
        # the others -- and the element's own label -- must not move.
        {
            "id": "btn_look", "type": "button", "label": "Power",
            "bindings": {"show": {"look": {
                "key": "var.power", "default_state": "off",
                "states": {
                    "on": {"label": "ON AIR", "bg_color": "#2e7d32"},
                    "off": {"label": "Standby", "bg_color": "#555555"},
                },
            }}},
        },
        # A container's caption, which lives in a child node of the group.
        {"id": "grp", "type": "group", "label": "Audio"},
        # Words with no authored source: a script owns them at run time.
        {"id": "btn_scripted", "type": "button", "label": "Authored Word"},
        # The caption-only family. Each of these draws far more text than the
        # one field an edit may write, so the caret has to land in the caption
        # and nowhere near the options, the readout or the scale.
        {"id": "sel_input", "type": "select", "label": "Source",
         "options": [{"label": "Laptop", "value": "1"}, {"label": "Room PC", "value": "2"}]},
        {"id": "gau_temp", "type": "gauge", "label": "Rack Temp", "min": 0, "max": 60,
         "unit": "C", "bindings": {"show": {"value": {"key": "var.temp"}}}},
        {"id": "fad_mic", "type": "fader", "label": "Mic 1", "min": -60, "max": 10,
         "unit": "dB", "style": {"show_value": True}},
        # A nav button with no name of its own: it draws its target page id,
        # which is visible but NOT authored.
        {"id": "nav_unnamed", "type": "page_nav", "target_page": "main"},
        # A nav button that has been named.
        {"id": "nav_named", "type": "page_nav", "target_page": "main",
         "label": "Back"},
        # Deliberately NOT editable in place.
        {"id": "cam_wide", "type": "camera_preset", "label": "Wide Shot",
         "preset_number": 3,
         "bindings": {"show": {"look": {"key": "var.power", "condition": {"equals": "on"},
                                        "style_active": {"bg_color": "#2e7d32"}}}}},
    ]
    placements = {
        "btn_plain": {"x": 2, "y": 4, "w": 20, "h": 14},
        "lbl_plain": {"x": 26, "y": 4, "w": 20, "h": 14},
        "lbl_bound": {"x": 50, "y": 4, "w": 26, "h": 14},
        "lbl_cond": {"x": 2, "y": 24, "w": 20, "h": 14},
        "btn_look": {"x": 26, "y": 24, "w": 20, "h": 14},
        "grp": {"x": 50, "y": 24, "w": 24, "h": 22},
        "btn_scripted": {"x": 2, "y": 52, "w": 24, "h": 14},
        "sel_input": {"x": 28, "y": 52, "w": 22, "h": 14},
        "gau_temp": {"x": 52, "y": 52, "w": 20, "h": 24},
        "fad_mic": {"x": 74, "y": 52, "w": 12, "h": 24},
        "cam_wide": {"x": 2, "y": 78, "w": 22, "h": 14},
        "nav_unnamed": {"x": 26, "y": 78, "w": 22, "h": 14},
        "nav_named": {"x": 50, "y": 78, "w": 22, "h": 14},
    }
    return {
        "openavc_version": "0.13.0", "devices": [],
        "ui": {
            "settings": {"theme_id": "dark-default"},
            "page_groups": [],
            # A master: not on any one page, drawn on all of them. Its words
            # are the highest-traffic text in a project, which is why it is
            # editable here too.
            "master_elements": [{
                "id": "mst_home", "type": "button", "label": "Home",
                "show_on": "all",
                "placements": {"landscape": {"x": 76, "y": 78, "w": 20, "h": 14}},
            }],
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{"id": "d", "placements": placements}],
                "elements": elements,
            }],
        },
    }


@pytest.fixture
def builder(server_factory, page: Page) -> Page:
    handle = server_factory(project_overrides=_project())
    for key, value in SEED_STATE.items():
        page.request.put(f"{handle.base_url}/api/state/{key}", data={"value": value})
    page.set_viewport_size({"width": 1600, "height": 1000})
    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator("nav button").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.evaluate(
        """() => document.querySelector('nav button[aria-label="UI Builder"]')?.click()"""
    )
    page.locator("[data-canvas-element]").first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    # The canvas reports which elements carry editable text after its first
    # render. Give the iframe its moment rather than racing it.
    page.wait_for_timeout(2500)
    return page


def _open(page: Page, element_id: str) -> str | None:
    """Double-click an element's hit box; return what the editor opened with.

    A master's hit box is a different component with a different attribute,
    which is exactly how it ended up with no double-click at all.
    """
    page.dblclick(
        f'[data-canvas-element="{element_id}"], [data-canvas-master="{element_id}"]'
    )
    page.wait_for_timeout(400)
    return page.evaluate(
        """() => {
            const doc = document.querySelector('iframe').contentDocument;
            const host = doc.querySelector('[data-avc-text-editing]');
            return host ? host.innerText : null;
        }"""
    )


def _type_and_commit(page: Page, text: str) -> None:
    """Type over the opened text and press Enter, through the keyboard.

    Real key events, not a DOM write: the space bar is the whole reason this
    test exists, and only a real press exercises what the browser does with it.

    No select-all first. Opening an edit selects the whole string already, so
    typing replaces it -- and a select-all here would be wrong anyway, since
    the chord differs per platform and Control+A is "go to line start" on
    macOS, which silently types the new words in FRONT of the old ones.
    """
    page.keyboard.type(text)
    page.keyboard.press("Enter")
    page.wait_for_timeout(700)


def _stored(page: Page, element_id: str) -> dict[str, Any]:
    return page.evaluate(
        """(id) => {
            const w = document.querySelector('iframe').contentWindow;
            const p = w.__openavcPanel;
            const page_ = p.uiDef.pages.find(x => x.id === p.currentPage);
            return page_.elements.find(e => e.id === id);
        }""",
        element_id,
    )


def _drawn(page: Page, element_id: str) -> str:
    return page.evaluate(
        """(id) => {
            const doc = document.querySelector('iframe').contentDocument;
            const el = doc.querySelector(`[data-element-id="${id}"]`);
            return el ? el.innerText : '';
        }""",
        element_id,
    )


def test_a_plain_label_opens_with_its_own_words(builder: Page) -> None:
    assert _open(builder, "btn_plain") == "Mute"
    _type_and_commit(builder, "Mic Mute")
    # The space survives. A <button> treats the space bar as its own
    # activation, which silently ate it before the editor inserted it itself.
    assert _stored(builder, "btn_plain")["label"] == "Mic Mute"
    assert _drawn(builder, "btn_plain") == "Mic Mute"


def test_a_label_element_writes_text_not_label(builder: Page) -> None:
    assert _open(builder, "lbl_plain") == "Room 101"
    _type_and_commit(builder, "Room 204")
    stored = _stored(builder, "lbl_plain")
    assert stored["text"] == "Room 204"
    assert "label" not in stored or not stored.get("label")


def test_a_bound_label_opens_as_its_format_and_renders_substituted(builder: Page) -> None:
    """The whole idea, in one test.

    On screen: "Amp draw: 0.076 A". In the editor: the literal format. After
    the commit: the new format on file and the value substituted back in.
    """
    assert _drawn(builder, "lbl_bound") == "Amp draw: 0.076 A"
    assert _open(builder, "lbl_bound") == "Amp draw: {value} A"
    _type_and_commit(builder, "Current {value} A")
    stored = _stored(builder, "lbl_bound")
    assert stored["bindings"]["show"]["value"]["format"] == "Current {value} A"
    assert stored["bindings"]["show"]["value"]["key"] == "var.amp_draw"
    assert _drawn(builder, "lbl_bound") == "Current 0.076 A"


def test_a_conditional_edits_only_the_half_on_screen(builder: Page) -> None:
    assert _open(builder, "lbl_cond") == "Live"
    _type_and_commit(builder, "On Air")
    binding = _stored(builder, "lbl_cond")["bindings"]["show"]["value"]
    assert binding["text_true"] == "On Air"
    # The half that was not showing is untouched. Editing it is the Properties
    # panel's job, which is what keeps this gesture unambiguous.
    assert binding["text_false"] == "Standby"


def test_a_look_edits_the_showing_state_and_nothing_else(builder: Page) -> None:
    assert _drawn(builder, "btn_look") == "ON AIR"
    assert _open(builder, "btn_look") == "ON AIR"
    _type_and_commit(builder, "LIVE")
    stored = _stored(builder, "btn_look")
    states = stored["bindings"]["show"]["look"]["states"]
    assert states["on"]["label"] == "LIVE"
    # Its colour, its sibling state, and the element's own label all hold.
    assert states["on"]["bg_color"] == "#2e7d32"
    assert states["off"]["label"] == "Standby"
    assert stored["label"] == "Power"


def test_a_group_caption_is_editable_in_its_own_node(builder: Page) -> None:
    assert _open(builder, "grp") == "Audio"
    _type_and_commit(builder, "Front Audio")
    assert _stored(builder, "grp")["label"] == "Front Audio"


def test_runtime_words_refuse_and_say_why(builder: Page) -> None:
    """A script owns these words, so there is nothing authored to edit.

    Writing the element's own label here would be a silent no-op -- the
    override would still be covering it -- so the gesture explains itself
    instead, and names the key doing it.
    """
    assert _drawn(builder, "btn_scripted") == "SET BY A SCRIPT"
    assert _open(builder, "btn_scripted") is None
    body = builder.locator("body")
    assert "ui.btn_scripted.label" in body.inner_text()
    # And nothing was written on the way past.
    assert _stored(builder, "btn_scripted")["label"] == "Authored Word"


def test_escape_abandons_the_edit(builder: Page) -> None:
    assert _open(builder, "btn_plain") == "Mute"
    builder.keyboard.type("Discarded")
    builder.keyboard.press("Escape")
    builder.wait_for_timeout(400)
    assert _stored(builder, "btn_plain")["label"] == "Mute"
    assert _drawn(builder, "btn_plain") == "Mute"


def test_a_double_click_never_moves_the_element(builder: Page) -> None:
    """The first press of a double-click arms a drag.

    The commit only asks whether the box CHANGED, not whether the pointer
    travelled, so without arbitration a hand tremor between the two clicks
    writes a placement and an undo entry on the way to opening the editor.
    """
    before = builder.evaluate(
        """() => {
            const w = document.querySelector('iframe').contentWindow;
            const p = w.__openavcPanel;
            const pg = p.uiDef.pages.find(x => x.id === p.currentPage);
            return JSON.stringify(pg.layouts[0].placements);
        }"""
    )
    for element_id in ("btn_plain", "lbl_bound", "btn_look", "grp"):
        _open(builder, element_id)
        builder.keyboard.press("Escape")
        builder.wait_for_timeout(200)
    after = builder.evaluate(
        """() => {
            const w = document.querySelector('iframe').contentWindow;
            const p = w.__openavcPanel;
            const pg = p.uiDef.pages.find(x => x.id === p.currentPage);
            return JSON.stringify(pg.layouts[0].placements);
        }"""
    )
    assert before == after


# --- The caption-only family ---------------------------------------------
#
# A dropdown, a gauge and a fader draw a caption AND a pile of other text: the
# options, the value readout, the scale marks. Only the caption is the
# author's to retype here, so only the caption may take the caret.

CAPTION_CASES = [
    ("sel_input", "Source", "Laptop"),      # its options must stay put
    ("gau_temp", "Rack Temp", "31.4"),      # its readout must stay put
    ("fad_mic", "Mic 1", "-25.0 dB"),       # its readout and scale must stay put
]


@pytest.mark.parametrize("element_id,caption,untouched", CAPTION_CASES)
def test_only_the_caption_takes_the_caret(
    builder: Page, element_id: str, caption: str, untouched: str
) -> None:
    drawn_before = _drawn(builder, element_id)
    assert untouched in drawn_before
    # The caret opens on the caption alone, not on everything the control draws.
    assert _open(builder, element_id) == caption
    still = builder.evaluate(
        """(id) => {
            const doc = document.querySelector('iframe').contentDocument;
            const el = doc.querySelector(`[data-element-id="${id}"]`);
            const host = doc.querySelector('[data-avc-text-editing]');
            return { hostIsWholeControl: host === el, drawn: el.innerText };
        }""",
        element_id,
    )
    # And the rest of the control is still standing WHILE the edit is open.
    # Before the renderer marked its caption node, the editor fell back to the
    # element itself: opening a fader replaced the track, thumb, scale and
    # readout with the caption text, and restoring it from saved HTML handed
    # back nodes without the listeners that made it draggable.
    assert still["hostIsWholeControl"] is False
    assert untouched in still["drawn"]
    _type_and_commit(builder, "Renamed")
    assert _stored(builder, element_id)["label"] == "Renamed"
    assert untouched in _drawn(builder, element_id)


@pytest.mark.parametrize("element_id", ["cam_wide"])
def test_the_excluded_controls_are_not_offered(builder: Page, element_id: str) -> None:
    """Not every control that draws words is edited here.

    `camera_preset` joins `preset_number` and `label` with a newline in one
    text node, and `page_nav` falls back to its target page id, so committing
    what is on screen would freeze a copy of a name that should follow the
    page. A camera preset used to slip in anyway whenever it carried a look
    binding, because the evaluator recorded a source its renderer never
    composed on its own — which is why the set is now explicit and checked at
    both doors.
    """
    assert _open(builder, element_id) is None
    before = _stored(builder, element_id)
    builder.keyboard.type("Should not land")
    builder.wait_for_timeout(300)
    assert _stored(builder, element_id) == before


def test_a_master_elements_words_are_editable_too(builder: Page) -> None:
    """A master is drawn on every page, so its label is the most-used text there is.

    Its hit box is a different component from a page element's, which is how it
    came to have no double-click at all while every other control had one. It
    also writes somewhere else: masters live in their own list, under their own
    undo scope, not in the page.
    """
    assert _open(builder, "mst_home") == "Home"
    _type_and_commit(builder, "Main Menu")
    stored = builder.evaluate(
        """() => {
            const w = document.querySelector('iframe').contentWindow;
            return w.__openavcPanel.uiDef.master_elements.find(m => m.id === 'mst_home');
        }"""
    )
    assert stored["label"] == "Main Menu"
    assert _drawn(builder, "mst_home") == "Main Menu"


def test_a_named_nav_button_edits_its_label(builder: Page) -> None:
    assert _open(builder, "nav_named") == "Back"
    _type_and_commit(builder, "Go Back")
    stored = _stored(builder, "nav_named")
    assert stored["label"] == "Go Back"
    assert stored["target_page"] == "main"


def test_an_unnamed_nav_button_never_commits_its_target_page_id(builder: Page) -> None:
    """It draws "main" because it has no label. That id is not authored text.

    Opening it shows an empty box with the id behind it as a placeholder, so
    pressing Enter without typing writes nothing. Committing what was on screen
    would freeze a copy of a name that should keep following the page: rename
    the page afterwards and the button would still say the old one.
    """
    assert _drawn(builder, "nav_unnamed") == "main"
    assert _open(builder, "nav_unnamed") == ""
    builder.keyboard.press("Enter")
    builder.wait_for_timeout(600)
    stored = _stored(builder, "nav_unnamed")
    assert "label" not in stored or not stored.get("label")
    assert _drawn(builder, "nav_unnamed") == "main"
    # Typing, though, creates it.
    assert _open(builder, "nav_unnamed") == ""
    _type_and_commit(builder, "Home")
    assert _stored(builder, "nav_unnamed")["label"] == "Home"
