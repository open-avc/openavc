"""The setup screen's network settings, walked in a real browser.

The setup screen (``/setup``) is how a device with no keyboard joins a network:
WiFi, a static address, the WiFi radio, all from its own display. Each of
those calls answers ``success`` (with ``error`` beside it when the host said
no), and the screen has to read that answer, not guess it: a join that worked
and a join that failed must not look alike.

The host's network is a stand-in loaded the way an appliance image loads its
own: ``network.backend_module`` in system.json names a module whose backend
answers from a script file the test rewrites between steps. So everything
from the button to the API's answer is the real thing, and only the host's
network tooling is not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000

_BACKEND = '''
import json
import os

from openavc.system.network import NetworkBackend


def _answer(name):
    with open(os.environ["SETUP_SCREEN_NET_SCRIPT"], encoding="utf-8") as f:
        return dict(json.load(f)[name])


class _Backend(NetworkBackend):
    name = "stand-in"

    async def get_status(self):
        return {
            "backend": self.name,
            "hostname": "acme-room",
            "wifi_enabled": True,
            "capabilities": {
                "ipv4": True, "wifi": True, "hostname": True,
                "ipv4_apply": _answer("status")["ipv4_apply"],
            },
            "interfaces": [{
                "device": "eth0", "type": "ethernet", "state": "connected",
                "connection": "Wired connection 1", "mac": None,
                "ip4": {"addresses": ["192.168.1.50/24"], "gateway": "192.168.1.1", "dns": []},
                "config": {"method": "auto", "addresses": [], "gateway": None, "dns": []},
            }, {
                "device": "wlan0", "type": "wifi", "state": "disconnected",
                "connection": None, "mac": None,
                "ip4": {"addresses": [], "gateway": None, "dns": []},
                "config": None,
            }],
        }

    async def set_ipv4(self, connection, method, address=None, gateway=None, dns=None):
        return _answer("set_ipv4")

    async def wifi_scan(self):
        return [{"ssid": "Acme Lab", "signal": 80, "secured": True, "in_use": False}]

    async def wifi_connect(self, ssid, psk=None):
        return _answer("wifi_connect")

    async def wifi_set_enabled(self, enabled):
        return _answer("wifi_set_enabled")

    async def set_hostname(self, name):
        return {"ok": True}


def create_backend():
    return _Backend()
'''

_DEFAULTS: dict[str, Any] = {
    "status": {"ipv4_apply": "live"},
    "set_ipv4": {"ok": True},
    "wifi_connect": {"ok": True},
    "wifi_set_enabled": {"ok": True, "enabled": False},
}


class _Host:
    """The stand-in host network: what each call answers, rewritten per step."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "setup_screen_net.py").write_text(_BACKEND, encoding="utf-8")
        self.folder = folder
        self.script = folder / "answers.json"
        self.answers = dict(_DEFAULTS)
        self._write()

    def answer(self, call: str, reply: dict[str, Any]) -> None:
        self.answers[call] = reply
        self._write()

    def _write(self) -> None:
        self.script.write_text(json.dumps(self.answers), encoding="utf-8")


@pytest.fixture
def host(tmp_path: Path) -> _Host:
    return _Host(tmp_path / "host-net")


def _open_network_settings(server_factory, host: _Host, page: Page):
    server = server_factory(
        system_json={"network": {"backend_module": "setup_screen_net"}},
        env={
            "PYTHONPATH": str(host.folder),
            "SETUP_SCREEN_NET_SCRIPT": str(host.script),
        },
    )
    page.goto(f"{server.base_url}/setup")
    toggle = page.locator("#netcfg-toggle")
    expect(toggle).to_be_visible(timeout=EXPECT_TIMEOUT)
    toggle.click()
    expect(page.locator("#netcfg-card")).to_be_visible(timeout=EXPECT_TIMEOUT)
    return server


def _join(page: Page, password: str) -> None:
    page.locator("#wifi-list li", has_text="Acme Lab").click()
    page.locator("#wifi-psk").fill(password)
    page.locator("#wifi-connect").click()


def test_a_wifi_join_that_worked_says_connected_and_one_that_failed_says_why(
    server_factory, host: _Host, page: Page,
) -> None:
    _open_network_settings(server_factory, host, page)
    page.locator("#wifi-scan").click()
    expect(page.locator("#wifi-list li", has_text="Acme Lab")).to_be_visible(timeout=EXPECT_TIMEOUT)

    message = page.locator("#wifi-msg")
    _join(page, "right password")
    expect(message).to_have_text("Connected to Acme Lab.", timeout=EXPECT_TIMEOUT)
    expect(message).to_have_class("net-msg ok")
    expect(page.locator("#wifi-join")).to_be_hidden()

    host.answer("wifi_connect", {"ok": False, "error": "Secrets were required, but not provided."})
    _join(page, "wrong password")
    expect(message).to_have_text("Secrets were required, but not provided.", timeout=EXPECT_TIMEOUT)
    expect(message).to_have_class("net-msg err")


def test_an_address_change_that_applied_says_applied(
    server_factory, host: _Host, page: Page,
) -> None:
    _open_network_settings(server_factory, host, page)
    shared = page.locator("#netcfg-msg")

    page.locator(".iface-block", has_text="eth0").get_by_role("button", name="Apply").click()
    expect(shared).to_have_text("eth0: applied.", timeout=EXPECT_TIMEOUT)
    expect(shared).to_have_class("net-msg ok")

    host.answer("set_ipv4", {"ok": False, "rolled_back": True, "error": "Activation timed out."})
    page.locator(".iface-block", has_text="eth0").get_by_role("button", name="Apply").click()
    expect(shared).to_have_text(
        "eth0: change failed and the previous settings were restored: Activation timed out.",
        timeout=EXPECT_TIMEOUT,
    )


def test_an_address_change_saved_for_a_restart_says_the_device_is_restarting(
    server_factory, host: _Host, page: Page,
) -> None:
    host.answer("status", {"ipv4_apply": "reboot"})
    host.answer("set_ipv4", {"ok": True, "reboot": True})
    _open_network_settings(server_factory, host, page)

    page.locator(".iface-block", has_text="eth0").get_by_role("button", name="Apply").click()
    shared = page.locator("#netcfg-msg")
    expect(shared).to_have_text(
        "eth0: saved. The device is restarting to apply the new settings.",
        timeout=EXPECT_TIMEOUT,
    )
    expect(shared).to_have_class("net-msg ok")


def test_a_wifi_radio_change_the_host_refused_is_put_back_and_says_why(
    server_factory, host: _Host, page: Page,
) -> None:
    host.answer("wifi_set_enabled", {"ok": False, "error": "WiFi is held off on this device."})
    _open_network_settings(server_factory, host, page)
    radio = page.locator("#wifi-radio")
    expect(radio).to_be_checked(timeout=EXPECT_TIMEOUT)

    radio.uncheck()
    expect(page.locator("#wifi-radio-msg")).to_have_text(
        "WiFi is held off on this device.", timeout=EXPECT_TIMEOUT,
    )
    expect(radio).to_be_checked()
    expect(page.locator("#wifi-controls")).to_be_visible()
