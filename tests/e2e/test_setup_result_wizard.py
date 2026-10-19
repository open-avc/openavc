"""Real wizard, REST/WS and runner with an invented credential-creating driver.

The fixture has no network equipment. An append-only effect file independently
counts handler calls. The failure case injects an exception after the actual
project save/reconcile; it is not a power-loss or physical firmware test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

DRIVER = '''
import asyncio
from pathlib import Path
from openavc.drivers.base import BaseDriver
from openavc.core.setup_results import Outcome, SetupField, SetupFinding, SetupProgress, SetupResult
from openavc.core.outbound_messages import RoomText

class AcmePairing(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_pairing", "name": "Acme Pairing Fixture", "version": "1.0.0",
        "manufacturer": "Acme", "category": "controller", "transport": "tcp",
        "state_variables": {}, "commands": {},
        "config_schema": {"effect_path": {"type": "string"}, "fail_after_save": {"type": "boolean"}, "app_key": {"type": "password"}},
        "actions": [{"id": "pair", "label": "Pair Device", "kind": "setup", "result_contract": "setup/v1",
                     "availability": "always", "params": {"save": {"type": "boolean", "label": "Save settings", "default": False}}}],
    }
    async def connect(self):
        self._connected = True
        self.set_state("connected", True)
    async def disconnect(self):
        self._connected = False
        self.set_state("connected", False)
    async def send_command(self, command, params=None):
        raise ValueError("No control commands in this fixture")
    async def run_setup_action(self, action_id, params, progress):
        with Path(self.config["effect_path"]).open("a") as stream:
            stream.write("paired\\n")
        self.register_setup_secret({"app_key": "invented-browser-key"})
        await progress(SetupProgress("pair.created", "Device paired", 100))
        await asyncio.sleep(0.15)
        if params.get("save"):
            await progress(SetupProgress("pair.saving", "Saving settings", 90))
            engine = self._setup_context._engine
            original = engine.apply_project
            if self.config.get("fail_after_save"):
                async def fail_after_save(*args, **kwargs):
                    await original(*args, **kwargs)
                    raise OSError("injected after actual save")
                engine.apply_project = fail_after_save
            try:
                await self.request_config_update({"app_key": "invented-browser-key"})
            finally:
                engine.apply_project = original
            await self.request_reconnect()
        return SetupResult(Outcome.SUCCEEDED, RoomText("Device pairing completed."), (
            SetupFinding("pair.details", Outcome.SUCCEEDED, "Device report", (
                SetupField("Checks", 0), SetupField("Optional service", False),
                SetupField("Firmware", None), SetupField("Device label", "<example>"),
            )),
        ))
'''


@pytest.mark.parametrize("case,claimed,save,fail,lost", [
    ("saved", False, True, False, False),
    ("unsaved-signed-in", True, False, False, False),
    ("failed-save-signed-in", True, True, True, False),
    ("lost-start", False, False, False, True),
])
def test_setup_result_through_wizard(server_factory, page, tmp_path, case, claimed, save, fail, lost):
    effect = tmp_path / "equipment-effects.txt"
    env = {}
    if claimed:
        env.update(OPENAVC_PROGRAMMER_USERNAME="admin", OPENAVC_PROGRAMMER_PASSWORD="fixture-setup-password",
                   OPENAVC_ALLOW_ANONYMOUS="false")
    server = server_factory(env=env, drivers={"acme_pairing.py": DRIVER}, project_overrides={
        "devices": [{"id": "pairing", "name": "Pairing fixture", "driver": "acme_pairing",
                     "config": {"effect_path": str(effect), "fail_after_save": fail}}],
    })
    frames = []
    page.on("websocket", lambda ws: ws.on("framereceived", lambda frame: frames.append(frame)))
    if lost:
        def lose_start_response(route):
            response = route.fetch()
            assert response.status == 200
            route.abort()
        page.route("**/actions/pair/start", lose_start_response, times=1)
    page.goto(server.base_url + "/programmer/", wait_until="domcontentloaded")
    if claimed:
        page.get_by_label("Username").fill("admin")
        page.get_by_label("Password").fill("fixture-setup-password")
        page.get_by_role("button", name="Sign In", exact=True).click()
    page.get_by_role("button", name="Devices", exact=True).click()
    page.get_by_role("button").filter(has_text="Pairing fixture").first.click()
    page.get_by_test_id("quick-action-pair").click()
    dialog = page.get_by_role("dialog", name="Pair Device", exact=True)
    dialog.get_by_role("combobox").select_option("true" if save else "false")
    dialog.get_by_role("button", name="Start", exact=True).click()
    expect(dialog.get_by_text("Outcome not confirmed" if fail else "Completed", exact=True)).to_be_visible(timeout=15000)
    assert effect.read_text().splitlines() == ["paired"]
    assert "invented-browser-key" not in str(frames)
    if not fail:
        for value in ("0", "No", "Not reported", "<example>"):
            expect(dialog.get_by_text(value, exact=True)).to_be_visible()
    if save and not fail:
        expect(dialog.get_by_text("Generated credentials were saved with the device settings.")).to_be_visible()
        expect(dialog.get_by_role("button", name="Reveal credentials")).to_have_count(0)
    else:
        dialog.get_by_role("button", name="Close", exact=True).click()
        warning = page.get_by_role("alertdialog")
        expect(warning).to_be_visible()
        warning.get_by_role("button", name="Keep open").click()
        dialog.get_by_role("button", name="Reveal credentials").click()
        expect(dialog.get_by_role("textbox", name="app_key", exact=True)).to_have_value("invented-browser-key")
        if fail:
            expect(dialog.get_by_text("Not confirmed", exact=True)).to_be_visible()
        dialog.get_by_role("button", name="Copy app_key").click()
        expect(dialog.get_by_text("Copied!")).to_be_visible()
    out = Path(__file__).resolve().parents[2] / "build/outbound-policy/browser-typed-fields"
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"{case}.png"), full_page=True)
    if not save or fail:
        dialog.get_by_role("button", name="I've saved these credentials").click()
        expect(dialog.get_by_role("textbox", name="app_key", exact=True)).to_have_count(0)
    dialog.get_by_role("button", name="Close", exact=True).click()
    expect(dialog).not_to_be_visible()
    actual = json.loads(server.project_path.read_text())
    saved = next(item for item in actual["devices"] if item["id"] == "pairing")["config"]
    assert (saved.get("app_key") == "invented-browser-key") is save
    assert effect.read_text().splitlines() == ["paired"]
