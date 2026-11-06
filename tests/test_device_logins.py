"""Device logins saved on this system: the login file, its key, the marker.

What is pinned here:

- The file and its key: nothing on disk until a login is saved; the logins are
  encrypted, the key is a separate owner-only file, and an existing key is
  never replaced. A file that cannot be opened is left byte for byte as it is,
  finds nothing, and refuses every write.
- The rule: a login is handed back only for the device id AND the driver it was
  saved for. A removed device's logins are kept 90 days, then deleted.
- Filling them in: the engine's dial path fills each marked setting; the
  connection resolver the other readers use keeps the marker; the device
  audit's saved settings are filled the same way as the engine's.
- Never the marker on the wire: a setting still marked when the driver
  connects is refused before anything is built, with ``login_missing``, and is
  not retried. The marker is never registered as a secret to mask.

An invented device throughout.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.core import connection_fault as cf
from openavc.core.device_config import resolve_device_config
from openavc.core.device_logins import (
    KEY_FILENAME,
    LOGINS_FILENAME,
    REMOVED_KEEP,
    SAVED_LOGIN,
    DeviceLogins,
    LoginFileUnreadable,
    device_logins,
    fill_saved_logins,
    marked_fields,
)
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver, ConnectionFaultError
from openavc.drivers.registry import register_driver, unregister_driver
from openavc.utils.log_redaction import get_secret_registry

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


# --- the file and its key -------------------------------------------------------


def test_nothing_is_on_disk_until_a_login_is_saved(tmp_path):
    store = DeviceLogins(tmp_path)
    assert store.login("proj1", "acme_widget", "password") is None
    store.sweep([("proj1", "acme_widget")], now=NOW)
    assert store.unreadable is None
    assert list(tmp_path.iterdir()) == []


def test_a_saved_login_is_encrypted_and_its_key_is_a_separate_file(tmp_path):
    DeviceLogins(tmp_path).save("proj1", "acme_widget", {"password": "Hunter2-Acme"})

    login_file = tmp_path / LOGINS_FILENAME
    key_file = tmp_path / KEY_FILENAME
    text = login_file.read_text(encoding="utf-8")
    assert "Hunter2-Acme" not in text
    assert "proj1" not in text and "acme_widget" not in text
    assert json.loads(text)["format"] == "openavc-device-logins"
    assert key_file.read_bytes().strip() not in login_file.read_bytes()

    # Read back by a fresh store, the way the next start reads it.
    assert DeviceLogins(tmp_path).login("proj1", "acme_widget", "password") == "Hunter2-Acme"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_both_files_are_owner_read_write_only(tmp_path):
    DeviceLogins(tmp_path).save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    for name in (LOGINS_FILENAME, KEY_FILENAME):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600, name


def test_an_existing_key_is_never_replaced(tmp_path):
    DeviceLogins(tmp_path).save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    key = (tmp_path / KEY_FILENAME).read_bytes()
    copy = (tmp_path / LOGINS_FILENAME).read_bytes()

    (tmp_path / LOGINS_FILENAME).unlink()
    DeviceLogins(tmp_path).save("proj2", "acme_widget", {"password": "Other-Pass"})
    assert (tmp_path / KEY_FILENAME).read_bytes() == key

    # So a copy of the login file taken earlier still opens.
    (tmp_path / LOGINS_FILENAME).write_bytes(copy)
    assert DeviceLogins(tmp_path).login("proj1", "acme_widget", "password") == "Hunter2-Acme"


def test_a_saved_login_cannot_be_empty_or_the_marker(tmp_path):
    store = DeviceLogins(tmp_path)
    for value in ("", SAVED_LOGIN):
        with pytest.raises(ValueError):
            store.save("proj1", "acme_widget", {"password": value})
    assert list(tmp_path.iterdir()) == []


def test_saving_one_field_keeps_the_others(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme", "api_key": "k-123456"})
    store.save("proj1", "acme_widget", {"password": "Changed-Pass"})
    fresh = DeviceLogins(tmp_path)
    assert fresh.login("proj1", "acme_widget", "password") == "Changed-Pass"
    assert fresh.login("proj1", "acme_widget", "api_key") == "k-123456"


def _saved(tmp_path) -> bytes:
    DeviceLogins(tmp_path).save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    return (tmp_path / LOGINS_FILENAME).read_bytes()


def _no_key(tmp_path):
    (tmp_path / KEY_FILENAME).unlink()


def _wrong_key(tmp_path):
    from cryptography.fernet import Fernet

    (tmp_path / KEY_FILENAME).write_bytes(Fernet.generate_key())


def _newer_format(tmp_path):
    envelope = json.loads((tmp_path / LOGINS_FILENAME).read_text(encoding="utf-8"))
    envelope["version"] = 99
    (tmp_path / LOGINS_FILENAME).write_text(json.dumps(envelope), encoding="utf-8")


def _damaged(tmp_path):
    (tmp_path / LOGINS_FILENAME).write_text("{not json", encoding="utf-8")


@pytest.mark.parametrize("breakage", [_no_key, _wrong_key, _newer_format, _damaged])
def test_a_file_that_cannot_be_opened_is_left_as_it_is(tmp_path, breakage):
    _saved(tmp_path)
    breakage(tmp_path)
    before = (tmp_path / LOGINS_FILENAME).read_bytes()

    store = DeviceLogins(tmp_path)
    assert store.login("proj1", "acme_widget", "password") is None
    assert store.unreadable
    with pytest.raises(LoginFileUnreadable):
        store.save("proj2", "acme_widget", {"password": "Other-Pass"})
    store.sweep([], now=NOW + REMOVED_KEEP * 2)

    assert (tmp_path / LOGINS_FILENAME).read_bytes() == before


def test_the_file_opens_again_once_its_key_is_back(tmp_path):
    _saved(tmp_path)
    key = (tmp_path / KEY_FILENAME).read_bytes()
    _no_key(tmp_path)
    assert DeviceLogins(tmp_path).unreadable
    (tmp_path / KEY_FILENAME).write_bytes(key)
    assert DeviceLogins(tmp_path).login("proj1", "acme_widget", "password") == "Hunter2-Acme"


def test_a_damaged_key_is_refused_and_kept(tmp_path):
    (tmp_path / KEY_FILENAME).write_text("not a key\n", encoding="utf-8")
    with pytest.raises(LoginFileUnreadable):
        DeviceLogins(tmp_path).save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    assert (tmp_path / KEY_FILENAME).read_text(encoding="utf-8") == "not a key\n"
    assert not (tmp_path / LOGINS_FILENAME).exists()


# --- which saved login a device gets ----------------------------------------------


def test_a_login_is_handed_back_only_for_the_same_id_and_driver(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    assert store.login("proj1", "acme_widget", "password") == "Hunter2-Acme"
    assert store.login("proj1", "acme_other", "password") is None
    assert store.login("proj2", "acme_widget", "password") is None
    assert store.login("proj1", "acme_widget", "api_key") is None


def test_a_removed_device_keeps_its_logins_for_90_days(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})

    store.sweep([], now=NOW)
    assert store.login("proj1", "acme_widget", "password") == "Hunter2-Acme"
    # Dated once: a later sweep does not move the date on.
    store.sweep([], now=NOW + timedelta(days=60))
    store.sweep([], now=NOW + REMOVED_KEEP - timedelta(seconds=1))
    assert DeviceLogins(tmp_path).login("proj1", "acme_widget", "password") == "Hunter2-Acme"

    store.sweep([], now=NOW + REMOVED_KEEP)
    assert store.login("proj1", "acme_widget", "password") is None
    assert DeviceLogins(tmp_path).login("proj1", "acme_widget", "password") is None


def test_a_device_back_in_the_project_is_current_again(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    store.sweep([], now=NOW)
    store.sweep([("proj1", "acme_widget")], now=NOW + timedelta(days=89))
    store.sweep([("proj1", "acme_widget")], now=NOW + REMOVED_KEEP * 3)
    assert store.login("proj1", "acme_widget", "password") == "Hunter2-Acme"


def test_the_same_id_on_another_driver_is_a_removed_device(tmp_path):
    """projector1 was an Acme, now an Acme Pro: the Acme's login is not the Pro's,
    and it goes after 90 days like any removed device's."""
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    store.sweep([("proj1", "acme_widget_pro")], now=NOW)
    assert store.login("proj1", "acme_widget_pro", "password") is None
    store.sweep([("proj1", "acme_widget_pro")], now=NOW + REMOVED_KEEP)
    assert store.login("proj1", "acme_widget", "password") is None


