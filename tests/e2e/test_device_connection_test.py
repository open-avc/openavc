"""Test Connection on a device page names the address it tried.

The server resolves the address the way the device manager does (here the host
and port sit in the project's connections table, where the IDE saves them),
and the result line says where it went: "Connected to 127.0.0.1:<port> (N ms)",
or the server's sentence for the failure, which names the address once when
the address is what failed. A listener in this process stands in for the
device, so the test also sees the connection arrive.

A device that is connected is not dialed: its driver holds the connection, and
a device that serves one connection at a time would give it to the test. The
line names the address with no time, and the note says no second connection
was opened. The e2e controller reports connected as soon as it starts, so the
tests of the dial itself run it disabled.
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


def _not_connected(connection: dict) -> dict:
    """Project overrides for the seed controller disabled, so it is not
    connected and the test dials its address."""
    return {
        "devices": [{
            "id": "ctrl1", "driver": "e2e_test_controller", "name": "Test Controller",
            "config": {"initial_children": 0}, "enabled": False,
            "pending_settings": {}, "child_entities": {},
        }],
        "connections": {"ctrl1": connection},
    }


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
    server = server_factory(project_overrides=_not_connected({"host": "127.0.0.1", "port": port}))
    _press_test_connection(page, server.base_url)
    expect(page.get_by_text(f"Connected to 127.0.0.1:{port} (")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    assert accepted, "the test reported a connection the listener never saw"


def test_a_connected_device_is_not_dialed(server_factory, listener, page: Page):
    port, accepted = listener
    server = server_factory(project_overrides={
        "connections": {"ctrl1": {"host": "127.0.0.1", "port": port}},
    })
    _press_test_connection(page, server.base_url)
    line = page.get_by_text(f"Connected to 127.0.0.1:{port}").first
    expect(line).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert " ms)" not in line.inner_text(), line.inner_text()
    expect(page.get_by_text(
        "The device is connected now. The test did not open a second connection to it."
    )).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert not accepted, "the test opened a connection to a port the driver holds"


def test_a_refused_port_names_the_address_once(server_factory, page: Page):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = server_factory(project_overrides=_not_connected({"host": "127.0.0.1", "port": port}))
    _press_test_connection(page, server.base_url)
    line = page.get_by_text(f"Connection refused on 127.0.0.1:{port}.")
    expect(line).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert line.inner_text().count("127.0.0.1") == 1, line.inner_text()


def test_a_missing_port_says_what_to_do(server_factory, page: Page):
    """No port in the device's settings and none from its driver."""
    server = server_factory(project_overrides={
        "connections": {"ctrl1": {"host": "127.0.0.1"}},
    })
    _press_test_connection(page, server.base_url)
    line = page.get_by_text(
        "No port is set for this device, and its driver does not supply one."
    )
    expect(line).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert "127.0.0.1" not in line.inner_text(), line.inner_text()
