"""The custom-control review, one check at a time, asserted on its sentence.

The message IS the deliverable here, exactly as it is in ``page_review``: the
reader is a model that has just written a file and is about to report it
finished, or a person looking at an editor. "Bad reference" teaches nothing;
"loads 'https://cdn...' over the internet. A panel on a wall may have no
internet at all" gets fixed.

Every check in here earns its place by catching something that fails
**specifically in this environment**, so the tests are written the same way:
each one says what goes wrong in a real space if the check is missing.

Everything here is invented -- a fictional control in a fictional project. This
tests a platform capability, not any product.
"""

from __future__ import annotations

import pytest

from openavc.core.custom_ui_review import (
    STYLESHEET,
    Finding,
    review_control,
    review_file,
    review_stylesheet,
    stylesheet_class_names,
)

ENTRY = "room_map/index.html"


def _messages(findings: list[Finding], kind: str) -> list[str]:
    return [f.message for f in findings if f.kind == kind]


def _kinds(findings: list[Finding]) -> set[str]:
    return {f.kind for f in findings}


# --- References that do not survive the room -------------------------------


def test_a_cdn_script_is_named_with_what_happens_in_a_room():
    """The single most likely thing a model writes, and it renders as nothing.

    A room with no internet is normal in this industry -- a locked-down campus
    VLAN, a courtroom, a ship. Nothing else in the product catches this: the
    file saves, the panel loads it, and one script tag silently does not arrive.
    """
    source = '<script src="https://cdn.example.com/chart.min.js"></script>'
    findings = review_file(ENTRY, source, ui_files={ENTRY})

    assert len(findings) == 1
    message = findings[0].message
    assert findings[0].kind == "remote_reference"
    assert "https://cdn.example.com/chart.min.js" in message
    assert "no internet" in message
    assert "ui/" in message


def test_a_protocol_relative_reference_counts_as_remote():
    """`//fonts.example.com/x.css` inherits the page's scheme and still leaves
    the building."""
    findings = review_file(ENTRY, '<link href="//fonts.example.com/i.css">', ui_files={ENTRY})
    assert _kinds(findings) == {"remote_reference"}


def test_an_absolute_path_is_named_for_where_it_breaks():
    """It works on the LAN, which is exactly why it ships. It breaks through the
    cloud tunnel, where nobody is standing when it does."""
    findings = review_file(ENTRY, '<img src="/logo.png">', ui_files={ENTRY})

    assert _kinds(findings) == {"absolute_path"}
    assert "cloud tunnel" in findings[0].message


def test_a_call_to_the_platform_api_says_what_to_do_instead():
    """The frame carries no credential, so this is a 401 and nothing else.

    Naming the alternative is the whole value: a model that only learns the
    call fails will try a different URL.
    """
    findings = review_file(ENTRY, "fetch('/api/status').then(r => r.json())", ui_files={ENTRY})

    assert _kinds(findings) == {"uncredentialed_api_call"}
    assert "openavc:action" in findings[0].message


def test_a_relative_reference_to_a_file_nobody_wrote_lists_what_is_there():
    """A control is a folder, and half of writing one is getting the names right."""
    findings = review_file(
        ENTRY, '<script src="map.js"></script>', ui_files={ENTRY, "room_map/style.css"},
    )

    assert _kinds(findings) == {"dangling_reference"}
    assert "map.js" in findings[0].message
    assert "room_map/style.css" in findings[0].message


def test_a_relative_reference_that_exists_is_silent():
    findings = review_file(
        ENTRY, '<script src="map.js"></script>', ui_files={ENTRY, "room_map/map.js"},
    )
    assert findings == []


def test_a_reference_resolves_against_the_file_that_makes_it():
    """`../shared/util.js` from a control's own folder is a normal thing to
    write, and comparing it raw against the listing would call it missing."""
    findings = review_file(
        ENTRY, '<script src="../shared/util.js"></script>',
        ui_files={ENTRY, "shared/util.js"},
    )
    assert findings == []


