"""A condition's operator picker shows the operator the project holds.

The runtime takes other spellings of an operator (">=", "greater_than") and
fires a State Change trigger saved with a blank operator on any change. The
pickers listed only the operators' own names, so a saved spelling showed as
the first choice, and a blank trigger operator showed a Value box the engine
never reads.
"""

from playwright.sync_api import expect


def _picker_showing(page, label: str):
    """The operator picker whose shown option reads ``label``."""
    return page.locator("select").filter(has=page.locator("option:checked", has_text=label))


def test_each_picker_shows_the_stored_operator(server_factory, page):
    server = server_factory(project_overrides={
        "variables": [{"id": "level", "type": "number", "default": 0}],
        "macros": [{
            "id": "lights", "name": "Lights On",
            "steps": [{
                "action": "delay", "seconds": 0,
                "skip_if": {"key": "var.level", "operator": ">=", "value": 5},
            }],
            "triggers": [{
                "id": "trg_level", "type": "state_change", "enabled": True,
                "state_key": "var.level", "state_operator": "",
                "conditions": [{"key": "var.level", "operator": "greater_than", "value": 2}],
            }],
        }],
    })
    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("Lights On", exact=True).click(timeout=15_000)

    # The trigger's card sums it up as the engine reads it.
    expect(page.get_by_text("var.level changes").first).to_be_visible(timeout=10_000)

    page.get_by_text("var.level changes").first.click()
    state_picker = _picker_showing(page, "Changes (any value)")
    expect(state_picker).to_have_count(1)
    expect(state_picker).to_have_value("any")
    expect(_picker_showing(page, "Greater Than (greater_than)")).to_have_value("greater_than")

    # The step's Skip If.
    page.get_by_text("0s", exact=True).click()
    expect(_picker_showing(page, "greater or equal (>=)")).to_have_value(">=")
