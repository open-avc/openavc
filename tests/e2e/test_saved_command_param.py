"""A stream key typed into a macro step is saved on this system, in the real IDE.

The driver marks the command's key parameter secret. Typed into the macro
step's box and saved, the step shows "Saved on this system" with Change; no
request the page makes carries the key in a project, the project the server
holds names it only by a marker, and running the macro sends the key to the
device. The device is a small fake on loopback that records every line.
"""

from __future__ import annotations

import socketserver
import threading
import time
from collections.abc import Callable
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
KEY = "live-Acme-KEY-7f3a"

DRIVER_FILE = "e2e_stream_encoder.avcdriver"
DRIVER_SOURCE = """\
id: e2e_stream_encoder
name: Acme Stream Encoder
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: An encoder that takes a stream key.
transport: tcp
default_config:
  host: ""
  port: 23
  poll_interval: 30
config_schema:
  host:
    type: string
    required: true
    label: IP Address
  port:
    type: integer
    default: 23
    label: Port
delimiter: "\\r"
state_variables:
  live:
    type: boolean
    label: Live
commands:
  set_key:
    label: Set Stream Key
    send: "KEY {key}\\r"
    params:
      key:
        type: string
        secret: true
        required: true
        label: Stream Key
"""


class _Encoder(socketserver.BaseRequestHandler):
    """Records every line it is sent."""

    def handle(self) -> None:
        buf = b""
        try:
            while True:
                data = self.request.recv(1024)
                if not data:
                    return
                buf += data
                while b"\r" in buf:
                    line, buf = buf.split(b"\r", 1)
                    self.server.heard.append(line.decode("ascii", "replace"))  # type: ignore[attr-defined]
        except OSError:
            return


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def encoder():
    server = _Server(("127.0.0.1", 0), _Encoder)
    server.heard = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _until(check: Callable[[], bool], timeout: float = EXPECT_TIMEOUT / 1000) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return check()


def _project_text(base_url: str) -> str:
    with urlopen(f"{base_url}/api/project") as response:
        return response.read().decode("utf-8")


def test_a_stream_key_typed_into_a_step_is_saved_on_this_system(server_factory, page: Page, encoder):
    port = encoder.server_address[1]
    server = server_factory(
        drivers={DRIVER_FILE: DRIVER_SOURCE},
        project_overrides={
            "devices": [{"id": "enc1", "driver": "e2e_stream_encoder", "name": "Lobby Encoder",
                         "config": {}, "enabled": True}],
            "connections": {"enc1": {"host": "127.0.0.1", "port": port}},
            "macros": [{"id": "go_live", "name": "Go Live", "steps": [
                {"action": "device.command", "device": "enc1", "command": "set_key", "params": {}},
            ]}],
        },
    )
    sent_bodies: list[str] = []
    page.on("request", lambda request: sent_bodies.append(request.post_data or ""))

    page.goto(f"{server.base_url}/programmer/#macros")
    page.get_by_text("Go Live", exact=True).click(timeout=EXPECT_TIMEOUT)
    page.get_by_text("Lobby Encoder → Set Stream Key", exact=True).click()

    page.get_by_label("Stream Key", exact=True).fill(KEY)
    page.get_by_role("button", name="Save Stream Key").click()
    expect(page.get_by_text("Saved on this system", exact=True)).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_role("button", name="Change Stream Key")).to_be_visible()

    # The autosave lands the marker, never the key.
    assert _until(lambda: "<saved on this system #" in _project_text(server.base_url))
    assert KEY not in _project_text(server.base_url)
    assert KEY not in server.project_path.read_text(encoding="utf-8")
    # The key left the page once, to be saved, and in nothing else.
    carrying = [body for body in sent_bodies if KEY in body]
    assert carrying == [f'{{"value":"{KEY}"}}'], carrying

    page.get_by_role("button", name="Test", exact=True).click()
    expect(page.get_by_text("Last run: Completed", exact=True)).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert _until(lambda: f"KEY {KEY}" in encoder.heard), encoder.heard
