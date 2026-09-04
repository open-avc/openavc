"""The panel, loaded from a running server, and driven like a finger.

Every other panel test in this directory renders ``panel.js`` into a page the
test wrote itself, with ``fetch`` and ``WebSocket`` replaced by stubs. That is
the right harness for asking what the renderer draws, and it is worth keeping.
What it cannot ask is whether the panel a real instance serves ever reaches
that instance -- because in those files nothing is served and nothing is
reached.

The cost of that gap is on record. While the matrix work was being built, the
Programmer's Builder canvas asked for ``/api/api/ui/resolve-matrix`` -- its
request helper already carries the prefix -- so the call 404'd, the resolve
quietly fell back, and every generator-form matrix drew as an empty box. The
full suite stayed green and it was found by taking a screenshot. That one was
the Builder rather than the panel, and it was caught before it was committed,
so nothing shipped with it; what it showed is that a stubbed ``fetch`` answers
a wrong URL exactly as happily as a right one, and the panel is stubbed the
same way with nobody looking at its screenshots.

So these tests boot the real server, load ``/panel/`` from it, click with a
real mouse, and then ask the SERVER what changed. No device is involved: the
bindings here write ``var.`` keys, which is a complete round trip -- press,
WebSocket out, binding executed, state written, state broadcast back, control
redrawn -- with nothing on the end of a wire.
"""

from __future__ import annotations

import json
import re as _re
import time
from typing import Any
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 10_000
READY_TIMEOUT = 15_000

#: The destination's value is a string and the sources' are integers, on
#: purpose: a matrix entry's value is opaque (project format 0.10.0), and the
#: panel puts it in the DOM, sends it back over the WebSocket, and the server
#: matches it against what the project wrote. A test where everything is the
#: same integer proves none of that survives the round trip.
DEST = "out1"


