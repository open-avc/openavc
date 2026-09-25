"""A device audit, driven through the Programmer from the button to the file.

Devices > Drivers > Audit a Device, the address, the network check with its
progress arriving over the WebSocket, the verdict, the report step, the
download, and Finish. The target is the loopback address of the machine the
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
        assert set(zf.namelist()) == {"summary.html", "report.json", "timeline.txt"}
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
