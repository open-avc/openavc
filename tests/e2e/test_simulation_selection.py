"""The Programmer's selection survives the saves made by device controls.

The API already accepted a subset, but the sidebar never sent one and the
next device edit widened it to every device. Drive both sides of that contract
on an open and a claimed instance, with real simulator subprocesses.
"""

import json
import re
import secrets

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from tests.e2e.conftest import _pick_free_port  # noqa: E402

XDIST_SPREAD = True


@pytest.mark.parametrize("claimed", [False, True])
def test_selection_survives_edit_and_enable(server_factory, page, tmp_path, claimed):
    password = secrets.token_urlsafe(24)
    env = {}
    if claimed:
        env.update(OPENAVC_PROGRAMMER_USERNAME="admin", OPENAVC_PROGRAMMER_PASSWORD=password,
                   OPENAVC_ALLOW_ANONYMOUS="false")
    handle = server_factory(
        env=env,
        project_overrides={"devices": [
            {"id": "display", "name": "Display to simulate", "driver": "generic_tcp",
             "config": {"host": "127.0.0.1", "port": _pick_free_port()}},
            {"id": "controller", "name": "Controller to keep real", "driver": "generic_tcp",
             "config": {"host": "127.0.0.1", "port": _pick_free_port()}},
        ]},
        system_json={"simulation": {"ui_port": _pick_free_port(),
                                    "device_port_base": _pick_free_port()}},
    )
    page.goto(handle.base_url + "/programmer/", wait_until="domcontentloaded")
    if claimed:
        page.get_by_label("Username", exact=True).fill("admin")
        page.get_by_label("Password", exact=True).fill(password)
        page.get_by_role("button", name="Sign In", exact=True).click()

    page.get_by_role("button", name="Simulate Devices", exact=True).click()
    picker = page.get_by_role("dialog", name="Start Device Simulation")
    picker.get_by_role("checkbox", name="Controller to keep real controller").uncheck()
    expect(picker.get_by_text("1 of 2 selected")).to_be_visible()
    page.screenshot(path=str(tmp_path / "device-selection.png"))
    with page.expect_response(lambda r: r.url.endswith("/api/simulation/start")) as started:
        picker.get_by_role("button", name="Start Simulation", exact=True).click()
    assert started.value.status == 200, started.value.text()
    assert started.value.request.post_data_json == {"device_ids": ["display"]}
    expect(page.get_by_test_id("simulation-bar")).to_contain_text("Simulation is on.")

    auth = ("admin", password) if claimed else None
    with httpx.Client(base_url=handle.base_url, auth=auth) as client:
        def assert_selection():
            status = client.get("/api/simulation/status").raise_for_status().json()
            assert status["active"] is True
            assert set(status["devices"]) == {"display"}

        assert_selection()
        page.get_by_role("button", name="Devices", exact=True).click()
        page.get_by_role("button", name=re.compile(r"Controller to keep real generic_tcp$")).click()
        page.get_by_role("button", name="Edit", exact=True).click()
        edit = page.get_by_role("dialog", name="Edit Device")
        new_port = _pick_free_port()
        edit.get_by_label("Port", exact=True).fill(str(new_port))
        with page.expect_response(lambda r: r.request.method == "PATCH" and r.url.endswith("/api/devices/controller")) as saved:
            edit.get_by_role("button", name="Save Changes", exact=True).click()
        assert saved.value.status == 200, saved.value.text()
        expect(edit).not_to_be_visible()
        assert_selection()

        for label in ("Enabled", "Disabled"):
            # The toggle saves the project; Edit uses the per-device route.
            with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/api/project")) as toggled:
                page.get_by_role("button", name=label, exact=True).click()
            assert toggled.value.status == 200, toggled.value.text()
            assert_selection()
        expect(page.get_by_role("button", name="Enabled", exact=True)).to_be_visible()
        expect(page.get_by_text(f"Connection refused on 127.0.0.1:{new_port}.", exact=False).first).to_be_visible(timeout=15000)
        assert_selection()
        project = json.loads(handle.project_path.read_text())
        controller = next(d for d in project["devices"] if d["id"] == "controller")
        assert project["connections"]["controller"]["port"] == new_port
        assert controller["enabled"] is True

        with page.expect_response(lambda r: r.url.endswith("/api/simulation/stop")) as stopped:
            page.get_by_test_id("simulation-bar").get_by_role("button", name="Stop Simulation").click()
        assert stopped.value.status == 200, stopped.value.text()
        expect(page.get_by_test_id("simulation-bar")).to_have_count(0)
