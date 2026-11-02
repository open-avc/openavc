"""The ports a project's devices push to are written down for the host firewall.

A driver can have its device send updates to a port OpenAVC listens on: a
camera that dials back (``push: tcp_listener``) or a mixer that sends to a
multicast group (``push: multicast``). On Linux the firewall helper opens only
what the server writes down, so ``core/push_ports.py`` records those ports
beside ``plugin_ports.json``. The record comes from the project's enabled
devices, never from the listeners open at the moment, and names the port the
listener really binds. The devices here are invented.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.core import push_ports
from openavc.core.device_config import resolve_device_config
from openavc.core.device_manager import DeviceManager
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta, save_project
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


def _driver(driver_id: str, push: dict | None, default_config: dict) -> type[BaseDriver]:
    class _Acme(BaseDriver):
        DRIVER_INFO: dict[str, Any] = {
            "id": driver_id,
            "name": f"Acme {driver_id}",
            "manufacturer": "Acme",
            "category": "utility",
            "transport": "tcp",
            "default_config": default_config,
            "commands": {},
            "state_variables": {},
            **({"push": push} if push is not None else {}),
        }

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def send_command(self, command: str, params: dict | None = None) -> Any:
            return None

    return _Acme


DIALBACK = _driver(
    "acme_dialback_cam",
    {"type": "tcp_listener", "port": "{notify_port}"},
    {"port": 80, "notify_port": 31004},
)
MIXER = _driver(
    "acme_push_mixer",
    {"type": "multicast", "group": "{notification_group}", "port": "{notification_port}"},
    {"port": 17200, "notification_group": "225.0.0.100", "notification_port": 17000},
)
WEBHOOK = _driver("acme_webhook_box", {"type": "http_listener"}, {"port": 80})
PLAIN = _driver("acme_plain_box", None, {"port": 23})


@pytest.fixture(autouse=True)
def drivers():
    for cls in (DIALBACK, MIXER, WEBHOOK, PLAIN):
        register_driver(cls)
    yield
    for cls in (DIALBACK, MIXER, WEBHOOK, PLAIN):
        unregister_driver(cls.DRIVER_INFO["id"])


def _device(device_id: str, driver: str, name: str = "", enabled: bool = True,
            config: dict | None = None) -> DeviceConfig:
    return DeviceConfig(id=device_id, driver=driver, name=name or device_id,
                        enabled=enabled, config=config or {})


def _collect(*devices: DeviceConfig, connections: dict | None = None) -> list[dict]:
    project = SimpleNamespace(devices=list(devices), connections=connections or {})
    return push_ports.collect(project.devices, lambda d: resolve_device_config(d, project))


# --- which port a device listens on ---


def test_a_dial_back_listener_is_its_tcp_port():
    assert push_ports.push_listen_port(DIALBACK.DRIVER_INFO, {"notify_port": 31004}) == (31004, "tcp")


def test_a_multicast_group_is_its_udp_port():
    config = {"notification_group": "225.0.0.100", "notification_port": 17000}
    assert push_ports.push_listen_port(MIXER.DRIVER_INFO, config) == (17000, "udp")


@pytest.mark.parametrize("info, config", [
    (DIALBACK.DRIVER_INFO, {"notify_port": 0}),  # the system picks one each time
    (DIALBACK.DRIVER_INFO, {"notify_port": "not a port"}),
    (DIALBACK.DRIVER_INFO, {}),  # the template never resolved
    (DIALBACK.DRIVER_INFO, {"notify_port": 22}),  # the host's own port
    (MIXER.DRIVER_INFO, {"notification_group": "192.0.2.5", "notification_port": 17000}),
    (WEBHOOK.DRIVER_INFO, {}),  # reaches the main HTTP port
    (PLAIN.DRIVER_INFO, {}),
])
def test_no_port_to_open(info, config):
    assert push_ports.push_listen_port(info, config) is None


# --- what a project records ---


def test_every_enabled_device_that_listens_is_recorded_with_its_resolved_port():
    entries = _collect(
        _device("cam1", "acme_dialback_cam", "Camera 1"),
        _device("cam2", "acme_dialback_cam", "Camera 2", config={"notify_port": 31005}),
        _device("mix1", "acme_push_mixer", "Mixer"),
        _device("cam_off", "acme_dialback_cam", "Spare", enabled=False,
                config={"notify_port": 31009}),
        _device("hook", "acme_webhook_box"),
        _device("plain", "acme_plain_box"),
        _device("gone", "acme_not_installed"),
    )
    assert entries == [
        {"port": 17000, "protocol": "udp", "devices": ["mix1"], "reason": "Pushes from Mixer"},
        {"port": 31004, "protocol": "tcp", "devices": ["cam1"], "reason": "Pushes from Camera 1"},
        {"port": 31005, "protocol": "tcp", "devices": ["cam2"], "reason": "Pushes from Camera 2"},
    ]


def test_devices_sharing_a_port_record_it_once():
    entries = _collect(
        _device("cam1", "acme_dialback_cam", "Camera 1"),
        _device("cam2", "acme_dialback_cam", "Camera 2"),
    )
    assert entries == [{
        "port": 31004, "protocol": "tcp", "devices": ["cam1", "cam2"],
        "reason": "Pushes from Camera 1, Camera 2",
    }]


async def test_the_recorded_port_is_the_one_the_listener_binds():
    """One function resolves the port for both: they cannot disagree."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    device = _device("cam1", "acme_dialback_cam", config={"notify_port": free})
    project = SimpleNamespace(devices=[device], connections={"cam1": {"host": "127.0.0.1"}})
    resolved = resolve_device_config(device, project)
    [entry] = push_ports.collect([device], lambda d: resolve_device_config(d, project))

    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    driver = DIALBACK("cam1", resolved["config"], state, events)
    await driver._start_push()
    try:
        assert driver._push_subscription is not None
        assert (driver._push_subscription.port, "tcp") == (entry["port"], entry["protocol"])
    finally:
        driver._push_subscription.close()


