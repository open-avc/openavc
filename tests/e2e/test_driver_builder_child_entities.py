"""Playwright test for declaring child entity types in the Driver Builder.

Covers the authoring round-trip: an author opens the Driver Builder, declares a child entity type
with a state field, saves, and the declaration round-trips through the
.avcdriver YAML on disk and back into the editor after a full reload.

Like the other e2e tests, this boots a real ``server.main`` subprocess
(the ``openavc_server`` fixture in conftest) and drives a real Chromium.
New drivers created through the builder are persisted to
``{data_dir}/driver_repo/<id>.avcdriver`` by the create endpoint, so the
test asserts both the on-disk YAML and the reloaded editor state.

Selectors come from the data-testid attributes on
``web/programmer/src/components/driver-builder/ChildEntityTypesEditor.tsx``.
"""

from __future__ import annotations

import re
import time

import yaml
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000

DRIVER_ID = "e2e_child_driver"
DRIVER_NAME = "E2E Child Driver"


def _rename_id(page: Page, testid: str, new_id: str) -> None:
    """Type a new identifier into an ID field and commit it.

    The Builder's ID inputs commit on blur or Enter rather than on every
    keystroke: renaming per keystroke would remount the row under each
    half-typed name. So filling one is not enough — press Enter, or the old
    name stays and every locator keyed to the new one times out.
    """
    field = page.get_by_test_id(testid)
    field.fill(new_id)
    field.press("Enter")


def _open_driver_builder_create(page: Page, base_url: str) -> None:
    """Navigate to Devices -> Drivers sub-tab -> Create view tab."""
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    # Devices view sub-tabs are role="tab"; the Drivers panel hosts the
    # Driver Builder.
    page.get_by_role("tab", name="Drivers").click()
    # The Driver Builder's own view tabs (Installed / Create / Browse) are
    # plain buttons; "Create" exact-matches only the view tab, not the
    # "Create New Driver" button.
    page.get_by_role("button", name="Create", exact=True).click()


def _expand_child_entity_types_section(page: Page) -> None:
    """Open the 'Child Entity Types' collapsible if it isn't already.

    For a brand-new driver the section starts collapsed (no types yet);
    for a driver that already declares types it auto-opens. Toggling only
    when collapsed keeps both paths correct.
    """
    header = page.locator(
        'button[aria-expanded]:has-text("Child Entity Types")'
    )
    header.wait_for(state="visible", timeout=SELECT_TIMEOUT)
    if header.get_attribute("aria-expanded") == "false":
        header.click()