def test_a_query_string_does_not_make_a_file_missing():
    findings = review_file(
        ENTRY, '<img src="logo.png?v=2">', ui_files={ENTRY, "room_map/logo.png"},
    )
    assert findings == []


def test_a_data_uri_is_not_a_missing_file():
    source = '<img src="data:image/png;base64,iVBORw0KGgo=">'
    assert review_file(ENTRY, source, ui_files={ENTRY}) == []


def test_a_reference_in_a_comment_is_not_a_reference():
    """A control that documents where its design came from is not loading it."""
    source = "<!-- based on https://example.com/pattern -->\n<div>ok</div>"
    assert review_file(ENTRY, source, ui_files={ENTRY}) == []


def test_no_opinion_about_files_never_becomes_a_warning():
    """A caller that cannot list the folder must not turn every control into a
    missing-file report -- the same rule every injected lookup follows."""
    findings = review_file(ENTRY, '<script src="map.js"></script>', ui_files=None)
    assert findings == []


# --- What the sandbox makes fatal ------------------------------------------


@pytest.mark.parametrize(
    "source,named",
    [
        ("localStorage.setItem('k', 1)", "localStorage"),
        ("sessionStorage.getItem('k')", "sessionStorage"),
        ("const db = indexedDB.open('x')", "indexedDB"),
        ("document.cookie = 'a=b'", "document.cookie"),
    ],
)
def test_storage_that_throws_in_an_opaque_origin_is_named(source, named):
    """These do not degrade -- they raise, inside a frame nothing can see into.

    The control stops drawing and the only trace is a console message on a wall
    panel with no console. This check exists purely because the sandbox is
    ``allow-scripts`` and nothing else, and nothing else in the product would
    ever catch it.
    """
    findings = review_file("room_map/app.js", source, ui_files=None)

    assert _kinds(findings) == {"sandbox_fatal_api"}
    assert named in findings[0].message
    assert "var." in findings[0].message  # says where state should live instead


def test_reaching_for_the_panels_window_is_named_with_the_one_door():
    findings = review_file("room_map/app.js", "const u = window.parent.location.href", ui_files=None)

    assert _kinds(findings) == {"frame_escape"}
    assert "parent.postMessage" in findings[0].message


def test_the_documented_bridge_call_is_not_an_escape():
    """``parent.postMessage`` is the API. A check that flagged it would make the
    review worse than useless -- every correct control would trip it."""
    source = "parent.postMessage({type: 'openavc:activity'}, '*')"
    assert review_file("room_map/app.js", source, ui_files=None) == []


def test_an_ordinary_property_called_parent_is_not_an_escape():
    """A control that walks its own DOM is not reaching out of the frame."""
    source = "const box = node.parent.getBoundingClientRect()"
    assert review_file("room_map/app.js", source, ui_files=None) == []


@pytest.mark.parametrize(
    "source",
    [
        "const parent = document.getElementById('root');\nparent.appendChild(el);",
        "let parent;\nparent.replaceChildren();",
        "var parent = host;\nparent.classList.add('on');",
        "const {parent, label} = opts;\nparent.append(label);",
        "const [parent] = boxes;\nparent.append(el);",
        "function chip(parent, label) { parent.appendChild(tag(label)); }",
        "const chip = (parent, label) => { parent.textContent = label; };",
        "boxes.forEach(parent => parent.classList.add('fault'));",
        "try { draw() } catch (parent) { parent.report() }",
    ],
)
def test_a_control_that_declares_its_own_parent_is_not_escaping(source):
    """The false positive that cost the first real AI-authored control a write.

    ``parent`` is an ordinary name for the box you are about to append to, and
    a control that binds it is reading its own variable, not the panel's window.
    The pattern cannot demand ``window.`` to tell them apart, because the one
    documented way out of the frame is bare ``parent.postMessage`` -- so a local
    binding anywhere in the file is what silences the bare form.
    """
    assert review_file("room_map/app.js", source, ui_files=None) == []


