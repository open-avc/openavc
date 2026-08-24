"""How many times a panel fetches a custom control's file to draw it once.

This is the one property in the render path that neither caches nor amortises.
Project assets carry ``max-age=3600`` and are fetched once an hour; the ``ui/``
route is ``no-cache`` by design (an author saving a file must see it), so every
request a control costs is a real round trip on every visit, forever. On a wall
tablet over wifi that is latency in front of the person standing at the panel,
which is why it is worth a test rather than a comment.

The page here is deliberately MIXED -- a button beside the control. A page of
nothing but custom controls would pass a narrower fix that a real panel defeats:
a theme's element defaults change on cold start whether or not any of them
belong to a frame, so it is the button that keeps this honest.

Measured through the real server and the real panel, because that is the only
place the count is real: the stubbed-fetch harnesses answer a wrong URL as
happily as a right one, and loopback is exempt from the rate limiter, so no
count taken from the code would have caught the four this replaced.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

READY_TIMEOUT = 15_000

CONTROL_PATH = "room_map/index.html"
CONTROL_URL_TAIL = f"/api/projects/default/ui/{CONTROL_PATH}"

CONTROL_HTML = """<!DOCTYPE html>
<html><body style="margin:0">
  <div id="room">Room map</div>
  <script>
    window.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'openavc:init') {
        document.getElementById('room').textContent = 'ready';
      }
    });
  </script>
</body></html>
"""


def _project(*, control_file: str = CONTROL_PATH) -> dict[str, Any]:
    """A room with one ordinary control and one the integrator wrote."""
    return {
        "openavc_version": "0.11.0",
        "variables": [
            {"id": "last_press", "type": "string", "default": "none",
             "label": "Last button pressed"},
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
                        "map": {"x": 4.0, "y": 24.0, "w": 60.0, "h": 60.0},
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
                        "id": "map",
                        "type": "custom",
                        "label": "Room map",
                        "parent": None,
                        "custom_file": control_file,
                        "custom_config": {},
                    },
                ],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


def _boot(server_factory, page: Page, *, control_file: str = CONTROL_PATH,
          write_control: bool = True):
    """Serve a panel carrying one custom control, counting what it fetches."""
    handle = server_factory(project_overrides=_project(control_file=control_file))

    if write_control:
        target = handle.project_path.parent / "ui" / CONTROL_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(CONTROL_HTML, encoding="utf-8")

    # `resource_type` is what tells the two multipliers apart in a failure
    # message: "document" is a frame loading itself, "fetch" is a probe beside
    # it, and two documents means the page rendered twice.
    fetched: list[str] = []
    page.on("request", lambda r: (
        fetched.append(r.resource_type) if CONTROL_URL_TAIL in r.url else None
    ))
    page.goto(f"{handle.base_url}/panel/", wait_until="domcontentloaded")
    return handle, fetched


def test_a_custom_control_is_fetched_once_to_draw_it_once(
    server_factory, page: Page,
) -> None:
    """One control on one page costs ONE request for its file.

    It used to cost four, and both multipliers were invisible from the code:
    the page renders twice on a cold start (the theme fetch lands after the
    first pass and changes element defaults, so every iframe on the page is
    built, torn down and built again), and each frame fired a second
    ``fetch(src, {cache: 'no-store'})`` beside itself purely to find out
    whether the file was there.
    """
    _handle, fetched = _boot(server_factory, page)

    page.frame_locator('[data-element-id="map"] iframe').locator(
        "#room"
    ).wait_for(state="attached", timeout=READY_TIMEOUT)
    # The second render arrived ~40ms after the first when this was four, so a
    # settle window well past that is what makes a passing count mean anything.
    page.wait_for_timeout(1500)

    assert len(fetched) == 1, (
        f"the panel fetched the control's file {len(fetched)} times to draw it "
        f"once: {fetched}"
    )
    # Reading the status off the frame's own request has a failure mode the
    # count cannot see: a browser that will not report it reads as 0, which is
    # also what a dead request reads as. Get that wrong and every working
    # control wears a failure strip while the count still says one.
    assert page.locator('[data-element-id="map"] .panel-iframe-fault').count() == 0, (
        "a control that loaded fine was accused of failing"
    )


def test_a_missing_file_still_says_so_in_the_box(
    server_factory, page: Page,
) -> None:
    """The probe that was removed existed for this, and this is what had to survive.

    An iframe pointed at a 404 renders the server's JSON error as text, which
    reads as "this control is broken in some unknowable way" rather than "that
    file is not in the project" -- and on a wall panel there is no console to
    check. The status now comes off the frame's OWN request instead of a second
    one, so this is the assertion that proves the saving cost nothing.
    """
    _handle, fetched = _boot(server_factory, page, write_control=False)

    fault = page.locator('[data-element-id="map"] .panel-iframe-fault')
    fault.wait_for(timeout=READY_TIMEOUT)
    assert fault.inner_text() == f"{CONTROL_PATH} could not be loaded (404)"

    # And it is still one request: knowing it was missing cost nothing extra.
    assert len(fetched) == 1, (
        f"finding out the file was missing cost {len(fetched)} requests: {fetched}"
    )