def test_declare_child_type_persists_to_yaml_and_reload(
    openavc_server, page: Page,
):
    """Author a driver with one child type + field, save, and verify the
    declaration survives both on disk (.avcdriver YAML) and a full page
    reload back into the editor.
    """
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)

    # ── Create a new driver ────────────────────────────────────────────
    _open_driver_builder_create(page, handle.base_url)
    page.get_by_role("button", name="Create New Driver").click()

    # General tab — id + name are the minimum to save.
    page.get_by_placeholder("e.g., extron_sw4").fill(DRIVER_ID)
    page.get_by_placeholder("e.g., Extron SW4 HD 4K").fill(DRIVER_NAME)

    # ── Declare a child type on the Behavior tab ───────────────────────
    page.get_by_role("button", name="Behavior", exact=True).click()
    _expand_child_entity_types_section(page)

    page.get_by_test_id("add-child-type").click()
    # New card seeds as child_type_1; rename it to "encoder". The ID field
    # commits on blur or Enter, not on every keystroke — renaming per keystroke
    # would remount the row under a half-typed name — so press Enter to commit
    # before anything looks for the new name.
    _rename_id(page, "child-type-id-child_type_1", "encoder")

    page.get_by_test_id("child-type-label-encoder").fill("Encoder")

    # Declare one state field for the child type.
    page.get_by_test_id("add-child-field").click()
    field_id = page.get_by_test_id("child-field-id-field_1")
    expect(field_id).to_be_visible(timeout=EXPECT_TIMEOUT)
    _rename_id(page, "child-field-id-field_1", "signal_present")
    # Re-locate after the rename (testid tracks the field name).
    page.get_by_test_id("child-field-id-signal_present").wait_for(
        state="visible", timeout=EXPECT_TIMEOUT,
    )

    # ── Save and verify the on-disk YAML ───────────────────────────────
    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = handle.data_dir / "driver_repo" / f"{DRIVER_ID}.avcdriver"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not driver_file.exists():
        time.sleep(0.1)
    assert driver_file.exists(), (
        f"Driver was not saved to {driver_file} within budget"
    )

    saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
    cet = saved.get("child_entity_types")
    assert cet, f"child_entity_types missing from saved YAML: {saved!r}"
    assert "encoder" in cet, f"encoder type missing: {cet!r}"
    encoder = cet["encoder"]
    assert encoder["label"] == "Encoder"
    assert encoder["id_format"]["type"] == "integer"
    assert "signal_present" in encoder["state_variables"]

    # ── Full reload — editor must rebuild the declaration from disk ────
    page.reload(wait_until="domcontentloaded")
    _open_driver_builder_create(page, handle.base_url)
    # The saved driver shows up in the list; open it. `.first` avoids the
    # per-row Copy/Export/Delete sub-buttons tripping strict mode.
    page.locator(f'button:has-text("{DRIVER_NAME}")').first.click()

    page.get_by_role("button", name="Behavior", exact=True).click()
    _expand_child_entity_types_section(page)

    # Card, label, and field all came back from the persisted YAML.
    expect(page.get_by_test_id("child-type-card-encoder")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(page.get_by_test_id("child-type-label-encoder")).to_have_value(
        "Encoder", timeout=EXPECT_TIMEOUT,
    )
    expect(
        page.get_by_test_id("child-field-id-signal_present")
    ).to_be_visible(timeout=EXPECT_TIMEOUT)


def test_command_child_id_param_persists(openavc_server, page: Page):
    """A command parameter typed as 'Child ID' referencing a declared
    child type round-trips to YAML as ``type: child_id`` with the
    selected ``child_type``.
    """
    handle = openavc_server
    page.set_default_timeout(SELECT_TIMEOUT)

    _open_driver_builder_create(page, handle.base_url)
    page.get_by_role("button", name="Create New Driver").click()
    page.get_by_placeholder("e.g., extron_sw4").fill("e2e_child_cmd_driver")
    page.get_by_placeholder("e.g., Extron SW4 HD 4K").fill("E2E Child Cmd Driver")

    page.get_by_role("button", name="Behavior", exact=True).click()

    # Declare the child type first so the command param dropdown has a
    # type to reference.
    _expand_child_entity_types_section(page)
    page.get_by_test_id("add-child-type").click()
    _rename_id(page, "child-type-id-child_type_1", "decoder")

    # Add a command with a child_id parameter.
    page.get_by_role("button", name="Add Command").click()
    # The new command auto-expands; add a parameter.
    page.get_by_role("button", name="+ Add Parameter").click()
    # Switch the param type to Child ID. Only the command param type
    # <select> carries a child_id option, so this targets it unambiguously
    # (id_format and state-field selects don't offer child_id).
    type_select = page.locator('select:has(option[value="child_id"])').last
    type_select.select_option("child_id")
    # Selecting child_id reveals the child-type dropdown — the only select
    # offering a "decoder" option.
    child_type_select = page.locator('select:has(option[value="decoder"])').last
    child_type_select.select_option("decoder")

    # A TCP command needs a non-empty wire string or the server rejects it
    # (validate_driver_definition requires send/path/address). Reference
    # the child_id param so the substitution is realistic.
    page.get_by_placeholder(re.compile("POWR")).fill("ROUTE {param1}")

    page.get_by_role("button", name="Save", exact=True).click()

    driver_file = (
        handle.data_dir / "driver_repo" / "e2e_child_cmd_driver.avcdriver"
    )
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not driver_file.exists():
        time.sleep(0.1)
    assert driver_file.exists(), f"Driver not saved to {driver_file}"

    saved = yaml.safe_load(driver_file.read_text(encoding="utf-8"))
    commands = saved.get("commands", {})
    assert commands, f"no commands persisted: {saved!r}"
    # Exactly one command was added; find its child_id param.
    params = next(iter(commands.values())).get("params", {})
    child_params = [
        p for p in params.values() if p.get("type") == "child_id"
    ]
    assert child_params, f"no child_id param persisted: {params!r}"
    assert child_params[0]["child_type"] == "decoder"
