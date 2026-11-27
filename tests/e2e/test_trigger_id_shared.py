"""Two triggers with one id, on the screen.

Only the trigger loaded last with an id fires. The macro editor marks the
other one, naming the trigger that has its id by its macro's name.
"""

from playwright.sync_api import expect

SENTENCE = (
    'This trigger never fires because Trigger 1 of "Evening Off" has the same id. '
    "Delete this trigger and add it again."
)


def _macro(macro_id: str, name: str) -> dict:
    return {
        "id": macro_id, "name": name,
        "steps": [{"action": "delay", "seconds": 0}],
        "triggers": [{
            "id": "trg_shared", "type": "event", "enabled": True,
            "event_pattern": "custom.acme_widget_ready",
        }],
    }


def test_the_trigger_that_never_fires_is_marked(server_factory, page):
    server = server_factory(project_overrides={
        "macros": [_macro("morning_on", "Morning On"), _macro("evening_off", "Evening Off")],
    })
    page.goto(f"{server.base_url}/programmer/#macros")

    # The list marks the macro whose trigger never fires, and only that one.
    expect(page.get_by_text("1 trigger won't run", exact=True)).to_have_count(1, timeout=15_000)

    page.get_by_text("Morning On", exact=True).click()
    expect(page.get_by_text("1 trigger won't run as built")).to_be_visible(timeout=10_000)
    expect(page.get_by_text(SENTENCE)).to_be_visible()

    page.get_by_text("Evening Off", exact=True).click()
    expect(page.get_by_text("won't run as built")).to_have_count(0)
