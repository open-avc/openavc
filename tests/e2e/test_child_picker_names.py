"""A command's child picker follows the names the device reports while it is open.

A router reports its decoders' names after they register, and an integrator
renames a decoder on the device itself. The Child Entities table showed the
new name while an open Send Command dropdown kept the old one, so the picker
offered equipment under a name it no longer has. The device here is a small
fake with no transport: it reads the names it should report from a file the
test writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000

DRIVER_FILE = "e2e_acme_router.py"
DRIVER_SOURCE = '''\
import asyncio
import json

from openavc.drivers.base import BaseDriver


class AcmeRouter(BaseDriver):
    DRIVER_INFO = {
        "id": "e2e_acme_router",
        "name": "Acme Router",
        "manufacturer": "Acme",
        "category": "switcher",
        "transport": "tcp",
        "config_schema": {"names_file": {"type": "string", "default": ""}},
        "child_entity_types": {
            "decoder": {
                "label": "Decoder",
                "label_plural": "Decoders",
                "id_format": {"type": "integer", "min": 1, "max": 99, "pad_width": 3},
                "state_variables": {"name": {"type": "string", "label": "Name"}},
                "summary_fields": ["name"],
                "label_field": "name",
            },
        },
        "commands": {
            "route": {
                "label": "Route Source",
                "params": {
                    "decoder": {
                        "type": "child_id", "child_type": "decoder",
                        "required": True, "label": "Decoder",
                    },
                },
            },
        },
    }

    async def connect(self):
        self._watch = None
        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")
        self.register_child("decoder", 1, initial_state={"name": "Decoder 001"})
        path = self.config.get("names_file") or ""
        if path:
            self._watch = asyncio.create_task(self._follow(path))

    async def disconnect(self):
        if self._watch and not self._watch.done():
            self._watch.cancel()
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")

    async def send_command(self, command, params=None):
        return None

    async def _follow(self, path):
        seen = None
        while True:
            try:
                with open(path, encoding="utf-8") as f:
                    names = json.load(f)
            except (OSError, ValueError):
                names = None
            if names is not None and names != seen:
                seen = names
                for lid, name in names.items():
                    lid = int(lid)
                    if self.is_child_registered("decoder", lid):
                        self.set_child_state("decoder", lid, "name", name)
                    else:
                        self.register_child("decoder", lid, initial_state={"name": name})
            await asyncio.sleep(0.2)
'''


def _names(path: Path, names: dict[str, str]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(names), encoding="utf-8")
    tmp.replace(path)


def test_an_open_picker_follows_reported_names_and_new_children(
    server_factory, tmp_path: Path, page: Page,
):
    names_file = tmp_path / "decoder-names.json"
    handle = server_factory(
        project_overrides={"devices": [{
            "id": "router", "driver": "e2e_acme_router", "name": "Lobby Router",
            "config": {"names_file": str(names_file)},
            "enabled": True, "pending_settings": {}, "child_entities": {},
        }]},
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )

    page.goto(f"{handle.base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('button[aria-label="Devices"]').click(timeout=EXPECT_TIMEOUT)
    page.locator('button:has-text("Lobby Router")').first.click(timeout=EXPECT_TIMEOUT)
    page.locator('[data-testid="command-picker"]').click(timeout=EXPECT_TIMEOUT)
    page.get_by_role("option", name="Route Source").click()

    picker = page.locator("select", has=page.locator("option", has_text="(select decoder)"))
    expect(picker.locator("option", has_text="Decoder 001 (1)")).to_have_count(1, timeout=EXPECT_TIMEOUT)
    picker.select_option("1")

    # The device renames decoder 1 while the picker stays open.
    _names(names_file, {"1": "Main Display"})
    expect(picker.locator("option", has_text="Main Display (1)")).to_have_count(1, timeout=EXPECT_TIMEOUT)
    expect(picker.locator("option", has_text="Decoder 001")).to_have_count(0)
    expect(picker).to_have_value("1")

    # A decoder that registers later is offered too, under its own name.
    _names(names_file, {"1": "Main Display", "2": "Ceiling Display"})
    expect(picker.locator("option", has_text="Ceiling Display (2)")).to_have_count(1, timeout=EXPECT_TIMEOUT)
    expect(picker).to_have_value("1")
