"""The Builder and the AI door must reach identical verdicts about one file.

Two implementations of the same arithmetic exist on purpose: the AI writes
blind through ``openavc/ui/page_review.py``, and a human drags a box in the
Builder, which cannot call Python. They read the same measured numbers, and the
tables neither of them owns are generated rather than copied -- but the
arithmetic over those numbers is written twice, and the moment one side is
edited alone the two disagree about the same project while each looks correct
on its own. Nobody would notice: the AI would be warned about something the
canvas shows as fine, or the canvas would badge something the AI was told was
acceptable, and the number that gets believed would be whichever one was read.

So this pushes a corpus through both and compares **message for message**.
Byte-exact, not just finding-for-finding: the sentence is the deliverable. A
warning that says "too small" teaches nothing and gets guessed at; one that says
"28px wide, needs 29px, so give it at least 2.27% of its container" is checkable
and gets acted on. If the two sides ever phrase the same defect differently, one
of them has been edited and the other has not.

Every device and element here is invented. This tests a platform capability.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

from openavc.core.project_loader import ProjectConfig
from openavc.ui.page_review import review_master_element, review_page

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_review_parity_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "uiBuilderHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"

# 1280 x 800 is the reference, so a percentage states itself in pixels: 1% of
# the width is 12.8px and 1% of the height is 8px.
PX_W = 12.8
PX_H = 8.0


def _box(x, y, w_px, h_px) -> dict:
    return {"x": x, "y": y, "w": w_px / PX_W, "h": h_px / PX_H}


def _pct_box(x, y, w, h) -> dict:
    return {"x": x, "y": y, "w": w, "h": h}


def _landscape(placements, **kwargs) -> dict:
    return {
        "id": "landscape", "orientation": "landscape", "primary": True,
        "placements": placements, "hidden": [], **kwargs,
    }


def _portrait(placements, **kwargs) -> dict:
    return {
        "id": "portrait", "orientation": "portrait", "primary": True,
        "placements": placements, "hidden": [], **kwargs,
    }


# The same arithmetic turned: 1% of an 800px width is 8px, 1% of a 1280px
# height is 12.8px. Exactly the swap that was missing.
P_PX_W = 8.0
P_PX_H = 12.8


def _p_box(x, y, w_px, h_px) -> dict:
    return {"x": x, "y": y, "w": w_px / P_PX_W, "h": h_px / P_PX_H}


def _page(page_id, elements, layouts) -> dict:
    return {"id": page_id, "name": page_id.title(), "elements": elements, "layouts": layouts}


def _project(pages, masters=(), theme_overrides=None) -> dict:
    return {
        "project": {"id": "parity", "name": "Parity", "description": ""},
        "ui": {
            "settings": {"theme_overrides": theme_overrides or {}},
            "pages": list(pages),
            "master_elements": list(masters),
        },
    }


# --- The corpus ------------------------------------------------------------
#
# One case per behaviour rather than one big page, so a parity failure names the
# check that drifted instead of "something in the project".

CASES: dict[str, dict] = {}

# Every type that has a floor, sized one pixel under it on each axis that has
# one. This is the check that did not exist on the Builder side at all.
CASES["starvation_every_type"] = _project([
    _page(
        "main",
        [
            {"id": "led_labelled", "type": "status_led", "label": "Ready"},
            {"id": "led_bare", "type": "status_led"},
            {"id": "led_bound", "type": "status_led",
             "bindings": {"show": {"value": {"key": "device.acme.name"}}}},
            {"id": "fdr", "type": "fader"},
            {"id": "sld", "type": "slider"},
            {"id": "sld_thick", "type": "slider", "thumb_size": 6},
            {"id": "lst", "type": "list"},
            {"id": "lst_tall", "type": "list", "item_height": 6},
            # A matrix's floor is a function of how many entries it resolves to,
            # so it needs entries before it has one to breach.
            {"id": "mtx", "type": "matrix", "matrix_config": {
                "sources": {"from": {"count": 4}},
                "destinations": {"from": {
                    "count": 4, "route_key": "device.acme.output.*.input"}},
            }},
            {"id": "meter", "type": "level_meter"},
            {"id": "pad", "type": "keypad"},
            {"id": "sel", "type": "select"},
            {"id": "txt", "type": "text_input"},
            # No floor at all: limited by their text, which is not a minimum box.
            {"id": "btn", "type": "button", "label": "Go"},
            {"id": "lbl", "type": "label", "text": "Hello"},
            {"id": "img", "type": "image", "src": "assets://logo.png"},
            {"id": "dial", "type": "gauge"},
        ],
        [_landscape({
            "led_labelled": _box(0, 0, 28, 44),
            "led_bare": _box(10, 0, 28, 44),
            "led_bound": _box(20, 0, 28, 44),
            "fdr": _box(30, 0, 60, 90),
            "sld": _box(40, 0, 60, 70),
            "sld_thick": _box(50, 0, 100, 110),
            "lst": _box(60, 0, 20, 70),
            "lst_tall": _box(70, 0, 40, 100),
            "mtx": _box(0, 20, 319, 242),
            "meter": _box(20, 20, 8, 60),
            "pad": _box(30, 20, 60, 180),
            "sel": _box(40, 20, 40, 44),
            "txt": _box(50, 20, 40, 44),
            # Comfortably over the finger minimum, so the silence below means
            # "this type has no contents floor" rather than "it happened to be
            # big enough to touch".
            "btn": _box(60, 20, 60, 60),
            "lbl": _box(70, 20, 60, 60),
            "img": _box(80, 20, 60, 60),
            "dial": _box(90, 20, 60, 60),
        })],
    ),
])

# The one theme value any minimum moves with: a slider's thumb. The element
# wins, then the project's element_defaults, then the 44px default.
CASES["slider_thumb_from_theme"] = _project(
    [_page(
        "main",
        [
            {"id": "sld_default", "type": "slider"},
            {"id": "sld_own", "type": "slider", "thumb_size": 2},
        ],
        [_landscape({
            "sld_default": _box(0, 0, 70, 80),
            "sld_own": _box(20, 0, 70, 80),
        })],
    )],
    theme_overrides={"element_defaults": {"slider": {"thumb_size": 5}}},
)

# All four sides, at page level and inside a container, plus a box that ends
# exactly on the edge and must stay quiet.
CASES["overhangs"] = _project([
    _page(
        "main",
        [
            {"id": "off_left", "type": "button", "label": "L"},
            {"id": "off_top", "type": "button", "label": "T"},
            {"id": "off_right", "type": "button", "label": "R"},
            {"id": "off_bottom", "type": "button", "label": "B"},
            {"id": "off_both", "type": "button", "label": "X"},
            {"id": "flush", "type": "button", "label": "F"},
            {"id": "holder", "type": "group"},
            {"id": "kid_out", "type": "button", "parent": "holder", "label": "K"},
        ],
        [_landscape({
            "off_left": _pct_box(-4, 0, 10, 10),
            "off_top": _pct_box(10, -3.5, 10, 10),
            "off_right": _pct_box(95, 0, 10, 10),
            "off_bottom": _pct_box(30, 95, 10, 10),
            "off_both": _pct_box(-2, -2, 10, 10),
            "flush": _pct_box(50, 90, 50, 10),
            "holder": _pct_box(0, 40, 40, 40),
            "kid_out": _pct_box(70, 10, 50, 20),
        })],
    ),
])

# Sibling collisions, and the three things that look like one and are not: a
# container holding its own child, two boxes under different containers, and a
# pair that grazes by less than a pixel.
CASES["overlaps"] = _project([
    _page(
        "main",
        [
            {"id": "a", "type": "button", "label": "A"},
            {"id": "b", "type": "button", "label": "B"},
            {"id": "c", "type": "button", "label": "C"},
            {"id": "graze", "type": "button", "label": "G"},
            {"id": "left_box", "type": "group"},
            {"id": "right_box", "type": "group"},
            {"id": "left_kid", "type": "button", "parent": "left_box", "label": "LK"},
            {"id": "right_kid", "type": "button", "parent": "right_box", "label": "RK"},
        ],
        [_landscape({
            "a": _pct_box(0, 0, 20, 20),
            "b": _pct_box(10, 10, 20, 20),
            "c": _pct_box(15, 15, 20, 20),
            "graze": _pct_box(20, 0, 20, 20.001),
            "left_box": _pct_box(0, 50, 50, 40),
            "right_box": _pct_box(50, 50, 50, 40),
            # Both fill their own container, so in page space they sit side by
            # side. Different parents, so never compared.
            "left_kid": _pct_box(0, 0, 100, 100),
            "right_kid": _pct_box(0, 0, 100, 100),
        })],
    ),
])

# The nine types that publish no floor still cannot be drawn at 6x4px. Sized
# either side of the degenerate threshold, and once with a floor present, so the
# silence below means "this check knows to stay out of the way".
CASES["degenerate_boxes"] = _project([
    _page(
        "main",
        [
            {"id": "gauge_speck", "type": "gauge"},
            {"id": "clock_speck", "type": "clock"},
            {"id": "btn_hairline", "type": "button", "label": "H"},
            {"id": "img_flat", "type": "image"},
            # Exactly on the threshold on both axes, so it draws.
            {"id": "gauge_just", "type": "gauge"},
            # A type WITH a floor is the starvation check's business, not this
            # one's, however small it gets.
            {"id": "led_speck", "type": "status_led"},
        ],
        [_landscape({
            "gauge_speck": _pct_box(0, 0, 0.5, 0.5),
            "clock_speck": _pct_box(5, 0, 0.5, 0.5),
            "btn_hairline": _pct_box(10, 0, 20, 0.5),
            "img_flat": _pct_box(35, 0, 0.5, 20),
            "gauge_just": _box(40, 0, 10, 10),
            "led_speck": _box(50, 0, 4, 4),
        })],
    ),
])

# `style` measurements are rem (px / 14), and the number an author reaches for is
# the pixel one. Reported only where the result cannot fit the element, so the
# same value on a box big enough to hold it stays silent.
CASES["style_in_pixels"] = _project([
    _page(
        "main",
        [
            {"id": "shouty", "type": "label", "text": "Heading",
             "style": {"font_size": 24, "padding": 8, "border_radius": 12}},
            {"id": "boxed", "type": "label", "text": "Edged",
             "style": {"border_width": 3, "letter_spacing": 9}},
            {"id": "sideways", "type": "label", "text": "Wide",
             "style": {"padding_horizontal": 6, "padding_vertical": 0.5}},
            # The same numbers on a box that can hold them. Nothing to say.
            {"id": "roomy", "type": "label", "text": "Big",
             "style": {"font_size": 24, "padding": 8, "border_radius": 12}},
            # A radius past the box draws a legal pill, and a margin is outside
            # a box the layout already placed. Neither is a defect.
            {"id": "pill", "type": "label", "text": "Pill",
             "style": {"border_radius": 40, "margin": 30}},
        ],
        [_landscape({
            "shouty": _box(0, 0, 102, 32),
            "boxed": _box(20, 0, 102, 32),
            "sideways": _box(40, 0, 102, 32),
            "roomy": _pct_box(0, 20, 40, 60),
            "pill": _box(60, 0, 102, 32),
        })],
    ),
])


def _when(**cond) -> dict:
    return {"bindings": {"show": {"visible_when": cond}}}


# Overlap reporting, which is the check most able to drown out the others: it is
# the only O(n^2) one, and the only one that fires on a correct page. Everything
# here is a `group` so nothing has a floor or a finger minimum to trip, and the
# groups sit far enough apart that each scenario answers on its own.
CASES["overlap_noise"] = _project([
    _page(
        "main",
        [
            # One box over five, which used to be five findings.
            {"id": "huge", "type": "group"},
            {"id": "n1", "type": "group"},
            {"id": "n2", "type": "group"},
            {"id": "n3", "type": "group"},
            {"id": "n4", "type": "group"},
            {"id": "n5", "type": "group"},
            # Out of the page AND lying on a neighbour. Both are said: pulling
            # it back inside does not move it off `settled`, so treating the
            # collision as a consequence of the overflow would hide a real one.
            {"id": "runaway", "type": "group"},
            {"id": "settled", "type": "group"},
            # A tab strip: same key, different values, never both on screen.
            {"id": "tab_audio", "type": "group", **_when(key="var.tab", value="audio")},
            {"id": "tab_video", "type": "group", **_when(key="var.tab", value="video")},
            # Gated against ungated proves nothing, so this still warns.
            {"id": "gated", "type": "group", **_when(key="var.mode", value="x")},
            {"id": "ungated", "type": "group"},
            # Same key, and both hold at once for anything at or above zero.
            {"id": "warm", "type": "group",
             **_when(key="var.level", operator="gte", value=0)},
            {"id": "any_level", "type": "group",
             **_when(key="var.level", operator="gte", value=-10)},
            # Two bounds with no room between them.
            {"id": "cold", "type": "group",
             **_when(key="var.level", operator="lt", value=0)},
            {"id": "hot", "type": "group",
             **_when(key="var.level", operator="gte", value=0)},
            # Every branch of one contradicts every branch of the other.
            {"id": "modes_ab", "type": "group", "bindings": {"show": {"visible_when": {
                "any": [{"key": "var.mode", "value": "a"}, {"key": "var.mode", "value": "b"}],
            }}}},
            {"id": "modes_cd", "type": "group", "bindings": {"show": {"visible_when": {
                "any": [{"key": "var.mode", "value": "c"}, {"key": "var.mode", "value": "d"}],
            }}}},
            {"id": "on_when", "type": "group", **_when(key="var.on", operator="truthy")},
            {"id": "off_when", "type": "group", **_when(key="var.on", operator="falsy")},
        ],
        [_landscape({
            "huge": _pct_box(0, 0, 30, 30),
            "n1": _pct_box(2, 2, 4, 4),
            "n2": _pct_box(8, 2, 4, 4),
            "n3": _pct_box(14, 2, 4, 4),
            "n4": _pct_box(20, 2, 4, 4),
            "n5": _pct_box(2, 10, 4, 4),
            "runaway": _pct_box(90, 0, 20, 20),
            "settled": _pct_box(92, 2, 4, 4),
            "tab_audio": _pct_box(35, 40, 12, 12),
            "tab_video": _pct_box(35, 40, 12, 12),
            "gated": _pct_box(50, 40, 12, 12),
            "ungated": _pct_box(50, 40, 12, 12),
            "warm": _pct_box(65, 40, 12, 12),
            "any_level": _pct_box(65, 40, 12, 12),
            "cold": _pct_box(35, 60, 12, 12),
            "hot": _pct_box(35, 60, 12, 12),
            "modes_ab": _pct_box(50, 60, 12, 12),
            "modes_cd": _pct_box(50, 60, 12, 12),
            "on_when": _pct_box(65, 60, 12, 12),
            "off_when": _pct_box(65, 60, 12, 12),
        })],
    ),
])

# An element the primary arrangement never positions. The renderer fills the
# parent edge to edge and nothing in the file says so.
CASES["no_placement"] = _project([
    _page(
        "main",
        [
            {"id": "placed", "type": "button", "label": "P"},
            {"id": "unplaced", "type": "button", "label": "U"},
            {"id": "holder", "type": "group"},
            {"id": "unplaced_kid", "type": "label", "parent": "holder", "text": "K"},
        ],
        [_landscape({
            "placed": _pct_box(0, 0, 20, 20),
            "holder": _pct_box(50, 0, 40, 40),
        })],
    ),
])

# Bindings the renderer for that type never reads, which is the defect class
# with no visible symptom: the element draws and the state key resolves.
CASES["binding_reach"] = _project([
    _page(
        "main",
        [
            {"id": "text_with_look", "type": "label",
             "bindings": {"show": {"look": {"key": "device.acme.online",
                                            "states": {"true": {"label": "ONLINE"},
                                                       "false": {"label": "OFFLINE"}}}}}},
            {"id": "led_with_value", "type": "status_led", "label": "Ok",
             "bindings": {"show": {"value": {"key": "device.acme.level"}}}},
            {"id": "led_with_state_labels", "type": "status_led", "label": "Ok",
             "bindings": {"show": {"look": {"key": "device.acme.online",
                                            "states": {"on": {"color": "#0f0", "label": "UP"}}}}}},
            {"id": "sel_with_state_labels", "type": "select",
             "bindings": {"show": {"look": {"key": "device.acme.mode",
                                            "states": {"a": {"label": "A"}}},
                                   "value": {"key": "device.acme.mode"}}}},
            {"id": "btn_with_state_labels", "type": "button",
             "bindings": {"show": {"look": {"key": "device.acme.online",
                                            "states": {"true": {"label": "ON"}}}}}},
            {"id": "pad_with_everything", "type": "keypad",
             "bindings": {"show": {"value": {"key": "device.acme.code"},
                                   "items": {"key": "device.acme.list"}}}},
            {"id": "list_ok", "type": "list",
             "bindings": {"show": {"items": {"key_pattern": "device.acme.input_*_name"},
                                   "value": {"key": "device.acme.selected"}}}},
            # The same binding spelled the way a value binding is spelled. The
            # items evaluator reads `key_pattern` and nothing else, so this one
            # resolves to the static items and the state list never appears --
            # which is how it sat in this corpus, named "ok", until the check
            # for a slot that names nothing to read went in.
            {"id": "list_key_spelling", "type": "list", "label": "Sources",
             "items": [{"label": "Laptop", "value": "1"}],
             "bindings": {"show": {"items": {"key": "device.acme.inputs"}}}},
            {"id": "visible_only", "type": "image", "src": "assets://bg.png",
             "bindings": {"show": {"visible_when": {"key": "var.admin", "equals": True}}}},
            # A type the panel cannot draw. It answers about the TYPE and says
            # nothing about the binding: a slot reaching a renderer that does
            # not exist is not a second, separate problem.
            #
            # The `plugin:<id>:<type>` spelling is deliberate but NOT a shape
            # that reaches a project: it is the Builder's palette key, and
            # createElement turns it into type: "plugin" with both ids before
            # anything is stored. It is here because it is the most plausible
            # wrong type anyone could write by hand.
            {"id": "unknown_kind", "type": "plugin:acme:widget",
             "bindings": {"show": {"look": {"key": "device.acme.online"}}}},
            # `plugin` is a real type the panel draws, and the one the Builder's
            # palette and the authoring prompt both leave out -- so it has to
            # come back clean here or the check would teach the wrong set.
            {"id": "plugin_ok", "type": "plugin",
             "plugin_id": "acme", "plugin_type": "widget"},
        ],
        [_landscape({
            "text_with_look": _pct_box(0, 0, 20, 10),
            "led_with_value": _pct_box(25, 0, 8, 10),
            "led_with_state_labels": _pct_box(35, 0, 8, 10),
            "sel_with_state_labels": _pct_box(45, 0, 20, 10),
            "btn_with_state_labels": _pct_box(0, 20, 20, 10),
            "pad_with_everything": _pct_box(25, 20, 20, 40),
            "list_ok": _pct_box(50, 20, 20, 40),
            "list_key_spelling": _pct_box(28, 20, 20, 40),
            "visible_only": _pct_box(75, 20, 20, 20),
            "unknown_kind": _pct_box(75, 45, 20, 20),
            "plugin_ok": _pct_box(0, 70, 20, 20),
        })],
    ),
])

# The element-level vocabulary checks: properties the renderer never reads, and
# the matrix's config, which is the one property with no schema at any layer.
#
# Drawn from a real AI-authored panel. The matrix here is exactly what it wrote
# for an 8x8 switcher -- a device id, per-input and per-output objects, a
# state_key on each output -- all of which stored perfectly and drew a 4x4 grid
# whose crosspoints could never light.
CASES["vocabulary"] = _project([
    _page(
        "main",
        [
            # The asymmetry the whole property table exists for: `label` draws
            # on nearly every type, and not on the one named after it.
            {"id": "lbl_wrong", "type": "label", "label": "Room Ready"},
            {"id": "lbl_right", "type": "label", "text": "Room Ready"},
            # Bound instead of static: needs no `text`, and must stay quiet.
            {"id": "lbl_bound", "type": "label",
             "bindings": {"show": {"value": {"key": "device.acme.name"}}}},
            # A label whose words come from its look, both shapes. A label DOES
            # draw show.look's per-state text, so neither is an empty box.
            {"id": "lbl_look_states", "type": "label",
             "bindings": {"show": {"look": {
                 "key": "var.room_state", "default_state": "standby",
                 "states": {"standby": {"label": "Standby"},
                            "active": {"label": "In Use"}}}}}},
            {"id": "lbl_look_binary", "type": "label",
             "bindings": {"show": {"look": {
                 "key": "device.acme.online", "condition": {"equals": "true"},
                 "label_active": "Online", "label_inactive": "Offline"}}}},
            # A look carrying a per-state ICON. The words and the colour are
            # drawn, the icon is not: only the button's evaluator rebuilds the
            # icon layout, and the label's does colour and text and stops.
            {"id": "lbl_look_icon", "type": "label",
             "bindings": {"show": {"look": {
                 "key": "var.room_state", "default_state": "standby",
                 "states": {"standby": {"label": "Standby", "icon": "moon"},
                            "active": {"label": "In Use", "icon": "users"}}}}}},
            # ...and the binary spelling of the same mistake, on a type that
            # takes only colour from a look, so the sentence differs twice over.
            {"id": "led_look_icon", "type": "status_led", "label": "Mic",
             "bindings": {"show": {"look": {
                 "key": "device.acme.online", "condition": {"equals": "true"},
                 "style_active": {"bg_color": "#0f0", "icon": "check"},
                 "style_inactive": {"bg_color": "#f00"}}}}},
            # A binding the renderer reads, that names nothing to read. The
            # half-finished state the Text editor passes through, and the one
            # every other check treats as supplied and stays quiet about.
            {"id": "lbl_keyless", "type": "label", "text": "Room Ready",
             "bindings": {"show": {"value": {"source": "state", "key": ""}}}},
            # The same hole one slot over: a list whose items pattern is blank.
            {"id": "list_keyless", "type": "list", "label": "Sources",
             "items": [{"label": "Laptop", "value": "1"}],
             "bindings": {"show": {"items": {"source": "state", "key_pattern": ""}}}},
            # ...but a macro-progress label names no state key ON PURPOSE.
            {"id": "lbl_macro", "type": "label",
             "bindings": {"show": {"value": {
                 "source": "macro_progress", "macro": "start_meeting",
                 "idle_text": "Ready"}}}},
            # ...but a look carrying only COLOUR still leaves nothing to draw.
            {"id": "lbl_look_colour_only", "type": "label",
             "bindings": {"show": {"look": {
                 "key": "device.acme.online",
                 "states": {"true": {"bg_color": "#0f0"}}}}}},
            # Nothing to draw at all: the shapes that render an empty box.
            {"id": "img_srcless", "type": "image"},
            {"id": "nav_nowhere", "type": "page_nav", "label": "Go"},
            # A property that exists on no type at all. `segments` was in the
            # authoring prompt for a level_meter; the real key is a style one,
            # and its default of 20 makes the wrong write look right.
            {"id": "meter_invented", "type": "level_meter", "segments": 12},
            # The right idea one level too high.
            {"id": "mtx_flat", "type": "matrix", "show_lock": True,
             "matrix_config": {
                 "sources": {"from": {"count": 2}},
                 "destinations": {"from": {
                     "count": 2, "route_key": "device.acme.output.*.input"}},
             }},
            # The authored-by-AI shape, in full. Nothing here is a key the
            # renderer reads, so both axes resolve to nothing as well.
            {"id": "mtx_invented", "type": "matrix", "matrix_config": {
                "device": "acme",
                "inputs": [{"id": 1, "label": "IN 1"}],
                "outputs": [{"id": 1, "label": "OUT 1",
                             "state_key": "device.acme.output.1.input"}],
                "presets": [{"name": "All 1", "macro": "scene_all_1"}],
            }},
            # Configured, sized, and still blind -- and blind on ALL eight, which
            # is where the naming cap earns itself.
            {"id": "mtx_no_feedback", "type": "matrix",
             "matrix_config": {"sources": {"from": {"count": 8}},
                               "destinations": {"from": {"count": 8}}}},
            # Fully correct: this one must come back clean, or the check would
            # be firing on the spelling it is trying to teach. A 4x4 rather than
            # an 8x8 because the floor is a function of the counts now, and an
            # 8x8 does not fit the box the other three sit in -- which is what
            # the three of them are covering.
            {"id": "mtx_ok", "type": "matrix", "matrix_config": {
                "sources": {"from": {"count": 4}},
                "destinations": {"from": {
                    "count": 4, "route_key": "device.acme.output.*.input"}},
                "show_lock": False, "show_mute": False,
            }},
            # A custom control with no page chosen renders an empty box, the
            # same shape as the srcless image above.
            {"id": "custom_fileless", "type": "custom"},
            # ...and one that names its page is correct and must stay quiet,
            # config and all.
            {"id": "custom_ok", "type": "custom",
             "custom_file": "room_map/index.html", "custom_config": {"room": "204"}},
        ],
        [_landscape({
            "lbl_wrong": _pct_box(0, 0, 20, 8),
            "lbl_right": _pct_box(25, 0, 20, 8),
            "lbl_bound": _pct_box(70, 0, 20, 8),
            "lbl_look_states": _pct_box(68, 25, 20, 8),
            "lbl_look_binary": _pct_box(68, 35, 20, 8),
            # The bottom-right band, which nothing else on this page uses --
            # these cases are about what a binding says, and an incidental
            # overlap finding on top of that just makes the diff harder to read.
            "lbl_keyless": _pct_box(66, 68, 20, 8),
            "lbl_macro": _pct_box(66, 78, 20, 8),
            "list_keyless": _pct_box(66, 88, 20, 10),
            "lbl_look_icon": _pct_box(88, 68, 11, 8),
            "led_look_icon": _pct_box(88, 78, 6, 8),
            "lbl_look_colour_only": _pct_box(68, 45, 20, 8),
            "img_srcless": _pct_box(70, 10, 12, 12),
            "nav_nowhere": _pct_box(85, 10, 12, 12),
            "meter_invented": _pct_box(50, 0, 8, 40),
            "mtx_flat": _pct_box(0, 10, 30, 40),
            "mtx_invented": _pct_box(35, 10, 30, 40),
            "mtx_no_feedback": _pct_box(0, 55, 30, 40),
            "mtx_ok": _pct_box(35, 55, 30, 40),
            "custom_fileless": _pct_box(70, 55, 12, 12),
            "custom_ok": _pct_box(85, 55, 12, 12),
        })],
    ),
    # The other half of project format 0.10.0: entries written out one at a
    # time, where a value is opaque and a route key is per destination. Its own
    # page so the geometry above stays where it was measured.
    _page(
        "written",
        [
            # Two sources naming one port. Only reachable now that a value is
            # authored rather than a row number -- and the collision is decided
            # by the SAME comparison that lights a crosspoint, so 'IN1' and 1
            # are the same source however differently they are spelled.
            {"id": "mtx_collide", "type": "matrix", "matrix_config": {
                "sources": [
                    {"value": 1, "label": "Laptop"},
                    {"value": "IN1", "label": "Laptop HDMI"},
                    {"value": 2, "label": "Room PC"},
                ],
                "destinations": [
                    {"value": 1, "label": "Main LCD",
                     "route_key": "device.acme.output.1.input"},
                ],
                "show_lock": False, "show_mute": False,
            }},
            # Half a matrix reporting and half of it blind, which the old
            # per-element route_key_pattern could not express at all: either
            # every crosspoint lit or none did.
            {"id": "mtx_half_blind", "type": "matrix", "matrix_config": {
                "sources": [{"value": 1, "label": "Cam"}, {"value": 2, "label": "PC"}],
                "destinations": [
                    {"value": 1, "label": "Main LCD",
                     "route_key": "device.acme.output.1.input"},
                    {"value": "stream", "label": "Stream",
                     "route": [{"action": "macro", "macro": "start_stream"}]},
                ],
                "show_lock": False, "show_mute": False,
            }},
            # Written out and correct: values a device reports, a key each, a
            # per-destination action override on one of them. Must stay quiet,
            # or the check is firing on the spelling it exists to teach.
            {"id": "mtx_written_ok", "type": "matrix", "matrix_config": {
                "sources": [
                    {"value": 1, "label": "Apple TV"},
                    {"value": "HDMI_A", "label": "Laptop"},
                    {"value": 7, "label": "Room PC",
                     "label_key": "device.acme.input.7.name"},
                ],
                "destinations": [
                    {"value": 1, "label": "Main LCD",
                     "route_key": "device.acme.output.1.input"},
                    {"value": 6, "label": "Confidence",
                     "route_key": "device.acme.output.6.input",
                     "audio_route_key": "device.acme.output.6.audio"},
                    {"value": "rtsp://10.0.0.9/live", "label": "Stream",
                     "route_key": "device.enc.source",
                     "route": [{"action": "macro", "macro": "start_stream"}]},
                ],
                "show_lock": False, "show_mute": False,
            }},
            # Lock buttons backed by nothing. Drawn, pressable, and remembered
            # only by the panel that pressed them -- gone on the next redraw and
            # invisible to every other panel in the space, which is the whole of
            # F10 and is why the lock is opt-in now.
            {"id": "mtx_lock_local", "type": "matrix", "matrix_config": {
                "sources": [{"value": 1, "label": "Cam"}, {"value": 2, "label": "PC"}],
                "destinations": [
                    {"value": 1, "label": "Main LCD",
                     "route_key": "device.acme.output.1.input"},
                    {"value": 2, "label": "Confidence",
                     "route_key": "device.acme.output.2.input",
                     "lock_key": "device.acme.output.2.locked"},
                ],
                "show_lock": True, "show_mute": False,
            }},
            # A tile wall, correct: a lock per destination in a variable the
            # panel is allowed to write, and a source routed by one vocabulary
            # while the device reports another. Must stay quiet on both sides.
            {"id": "mtx_tiles_ok", "type": "matrix", "matrix_style": "tiles",
             "matrix_config": {
                 "sources": [
                     {"value": "0", "label": "Mic", "report_value": "Mic"},
                     {"value": "1", "label": "Line", "report_value": "Line"},
                 ],
                 "destinations": [
                     {"value": 1, "label": "Main LCD",
                      "route_key": "device.acme.output.1.input",
                      "lock_key": "var.mtx_tiles_ok_lock_1"},
                     {"value": 2, "label": "Confidence",
                      "route_key": "device.acme.output.2.input",
                      "lock_key": "var.mtx_tiles_ok_lock_2"},
                     {"value": 3, "label": "Lobby",
                      "route_key": "device.acme.output.3.input",
                      "lock_key": "var.mtx_tiles_ok_lock_3"},
                     {"value": 4, "label": "Stream",
                      "route_key": "device.acme.output.4.input",
                      "lock_key": "var.mtx_tiles_ok_lock_4"},
                 ],
                 "show_lock": True, "show_mute": False,
             }},
        ],
        [_landscape({
            "mtx_collide": _pct_box(0, 0, 30, 40),
            "mtx_half_blind": _pct_box(35, 0, 30, 40),
            "mtx_written_ok": _pct_box(0, 45, 30, 45),
            "mtx_lock_local": _pct_box(70, 0, 28, 40),
            "mtx_tiles_ok": _pct_box(35, 45, 30, 45),
        })],
    ),
])


# A portrait arrangement, measured against a portrait screen.
#
# This case is the only evidence the Q-087 fix works, and it has to be, because
# the bug failed toward ACCEPTING: a portrait page was measured against 1280x800,
# so a control too narrow to touch came back clean on every surface at once and
# nothing already in this corpus was red. Nothing here would trip a single check
# before the fix.
#
# Both halves are exercised: the reference box (a percentage resolves to fewer
# pixels across a portrait screen and more down it) and the tile wall's shape
# (eight destinations are 4x2 in landscape and 2x4 here, so the floor is a
# different rectangle -- 514x150 against 262x290).
CASES["portrait"] = _project([
    _page(
        "main",
        [
            # 5% of 800px is 40px, under the 9mm finger rule. The same 5% of a
            # landscape 1280 is 64px and passes, which is exactly what shipped.
            {"id": "p_narrow", "type": "button", "label": "Mute"},
            # Comfortable on both axes here: the check has to stay capable of
            # silence in portrait, not just of speech.
            {"id": "p_btn_ok", "type": "button", "label": "Go"},
            # A status LED whose 20px dot fits across a portrait width only
            # because the width is stated as a bigger percentage of a smaller
            # screen. Starved if the reference is not turned.
            {"id": "p_led", "type": "status_led", "label": "Ready"},
            # Eight destinations drawn 2 across and 4 down. 400x256px is over
            # the landscape floor and under the portrait one.
            {"id": "p_wall", "type": "matrix", "matrix_style": "tiles",
             "matrix_config": {
                 "sources": [
                     {"value": 1, "label": "Cam"},
                     {"value": 2, "label": "PC"},
                 ],
                 "destinations": [
                     {"value": i, "label": f"Out {i}",
                      "route_key": f"device.acme.output.{i}.input",
                      "lock_key": f"var.p_wall_lock_{i}"}
                     for i in range(1, 9)
                 ],
                 "show_lock": True, "show_mute": False,
             }},
            # The same wall, sized for the shape it is actually drawn in.
            {"id": "p_wall_ok", "type": "matrix", "matrix_style": "tiles",
             "matrix_config": {
                 "sources": [
                     {"value": 1, "label": "Cam"},
                     {"value": 2, "label": "PC"},
                 ],
                 "destinations": [
                     {"value": i, "label": f"Out {i}",
                      "route_key": f"device.acme.output.{i}.input",
                      "lock_key": f"var.p_wall_ok_lock_{i}"}
                     for i in range(1, 9)
                 ],
                 "show_lock": True, "show_mute": False,
             }},
        ],
        [_portrait({
            "p_narrow": _pct_box(0, 0, 5, 10),
            "p_btn_ok": _pct_box(60, 0, 30, 10),
            "p_led": _p_box(0, 15, 60, 44),
            "p_wall": _pct_box(0, 25, 50, 20),
            "p_wall_ok": _pct_box(0, 50, 50, 30),
        })],
    ),
])

# Geometry is per-arrangement. A variant can starve or strand a control the
# primary leaves fine, and it can hide one so neither question applies.
CASES["arrangements"] = _project([
    _page(
        "main",
        [
            {"id": "led", "type": "status_led", "label": "Ready"},
            {"id": "fine_here", "type": "button", "label": "F"},
            {"id": "hidden_in_portrait", "type": "select"},
            {"id": "starved_everywhere", "type": "select"},
        ],
        [
            _landscape({
                "led": _box(0, 0, 40, 44),
                "fine_here": _pct_box(50, 0, 20, 20),
                "hidden_in_portrait": _box(0, 30, 40, 44),
                "starved_everywhere": _box(50, 30, 40, 44),
            }),
            {
                "id": "portrait", "orientation": "portrait", "primary": False,
                "inherits": "landscape",
                "placements": {
                    # Fine in landscape, starved here.
                    "led": _box(0, 0, 24, 44),
                    "fine_here": _pct_box(95, 0, 20, 20),
                },
                "hidden": ["hidden_in_portrait"],
            },
            # A dangling inherits collapses the variant to nothing. It carries
            # no boxes, so it must report nothing rather than everything.
            {
                "id": "broken", "orientation": "portrait", "primary": False,
                "inherits": "gone", "placements": {}, "hidden": [],
            },
        ],
    ),
])

# Containers nested three deep: a percentage of a percentage of a percentage,
# which is exactly the arithmetic that has to fold the same way on both sides.
CASES["nesting"] = _project([
    _page(
        "main",
        [
            {"id": "outer", "type": "group"},
            {"id": "middle", "type": "group", "parent": "outer"},
            {"id": "inner_led", "type": "status_led", "parent": "middle", "label": "Deep"},
            {"id": "inner_sel", "type": "select", "parent": "middle"},
            # A parent that does not exist, and one that is itself: both are
            # treated as no parent, because the page still has to draw.
            {"id": "orphan", "type": "status_led", "parent": "nowhere", "label": "O"},
            {"id": "selfish", "type": "status_led", "parent": "selfish", "label": "S"},
        ],
        [_landscape({
            "outer": _pct_box(0, 0, 50, 50),
            "middle": _pct_box(0, 0, 50, 50),
            "inner_led": _pct_box(0, 0, 20, 40),
            "inner_sel": _pct_box(30, 0, 40, 40),
            "orphan": _box(60, 0, 24, 44),
            "selfish": _box(70, 0, 24, 44),
        })],
    ),
])

# A master borrows no page's layout: its box is a percentage of the viewport,
# keyed by orientation, so it has no siblings and no container.
CASES["masters"] = _project(
    [_page("main", [], [_landscape({})])],
    masters=[
        {"id": "bar_led", "type": "status_led", "label": "Sys", "pages": "*",
         "placements": {"landscape": _box(0, 0, 24, 44),
                        "portrait": _box(0, 0, 40, 44)}},
        {"id": "bar_sel", "type": "select", "pages": ["main"],
         "placements": {"landscape": _box(10, 0, 60, 40)}},
        {"id": "bar_label", "type": "label", "text": "Room",
         "bindings": {"show": {"look": {"key": "device.acme.online",
                                        "states": {"true": {"label": "UP"}}}}},
         "placements": {"landscape": _pct_box(50, 0, 20, 8)}},
    ],
)

# ...and the other half of that: a master has no sibling in any layout, but the
# controls on the pages it draws under are laid over the top of it. Masters are
# appended to the page surface FIRST, so a control on top of one hides it and
# takes the finger -- and a master nav bar is how somebody gets off a page.
#
# The measurements are the bench project this was found on: `master_home` is
# 203x63px at the origin, and the page's own video element starts 51px in.
CASES["master_buried"] = _project(
    [
        {
            **_page(
                "main",
                [
                    {"id": "vid_wide", "type": "image", "src": "assets://wall.png"},
                    {"id": "nav_clear", "type": "button", "label": "Go"},
                    # Clear of the landscape bar, on top of the portrait one.
                    {"id": "port_ctl", "type": "button", "label": "P"},
                ],
                [
                    _landscape({
                        "vid_wide": _box(3.984375, 0, 624, 160),
                        "nav_clear": _pct_box(20, 25, 20, 12),
                        "port_ctl": _pct_box(70, 20, 20, 12),
                    }),
                    {
                        "id": "portrait", "orientation": "portrait", "primary": False,
                        "inherits": "landscape", "hidden": [],
                        "placements": {"port_ctl": _pct_box(0, 92, 30, 8)},
                    },
                ],
            ),
        },
        # A container over the bar, with children inside it. The container is
        # what has to move, so it answers alone -- and the child that is inside
        # it but clear of the bar says nothing either way.
        _page(
            "nested",
            [
                {"id": "grp_over", "type": "group"},
                {"id": "nav_kid", "type": "button", "label": "K", "parent": "grp_over"},
                {"id": "off_kid", "type": "label", "text": "F", "parent": "grp_over"},
            ],
            [_landscape({
                "grp_over": _pct_box(0, 0, 20, 14),
                "nav_kid": _pct_box(50, 0, 50, 50),
                "off_kid": _pct_box(85, 60, 15, 40),
            })],
        ),
        # A control and a master that can never be on screen together.
        _page(
            "modes",
            [
                {"id": "mode_b_lbl", "type": "label", "text": "B",
                 **_when(key="var.mode", value="b")},
            ],
            [_landscape({"mode_b_lbl": _pct_box(60, 0, 20, 10)})],
        ),
        # A page that draws its own markup paints the masters OVER the frame, so
        # nothing on it can bury one. It is also the page whose controls are not
        # drawn at all, which is the only thing said about it.
        {
            **_page(
                "own_markup",
                [{"id": "hidden_ctl", "type": "button", "label": "X"}],
                [_landscape({"hidden_ctl": _pct_box(0, 0, 20, 10)})],
            ),
            "render_mode": "custom",
            "custom_file": "room/index.html",
        },
        _page("quiet_page", [], [_landscape({})]),
    ],
    masters=[
        {"id": "nav_bar", "type": "button", "label": "Home", "pages": "*",
         "placements": {"landscape": _box(0, 0, 203, 63),
                        "portrait": _pct_box(0, 90, 100, 10)}},
        # Listed on a page that has nothing on it: a master nobody is sitting on.
        {"id": "logo", "type": "image", "src": "assets://logo.png",
         "pages": ["quiet_page"],
         "placements": {"landscape": _pct_box(0, 0, 100, 100)}},
        # Drawn nowhere at all, over everything if it were.
        {"id": "ghost", "type": "status_led", "label": "G", "pages": "*", "hidden": True,
         "placements": {"landscape": _pct_box(0, 0, 100, 100)}},
        # Only ever on screen in one mode, and `mode_b_lbl` is the other one.
        {"id": "mode_a_bar", "type": "label", "text": "A", "pages": "*",
         **_when(key="var.mode", value="a"),
         "placements": {"landscape": _pct_box(60, 0, 20, 10)}},
    ],
)


# A floor larger than the box that holds it, which has no remedy expressed as a
# percentage of that box -- 100% of it is already too small. The container is
# what has to move, and which container depends on how far up the room is.
CASES["floor_bigger_than_the_container"] = _project([
    _page(
        "main",
        [
            # Two levels up: the fader needs 72x100, the group holding it can
            # only reach 144x200 inside ITS parent, so the fix is the outer one.
            {"id": "outer", "type": "group"},
            {"id": "mid", "type": "group", "parent": "outer"},
            {"id": "deep_fader", "type": "fader", "parent": "mid"},
            # item_height is rem: 100 is 1400px of row on an 800px page, so no
            # arrangement of anything can hold it.
            {"id": "tall_list", "type": "list", "item_height": 100},
            # A container so small that growing it to the whole page still
            # leaves the matrix short.
            {"id": "pinched_box", "type": "group"},
            {"id": "pinched_matrix", "type": "matrix", "parent": "pinched_box"},
        ],
        [_landscape({
            "outer": _pct_box(0, 0, 10, 10),
            "mid": _pct_box(0, 0, 50, 50),
            "deep_fader": _pct_box(0, 0, 50, 50),
            "tall_list": _pct_box(20, 0, 10, 10),
            "pinched_box": _pct_box(40, 0, 1, 1),
            "pinched_matrix": _pct_box(0, 0, 5, 5),
        })],
    ),
])

# A page with no layouts at all, which a hand-built or half-migrated project can
# reach. Nothing has a box, so geometry says nothing and bindings still answer.
CASES["no_layouts"] = _project([
    {
        "id": "bare", "name": "Bare",
        "elements": [
            {"id": "led", "type": "status_led", "label": "R"},
            {"id": "text_with_look", "type": "label",
             "bindings": {"show": {"look": {"key": "device.acme.online"}}}},
        ],
        "layouts": [],
    },
])


# The adversarial case, and the only one here that was not written to exercise a
# check. The cloud AI built this page live after being told what the guards were
# and asked to break them: roughly forty hostile shapes on one page, including
# several nobody thought to write a fixture for -- a floor whose remedy is over
# 100% of its container, a control with no floor at all sized to 6x4px, a box
# with a zero dimension, `style` measurements written in px on a control whose
# units are rem, and an element type that does not exist. Kept verbatim apart
# from the device id, which was a real product.
CASES["stress_test"] = _project([
    _page(
        "stress_test",
        [
            {"id": "led_a1", "type": "status_led", "label": "Sig"},
            {"id": "led_a2", "type": "status_led"},
            {"id": "led_a3", "type": "status_led", "label": "OK"},
            {
                "id": "led_a4",
                "type": "status_led",
                "bindings": {
                    "show": {
                        "value": {"source": "state", "key": "device.acme_amp.channel.01.name"},
                    },
                },
            },
            {"id": "led_a5", "type": "status_led", "label": "Fault"},
            {"id": "grp_tiny", "type": "group", "label": "Tiny Box"},
            {"id": "led_b1", "type": "status_led", "label": "In", "parent": "grp_tiny"},
            {
                "id": "led_b2",
                "type": "status_led",
                "label": "In2",
                "parent": "grp_tiny",
            },
            {
                "id": "ovf_child",
                "type": "button",
                "label": "Overflow",
                "parent": "grp_tiny",
            },
            {"id": "grp_strip", "type": "group", "label": "Ch Strip"},
            {
                "id": "fader_strip",
                "type": "fader",
                "min": -80,
                "max": 0,
                "step": 0.5,
                "orientation": "vertical",
                "parent": "grp_strip",
            },
            {
                "id": "meter_strip",
                "type": "level_meter",
                "min": -60,
                "max": 0,
                "parent": "grp_strip",
            },
            {
                "id": "sel_strip",
                "type": "select",
                "options": [
                    {"label": "Analog 1", "value": "a1"},
                    {"label": "Digital 1", "value": "d1"},
                ],
                "parent": "grp_strip",
            },
            {
                "id": "btn_strip",
                "type": "button",
                "label": "Mute",
                "parent": "grp_strip",
            },
            {"id": "keypad_d", "type": "keypad", "digits": 4},
            {
                "id": "matrix_d",
                "type": "matrix",
                "matrix_config": {
                    "inputs": [{"id": "1", "label": "In 1"}, {"id": "2", "label": "In 2"}],
                    "outputs": [{"id": "1", "label": "Out 1"}, {"id": "2", "label": "Out 2"}],
                },
                "matrix_style": "crosspoint",
            },
            {"id": "list_d", "type": "list", "list_style": "selectable"},
            {
                "id": "list_d2",
                "type": "list",
                "list_style": "selectable",
                "item_height": 20,
            },
            {
                "id": "slider_d",
                "type": "slider",
                "min": 0,
                "max": 100,
                "step": 1,
                "thumb_size": 20,
            },
            {"id": "txt_d", "type": "text_input", "placeholder": "name"},
            {
                "id": "fader_ok",
                "type": "fader",
                "min": -80,
                "max": 0,
                "step": 0.5,
                "orientation": "vertical",
            },
            {"id": "btn_tiny", "type": "button", "label": "X"},
            {"id": "btn_stack_a", "type": "button", "label": "Under"},
            {"id": "btn_stack_b", "type": "button", "label": "Over"},
            {"id": "btn_offpage", "type": "button", "label": "Gone"},
            {"id": "btn_negative", "type": "button", "label": "Neg"},
            {"id": "btn_zero_h", "type": "button", "label": "Flat"},
            {
                "id": "cam_tiny",
                "type": "camera_preset",
                "label": "P1",
                "preset_number": 1,
            },
            {
                "id": "nav_tiny",
                "type": "page_nav",
                "label": "Back",
                "target_page": "main",
            },
            {"id": "gauge_tiny", "type": "gauge", "min": 0, "max": 100},
            {"id": "clock_tiny", "type": "clock", "clock_mode": "time"},
            {
                "id": "lbl_bigfont",
                "type": "label",
                "text": "Heading",
                "style": {"font_size": 24, "padding": 8, "border_radius": 12},
            },
            {
                "id": "btn_bad_macro",
                "type": "button",
                "label": "Run",
                "bindings": {
                    "do": {
                        "press": [{"action": "macro", "macro": "macro_that_does_not_exist"}],
                    },
                },
            },
            {
                "id": "nav_bad_target",
                "type": "page_nav",
                "label": "Go",
                "target_page": "no_such_page",
            },
            {
                "id": "btn_bad_device",
                "type": "button",
                "label": "Dev",
                "bindings": {
                    "do": {
                        "press": [
                            {
                                "action": "device.command",
                                "device": "no_such_amp",
                                "command": "mute_on",
                                "params": {"channel": "01"},
                            },
                        ],
                    },
                },
            },
            {
                "id": "btn_bad_command",
                "type": "button",
                "label": "Cmd",
                "bindings": {
                    "do": {
                        "press": [
                            {
                                "action": "device.command",
                                "device": "acme_amp",
                                "command": "explode",
                                "params": {},
                            },
                        ],
                    },
                },
            },
            {
                "id": "lbl_bad_state",
                "type": "label",
                "text": "?",
                "bindings": {
                    "show": {
                        "value": {
                            "source": "state",
                            "key": "device.acme_amp.channel.99.not_a_thing",
                        },
                    },
                },
            },
            {
                "id": "sel_bad_valuemap",
                "type": "select",
                "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
                "bindings": {
                    "do": {
                        "change": [
                            {
                                "action": "value_map",
                                "map": {
                                    "x": {
                                        "action": "device.command",
                                        "device": "acme_amp",
                                        "command": "mute_on",
                                        "params": {"channel": "01"},
                                    },
                                    "y": {
                                        "action": "device.command",
                                        "device": "acme_amp",
                                        "command": "mute_off",
                                        "params": {"channel": "01"},
                                    },
                                },
                            },
                        ],
                    },
                },
            },
            {
                "id": "btn_press_as_object",
                "type": "button",
                "label": "ObjPress",
                "bindings": {
                    "do": {
                        "press": [
                            {
                                "action": "device.command",
                                "device": "acme_amp",
                                "command": "mute_on",
                                "params": {"channel": "01"},
                            },
                        ],
                    },
                },
            },
            {"id": "knob_x", "type": "knob", "label": "Knob"},
            {
                "id": "btn_value_only",
                "type": "button",
                "label": "Vol",
                "bindings": {
                    "show": {
                        "value": {
                            "source": "state",
                            "key": "device.acme_amp.channel.01.fader",
                            "format": "{value} dB",
                        },
                    },
                },
            },
            {
                "id": "lbl_look_only",
                "type": "label",
                "text": "Status",
                "bindings": {
                    "show": {
                        "look": {
                            "key": "device.acme_amp.channel.01.mute",
                            "default_state": "false",
                            "states": {"true": {"label": "MUTED"}, "false": {"label": "LIVE"}},
                        },
                    },
                },
            },
            {
                "id": "grp_value",
                "type": "group",
                "label": "Box",
                "bindings": {
                    "show": {"value": {"source": "state", "key": "device.acme_amp.uptime"}},
                },
            },
            {
                "id": "img_value",
                "type": "image",
                "src": "assets://logo.png",
                "bindings": {
                    "show": {"value": {"source": "state", "key": "device.acme_amp.model_id"}},
                },
            },
            {
                "id": "keypad_value",
                "type": "keypad",
                "digits": 4,
                "bindings": {"show": {"value": {"source": "state", "key": "var.code"}}},
            },
            {
                "id": "meter_look",
                "type": "level_meter",
                "min": -60,
                "max": 0,
                "bindings": {
                    "show": {
                        "look": {
                            "source": "state",
                            "key": "device.acme_amp.channel.01.limiting",
                            "map": {"true": "#FF9800", "false": "#4CAF50"},
                        },
                    },
                },
            },
            {
                "id": "matrix_value",
                "type": "matrix",
                "matrix_config": {
                    "inputs": [{"id": "1", "label": "In 1"}],
                    "outputs": [{"id": "1", "label": "Out 1"}],
                },
                "matrix_style": "crosspoint",
                "bindings": {"show": {"value": {"source": "state", "key": "var.route"}}},
            },
            {
                "id": "led_states_label",
                "type": "status_led",
                "label": "Load",
                "bindings": {
                    "show": {
                        "look": {
                            "source": "state",
                            "key": "device.acme_amp.channel.01.load_status",
                            "states": {
                                "Ok": {"label": "LOAD OK", "bg_color": "#4CAF50"},
                                "Short": {"label": "SHORT", "bg_color": "#F44336"},
                            },
                        },
                    },
                },
            },
            {
                "id": "list_default_h",
                "type": "list",
                "list_style": "selectable",
                "item_height": 44,
            },
            {
                "id": "slider_default_thumb",
                "type": "slider",
                "min": 0,
                "max": 100,
                "step": 1,
                "thumb_size": 44,
            },
        ],
        [_landscape({
            "led_a1": _pct_box(1, 1, 1.5, 1.5),
            "led_a2": _pct_box(3, 1, 1, 1),
            "led_a3": _pct_box(5, 1, 2.27, 2.5),
            "led_a4": _pct_box(8, 1, 1.6, 2.5),
            "led_a5": _pct_box(10, 1, 0.2, 0.2),
            "grp_tiny": _pct_box(14, 1, 10, 8),
            "led_b1": _pct_box(5, 10, 15, 25),
            "led_b2": _pct_box(25, 10, 22.66, 31.25),
            "ovf_child": _pct_box(80, 60, 60, 80),
            "grp_strip": _pct_box(26, 1, 4, 70),
            "fader_strip": _pct_box(5, 2, 90, 60),
            "meter_strip": _pct_box(5, 64, 20, 10),
            "sel_strip": _pct_box(5, 76, 80, 5),
            "btn_strip": _pct_box(5, 82, 95, 5),
            "keypad_d": _pct_box(31, 1, 3, 10),
            "matrix_d": _pct_box(35, 1, 8, 8),
            "list_d": _pct_box(44, 1, 1.5, 4),
            "list_d2": _pct_box(46, 1, 2.5, 6),
            "slider_d": _pct_box(49, 1, 3, 6),
            "txt_d": _pct_box(53, 1, 3, 5),
            "fader_ok": _pct_box(57, 1, 5.625, 12.5),
            "btn_tiny": _pct_box(64, 1, 1, 1),
            "btn_stack_a": _pct_box(66, 1, 6, 8),
            "btn_stack_b": _pct_box(66, 1, 6, 8),
            "btn_offpage": _pct_box(150, 120, 10, 10),
            "btn_negative": _pct_box(-20, -15, 10, 10),
            "btn_zero_h": _pct_box(75, 1, 20, 0),
            "cam_tiny": _pct_box(80, 1, 2, 2),
            "nav_tiny": _pct_box(83, 1, 1.5, 1.5),
            "gauge_tiny": _pct_box(86, 1, 0.5, 0.5),
            "clock_tiny": _pct_box(88, 1, 0.5, 0.5),
            "lbl_bigfont": _pct_box(90, 1, 8, 4),
            "btn_bad_macro": _pct_box(1, 46, 8, 8),
            "nav_bad_target": _pct_box(10, 46, 8, 8),
            "btn_bad_device": _pct_box(19, 46, 8, 8),
            "btn_bad_command": _pct_box(32, 46, 8, 8),
            "lbl_bad_state": _pct_box(41, 46, 8, 8),
            "sel_bad_valuemap": _pct_box(50, 46, 8, 8),
            "btn_press_as_object": _pct_box(1, 60, 8, 8),
            "knob_x": _pct_box(32, 60, 8, 8),
            "btn_value_only": _pct_box(1, 20, 8, 8),
            "lbl_look_only": _pct_box(10, 20, 8, 8),
            "grp_value": _pct_box(19, 20, 8, 8),
            "img_value": _pct_box(32, 20, 8, 8),
            "keypad_value": _pct_box(41, 20, 8, 28),
            "meter_look": _pct_box(50, 20, 8, 12),
            "matrix_value": _pct_box(59, 20, 22, 30),
            "led_states_label": _pct_box(86, 20, 8, 8),
            "list_default_h": _pct_box(10, 30, 10, 14),
            "slider_default_thumb": _pct_box(21, 30, 10, 14),
        })],
    ),
])


# A page that draws its own markup. Three shapes, and only two of them say
# anything: a page carrying controls it will not draw, a page set to custom that
# names no file (so it draws them after all), and the ordinary case -- a custom
# page with nothing else on it, which must stay silent on both sides.
CASES["custom_pages"] = _project([
    {
        **_page("kept", [
            {"id": "kept_btn", "type": "button", "label": "Power"},
            {"id": "kept_lbl", "type": "label", "text": "Room"},
        ], [_landscape({
            "kept_btn": _pct_box(1, 1, 20, 12),
            "kept_lbl": _pct_box(25, 1, 20, 12),
        })]),
        "render_mode": "custom",
        "custom_file": "room_map/index.html",
        # Every other finding about those two controls has to disappear, so one
        # of them is deliberately starved: if the page said nothing, this page
        # would still be reported for a control nobody will see.
    },
    {
        **_page("fileless", [
            {"id": "orphan_btn", "type": "button", "label": "Power"},
        ], [_landscape({"orphan_btn": _pct_box(1, 1, 20, 12)})]),
        "render_mode": "custom",
    },
    {
        **_page("clean", [], [_landscape({})]),
        "render_mode": "custom",
        "custom_file": "dashboard/index.html",
        "custom_config": {"room": "204"},
    },
])


def _python_findings(project: ProjectConfig) -> list[dict]:
    findings: list[dict] = []
    masters = project.ui.master_elements or []
    for page in project.ui.pages:
        theme = _theme(project)
        for finding in review_page(page, theme=theme, masters=masters)[0]:
            findings.append({
                "element_id": finding.element_id,
                "kind": finding.kind,
                "message": finding.message,
            })
    for master in project.ui.master_elements or []:
        for finding in review_master_element(master, _theme(project)):
            findings.append({
                "element_id": finding.element_id,
                "kind": finding.kind,
                "message": finding.message,
            })
    return findings


def _theme(project: ProjectConfig) -> dict | None:
    defaults = (project.ui.settings.theme_overrides or {}).get("element_defaults") or {}
    slider = defaults.get("slider")
    return slider if isinstance(slider, dict) else None


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "ui review parity harness missing"
    if not HELPERS.is_file():
        return "uiBuilderHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory) -> dict[str, tuple[list[dict], list[dict]]]:
    """Both sides' findings, keyed by case, from the SAME bytes.

    The projects go through the Pydantic models first and the harness is fed the
    dump rather than the literal above -- so a default the loader fills in, or a
    normalisation it applies, reaches both sides identically. Comparing a model
    against a hand-written dict would test the loader, not the reviewers.
    """
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)

    projects = {name: ProjectConfig.model_validate(raw) for name, raw in CASES.items()}
    dumps = {name: project.model_dump(mode="json") for name, project in projects.items()}

    cases_file = tmp_path_factory.mktemp("ui-review-parity") / "cases.json"
    cases_file.write_text(json.dumps(dumps), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS), str(cases_file)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"ui review parity harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        builder = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc

    return {
        name: (_python_findings(projects[name]), builder[name])
        for name in CASES
    }


@pytest.mark.parametrize("case", sorted(CASES))
def test_both_surfaces_say_exactly_the_same_thing(case, verdicts) -> None:
    """Same findings, same order, same sentences, down to the byte."""
    python_side, builder_side = verdicts[case]
    assert builder_side == python_side


def test_the_corpus_actually_exercises_every_check(verdicts) -> None:
    """A parity test over a corpus that trips nothing passes for free.

    This is the assertion that keeps the one above honest: if a future edit
    stops a whole check firing on both sides at once, every case still matches
    and the suite stays green. Naming the kinds is what catches that.
    """
    kinds = {
        finding["kind"]
        for python_side, _ in verdicts.values()
        for finding in python_side
    }
    assert kinds == {
        "too_small_for_contents",
        "small_touch_target",
        "outside_its_container",
        "overlap",
        "no_placement",
        "binding_not_rendered",
        "binding_without_key",
        "property_not_rendered",
        "nothing_to_draw",
        "unknown_element_type",
        "style_too_large",
        "too_small_to_draw",
        "matrix_not_configured",
        "matrix_config_unread",
        "matrix_no_route_feedback",
        "matrix_default_size",
        "matrix_duplicate_values",
        "matrix_lock_unbacked",
        "custom_page_elements_not_drawn",
        "custom_page_without_a_file",
        "covers_master",
    }


def test_the_corpus_also_produces_silence(verdicts) -> None:
    """Half of a checker's value is what it does NOT say.

    Parity on a corpus where everything is flagged would be satisfied by two
    implementations that flag everything. These are the elements that must come
    back clean on both sides: a control with no floor to breach, a container
    holding its own child, boxes under different parents, an element flush with
    the edge, a pair grazing by a fraction of a pixel, and a binding the
    renderer really does read.
    """
    flagged = {
        finding["element_id"]
        for python_side, _ in verdicts.values()
        for finding in python_side
    }
    for quiet in (
        "btn", "lbl", "img", "dial",          # no fixed internals at all
        "led_bare",                            # 20px of dot fits in 28px
        "flush",                               # ends exactly on the edge
        "graze",                               # overlaps by a thousandth of a percent
        "left_kid", "right_kid",               # different containers
        "list_ok", "visible_only",             # bindings the renderer reads
        "plugin_ok",                           # a real type both authoring surfaces omit
        "btn_with_state_labels",               # a button DOES draw state text
        "tab_audio", "tab_video",              # a tab strip: same key, different values
        "cold", "hot",                         # two bounds with no room between them
        "modes_ab", "modes_cd",                # every branch contradicts every branch
        "on_when", "off_when",                 # truthy against falsy
        "roomy", "pill",                       # rem measurements a big box can hold
        "gauge_just",                          # exactly on the degenerate threshold
        "lbl_right",                           # a label's static content IS `text`
        "mtx_ok",                              # a matrix spelled the way it works
        "mtx_written_ok",                      # ...and the same, written out entry by entry
        "mtx_tiles_ok",                        # a tile wall whose locks are real variables
        "lbl_bound",                           # show.value supplies the text
        "lbl_look_states", "lbl_look_binary",  # ...and so does show.look's state text
        "lbl_macro",                           # a macro-progress label needs no state key
        "custom_ok",                           # a custom control that names its page
        "clean",                               # a custom page with nothing left on it
        "kept_btn", "kept_lbl", "orphan_btn",  # controls a custom page answers FOR
        "nav_clear",                           # beside a master, not on it
        "nav_kid", "off_kid",                  # inside a container that answers for them
        "mode_b_lbl",                          # never on screen with the master it covers
        "hidden_ctl",                          # a custom page paints its masters on top
        "p_btn_ok", "p_wall_ok",               # sized for the shape they are drawn in
        "p_led",                               # 44px of height a portrait screen has
    ):
        assert quiet not in flagged, f"{quiet} should not have been flagged"


def test_a_portrait_arrangement_is_measured_against_portrait_glass(verdicts) -> None:
    """The two guards above cannot see this one go, so it says so itself.

    Neither of them would notice the portrait case being deleted: it trips no
    kind the rest of the corpus does not already trip, and an element that stops
    being flagged only ever makes the silence list longer. That matters more
    here than anywhere else in this file, because the defect this pins failed
    toward ACCEPTING -- a portrait page measured against 1280x800 came back
    clean on every surface at once, so there was nothing red to go green and
    this case is the whole of the evidence.

    Both halves are named. The reference box: 5% of an 800px width is 40px and
    under the finger rule, where the same 5% of 1280 is 64px and passes. And the
    tile wall's shape: eight destinations are 2x4 here rather than 4x2, so the
    floor is 262x290 instead of 514x150 and a 400x256px box is under it -- the
    same box that clears the landscape floor comfortably.
    """
    python_side, builder_side = verdicts["portrait"]
    assert builder_side == python_side  # said again here so a drift names portrait

    by_id = {f["element_id"]: f for f in python_side}
    assert set(by_id) == {"p_narrow", "p_wall"}

    assert by_id["p_narrow"]["kind"] == "small_touch_target"
    assert by_id["p_narrow"]["message"] == (
        "p_narrow (button) is about 40x128px, roughly 6.8x21.8mm on a 10-inch panel "
        "-- under the 9mm comfortable touch minimum on width (53px)."
    )

    assert by_id["p_wall"]["kind"] == "too_small_for_contents"
    assert by_id["p_wall"]["message"] == (
        "p_wall (matrix) is 400x256px at the 800x1280 reference, too small for what "
        "it draws: 256px tall, needs 290px (matrix-tile is 64px). Give it h at least "
        "22.66% of the page."
    )


def test_a_zero_dimension_is_degenerate_not_uncomfortable(verdicts) -> None:
    """A 256x0px button is not a control someone will struggle to hit.

    It used to be reported only as "roughly 43.6x0.0mm on a 10-inch panel --
    under the 9mm comfortable touch minimum on height", which invites making it
    a little bigger when the thing is not on screen at all.
    """
    python_side, _ = verdicts["stress_test"]
    kinds = [f["kind"] for f in python_side if f["element_id"] == "btn_zero_h"]
    assert kinds == ["too_small_to_draw"]


def test_one_box_over_many_is_one_finding(verdicts) -> None:
    """The check most able to drown out every other one.

    Overlap is the only O(n^2) finding here, so a single oversized box used to
    answer once per neighbour -- 23 warnings out of 56 on the page this corpus
    came from, which pushed the sizing failure that caused all of them out of
    reading range. It answers once now, names the worst offender rather than
    whichever id sorts first, and counts what it does not list.
    """
    python_side, _ = verdicts["overlap_noise"]
    overlaps = [f for f in python_side if f["kind"] == "overlap"]
    collapsed = [f for f in overlaps if f["element_id"] == "huge"]
    assert len(collapsed) == 1, "one box over five should answer once"
    assert "overlaps 5 elements" in collapsed[0]["message"]
    assert "and 2 more" in collapsed[0]["message"], "the rest are counted, not dropped"
    # And a lone collision keeps the pairwise sentence, with its arithmetic.
    lone = [f for f in overlaps if f["element_id"] == "gated"]
    assert len(lone) == 1
    assert "% of the smaller one) inside the page." in lone[0]["message"]
    # A box that is BOTH out of its container and on top of a neighbour answers
    # for both. Pulling it back inside does not move it off the neighbour, so
    # calling the collision a consequence of the overflow would hide a real one.
    assert any(f["element_id"] == "runaway" for f in overlaps)
    assert any(
        f["kind"] == "outside_its_container" and f["element_id"] == "runaway"
        for f in python_side
    )


def test_a_buried_master_is_named_on_the_control_that_buried_it(verdicts) -> None:
    """The check nobody had: a master is not in the page's element list.

    It was found on a real panel -- a nav bar under a video element, with both
    reviews silent -- and the thing that has to survive is not the number but
    which element gets told. The master has no page to be warned on, and the
    control is what moved.
    """
    python_side, _ = verdicts["master_buried"]
    buried = [f for f in python_side if f["kind"] == "covers_master"]
    assert [f["element_id"] for f in buried] == ["vid_wide", "port_ctl", "grp_over"]

    # The bench measurements, in the pixels somebody can hold a ruler to, and
    # named against the master rather than against "the smaller one".
    assert buried[0]["message"] == (
        "vid_wide (image) is drawn over the master element nav_bar (button), which "
        "draws on every page and sits behind a page's own controls. Move vid_wide off "
        "it, or stop nav_bar drawing on main. vid_wide covers 152x63px of nav_bar, "
        "75% of it."
    )
    # A master carries a box per orientation, so the portrait bar is a different
    # box in a different place -- and the arrangement it happens in is named.
    #
    # These pixels are measured against PORTRAIT glass, which is the Q-087 fix
    # visible in an assertion that predates it: the same 30% x 8% overlap read
    # 384x64px while every arrangement was measured against 1280x800, and reads
    # 240x102px now that a portrait one is measured against 800x1280. The share
    # is unchanged at 24% because that is a ratio of percentages and never
    # depended on the reference at all.
    assert buried[1]["message"].endswith(
        "port_ctl covers 240x102px of nav_bar, 24% of it in the 'portrait' arrangement."
    )
    # Nesting folded down: the container's own box is a percentage of the page,
    # and it covers the bar outright.
    assert "grp_over covers 203x63px of nav_bar, 100% of it." in buried[2]["message"]


def test_an_unknown_type_answers_once_about_the_type(verdicts) -> None:
    """And says nothing about its bindings.

    ``unknown_kind`` carries a ``show.look`` as well as a type the panel cannot
    draw. Warning that the slot is unread would be answering for a renderer that
    does not exist, and the fix for both is the same one edit.
    """
    findings = [
        finding
        for python_side, _ in verdicts.values()
        for finding in python_side
        if finding["element_id"] == "unknown_kind"
    ]
    assert [f["kind"] for f in findings] == ["unknown_element_type"]
    # The whole set is in the message, because this is where an author learns it.
    assert "plugin" in findings[0]["message"]