def _panel_project() -> dict[str, Any]:
    """A room with two controls, both of which write state and nothing else.

    Authored at the current project format rather than the conftest seed's
    0.5.0, because these sections are written in today's shapes: a migration
    chain running over them would rewrite geometry and bindings that need no
    rewriting.
    """
    return {
        "openavc_version": "0.11.0",
        "variables": [
            {"id": "last_press", "type": "string", "default": "none",
             "label": "Last button pressed"},
            # Declared as an integer because that is what the source's own
            # value is, and 0 because none of the sources is 0 -- so "nothing
            # routed yet" is a state the crosspoints can actually be in.
            {"id": "routed_source", "type": "integer", "default": 0,
             "label": "Source routed to the display"},
            {"id": "routed_destination", "type": "string", "default": "",
             "label": "Destination last routed"},
        ],
        "ui": {
            "settings": {"theme": "dark-default"},
            "pages": [{
                "id": "main",
                "name": "Main",
                "page_type": "page",
                "layouts": [{
                    "id": "landscape",
                    "orientation": "landscape",
                    "primary": True,
                    "inherits": None,
                    "placements": {
                        "btn_laptop": {"x": 4.0, "y": 4.0, "w": 30.0, "h": 14.0},
                        "mx1": {"x": 4.0, "y": 24.0, "w": 60.0, "h": 60.0},
                    },
                    "hidden": [],
                }],
                "elements": [
                    {
                        "id": "btn_laptop",
                        "type": "button",
                        "label": "Laptop",
                        "parent": None,
                        "bindings": {"do": {"press": [
                            {"action": "state.set",
                             "key": "var.last_press", "value": "laptop"},
                        ]}},
                    },
                    {
                        "id": "mx1",
                        "type": "matrix",
                        "label": "Routing",
                        "parent": None,
                        "matrix_config": {
                            "style": "crosspoint",
                            "sources": [
                                {"value": 1, "label": "Laptop"},
                                {"value": 2, "label": "Camera"},
                            ],
                            # One destination, and it watches the same key the
                            # route below writes. That is what closes the loop:
                            # the crosspoint lights because the SERVER said so,
                            # not because the panel remembered its own tap.
                            "destinations": [
                                {"value": DEST, "label": "Display",
                                 "route_key": "var.routed_source"},
                            ],
                        },
                        "bindings": {"do": {"route": [
                            {"action": "state.set",
                             "key": "var.routed_source", "value": "$input"},
                            {"action": "state.set",
                             "key": "var.routed_destination", "value": "$output"},
                        ]}},
                    },
                ],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


def _state(base_url: str, key: str) -> Any:
    """What the server says a state key holds, read over its own REST API."""
    with urlopen(f"{base_url}/api/state/{key}", timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8")).get("value")


class _ServedPanel:
    """A page showing the panel of a running instance, plus that instance."""

    def __init__(self, page: Page, base_url: str, failed: list[str]):
        self.page = page
        self.base_url = base_url
        #: Every same-origin response the browser got back with a 4xx/5xx.
        self.failed_requests = failed

    def state(self, key: str) -> Any:
        return _state(self.base_url, key)

    def element(self, element_id: str):
        return self.page.locator(f'[data-element-id="{element_id}"]')

    def crosspoint(self, source: Any, destination: Any):
        return self.page.locator(
            f'.matrix-crosspoint[data-input="{source}"]'
            f'[data-output="{destination}"]'
        )


@pytest.fixture
def served_panel(server_factory, page: Page):
    """Boot an instance, open its panel, and wait until it has drawn."""
    handle = server_factory(project_overrides=_panel_project())

    failed: list[str] = []

    def _record(response) -> None:
        if response.status >= 400 and response.url.startswith(handle.base_url):
            failed.append(f"{response.status} {response.url}")

    page.on("response", _record)

    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    # The panel renders from the ui.definition the server pushes on the
    # WebSocket, so the anchor is a control being on screen -- not the
    # document being parsed.
    page.locator('[data-element-id="btn_laptop"]').wait_for(
        state="visible", timeout=READY_TIMEOUT,
    )
    return _ServedPanel(page, handle.base_url, failed)


# ---------------------------------------------------------------------------
# A press reaches the instance
# ---------------------------------------------------------------------------

def test_a_press_on_a_served_panel_reaches_the_instance(served_panel) -> None:
    """The whole point of the file: click, then ask the server."""
    assert served_panel.state("var.last_press") == "none"

    served_panel.element("btn_laptop").click()

    _eventually(lambda: served_panel.state("var.last_press") == "laptop",
                "var.last_press never became 'laptop' on the server")


# ---------------------------------------------------------------------------
# A route reaches the instance, values intact
# ---------------------------------------------------------------------------

def test_a_matrix_tap_sends_the_values_the_project_wrote(served_panel) -> None:
    """Both halves of the crosspoint survive the wire, types and all.

    The source is an integer and the destination a string; a panel that sent
    row and column numbers, or stringified everything on the way through,
    would pass a renderer test and fail here.
    """
    served_panel.crosspoint(2, DEST).click()

    _eventually(lambda: served_panel.state("var.routed_source") == 2,
                "the server never saw source 2")
    _eventually(lambda: served_panel.state("var.routed_destination") == DEST,
                f"the server never saw destination {DEST!r}")


def test_the_crosspoint_lights_because_the_server_said_so(served_panel) -> None:
    """The return leg: state written by the binding comes back and redraws.

    Nothing here is optimistic -- the panel never marks its own tap. If the
    broadcast does not arrive, or arrives under a key the destination is not
    watching, this cell stays dark. Asserted on the aria-label rather than the
    class, because that sentence is what a person using a screen reader is
    told, and it names both ends of the route.
    """
    cell = served_panel.crosspoint(2, DEST)
    expect(cell).to_have_attribute(
        "aria-label", f"Inactive: 2 to {DEST}", timeout=EXPECT_TIMEOUT,
    )

    cell.click()

    expect(cell).to_have_attribute(
        "aria-label", f"Active: 2 to {DEST}", timeout=EXPECT_TIMEOUT,
    )
    expect(served_panel.crosspoint(1, DEST)).to_have_attribute(
        "aria-label", f"Inactive: 1 to {DEST}", timeout=EXPECT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Nothing the panel asked the instance for was missing
# ---------------------------------------------------------------------------

def test_loading_and_driving_the_panel_asks_for_nothing_that_is_not_there(
    served_panel,
) -> None:
    """No 4xx/5xx from the instance, start to finish.

    This is the assertion the stubbed harness cannot make. The Builder's
    doubled-prefix 404 was the same shape one door along, and nothing on the
    panel's side would notice its own version of it: a stub answers a wrong
    URL exactly as readily as a right one, and only a real server says 404.
    """
    served_panel.element("btn_laptop").click()
    served_panel.crosspoint(2, DEST).click()
    _eventually(lambda: served_panel.state("var.last_press") == "laptop",
                "the press never landed, so the request log proves nothing")

    assert served_panel.failed_requests == []


# ---------------------------------------------------------------------------
# A press that failed says why, on the glass
# ---------------------------------------------------------------------------

#: RFC 5737 documentation address. Routed nowhere by anyone, so the projector
#: is reliably absent rather than absent-until-somebody-plugs-something-in.
DEAD_HOST = "192.0.2.13"

#: What the device is called in the room. The message has to use this rather
#: than the device id -- it is the only one of the two names anybody standing
#: at the panel has ever seen.
DEAD_NAME = "Ceiling Projector"


def _dead_device_project(*, show_error_messages: bool = True) -> dict[str, Any]:
    """A room whose projector is not on the network, and two buttons for it.

    Both buttons send the same command to the same absent device and then write
    a variable. That second action is doing real work in these tests: it is
    what proves the press ARRIVED when no message is expected, and it pins the
    property that makes this failure quiet in the first place -- one dead
    device does not stop the rest of a press.

    Two buttons rather than one because where the message is drawn depends on
    where the finger was: one is at the top of the page and one at the bottom.
    """
    settings: dict[str, Any] = {"theme": "dark-default"}
    if not show_error_messages:
        settings["show_error_messages"] = False

    def _button(element_id: str) -> dict[str, Any]:
        return {
            "id": element_id,
            "type": "button",
            "label": "Power",
            "parent": None,
            "bindings": {"do": {"press": [
                {"action": "device.command", "device": "projector",
                 "command": "power_on"},
                {"action": "state.set", "key": "var.last_press",
                 "value": element_id},
            ]}},
        }

    return {
        "openavc_version": "0.11.0",
        "devices": [{
            "id": "projector",
            "driver": "generic_tcp",
            "name": DEAD_NAME,
            "config": {},
            "enabled": True,
            "pending_settings": {},
            "child_entities": {},
        }],
        "connections": {
            "projector": {"host": DEAD_HOST, "port": 4352},
        },
        "variables": [
            {"id": "last_press", "type": "string", "default": "none",
             "label": "Last button pressed"},
        ],
        "ui": {
            "settings": settings,
            "pages": [{
                "id": "main",
                "name": "Main",
                "page_type": "page",
                "layouts": [{
                    "id": "landscape",
                    "orientation": "landscape",
                    "primary": True,
                    "inherits": None,
                    "placements": {
                        "btn_high": {"x": 4.0, "y": 6.0, "w": 30.0, "h": 14.0},
                        "btn_low": {"x": 4.0, "y": 74.0, "w": 30.0, "h": 14.0},
                    },
                    "hidden": [],
                }],
                "elements": [_button("btn_high"), _button("btn_low")],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


@pytest.fixture
def dead_device_panel(server_factory, page: Page):
    """Factory: a panel whose projector is not there, message on or off."""
    def _open(*, show_error_messages: bool = True) -> _ServedPanel:
        handle = server_factory(project_overrides=_dead_device_project(
            show_error_messages=show_error_messages,
        ))
        page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
        page.locator('[data-element-id="btn_high"]').wait_for(
            state="visible", timeout=READY_TIMEOUT,
        )
        return _ServedPanel(page, handle.base_url, [])
    return _open


def test_a_press_that_never_reached_its_device_says_so_on_the_panel(
    dead_device_panel,
) -> None:
    """The whole item, end to end: press, and read the reason off the glass.

    Nothing here is stubbed. The command is refused by the device manager
    because the projector is not connected, swallowed by the binding runtime so
    the rest of the press still runs, reported to this socket alone, and drawn
    by the panel. Every one of those four had to work.
    """
    panel = dead_device_panel()
    band = panel.page.locator("#panel-failure-message")

    expect(band).to_have_count(0)

    panel.element("btn_high").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    # The whole sentence, because the sentence is the deliverable. The device
    # is named the way the room names it: an id would be a message about our
    # data model, handed to somebody holding a remote.
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )


def test_the_message_never_covers_the_control_that_was_just_pressed(
    dead_device_panel,
) -> None:
    """It sits at the bottom, and gets out of the way when that is the wrong end.

    A message drawn over the button somebody still has a finger on cannot be
    read, and looks like the button changed under them.
    """
    panel = dead_device_panel()
    band = panel.page.locator("#panel-failure-message")

    panel.element("btn_low").click()
    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_class(_re.compile(r"\bat-top\b"), timeout=EXPECT_TIMEOUT)

    panel.element("btn_high").click()
    expect(band).not_to_have_class(_re.compile(r"\bat-top\b"), timeout=EXPECT_TIMEOUT)


def test_pressing_a_dead_button_four_times_leaves_one_message(
    dead_device_panel,
) -> None:
    """One problem, one message -- not a wall of them at the worst moment."""
    panel = dead_device_panel()
    band = panel.page.locator("#panel-failure-message")

    for _ in range(4):
        panel.element("btn_high").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_count(1)


def test_a_room_that_turned_the_message_off_never_sees_one(
    dead_device_panel,
) -> None:
    """The switch, proved against a press that definitely arrived.

    The binding's second action writes a variable whatever the first one does,
    so the server confirms the press landed and the command failed inside it.

    Then it waits. "Nothing appeared" is an assertion that needs time to be
    wrong in, and the state poll can beat the frame it is standing in for by a
    few milliseconds -- which is not a bug in the panel but is enough to make
    this test pass while ignoring the setting entirely. The band is up in well
    under a tenth of a second in the test above, so a second and a half is
    generous by more than an order of magnitude.
    """
    panel = dead_device_panel(show_error_messages=False)

    panel.element("btn_high").click()

    _eventually(lambda: panel.state("var.last_press") == "btn_high",
                "the press never reached the instance, so nothing is proved")
    panel.page.wait_for_timeout(1500)
    expect(panel.page.locator("#panel-failure-message")).to_have_count(0)


# ---------------------------------------------------------------------------
# A macro that failed mid-run says why, on the panel that started it (Q-104)
# ---------------------------------------------------------------------------

#: A second absent device, so a macro can fail twice in one run.
DEAD_HOST_2 = "192.0.2.14"
DEAD_NAME_2 = "Rear Projector"


def _macro_project() -> dict[str, Any]:
    """A room whose two projectors are absent, and a preset button for them.

    The macro's last step writes a variable, which is what proves the run
    carried on past the failure and ended on ``completed``: the message
    therefore cannot have come from the run's outcome, because the outcome is
    indistinguishable from a clean run. Same reason the press tests above give
    every button a second action.
    """
    def _device(device_id: str, name: str) -> dict[str, Any]:
        return {
            "id": device_id, "driver": "generic_tcp", "name": name,
            "config": {}, "enabled": True,
            "pending_settings": {}, "child_entities": {},
        }

    def _button(element_id: str, macro_id: str) -> dict[str, Any]:
        return {
            "id": element_id, "type": "button", "label": "Start",
            "parent": None,
            "bindings": {"do": {"press": [{"action": "macro", "macro": macro_id}]}},
        }

    return {
        "openavc_version": "0.11.0",
        "devices": [
            _device("projector", DEAD_NAME),
            _device("projector2", DEAD_NAME_2),
        ],
        "connections": {
            "projector": {"host": DEAD_HOST, "port": 4352},
            "projector2": {"host": DEAD_HOST_2, "port": 4352},
        },
        "variables": [
            {"id": "macro_ran", "type": "string", "default": "no",
             "label": "Did the macro reach its last step"},
            {"id": "routed", "type": "integer", "default": 0,
             "label": "Source routed to the display"},
            {"id": "routed_lobby", "type": "integer", "default": 0,
             "label": "Source routed to the lobby"},
        ],
        "macros": [
            {
                "id": "system_on", "name": "System On",
                "steps": [
                    {"action": "device.command", "device": "projector",
                     "command": "power_on"},
                    {"action": "state.set", "key": "var.macro_ran", "value": "yes"},
                ],
            },
            {
                "id": "outer", "name": "Start Everything",
                "steps": [
                    {"action": "macro", "macro": "system_on"},
                ],
            },
            {
                "id": "both_projectors", "name": "Both Projectors",
                "steps": [
                    {"action": "device.command", "device": "projector",
                     "command": "power_on"},
                    {"action": "device.command", "device": "projector2",
                     "command": "power_on"},
                    {"action": "state.set", "key": "var.macro_ran", "value": "yes"},
                ],
            },
        ],
        "ui": {
            "settings": {"theme": "dark-default"},
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{
                    "id": "landscape", "orientation": "landscape",
                    "primary": True, "inherits": None,
                    "placements": {
                        "btn_on": {"x": 4.0, "y": 6.0, "w": 30.0, "h": 14.0},
                        "btn_both": {"x": 40.0, "y": 6.0, "w": 30.0, "h": 14.0},
                        "btn_gone": {"x": 4.0, "y": 24.0, "w": 30.0, "h": 14.0},
                        "btn_outer": {"x": 4.0, "y": 76.0, "w": 30.0, "h": 14.0},
                        "mx_presets": {"x": 40.0, "y": 24.0, "w": 30.0, "h": 24.0},
                        # Deliberately in the bottom half: the failure band has
                        # to get out of the way of the preset bar, and where
                        # the control is decides which end it draws at.
                        "mx_low": {"x": 40.0, "y": 58.0, "w": 40.0, "h": 38.0},
                    },
                    "hidden": [],
                }],
                "elements": [
                    _button("btn_on", "system_on"),
                    _button("btn_both", "both_projectors"),
                    _button("btn_outer", "outer"),
                    # The macro this one runs is not in the project. Deleting a
                    # macro and leaving the button that ran it is the ordinary
                    # way a room arrives here.
                    _button("btn_gone", "retired_preset"),
                    {
                        "id": "mx_low",
                        "type": "matrix",
                        "label": "Routing",
                        "parent": None,
                        # Its own source and destination values, so the
                        # crosspoint locator above still names exactly one
                        # cell on the page.
                        "matrix_config": {
                            "style": "crosspoint",
                            "sources": [{"value": 9, "label": "Podium"}],
                            "destinations": [
                                {"value": "out9", "label": "Lobby",
                                 "route_key": "var.routed_lobby"},
                            ],
                            "presets": [
                                {"name": "Presentation", "macro": "system_on"},
                            ],
                        },
                        "bindings": {"do": {"route": []}},
                    },
                    {
                        "id": "mx_presets",
                        "type": "matrix",
                        "label": "Routing",
                        "parent": None,
                        "matrix_config": {
                            "style": "crosspoint",
                            "sources": [{"value": 1, "label": "Laptop"}],
                            # The destination carries its OWN route action list,
                            # which runs instead of the element's for this row.
                            # That list is not under `bindings.do` at all, which
                            # is the whole reason this element is here.
                            "destinations": [{
                                "value": "out1", "label": "Display",
                                "route_key": "var.routed",
                                "route": [{"action": "macro", "macro": "system_on"}],
                            }],
                        },
                        "bindings": {"do": {"route": [
                            {"action": "state.set", "key": "var.macro_ran",
                             "value": "the element default ran"},
                        ]}},
                    },
                ],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


@pytest.fixture
def macro_panel(server_factory, page: Page):
    """A panel whose preset buttons run macros that cannot succeed."""
    handle = server_factory(project_overrides=_macro_project())
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    page.locator('[data-element-id="btn_on"]').wait_for(
        state="visible", timeout=READY_TIMEOUT,
    )
    return _ServedPanel(page, handle.base_url, [])


def test_a_macro_that_failed_mid_run_says_so_on_the_panel_that_started_it(
    macro_panel,
) -> None:
    """The item, end to end.

    Nothing comes back on the press itself -- starting a macro succeeds, and
    the engine acks it as accepted -- so this message can only have come from
    the step frame that arrives afterwards, and only because the panel knew
    the press was its own.
    """
    band = macro_panel.page.locator("#panel-failure-message")
    expect(band).to_have_count(0)

    macro_panel.element("btn_on").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )
    # And the run finished, which is the half that makes the failure invisible
    # to anything watching the outcome.
    _eventually(lambda: macro_panel.state("var.macro_ran") == "yes",
                "the macro never reached its last step, so nothing is proved")


def test_only_the_first_failure_of_a_macro_run_draws_a_message(
    macro_panel,
) -> None:
    """Two dead devices in one run, one message, and it names the first.

    A macro that cannot reach a device on step 1 usually cannot reach the next
    one either. Three sentences swapping through one band inside a tenth of a
    second is unreadable, and the first one names the thing to go and fix.
    """
    band = macro_panel.page.locator("#panel-failure-message")

    macro_panel.element("btn_both").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )
    _eventually(lambda: macro_panel.state("var.macro_ran") == "yes",
                "the macro never reached its last step, so the second failure "
                "had not happened yet and this proves nothing")
    # The second failure has been and gone by now. The band still reads the
    # first, rather than having been overwritten.
    expect(band).to_have_text(f"{DEAD_NAME} is not connected.")
    expect(band).to_have_count(1)


def test_a_failure_inside_a_sub_macro_still_reaches_the_panel(macro_panel) -> None:
    """The button ran "Start Everything"; the step that failed belongs to
    "System On", which it calls. A panel matching on the failing macro's own
    id alone would go quiet on the composition the docs recommend."""
    band = macro_panel.page.locator("#panel-failure-message")

    macro_panel.element("btn_outer").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )


def test_a_macro_run_from_a_matrix_row_is_this_panel_s_too(macro_panel) -> None:
    """A destination's own route override is not under `bindings.do`.

    It replaces the element's route list for that one row, so a panel that
    only looked at `bindings.do` would tap this crosspoint, run the macro,
    watch it fail and say nothing -- the exact silence this item is about,
    one level in.
    """
    band = macro_panel.page.locator("#panel-failure-message")

    macro_panel.crosspoint(1, "out1").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )
    # The override ran, not the element's default -- otherwise the macro never
    # started and the message came from somewhere this test does not know.
    _eventually(lambda: macro_panel.state("var.macro_ran") == "yes",
                "the row's own route list never ran, so this proves nothing")


def test_a_macro_this_panel_did_not_start_says_nothing(macro_panel) -> None:
    """Aaron's call, proved: the message belongs to whoever pressed something.

    Run from the instance's own REST API instead of from this panel -- which
    is what a schedule, a trigger, a script or another panel looks like from
    here. The same failure happens, the same frame is broadcast to every
    connected panel, and this one stays quiet because nobody standing at it
    asked for anything.
    """
    with urlopen(
        Request(f"{macro_panel.base_url}/api/macros/system_on/execute", method="POST"),
        timeout=10.0,
    ) as resp:
        assert json.loads(resp.read().decode("utf-8"))["status"] == "executed"

    _eventually(lambda: macro_panel.state("var.macro_ran") == "yes",
                "the macro never ran, so a silent panel proves nothing")
    # "Nothing appeared" needs time to be wrong in. The band is up in well
    # under a tenth of a second in the test above.
    macro_panel.page.wait_for_timeout(1500)
    expect(macro_panel.page.locator("#panel-failure-message")).to_have_count(0)


def test_a_button_whose_macro_was_deleted_says_so_instead_of_nothing(
    macro_panel,
) -> None:
    """Q-139/E3, end to end: the quietest failure of the lot.

    Everything about this press works -- the socket, the binding runtime, the
    element -- and the macro it names is simply not there any more. The start
    happens in a background task nobody awaits, so the refusal used to land in
    the log and the room got a button that does nothing and says nothing.
    """
    band = macro_panel.page.locator("#panel-failure-message")
    expect(band).to_have_count(0)

    macro_panel.element("btn_gone").click()

    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        "No macro named 'retired_preset'.", timeout=EXPECT_TIMEOUT,
    )


def test_the_message_gets_out_of_the_way_of_a_matrix_preset(macro_panel) -> None:
    """Q-139/E8: a preset's frame names no element, so the band placed itself
    against whatever was touched before it.

    Press something at the top of the page first, so "where the last press was"
    is a wrong answer that is available. Then press a preset at the bottom: the
    band has to move to the top, off the preset bar the finger is still on.
    """
    band = macro_panel.page.locator("#panel-failure-message")

    macro_panel.element("btn_on").click()
    expect(band).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(band).not_to_have_class(_re.compile(r"\bat-top\b"), timeout=EXPECT_TIMEOUT)

    macro_panel.page.locator('[data-element-id="mx_low"] .matrix-preset-btn').click()

    expect(band).to_have_class(_re.compile(r"\bat-top\b"), timeout=EXPECT_TIMEOUT)
    expect(band).to_have_text(
        f"{DEAD_NAME} is not connected.", timeout=EXPECT_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _eventually(predicate, message: str, *, timeout: float = 10.0) -> None:
    """Poll the server until it agrees, or fail saying what never happened.

    A press that works is fire-and-forget over the WebSocket -- only a failure
    comes back -- so there is nothing in the browser to await. Polling the
    instance is the honest wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(message)