def test_declaring_a_parent_does_not_excuse_reaching_for_the_real_one():
    """The half that keeps the check: ``window.parent`` names the global outright.

    A control is free to have its own ``parent`` and still try to read the
    panel's session out of the window above it. That still fires.
    """
    source = "function chip(parent) { parent.append(el); }\nconst u = window.parent.location.href;"
    findings = review_file("room_map/app.js", source, ui_files=None)

    assert _kinds(findings) == {"frame_escape"}
    assert "window.parent.location" in findings[0].message


# --- The whole control -----------------------------------------------------


def test_a_control_with_no_bridge_is_told_it_will_never_react():
    findings = review_control(ENTRY, {ENTRY: "<div>Room map</div>"})

    assert "no_bridge" in _kinds(findings)
    assert "openavc:init" in _messages(findings, "no_bridge")[0]


def test_a_control_with_no_error_reporting_is_told_what_a_break_looks_like():
    """Without the one line, a throw is a blank rectangle and silence."""
    findings = review_control(ENTRY, {ENTRY: "<script>window.addEventListener('message', e => e)</script>"})

    assert "no_error_report" in _kinds(findings)
    assert "openavc:error" in _messages(findings, "no_error_report")[0]


def test_a_bridge_in_the_control_s_own_script_file_counts():
    """A control that keeps its JavaScript in a second file is just as correct,
    and reporting it bridgeless would teach the model to inline everything."""
    sources = {
        ENTRY: '<script src="app.js"></script>',
        "room_map/app.js": (
            "window.addEventListener('message', e => { if (e.data.type === 'openavc:init') draw(e.data); });\n"
            "window.onerror = m => parent.postMessage({type: 'openavc:error', message: String(m)}, '*');"
        ),
    }
    findings = review_control(ENTRY, sources)

    assert "no_bridge" not in _kinds(findings)
    assert "no_error_report" not in _kinds(findings)


def test_a_grant_the_control_never_names_is_reported_with_the_ids():
    """Over-granting is the risk that belongs to markup somebody else wrote.

    It is also the one thing here that is exactly detectable: a control that
    never writes the id cannot be using it. The Builder's **Can reach** section
    is where a human sees this; this is where the assistant does.
    """
    sources = {ENTRY: "<script>/* openavc:init openavc:error */ send('lights')</script>"}
    findings = review_control(
        ENTRY, sources, holder="element 'room_map'", granted=("lights", "dsp1"),
    )

    assert "over_granted" in _kinds(findings)
    message = _messages(findings, "over_granted")[0]
    assert "element 'room_map'" in message
    assert "'dsp1'" in message
    assert "'lights'" not in message  # it IS used, so it is not over-granted


def test_a_grant_the_control_uses_is_silent():
    sources = {ENTRY: "<script>/* openavc:init openavc:error */ cmd('dsp1', 'mute')</script>"}
    findings = review_control(ENTRY, sources, granted=("dsp1",))
    assert "over_granted" not in _kinds(findings)


def test_a_page_sized_in_pixels_is_told_where_the_content_goes():
    """Content that does not fit scrolls inside the box, which on a wall is a
    scrollbar somebody has to drag and content nobody can reach."""
    sources = {ENTRY: "<style>body { margin: 0; width: 900px; height: 600px }</style>"}
    findings = review_control(ENTRY, sources)

    assert "fixed_pixel_size" in _kinds(findings)
    assert "scrolls out of reach" in _messages(findings, "fixed_pixel_size")[0]


def test_a_page_without_margin_zero_is_told_about_the_gap():
    sources = {ENTRY: "<style>body { font-family: sans-serif }</style>"}
    findings = review_control(ENTRY, sources)

    assert "page_margin" in _kinds(findings)
    assert "margin: 0" in _messages(findings, "page_margin")[0]


def test_the_sizing_rule_reads_the_sheet_the_page_loads():
    """A control keeps its CSS in a second file more often than not, and a
    review that only read the HTML would report a margin sitting in style.css
    as missing on every save."""
    sources = {
        ENTRY: '<link rel="stylesheet" href="style.css">',
        "room_map/style.css": "html, body { margin: 0; height: 100% }",
    }
    findings = review_control(ENTRY, sources)

    assert "page_margin" not in _kinds(findings)