def test_a_sweep_that_changes_nothing_writes_nothing(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    before = (tmp_path / LOGINS_FILENAME).read_bytes()
    store.sweep([("proj1", "acme_widget")], now=NOW)
    assert (tmp_path / LOGINS_FILENAME).read_bytes() == before


# --- filling them in --------------------------------------------------------------


def _record(**config: Any) -> dict[str, Any]:
    return {"id": "proj1", "driver": "acme_widget", "name": "Projector", "config": config}


def test_each_marked_setting_is_filled_and_the_rest_left_alone(tmp_path):
    store = DeviceLogins(tmp_path)
    store.save("proj1", "acme_widget", {"password": "Hunter2-Acme"})
    record = _record(host="10.0.0.5", username="admin", password=SAVED_LOGIN)
    filled = fill_saved_logins(record, store)
    assert filled["config"] == {"host": "10.0.0.5", "username": "admin", "password": "Hunter2-Acme"}
    assert record["config"]["password"] == SAVED_LOGIN  # the caller's copy is not touched


def test_a_marked_setting_with_no_saved_login_keeps_the_marker(tmp_path):
    record = _record(host="10.0.0.5", password=SAVED_LOGIN, api_key=SAVED_LOGIN)
    DeviceLogins(tmp_path).save("proj1", "acme_widget", {"api_key": "k-123456"})
    filled = fill_saved_logins(record, DeviceLogins(tmp_path))
    assert filled["config"]["password"] == SAVED_LOGIN
    assert filled["config"]["api_key"] == "k-123456"
    assert marked_fields(filled["config"]) == ["password"]


def test_a_record_with_nothing_marked_is_returned_as_it_is(tmp_path):
    record = _record(host="10.0.0.5", password="plain-old")
    assert fill_saved_logins(record, DeviceLogins(tmp_path)) is record


# --- never the marker on the wire ---------------------------------------------------


class _AcmeWidget(BaseDriver):
    """BaseDriver's own connect(), so the platform's checks run."""

    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_widget_login",
        "name": "Acme Widget",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "password": {"type": "string", "secret": True, "label": "Password"},
        },
        "state_variables": {},
        "commands": {},
    }
    built = 0

    async def _create_transport(self, transport_type: str) -> None:
        type(self).built += 1

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


