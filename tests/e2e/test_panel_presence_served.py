"""A served panel's marks follow a real device's presence word as it drops and comes back.

The panel decides whether a reading counts and whether a press can land from
``device.<id>.presence``, the one word the server publishes for a device. The
harness tests (tests/test_panel_js.py) feed that word by hand. What they cannot
show is that a running instance sends it to a panel as its device comes and
goes. So this boots the real server with a device on a local TCP socket the
test opens and closes, loads ``/panel/`` from it, and watches the marks: up,
dropped, and back.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
from typing import Any
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
READY_TIMEOUT = 15_000
DEVICE = "acme_amp"
PRESENCE_KEY = f"device.{DEVICE}.presence"
XDIST_SPREAD = True


class _Listener:
    """A TCP port that takes connections and holds them until it is closed.

    Closing it frees the port at once, so the test can open it again. On
    Linux, closing a socket another thread is waiting in ``accept()`` on
    leaves it listening until that wait ends, so the wait is short and
    ``close()`` ends it before closing the socket.
    """

    def __init__(self, port: int = 0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", port))
        self.sock.listen(4)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.held: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.held.append(conn)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for conn in self.held:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.sock.close()


def _project(port: int) -> dict[str, Any]:
    """One device and three controls for it: one showing its word, one
    reading from it, one whose press reaches it."""
    def _label(element_id: str, key: str) -> dict[str, Any]:
        return {"id": element_id, "type": "label", "text": "", "parent": None,
                "bindings": {"show": {"value": {"key": key}}}}

    return {
        "openavc_version": "0.11.0",
        "devices": [{
            "id": DEVICE, "driver": "generic_tcp", "name": "Amplifier",
            "config": {}, "enabled": True, "pending_settings": {}, "child_entities": {},
        }],
        "connections": {DEVICE: {"host": "127.0.0.1", "port": port}},
        "ui": {
            "settings": {"theme": "dark-default"},
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "layouts": [{
                    "id": "landscape", "orientation": "landscape", "primary": True,
                    "inherits": None, "hidden": [],
                    "placements": {
                        "word": {"x": 4.0, "y": 6.0, "w": 30.0, "h": 14.0},
                        "reading": {"x": 4.0, "y": 30.0, "w": 30.0, "h": 14.0},
                        "press": {"x": 4.0, "y": 54.0, "w": 30.0, "h": 14.0},
                    },
                }],
                "elements": [
                    _label("word", PRESENCE_KEY),
                    _label("reading", f"device.{DEVICE}.gain"),
                    {"id": "press", "type": "button", "label": "Mute", "parent": None,
                     "bindings": {"do": {"press": [
                         {"action": "device.command", "device": DEVICE, "command": "mute"},
                     ]}}},
                ],
                "master_elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


def _word(base_url: str) -> Any:
    with urlopen(f"{base_url}/api/state/{PRESENCE_KEY}", timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8")).get("value")


def _eventually(predicate, message: str, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError(message)


def _expect_marks(page: Page, *, away: bool) -> None:
    reading = page.locator('[data-element-id="reading"]')
    press = page.locator('[data-element-id="press"]')
    mark = re.compile(r"\bdevice-offline\b")
    edge = re.compile(r"\baction-offline\b")
    if away:
        expect(reading).to_have_class(mark, timeout=EXPECT_TIMEOUT)
        expect(press).to_have_class(edge, timeout=EXPECT_TIMEOUT)
    else:
        expect(reading).not_to_have_class(mark, timeout=EXPECT_TIMEOUT)
        expect(press).not_to_have_class(edge, timeout=EXPECT_TIMEOUT)


def test_a_closed_listener_gives_its_port_back_at_once() -> None:
    """The drop-and-return test below opens the same port again right after
    closing it, before the server has tried to reach it."""
    first = _Listener()
    first.close()
    again = _Listener(first.port)
    again.close()


def test_the_marks_follow_the_word_as_a_device_drops_and_comes_back(server_factory, page: Page) -> None:
    listener = _Listener()
    port = listener.port
    try:
        handle = server_factory(project_overrides=_project(port))
        base = handle.base_url
        page.goto(f"{base}/panel/", wait_until="domcontentloaded")
        word = page.locator('[data-element-id="word"]')
        word.wait_for(state="visible", timeout=READY_TIMEOUT)

        _eventually(lambda: _word(base) == "online", "the device never came online")
        expect(word).to_have_text("online", timeout=EXPECT_TIMEOUT)
        _expect_marks(page, away=False)

        # The far end goes away: the server drops the device and says so in
        # its word, and the panel draws it away without being told anything
        # else.
        listener.close()
        _eventually(lambda: _word(base) not in (None, "online"), "the drop never reached the word")
        _expect_marks(page, away=True)
        expect(word).to_have_text(re.compile(r"^(offline|connecting)$"), timeout=EXPECT_TIMEOUT)

        # It comes back on the same port, and the marks go with the word.
        listener = _Listener(port)
        with urlopen(Request(f"{base}/api/devices/{DEVICE}/reconnect", method="POST"), timeout=10):
            pass
        _eventually(lambda: _word(base) == "online", "the device never came back online")
        expect(word).to_have_text("online", timeout=EXPECT_TIMEOUT)
        _expect_marks(page, away=False)
    finally:
        listener.close()