def test_a_file_that_is_not_a_page_gets_no_whole_control_checks():
    """`map.js` is part of a control, not a control. Asking it for a bridge
    would report the same missing bridge twice, once per file."""
    assert review_control("room_map/map.js", {"room_map/map.js": "const x = 1"}) == []


def test_a_clean_control_says_nothing_at_all():
    """The silence half. A review that flags every control teaches the reader to
    skip the field, which costs more than the checks buy."""
    sources = {
        ENTRY: (
            "<!DOCTYPE html><html><head><style>html, body { margin: 0; height: 100% }</style>"
            "</head><body><div id='level'></div><script>"
            "window.onerror = m => parent.postMessage({type: 'openavc:error', message: String(m)}, '*');"
            "window.addEventListener('message', e => {"
            "  if (e.data.type === 'openavc:init') draw(e.data.state['device.dsp1.level']);"
            "});"
            "function draw(v) { document.getElementById('level').textContent = v; }"
            "</script></body></html>"
        ),
    }
    assert review_file(ENTRY, sources[ENTRY], ui_files={ENTRY}) == []
    assert review_control(ENTRY, sources, granted=("dsp1",)) == []


# --- The project stylesheet ------------------------------------------------


def test_an_unclosed_rule_says_what_it_swallows():
    findings = review_stylesheet(".a { color: red\n.b { color: blue }")

    assert _kinds(findings) == {"unclosed_rule"}
    assert findings[0].path == STYLESHEET


def test_a_rule_on_a_bare_element_says_why_it_is_not_a_suggestion():
    """The panel re-marks every declaration here !important so that the obvious
    rule works at all -- which also means ``button { display: none }`` blacks
    out every button in the project rather than losing to the theme."""
    findings = review_stylesheet("button { display: none }")

    assert "bare_element_selector" in _kinds(findings)
    assert "!important" in _messages(findings, "bare_element_selector")[0]
    assert "css_class" in _messages(findings, "bare_element_selector")[0]


def test_a_global_selector_is_named_as_the_whole_panel():
    findings = review_stylesheet(":root { --panel-bg: #000 }")
    assert "global_selector" in _kinds(findings)


def test_a_class_rule_is_exactly_what_the_feature_is_for():
    assert review_stylesheet(".brand-button { background: #8AB493 }") == []


def test_a_class_an_element_names_and_the_sheet_never_defines():
    """Typing into the dark: the class lands on the node, the sheet says nothing
    about it, and the only symptom is that nothing happened."""
    findings = review_stylesheet(
        ".brand { color: red }", used={"brand": ["element 'a'"], "brnad": ["element 'b'"]},
    )

    assert "undefined_class" in _kinds(findings)
    assert "brnad" in _messages(findings, "undefined_class")[0]


def test_a_class_nothing_carries_is_mentioned_once():
    findings = review_stylesheet(".unused { color: red }", used={})
    assert "unused_class" in _kinds(findings)


def test_class_usage_is_not_guessed_at_when_the_caller_cannot_say():
    """None is "no opinion" here too: without the project, every class in the
    sheet would read as dead weight."""
    findings = review_stylesheet(".brand { color: red }", used=None)
    assert findings == []


def test_a_remote_font_in_the_stylesheet_is_named():
    findings = review_stylesheet('@import url("https://fonts.example.com/i.css");')
    assert "remote_reference" in _kinds(findings)


# --- The class scan (the one piece that also exists in TypeScript) ---------


def test_class_names_come_back_in_the_order_the_sheet_mentions_them():
    css = ".b { color: red } .a { color: blue } .b { color: green }"
    assert stylesheet_class_names(css) == ["b", "a"]


def test_a_declaration_is_never_read_as_a_class():
    """`border-radius: 0.5rem` holds a dot followed by a word, and reading
    declarations as selectors is how ``5rem`` ends up offered as a class."""
    assert stylesheet_class_names(".card { border-radius: 0.5rem }") == ["card"]


def test_a_class_inside_a_media_query_still_counts():
    assert stylesheet_class_names("@media (min-width: 30rem) { .wide { display: block } }") == ["wide"]