async def test_a_marked_setting_is_refused_before_anything_is_built():
    _AcmeWidget.built = 0
    driver = _AcmeWidget("w1", {"host": "10.0.0.5", "password": SAVED_LOGIN}, StateStore(), EventBus())
    with pytest.raises(ConnectionFaultError) as caught:
        await driver.connect()
    assert caught.value.fault_code == cf.LOGIN_MISSING
    assert _AcmeWidget.built == 0


def test_login_missing_is_not_retried_and_says_what_to_do():
    assert cf.is_permanent_fault(cf.LOGIN_MISSING)
    message = cf.default_fault_message(cf.LOGIN_MISSING)
    assert message == (
        "No login for this device is saved on this system. "
        "Enter it on the device's page in the Programmer."
    )


def test_the_marker_is_never_registered_as_a_secret():
    registry = get_secret_registry()
    _AcmeWidget("w_marker", {"host": "10.0.0.5", "password": SAVED_LOGIN}, StateStore(), EventBus())
    assert SAVED_LOGIN not in registry.secrets_for("w_marker")
    _AcmeWidget("w_filled", {"host": "10.0.0.5", "password": "Hunter2-Acme"}, StateStore(), EventBus())
    assert "Hunter2-Acme" in registry.secrets_for("w_filled")
    registry.forget("w_marker")
    registry.forget("w_filled")


# --- the engine and the audit --------------------------------------------------------


def _project(password: str) -> ProjectConfig:
    return ProjectConfig(
        project=ProjectMeta(id="p1", name="Test Project"),
        devices=[DeviceConfig(id="proj1", driver="acme_widget_login", name="Projector", config={})],
        connections={"proj1": {"host": "10.0.0.5", "port": 5500, "password": password}},
    )


@pytest.fixture
def engine(tmp_path, monkeypatch):
    from openavc.core.engine import Engine

    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path / "data"))
    register_driver(_AcmeWidget)
    try:
        yield Engine(str(tmp_path / "project.avc"))
    finally:
        unregister_driver("acme_widget_login")


def test_the_engine_dials_with_the_saved_login_and_every_other_reader_sees_the_marker(engine):
    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", "acme_widget_login", {"password": "Hunter2-Acme"})
    device = engine.project.devices[0]

    assert engine.resolved_device_config(device)["config"]["password"] == "Hunter2-Acme"
    assert resolve_device_config(device, engine.project)["config"]["password"] == SAVED_LOGIN
    assert engine.project.connections["proj1"]["password"] == SAVED_LOGIN


