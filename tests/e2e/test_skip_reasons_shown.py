"""Why a trigger's fire or a macro's run did not go ahead, on the screen.

The server has said why since its reason table came in; the trigger card and
the macro editor showed nothing, and the Test button guessed.
"""

import json
import time
from urllib.request import Request, urlopen

from playwright.sync_api import TimeoutError as PlaywrightTimeout
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

    # The trigger fires once, then its own Cooldown refuses every change after.
    # The card hears only a skip sent while its socket is up, so changes go on
    # until one is heard.
    _set(server, "var.level", 1)
    skipped = page.get_by_text(
        "Skipped: The trigger's Cooldown had not passed since it last started its macro."
    )
    for level in range(2, 40):
        _set(server, "var.level", level)
        try:
            skipped.wait_for(state="visible", timeout=500)
            break
        except PlaywrightTimeout:
            continue
    expect(skipped).to_be_visible()

    # The macro has run, so its own Cooldown refuses Test, which says why.
    page.get_by_role("button", name="Test", exact=True).click()
    reason = "The macro's Cooldown (600 seconds) had not passed since it last started."
    expect(page.get_by_text(f"The macro didn't start. {reason}")).to_be_visible(timeout=10_000)
    # And the editor keeps it under the steps, as it would for a refusal at
    # any other door.
    expect(page.get_by_text(f"Skipped: {reason}")).to_be_visible()
    # A second press straight after is the server's to refuse, and says so.
    page.get_by_role("button", name="Test", exact=True).click()
    expect(page.get_by_text("Too many requests", exact=False).first).to_be_visible(timeout=10_000)


def test_a_help_step_its_cooldown_held_back_is_shown_skipped(server_factory, page):
    """An Ask for Help reached again inside its Send at most every sends
    nothing; the run still completes, and the last run says which step was
    skipped and why."""
    server = server_factory(project_overrides={
        "macros": [{
            "id": "call_help", "name": "Call for Help",
            "steps": [{"action": "help.request", "message": "Room 2", "cooldown": 600}],
        }],
    })
    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("Call for Help", exact=True).click(timeout=15_000)

    test = page.get_by_role("button", name="Test", exact=True)
    test.click()
    expect(page.get_by_text("Last run: Completed")).to_be_visible(timeout=10_000)
    # The run button takes one call per 2 seconds for a macro.
    page.wait_for_timeout(2_100)
    test.click()
    reason = (
        "Help was asked for less than 600 seconds ago, and this step sends at most "
        "once in that time, so no new request was sent."
    )
    summary_line = page.get_by_text(f"Skipped: {reason}").last
    expect(summary_line).to_be_visible(timeout=10_000)
    expect(page.get_by_text("Step 1:", exact=True)).to_be_visible()
    expect(page.get_by_text("Last run: Completed")).to_be_visible()


def _last_outcome(server, trigger_id: str):
    with urlopen(f"{server.base_url}/api/triggers") as response:
        triggers = json.load(response)["triggers"]
    return next(t for t in triggers if t["id"] == trigger_id)["last_outcome"]


def _wait_for_outcome(server, trigger_id: str, outcome: str) -> None:
    for _ in range(200):
        if _last_outcome(server, trigger_id) == outcome:
            return
        time.sleep(0.05)
    assert _last_outcome(server, trigger_id) == outcome


def test_a_fire_its_macro_refused_is_said_on_the_trigger_card(server_factory, page):
    """The trigger fired and its macro's own Cooldown refused the run: the
    card says the macro's reason, from the trigger's last run, so it is there
    when the Programmer is opened afterwards."""
    server = server_factory(project_overrides={
        "variables": [{"id": "level", "type": "number", "default": 0}],
        "macros": [{
            "id": "lights", "name": "Lights On", "cooldown_seconds": 600,
            "steps": [{"action": "delay", "seconds": 0}],
            "triggers": [{
                "id": "trg_level", "type": "state_change", "enabled": True,
                "state_key": "var.level", "state_operator": "any",
            }],
        }],
    })
    _set(server, "var.level", 1)
    _wait_for_outcome(server, "trg_level", "completed")
    _set(server, "var.level", 2)
    _wait_for_outcome(server, "trg_level", "skipped")

    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("Lights On", exact=True).click(timeout=15_000)

    reason = "The macro's Cooldown (600 seconds) had not passed since it last started."
    expect(page.get_by_text(f"Skipped: {reason}")).to_be_visible(timeout=10_000)
    expect(page.get_by_text("Last run skipped")).to_have_count(0)
