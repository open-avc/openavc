"""A device audit, driven through the Programmer from the button to the file.

Devices > Drivers > Audit a Device, the address, the network check with its
progress arriving over the WebSocket, the verdict, the driver step answered
"no driver yet", the report step, the download, and Finish. The target is the loopback address of the machine the
test runs on, which always answers something (at least a refused port), so the
check reaches a verdict everywhere; which verdict depends on the machine, so
the test asserts that one was said, not which.

Slow on purpose: the check listens for announcements for a minute from the
session's start, because a beacon sent once a minute is what it exists to
hear, and a browser test of it should run the real check.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from urllib.request import urlopen

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
#: The listening window (60 s) plus the check's own steps, with room to spare.
CHECK_TIMEOUT = 180_000

VERDICT = re.compile(
    r"OpenAVC recognizes this device|OpenAVC found (a driver|a few drivers)"
    r"|OpenAVC can see this device|Nothing answered at this address"
)


def test_an_audit_runs_through_the_programmer_and_hands_over_its_report(
    openavc_server, page: Page, tmp_path,
) -> None:
    base = openavc_server.base_url
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Drivers", exact=True).click()
    page.get_by_role("button", name="Audit a Device", exact=True).click()

    dialog = page.get_by_role("dialog", name="Audit a device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    dialog.get_by_label("IP address or host name").fill("127.0.0.1")
    dialog.get_by_role("button", name="Start the network check").click()

    # Progress arrives while the check runs; the verdict when it ends.
    expect(dialog.get_by_text("Checking the address")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text("127.0.0.1 answers ping.").or_(
        dialog.get_by_text("127.0.0.1 did not answer ping.")
    )).to_be_visible(timeout=60_000)
    proceed = dialog.get_by_role("button", name="Continue")
    expect(proceed).to_be_enabled(timeout=CHECK_TIMEOUT)
    expect(dialog.get_by_text(VERDICT).first).to_be_visible(timeout=EXPECT_TIMEOUT)

    proceed.click()
    expect(dialog.get_by_role("heading", name="Which driver?")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_role("button", name="No driver yet: skip to the report").click()
    expect(dialog.get_by_text("What the audit could not see")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    dialog.get_by_label("Name", exact=True).fill("Browser Test")

    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Download report").click()
    download = info.value
    assert download.suggested_filename.startswith("openavc-device-audit-")
    assert download.suggested_filename.endswith(".zip")
    saved = tmp_path / download.suggested_filename
    download.save_as(saved)
    with zipfile.ZipFile(io.BytesIO(saved.read_bytes())) as zf:
        assert set(zf.namelist()) == {"summary.html", "report.json", "timeline.txt", "log.txt"}
        report = json.loads(zf.read("report.json"))
    assert report["report_version"] == 1
    assert report["target"]["ip"] == "127.0.0.1"
    assert report["session"]["tester"] == {"name": "Browser Test"}
    assert report["complete"] is True
    expect(dialog.get_by_text(f"Saved {download.suggested_filename}.")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )

    dialog.get_by_role("button", name="Finish").click()
    expect(dialog).to_be_hidden(timeout=EXPECT_TIMEOUT)
    with urlopen(f"{base}/api/audit/sessions/current", timeout=10) as resp:
        assert json.loads(resp.read())["session"] is None
    with urlopen(f"{base}/api/audit/reports", timeout=10) as resp:
        kept = [r["name"] for r in json.loads(resp.read())["reports"]]
    assert kept == [download.suggested_filename]


def test_an_audit_that_ends_while_the_page_is_open_says_so_and_offers_its_report(
    openavc_server, page: Page, tmp_path,
) -> None:
    """The idle timeout, a second tab or a restart can end an audit under an open
    page. The page says how it ended and offers the report it saved, in place of
    a step whose buttons can no longer do anything."""
    from urllib.request import Request

    base = openavc_server.base_url
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Drivers", exact=True).click()
    page.get_by_role("button", name="Audit a Device", exact=True).click()
    dialog = page.get_by_role("dialog", name="Audit a device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    dialog.get_by_label("IP address or host name").fill("127.0.0.1")
    dialog.get_by_role("button", name="Start the network check").click()
    # The check has begun, so there is something to report.
    expect(dialog.get_by_text("127.0.0.1 answers ping.").or_(
        dialog.get_by_text("127.0.0.1 did not answer ping.")
    )).to_be_visible(timeout=60_000)

    # Ended from outside the page, as another tab's Finish would.
    with urlopen(f"{base}/api/audit/sessions/current", timeout=10) as resp:
        session_id = json.loads(resp.read())["session"]["session_id"]
    with urlopen(Request(f"{base}/api/audit/sessions/{session_id}", method="DELETE"), timeout=30):
        pass

    expect(dialog.get_by_text("This audit is finished.")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text("Checking the address")).to_be_hidden()
    expect(dialog.get_by_text(re.compile(r"^What it found up to then is saved in its report"))).to_be_visible()
    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Download report").click()
    name = info.value.suggested_filename
    assert name.startswith("openavc-device-audit-") and name.endswith(".zip")
    expect(dialog.get_by_text(f"Saved {name}.")).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_role("button", name="Start a new audit")).to_be_visible()


def _current_session(base: str):
    with urlopen(f"{base}/api/audit/sessions/current", timeout=10) as resp:
        return json.loads(resp.read())["session"]


def test_a_reload_keeps_the_audit_and_leaving_the_page_cancels_it(
    openavc_server, page: Page,
) -> None:
    """A page that goes away cancels the audit unless a page follows it again
    within the grace; a reload of the same tab reopens the wizard on it."""
    import time

    from openavc.api.routes.audit import CLOSE_GRACE_SECONDS

    base = openavc_server.base_url
    page.goto(f"{base}/programmer/#devices", wait_until="domcontentloaded")
    page.get_by_role("tab", name="Drivers", exact=True).click()
    page.get_by_role("button", name="Audit a Device", exact=True).click()
    dialog = page.get_by_role("dialog", name="Audit a device")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    dialog.get_by_label("IP address or host name").fill("127.0.0.1")
    dialog.get_by_role("button", name="Start the network check").click()
    expect(dialog.get_by_text("Checking the address")).to_be_visible(timeout=EXPECT_TIMEOUT)
    session_id = _current_session(base)["session_id"]

    page.reload(wait_until="domcontentloaded")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(dialog.get_by_text("Checking the address")).to_be_visible(timeout=EXPECT_TIMEOUT)
    page.wait_for_timeout((CLOSE_GRACE_SECONDS + 2) * 1000)
    current = _current_session(base)
    assert current is not None and current["session_id"] == session_id
    assert current["status"] == "active"

    page.goto("about:blank")
    deadline = time.monotonic() + CLOSE_GRACE_SECONDS + 20
    while _current_session(base) is not None and time.monotonic() < deadline:
        time.sleep(0.5)
    assert _current_session(base) is None