def test_a_project_with_no_marker_reads_and_writes_no_login_file(engine, tmp_path):
    engine.project = _project("plain-old")
    device = engine.project.devices[0]
    assert engine.resolved_device_config(device)["config"]["password"] == "plain-old"
    engine._sweep_device_logins()
    data = tmp_path / "data"
    assert not (data / LOGINS_FILENAME).exists() and not (data / KEY_FILENAME).exists()


def test_the_engine_sweep_dates_a_device_that_left_the_project(engine):
    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", "acme_widget_login", {"password": "Hunter2-Acme"})
    engine.device_logins.save("gone1", "acme_widget_login", {"password": "Old-Pass"})
    engine._sweep_device_logins()
    entries = engine.device_logins._entries
    assert entries[(None, "device", "proj1", "acme_widget_login")].removed_at is None
    assert entries[(None, "device", "gone1", "acme_widget_login")].removed_at is not None


def test_the_store_is_one_per_data_folder(tmp_path):
    assert device_logins(tmp_path) is device_logins(tmp_path)
    assert device_logins(tmp_path / "other") is not device_logins(tmp_path)


def test_the_audits_saved_settings_are_filled_like_the_engines(engine, monkeypatch):
    from openavc.audit import passes

    engine.project = _project(SAVED_LOGIN)
    engine.device_logins.save("proj1", "acme_widget_login", {"password": "Hunter2-Acme"})
    monkeypatch.setattr(
        passes, "saved_settings", lambda session, project: [{"device_id": "proj1"}],
    )
    config = passes._saved_config(SimpleNamespace(), engine.project, "proj1")
    assert config["password"] == "Hunter2-Acme"


class _AcmeWidgetDialed(_AcmeWidget):
    """BaseDriver's own transport, so a dial would reach TCPTransport.create."""

    DRIVER_INFO = {**_AcmeWidget.DRIVER_INFO, "id": "acme_widget_login_dialed"}
    _create_transport = BaseDriver._create_transport


async def test_the_card_says_needs_login_nothing_is_dialed_and_nothing_retries():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from openavc.core.device_manager import DeviceManager

    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    register_driver(_AcmeWidgetDialed)
    dial = AsyncMock()
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", dial):
            await dm.add_device({
                "id": "w1", "driver": "acme_widget_login_dialed", "name": "W1",
                "config": {"host": "192.0.2.10", "port": 5500, "password": SAVED_LOGIN},
            })
            await asyncio.sleep(0.05)
        assert dial.await_count == 0
        assert dm.state.get("device.w1.connected") is False
        assert dm.state.get("device.w1.offline_reason") == "login_missing"
        assert dm.state.get("device.w1.offline_detail") == cf.default_fault_message(cf.LOGIN_MISSING)
        assert dm.state.get("device.w1.reconnect_failed") is True
        assert "w1" not in dm._reconnect_tasks
    finally:
        await dm._cancel_reconnect("w1")
        unregister_driver("acme_widget_login_dialed")


async def test_a_login_saved_later_reaches_the_device_on_the_next_reconcile(engine):
    """The reconcile compares what the device was added with to what it would
    be added with now, both filled from the login file, so a saved login is a
    change: the device is re-added with it, and the project keeps the marker."""
    from unittest.mock import AsyncMock, patch

    register_driver(_AcmeWidgetDialed)
    engine.project = ProjectConfig(
        project=ProjectMeta(id="p1", name="Test Project"),
        devices=[DeviceConfig(id="proj1", driver="acme_widget_login_dialed", name="Projector", config={})],
        connections={"proj1": {"host": "192.0.2.10", "port": 5500, "password": SAVED_LOGIN}},
    )
    try:
        with patch("openavc.transport.tcp.TCPTransport.create", AsyncMock()):
            await engine._sync_devices(defer_connect=True)
            assert engine.devices.get_device_config("proj1")["config"]["password"] == SAVED_LOGIN

            engine.device_logins.save("proj1", "acme_widget_login_dialed", {"password": "Hunter2-Acme"})
            await engine._sync_devices(defer_connect=True)
        assert engine.devices.get_device_config("proj1")["config"]["password"] == "Hunter2-Acme"
        assert engine.project.connections["proj1"]["password"] == SAVED_LOGIN
    finally:
        await engine.devices.disconnect_all()
        unregister_driver("acme_widget_login_dialed")


def test_a_system_with_no_saved_login_never_loads_the_cipher():
    """Starting the engine and loading drivers touches nothing of the login
    file's encryption until a login file is read or written."""
    import subprocess
    import sys

    probe = (
        "import sys; import openavc.drivers.base, openavc.core.engine; "
        "print('cryptography.fernet' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
