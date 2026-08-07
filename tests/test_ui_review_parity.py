"""The Builder and the AI door must reach identical verdicts about one file.

Two implementations of the same arithmetic exist on purpose: the AI writes
blind through ``server/ui/page_review.py``, and a human drags a box in the
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

from server.core.project_loader import ProjectConfig
from server.ui.page_review import review_master_element, review_page

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "ui_review_parity_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "uiBuilderHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
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
            {"id": "mtx", "type": "matrix"},
            {"id": "meter", "type": "level_meter"},
            {"id": "pad", "type": "keypad"},
            {"id": "sel", "type": "select"},
            {"id": "txt", "type": "text_input"},
            # No floor at all: limited by their text, which is not a minimum box.
            {"id": "btn", "type": "button", "label": "Go"},
            {"id": "lbl", "type": "label", "text": "Hello"},
            {"id": "img", "type": "image"},
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
            "mtx": _box(0, 20, 200, 200),
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
            # Out of the page, so the collision under it is the same defect.
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
             "bindings": {"show": {"items": {"key": "device.acme.inputs"},
                                   "value": {"key": "device.acme.selected"}}}},
            {"id": "visible_only", "type": "image",
             "bindings": {"show": {"visible_when": {"key": "var.admin", "equals": True}}}},
            # A type the panel cannot draw. It answers about the TYPE and says
            # nothing about the binding: a slot reaching a renderer that does
            # not exist is not a second, separate problem.
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
            "visible_only": _pct_box(75, 20, 20, 20),
            "unknown_kind": _pct_box(75, 45, 20, 20),
            "plugin_ok": _pct_box(0, 70, 20, 20),
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


def _python_findings(project: ProjectConfig) -> list[dict]:
    findings: list[dict] = []
    for page in project.ui.pages:
        theme = _theme(project)
        for finding in review_page(page, theme=theme)[0]:
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
        return "esbuild not installed (run `npm ci` in web/programmer)"
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
        "unknown_element_type",
        "style_too_large",
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
        "settled",                             # its neighbour left the page; one fix
        "roomy", "pill",                       # rem measurements a big box can hold
    ):
        assert quiet not in flagged, f"{quiet} should not have been flagged"


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
