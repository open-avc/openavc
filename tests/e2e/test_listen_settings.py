"""Settings > Network > Accept connections from, walked in a real browser
through a real restart.

Two scratch servers that no ``OPENAVC_BIND`` holds, so the choice is
Settings' to make, both starting on Any network:

- One the browser opens at a second loopback address, ``127.0.0.2``. A
  listener on any network answers there; after choosing This computer only
  it answers on ``127.0.0.1`` alone. The browser is on this computer, so
  nothing is armed, and the restart dialog has to move the page to
  ``127.0.0.1`` rather than wait where it was.
- One the browser opens at this computer's own network address, which the
  server sees as a device on the network (a connection to a loopback
  address always arrives from ``127.0.0.1``, which is this computer's own
  screen, so only a network address can be cut off). Choosing This computer
  only asks first ("Change who can connect?"), the restart dialog hands over
  instead of failing, and opening the Programmer on ``127.0.0.1`` afterwards
  confirms the change: the "Kept" banner, and the safety net cleared.

A restart here is the dev-mode one: the server spawns its replacement and
exits. The replacement's process id is in the first server's log, which is
how the test stops it. The servers are started as a dev run whatever starts
the suite: a server that sees `INVOCATION_ID` (systemd sets it for every
process a service starts) leaves the restart to a service manager, and here
there is none.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect

from tests import gates

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
ANY_NETWORK = {"network": {"accept_connections_from": "any", "accept_connections_adapter": {}}}

# Nothing here restarts the server but the server itself (main._is_service_managed).
DEV_RUN = {"INVOCATION_ID": "", "OPENAVC_SERVICE_MANAGED": "0"}


def _lan_address() -> str | None:
    """This computer's address on the network the default route uses."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        address = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if address.startswith("127.") else address


def _can_bind(address: str) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((address, 0))
        return True
    except OSError:
        return False


def _port_of(base_url: str) -> int:
    return int(base_url.rsplit(":", 1)[1])


def _answers(url: str) -> bool:
    try:
        with urlopen(url, timeout=2.0) as resp:
            return resp.status == 200
    except OSError:
        return False


def _tail(path, size: int = 2500) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-size:]
    except OSError:
        return "<none>"


def _wait_for(condition, message: str, handle=None, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.5)
    if handle is not None:
        server_log = _tail(handle.data_dir.parent / "server.log")
        child_log = _tail(handle.data_dir / "logs" / "restart-child.log")
        message += (
            f"\n--- server.log ---\n{server_log}"
            f"\n--- restart-child.log ---\n{child_log}"
        )
    raise AssertionError(message)


def _status(port: int) -> dict:
    with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _replacement_pid(handle) -> int | None:
    """The process the first server spawned to take its place."""
    log = handle.data_dir.parent / "server.log"
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"Spawned replacement process pid=(\d+)", text)
    return int(match.group(1)) if match else None


def _stop(pid: int | None) -> None:
    """Stop the replacement by its own process id, never by a pattern."""
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows
    except OSError:
        return
    if os.name == "nt":
        return
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _choose(page: Page, label: str) -> None:
    select = page.get_by_label("Accept connections from")
    value = select.evaluate(
        "(el, label) => [...el.options].find((o) => o.textContent === label).value", label,
    )
    select.select_option(value)


