"""The panel, loaded from a running server, and driven like a finger.

Every other panel test in this directory renders ``panel.js`` into a page the
test wrote itself, with ``fetch`` and ``WebSocket`` replaced by stubs. That is
the right harness for asking what the renderer draws, and it is worth keeping.
What it cannot ask is whether the panel a real instance serves ever reaches
that instance -- because in those files nothing is served and nothing is
reached.

That gap has already shipped a defect: a request built as
``/api/api/ui/resolve-matrix`` went out for weeks with the suite green, and was
found by looking at a screenshot. A stubbed ``fetch`` answers a wrong URL
exactly as happily as a right one.

So these tests boot the real server, load ``/panel/`` from it, click with a
real mouse, and then ask the SERVER what changed. No device is involved: the
bindings here write ``var.`` keys, which is a complete round trip -- press,
WebSocket out, binding executed, state written, state broadcast back, control
redrawn -- with nothing on the end of a wire.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import urlopen

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

    This is the assertion the stubbed harness cannot make and the one that
    would have caught ``/api/api/ui/resolve-matrix``: a URL the panel builds
    wrong is answered by a stub exactly as readily as a right one, and only a
    real server says 404.
    """
    served_panel.element("btn_laptop").click()
    served_panel.crosspoint(2, DEST).click()
    _eventually(lambda: served_panel.state("var.last_press") == "laptop",
                "the press never landed, so the request log proves nothing")

    assert served_panel.failed_requests == []


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _eventually(predicate, message: str, *, timeout: float = 10.0) -> None:
    """Poll the server until it agrees, or fail saying what never happened.

    The press is fire-and-forget over the WebSocket -- the panel gets no
    receipt -- so there is nothing in the browser to await. Polling the
    instance is the honest wait.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(message)
