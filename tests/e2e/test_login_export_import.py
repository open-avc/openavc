"""Device logins in an export, and the import's passphrase.

A project whose device has a password and an API key saved on this system:
Export asks whether to include them. Left out, the file holds none. Included,
they are sealed with a passphrase typed twice, and no member of the zip holds
either login readable. Imported on another system, the file asks for the
passphrase before the project opens: a wrong one is said and can be tried
again, the right one brings both logins in; Skip opens the project with the
device on Needs login.
"""

from __future__ import annotations

import base64
import io
import socket
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

pytest.importorskip("playwright.sync_api")

EXPECT_TIMEOUT = 15_000
DRIVER_ID = "e2e_export_login_widget"
PASSWORD = "Export-Acme-Pw-41"
API_KEY = "Export-Acme-Key-73"
PASSPHRASE = "Blue harbour 42"
BUNDLE = "device_logins.sealed.json"

DRIVER_FILE = f"{DRIVER_ID}.avcdriver"
DRIVER_SOURCE = f"""\
id: {DRIVER_ID}
name: Acme Export Login Widget
manufacturer: Acme
category: utility
version: 1.0.0
author: OpenAVC
description: A widget that signs in with a password and an API key.
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
  password:
    type: string
    secret: true
    label: Password
  api_key:
    type: string
    secret: true
    label: API Key
delimiter: "\\r"
state_variables:
  power:
    type: boolean
    label: Power
commands:
  power_on:
    label: Power On
    send: "PWR 1\\r"
"""


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _source(server_factory):
    return server_factory(
        project_overrides={
            "devices": [{
                "id": "widget", "driver": DRIVER_ID, "name": "Rack Widget",
                "config": {"poll_interval": 30, "api_key": API_KEY},
                "enabled": True, "pending_settings": {}, "child_entities": {},
            }],
            "connections": {
                "widget": {"host": "127.0.0.1", "port": _closed_port(), "password": PASSWORD},
            },
        },
        drivers={DRIVER_FILE: DRIVER_SOURCE},
    )


def _fresh(server_factory):
    return server_factory(project_overrides={"devices": [], "connections": {}},
                          drivers={DRIVER_FILE: DRIVER_SOURCE})


def _saved(data_dir, field: str) -> str | None:
    from openavc.core.device_logins import DeviceLogins

    return DeviceLogins(data_dir).login("widget", DRIVER_ID, field)


def _program(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.get_by_role("button", name="Program", exact=True).click(timeout=EXPECT_TIMEOUT)


def _export(page: Page, tmp_path: Path, name: str, passphrase: str | None) -> dict[str, bytes]:
    page.get_by_role("button", name="Export", exact=True).first.click(timeout=EXPECT_TIMEOUT)
    dialog = page.get_by_role("dialog", name="Export Project")
    expect(dialog).to_be_visible(timeout=EXPECT_TIMEOUT)
    include = dialog.get_by_role("checkbox", name="Include device logins")
    expect(include).not_to_be_checked()
    if passphrase is not None:
        include.check()
        dialog.get_by_label("Passphrase", exact=True).fill(passphrase)
        dialog.get_by_label("Passphrase again", exact=True).fill(passphrase)
    with page.expect_download(timeout=EXPECT_TIMEOUT) as info:
        dialog.get_by_role("button", name="Export", exact=True).click()
    expect(dialog).to_have_count(0, timeout=EXPECT_TIMEOUT)
    path = tmp_path / name
    info.value.save_as(str(path))
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as zf:
        return {member: zf.read(member) for member in zf.namelist()}


def _readable(members: dict[str, bytes]) -> list[str]:
    hits = []
    for member, body in members.items():
        for login in (PASSWORD, API_KEY):
            if login.encode() in body or base64.b64encode(login.encode()).rstrip(b"=") in body:
                hits.append(f"{member}: {login}")
    return hits


def _import(page: Page, path: Path) -> None:
    with page.expect_file_chooser(timeout=EXPECT_TIMEOUT) as chooser:
        page.get_by_role("button", name="Import", exact=True).first.click()
    chooser.value.set_files(str(path))


def test_an_export_carries_logins_only_sealed_and_the_import_asks_for_them(
    server_factory, page: Page, tmp_path: Path,
):
    source = _source(server_factory)
    assert _saved(source.data_dir, "password") == PASSWORD

    _program(page, source.base_url)
    plain = _export(page, tmp_path, "plain.zip", None)
    assert BUNDLE not in plain
    assert _readable(plain) == []

    sealed = _export(page, tmp_path, "sealed.zip", PASSPHRASE)
    assert BUNDLE in sealed
    assert _readable(sealed) == []
    assert sealed["project.avc"] == plain["project.avc"]

    # Another system: a wrong passphrase, then the right one.
    target = _fresh(server_factory)
    _program(page, target.base_url)
    _import(page, tmp_path / "sealed.zip")
    prompt = page.get_by_role("dialog", name="Device Logins")
    expect(prompt).to_be_visible(timeout=EXPECT_TIMEOUT)
    expect(prompt).to_contain_text("This export includes device logins, sealed on")
    prompt.get_by_label("Passphrase", exact=True).fill("Green harbour 42")
    prompt.get_by_role("button", name="Bring them in", exact=True).click()
    expect(prompt.get_by_role("alert")).to_have_text(
        "That passphrase does not open these logins.", timeout=EXPECT_TIMEOUT,
    )
    assert _saved(target.data_dir, "password") is None

    prompt.get_by_label("Passphrase", exact=True).fill(PASSPHRASE)
    prompt.get_by_role("button", name="Bring them in", exact=True).click()
    expect(prompt).to_have_count(0, timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("Project imported.").first).to_be_visible(timeout=EXPECT_TIMEOUT)
    assert _saved(target.data_dir, "password") == PASSWORD
    assert _saved(target.data_dir, "api_key") == API_KEY
    project = target.project_path.read_text(encoding="utf-8")
    assert PASSWORD not in project and API_KEY not in project
    expect(page.get_by_text("need their login entered")).to_have_count(0)
    expect(page.get_by_text("needs its login entered")).to_have_count(0)


def test_a_skipped_passphrase_opens_the_project_on_needs_login(server_factory, page: Page, tmp_path: Path):
    source = _source(server_factory)
    _program(page, source.base_url)
    _export(page, tmp_path, "sealed.zip", PASSPHRASE)

    target = _fresh(server_factory)
    _program(page, target.base_url)
    _import(page, tmp_path / "sealed.zip")
    prompt = page.get_by_role("dialog", name="Device Logins")
    expect(prompt).to_be_visible(timeout=EXPECT_TIMEOUT)
    prompt.get_by_role("button", name="Skip", exact=True).click()

    expect(prompt).to_have_count(0, timeout=EXPECT_TIMEOUT)
    expect(page.get_by_text("1 device needs its login entered: Rack Widget")).to_be_visible(
        timeout=EXPECT_TIMEOUT,
    )
    assert _saved(target.data_dir, "password") is None