# --- the file ---


def test_the_file_is_written_beside_plugin_ports_and_read_back(tmp_path):
    entries = _collect(_device("cam1", "acme_dialback_cam", "Camera 1"))
    push_ports.record(tmp_path, entries)
    payload = json.loads((tmp_path / "push_ports.json").read_text())
    assert payload["ports"] == entries
    assert push_ports.read(tmp_path) == entries


def test_an_empty_set_is_written_so_the_helper_closes_the_port(tmp_path):
    push_ports.record(tmp_path, _collect(_device("cam1", "acme_dialback_cam")))
    push_ports.record(tmp_path, [])
    assert json.loads((tmp_path / "push_ports.json").read_text())["ports"] == []


def test_a_new_port_is_announced_on_linux_and_only_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sys, "platform", "linux")
    entries = _collect(_device("cam1", "acme_dialback_cam", "Camera 1"))
    with caplog.at_level(logging.INFO, logger="openavc.core.push_ports"):
        push_ports.record(tmp_path, entries)
        push_ports.record(tmp_path, entries)
    said = [r.getMessage() for r in caplog.records if r.name == "openavc.core.push_ports"]
    assert said == [
        "Devices send updates to port 31004/tcp. If this computer has a host "
        "firewall on (ufw or firewalld), OpenAVC opens it the next time its "
        "service starts."
    ]


def test_nothing_is_announced_off_linux(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(sys, "platform", "win32")
    with caplog.at_level(logging.INFO, logger="openavc.core.push_ports"):
        push_ports.record(tmp_path, _collect(_device("cam1", "acme_dialback_cam")))
    assert not [r for r in caplog.records if r.name == "openavc.core.push_ports"]


# --- when the server writes it ---


@pytest.fixture
async def engine(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(data_dir))
    project_path = tmp_path / "project.avc"
    engine = Engine(str(project_path))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="proj1", name="Push"),
        devices=[_device("cam1", "acme_dialback_cam", "Camera 1")],
        connections={"cam1": {"host": "192.0.2.10"}},
    )
    save_project(str(project_path), engine.project)
    for device in engine.project.devices:
        await engine.devices.add_device(engine.resolved_device_config(device), defer_connect=True)
    yield engine, data_dir
    await engine.devices.disconnect_all()


def _recorded(data_dir) -> list[tuple[int, str]]:
    return [(e["port"], e["protocol"]) for e in push_ports.read(data_dir)]


async def test_a_project_edit_records_the_ports_and_a_disabled_device_drops_its_own(engine):
    engine, data_dir = engine

    def add_mixer(project):
        project.devices.append(_device("mix1", "acme_push_mixer", "Mixer"))

    await engine.apply_project_edit(add_mixer)
    assert _recorded(data_dir) == [(17000, "udp"), (31004, "tcp")]

    def disable_camera(project):
        project.devices[0].enabled = False

    await engine.apply_project_edit(disable_camera)
    assert _recorded(data_dir) == [(17000, "udp")]


async def test_a_port_changed_in_the_device_settings_is_recorded(engine):
    engine, data_dir = engine

    def move(project):
        project.devices[0].config = {"notify_port": 31010}

    await engine.apply_project_edit(move)
    assert _recorded(data_dir) == [(31010, "tcp")]


async def test_stopping_the_devices_leaves_the_record_alone(engine):
    """Every device disconnects at shutdown; the next boot still needs the port."""
    engine, data_dir = engine
    engine._record_push_ports()
    await engine.devices.disconnect_all()
    assert _recorded(data_dir) == [(31004, "tcp")]


async def test_a_driver_reload_records_again():
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    manager = DeviceManager(state, events)
    calls: list[int] = []
    manager.on_driver_reloaded = lambda: calls.append(1)
    await manager.reload_driver("acme_dialback_cam")
    assert calls == [1]
