"""A macro's caller reports failure/cancellation after an awaited subroutine."""

import json
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("outcome", ["failure", "cancel"])
def test_nested_run_reports_the_parent_outcome(server_factory, page, outcome):
    child_step = (
        {"action": "wait_until", "condition": {"key": "var.ready", "operator": "eq", "value": True},
         "timeout": 0.05, "on_timeout": "fail"}
        if outcome == "failure" else {"action": "delay", "seconds": 60}
    )
    server = server_factory(project_overrides={
        "variables": [{"id": "ready", "type": "boolean", "default": False},
                      {"id": "session", "type": "boolean", "default": False}],
        "macros": [
            {"id": "parent", "name": "System On", "stop_on_error": True, "steps": [
                {"action": "macro", "macro": "child"},
                {"action": "state.set", "key": "var.session", "value": True},
            ]},
            {"id": "child", "name": "Display Start", "stop_on_error": True,
             "steps": [child_step]},
        ],
    })
    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("System On", exact=True).click(timeout=15_000)
    page.get_by_role("button", name="Test", exact=True).click()
    if outcome == "cancel":
        cancel = page.get_by_role("button", name="Cancel", exact=True)
        expect(cancel).to_be_enabled()
        cancel.click()
    label = "Failed" if outcome == "failure" else "Cancelled"
    expect(page.get_by_text(f"Last run: {label}", exact=True)).to_be_visible(timeout=10_000)
    expect(page.get_by_role("button", name="Cancel", exact=True)).to_be_disabled()
    with urlopen(f"{server.base_url}/api/state") as response:
        state = json.load(response)["state"]
    assert state["var.session"] is False