def _open_settings(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/#settings", wait_until="domcontentloaded")
    expect(page.get_by_label("Accept connections from")).to_be_enabled(timeout=EXPECT_TIMEOUT)
    expect(page.get_by_label("Accept connections from")).to_have_value("any")


def test_a_browser_on_this_computer_moves_to_127_0_0_1(server_factory, page: Page):
    if not _can_bind("127.0.0.2"):
        gates.skip_or_fail(gates.E2E, "this host cannot listen on a second loopback address")
    handle = server_factory(bind="127.0.0.2", hold_bind=False, system_json=ANY_NETWORK, env=DEV_RUN)
    port = _port_of(handle.base_url)
    try:
        _open_settings(page, handle.base_url)
        _choose(page, "This computer only")
        expect(page.get_by_test_id("accept-connections-help")).to_have_text(
            "Only this computer's own screen and browser can open the panel and the Programmer."
        )
        expect(page.get_by_test_id("advertise-help")).to_have_text(
            "Nothing on the network can connect, so there is nothing to announce."
        )
        page.get_by_role("button", name="Save").click()
        # On this computer: nothing to warn about, the usual restart offer.
        offer = page.get_by_role("alertdialog")
        expect(offer.get_by_text("Restart to apply changes?")).to_be_visible(timeout=EXPECT_TIMEOUT)
        offer.get_by_role("button", name="Restart now").click()

        # The dialog waits where the new choice answers, and moves the page.
        _wait_for(lambda: _answers(f"http://127.0.0.1:{port}/api/health"),
                  "the server did not come back on 127.0.0.1", handle)
        page.wait_for_url(f"http://127.0.0.1:{port}/programmer**", timeout=60_000)
        assert _replacement_pid(handle), "the restart spawned no replacement this test can stop"
        status = _status(port)
        assert status["listen"]["mode"] == "this_computer"
        assert status["listen"]["addresses"] == ["127.0.0.1"]
        assert not _answers(f"http://127.0.0.2:{port}/api/health")
        assert not (handle.data_dir / "listen-safety-net.json").exists()
    finally:
        _stop(_replacement_pid(handle))


def test_a_change_that_cuts_off_this_browser_asks_hands_over_and_is_kept(server_factory, page: Page):
    address = _lan_address()
    if address is None:
        gates.skip_or_fail(gates.E2E, "this host has no network address to open the Programmer at")
    handle = server_factory(bind=address, hold_bind=False, system_json=ANY_NETWORK, env=DEV_RUN)
    port = _port_of(handle.base_url)
    try:
        _open_settings(page, handle.base_url)
        _choose(page, "This computer only")
        page.get_by_role("button", name="Save").click()

        dialog = page.get_by_role("alertdialog")
        expect(dialog.get_by_text("Change who can connect?")).to_be_visible(timeout=EXPECT_TIMEOUT)
        warning = dialog.get_by_test_id("listen-cut-off-warning")
        expect(warning).to_contain_text(
            f"You're connected through {address}. After the restart, OpenAVC won't accept "
            "connections from that network, so this page will stop loading here."
        )
        expect(warning).to_contain_text("use this computer's own screen")
        expect(warning).to_contain_text(
            "If nobody opens the Programmer that way within 10 minutes, OpenAVC goes back to Any network."
        )
        dialog.get_by_role("button", name="Restart and apply").click()

        # Never the 60-second failure: it says where to open the Programmer.
        expect(page.get_by_test_id("restart-handoff-saved")).to_contain_text(
            "Saved. Open the Programmer on this computer's own screen", timeout=30_000,
        )
        expect(page.get_by_text("may have refused the new configuration")).to_have_count(0)

        _wait_for(lambda: _answers(f"http://127.0.0.1:{port}/api/health"),
                  "the server did not come back on 127.0.0.1", handle)
        assert _replacement_pid(handle), "the restart spawned no replacement this test can stop"
        assert not _answers(f"http://{address}:{port}/api/health")
        assert (handle.data_dir / "listen-safety-net.json").exists()

        # This computer's own screen is a connection the new choice allows:
        # opening the Programmer there keeps it.
        local = page.context.new_page()
        local.goto(f"http://127.0.0.1:{port}/programmer/", wait_until="domcontentloaded")
        expect(local.get_by_test_id("listen-notice")).to_contain_text(
            "Kept: Accept connections from This computer only.", timeout=EXPECT_TIMEOUT,
        )
        assert not (handle.data_dir / "listen-safety-net.json").exists()
        assert _status(port)["listen"]["mode"] == "this_computer"
    finally:
        _stop(_replacement_pid(handle))

