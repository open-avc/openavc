"""The page of a device that connects over SSH shows OpenAVC's own SSH key.

The section carries the public key line, what to do with it, its fingerprint
and a copy button; a device on any other transport shows none of it. The seed
controller is kept disabled so nothing dials the address.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 15_000

HELP = (
    "Add this key to the device's user account to sign in without a password. "
    "In Edit, choose key sign-in and leave the private key path blank."
)


def _project(connection: dict) -> dict:
    return {
        "devices": [{
            "id": "ctrl1", "driver": "e2e_test_controller", "name": "Test Controller",
            "config": {"initial_children": 0}, "enabled": False,
            "pending_settings": {}, "child_entities": {},
        }],
        "connections": {"ctrl1": connection},
    }


def _open_device(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.locator('button:has-text("Test Controller")').first.click()
    page.locator('button[title="Test device connection"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )


def test_an_ssh_device_shows_openavcs_key(server_factory, page: Page):
    server = server_factory(project_overrides=_project(
        {"transport": "ssh", "host": "127.0.0.1", "port": 22},
    ))
    _open_device(page, server.base_url)
    section = page.get_by_test_id("ssh-key-section")
    expect(section.get_by_role("heading", name="OpenAVC SSH key")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    expect(section.get_by_text(HELP)).to_be_visible()
    key = section.get_by_label("OpenAVC SSH public key")
    expect(key).to_contain_text("ssh-rsa AAAA", timeout=EXPECT_TIMEOUT)
    assert "PRIVATE" not in key.inner_text()
    expect(section.get_by_text("Fingerprint SHA256:")).to_be_visible()
    expect(section.get_by_role("button", name="Copy the SSH key")).to_be_visible()


def test_a_device_on_another_transport_shows_no_key(server_factory, page: Page):
    server = server_factory(project_overrides=_project(
        {"host": "127.0.0.1", "port": 9},
    ))
    _open_device(page, server.base_url)
    expect(page.get_by_text("Live State").first).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_test_id("ssh-key-section")).to_have_count(0)
