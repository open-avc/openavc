"""Why a trigger's fire or a macro's run did not go ahead, on the screen.

The server has said why since its reason table came in; the trigger card and
the macro editor showed nothing, and the Test button guessed.
"""

import json
import time
from urllib.request import Request, urlopen

from playwright.sync_api import expect


def _set(server, key: str, value) -> None:
    request = Request(
        f"{server.base_url}/api/state/{key}",
        data=json.dumps({"value": value}).encode(),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urlopen(request):
        pass


def test_the_trigger_card_and_the_macro_editor_say_why(server_factory, page):
    server = server_factory(project_overrides={
        "variables": [{"id": "level", "type": "number", "default": 0}],
        "macros": [{
            "id": "lights", "name": "Lights On", "cooldown_seconds": 600,
            "steps": [{"action": "delay", "seconds": 0}],
            "triggers": [{
                "id": "trg_level", "type": "state_change", "enabled": True,
                "state_key": "var.level", "state_operator": "any",
                "cooldown_seconds": 600,
            }],
        }],
    })
    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("Lights On", exact=True).click(timeout=15_000)

    # The trigger fires once, then its own Cooldown refuses the next change.
    _set(server, "var.level", 1)
    time.sleep(0.5)
    _set(server, "var.level", 2)
    expect(
        page.get_by_text("Skipped: The trigger's Cooldown had not passed since it last fired.")
    ).to_be_visible(timeout=10_000)

    # The macro has run, so its own Cooldown refuses Test, which says why.
    page.get_by_role("button", name="Test", exact=True).click()
    reason = "The macro's Cooldown (600 seconds) had not passed since it last started."
    expect(page.get_by_text(f"The macro didn't start. {reason}")).to_be_visible(timeout=10_000)
    # And the editor keeps it under the steps, as it would for a refusal at
    # any other door.
    expect(page.get_by_text(f"Skipped: {reason}")).to_be_visible()
