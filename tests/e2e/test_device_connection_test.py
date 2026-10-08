"""Test Connection on a device page names the address it tried.

The server resolves the address the way the device manager does (here the host
and port sit in the project's connections table, where the IDE saves them),
and the result line says where it went: "Connected to 127.0.0.1:<port> (N ms)",
or "Could not connect to <address>: <reason>". A listener in this process
stands in for the device, so the test also sees the connection arrive.
"""

from __future__ import annotations

import socket
import threading

import pytest
from playwright.sync_api import Page, expect

SELECT_TIMEOUT = 15_000
EXPECT_TIMEOUT = 10_000


@pytest.fixture
def listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.settimeout(0.2)
    accepted: list[int] = []
    stop = threading.Event()

    def accept_loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                continue
            accepted.append(1)
            conn.close()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1], accepted
    finally:
        stop.set()
        thread.join(timeout=2)
        sock.close()


def _press_test_connection(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').wait_for(
        state="visible", timeout=SELECT_TIMEOUT,
    )
    page.locator('button[aria-label="Devices"]').click()
    page.locator('button:has-text("Test Controller")').first.click()
    button = page.locator('button[title="Test device connection"]')
    button.wait_for(state="visible", timeout=SELECT_TIMEOUT)
    button.click()


def test_a_success_names_the_address_it_reached(server_factory, listener, page: Page):
    port, accepted = listener
    server = server_factory(project_overrides={
        "connections": {"ctrl1": {"host": "127.0.0.1", "port": port}},
    })
    _press_test_connection(page, server.base_url)
    expect(page.get_by_text(f"Connected to 127.0.0.1:{port} (")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    assert accepted, "the test reported a connection the listener never saw"


def test_a_failure_names_the_address_and_what_to_do(server_factory, page: Page):
    """No port in the device's settings and none from its driver."""
    server = server_factory(project_overrides={
        "connections": {"ctrl1": {"host": "127.0.0.1"}},
    })
    _press_test_connection(page, server.base_url)
    expect(page.get_by_text(
        "Could not connect to 127.0.0.1: No port is set for this device, "
        "and its driver does not supply one."
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
