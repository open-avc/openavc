"""Tests for local USB-to-serial support.

Covers the three new pieces: enumerating the host's serial ports for the
connection picker, finding the live port path for a stored adapter serial
number, and the connection resolver that rewrites a USB-serial device's port
from that stable identity.

Synthetic only — the fake ports stand in for whatever pyserial would enumerate
on a host. No real device names, no hardware dependency.
"""

from __future__ import annotations

from types import SimpleNamespace

import serial.tools.list_ports as list_ports_mod

import server.transport.serial_transport as st
from server.core.engine import Engine


def _fake_port(
    device,
    *,
    vid=None,
    pid=None,
    serial_number=None,
    manufacturer="",
    description="",
    hwid="",
):
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        serial_number=serial_number,
        manufacturer=manufacturer,
        description=description,
        hwid=hwid,
    )


# --- list_serial_ports ------------------------------------------------------


def test_list_serial_ports_maps_fields_and_flags_usb(monkeypatch):
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [
            _fake_port(
                "/dev/ttyUSB0",
                vid=0x0403,
                pid=0x6001,
                serial_number="AB12CD",
                manufacturer="Acme",
                description="Acme USB UART",
            )
        ],
    )
    [port] = st.list_serial_ports()
    assert port["device"] == "/dev/ttyUSB0"
    assert port["serial_number"] == "AB12CD"
    assert port["usb"] is True
    # The label is what the picker shows: it carries the path and the serial.
    assert "/dev/ttyUSB0" in port["label"]
    assert "AB12CD" in port["label"]


def test_list_serial_ports_non_usb_falls_back_to_device_path(monkeypatch):
    # A built-in UART pyserial can't describe ("n/a") and exposes no USB VID.
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [_fake_port("/dev/ttyS0", description="n/a")],
    )
    [port] = st.list_serial_ports()
    assert port["usb"] is False
    assert port["description"] == "/dev/ttyS0"  # not the useless "n/a"


def test_list_serial_ports_orders_usb_first(monkeypatch):
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [
            _fake_port("/dev/ttyS0", description="n/a"),
            _fake_port("/dev/ttyUSB0", vid=0x10C4, description="USB UART"),
        ],
    )
    devices = [p["device"] for p in st.list_serial_ports()]
    assert devices == ["/dev/ttyUSB0", "/dev/ttyS0"]


# --- resolve_serial_port_by_serial ------------------------------------------


def test_resolve_serial_port_by_serial(monkeypatch):
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [
            _fake_port("COM3", vid=0x0403, serial_number="AB12CD"),
            _fake_port("COM7", vid=0x0403, serial_number="ZZ99"),
        ],
    )
    assert st.resolve_serial_port_by_serial("ZZ99") == "COM7"
    assert st.resolve_serial_port_by_serial("not-attached") is None
    assert st.resolve_serial_port_by_serial("") is None


def test_resolve_ignores_adapters_with_no_serial(monkeypatch):
    # A clone exposing an empty serial must never match an empty stored id.
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [_fake_port("COM3", vid=0x1A86, serial_number="")],
    )
    assert st.resolve_serial_port_by_serial("") is None


# --- Engine._resolve_usb_binding --------------------------------------------


def test_usb_binding_rewrites_port_to_live_path(monkeypatch):
    monkeypatch.setattr(
        st, "resolve_serial_port_by_serial",
        lambda s: "COM9" if s == "AB12CD" else None,
    )
    cfg = {"transport": "serial", "usb_serial": "AB12CD", "port": "COM3"}
    out = Engine._resolve_usb_binding(cfg)
    assert out["port"] == "COM9"
    # Original config is not mutated.
    assert cfg["port"] == "COM3"


def test_usb_binding_left_alone_when_adapter_absent(monkeypatch):
    monkeypatch.setattr(st, "resolve_serial_port_by_serial", lambda s: None)
    cfg = {"transport": "serial", "usb_serial": "AB12CD", "port": "COM3"}
    assert Engine._resolve_usb_binding(cfg)["port"] == "COM3"


def test_usb_binding_resolves_when_transport_unset(monkeypatch):
    # A serial-default driver may carry no explicit transport; still resolve.
    monkeypatch.setattr(st, "resolve_serial_port_by_serial", lambda s: "COM9")
    cfg = {"usb_serial": "AB12CD", "port": "COM3"}
    assert Engine._resolve_usb_binding(cfg)["port"] == "COM9"


def test_usb_binding_skipped_without_usb_serial(monkeypatch):
    called = False

    def _spy(_s):
        nonlocal called
        called = True
        return "COM9"

    monkeypatch.setattr(st, "resolve_serial_port_by_serial", _spy)
    cfg = {"transport": "serial", "port": "COM3"}
    assert Engine._resolve_usb_binding(cfg) == cfg
    assert not called  # no enumeration when there's nothing to resolve


def test_usb_binding_skipped_for_network_and_bridge(monkeypatch):
    monkeypatch.setattr(st, "resolve_serial_port_by_serial", lambda s: "COM9")
    # Explicit network transport — a stray usb_serial must not hijack the port.
    net = {"transport": "tcp", "usb_serial": "AB12CD", "port": 23, "host": "1.2.3.4"}
    assert Engine._resolve_usb_binding(net) == net
    # Bridge-bound (already rewritten to tcp by the bridge resolver).
    bridged = {"usb_serial": "AB12CD", "bridge": "itach", "bridge_port": "serial:1"}
    assert Engine._resolve_usb_binding(bridged) == bridged


def test_stray_usb_serial_on_network_device_does_not_clobber_port(monkeypatch):
    """A leftover/hand-edited usb_serial on a device whose driver is a network
    (tcp) driver must not rewrite its numeric port to a serial path, even when
    the config carries no explicit transport. The resolver consults the driver's
    declared transport so the empty-transport case no longer passes the gate."""
    from server.core.device_manager import register_driver, unregister_driver
    from server.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
    from server.drivers.base import BaseDriver

    class _NetDriver(BaseDriver):
        DRIVER_INFO = {
            "id": "net_only_test",
            "transport": "tcp",
            "default_config": {},
            "state_variables": {},
            "commands": {},
        }

        async def connect(self):
            return None

        async def disconnect(self):
            return None

        async def send_command(self, command, params=None):
            return None

    register_driver(_NetDriver)
    try:
        # An adapter with this serial IS attached — the bug would grab it.
        monkeypatch.setattr(
            st, "resolve_serial_port_by_serial", lambda s: "/dev/ttyUSB0"
        )
        engine = Engine("x.avc")
        engine.project = ProjectConfig(
            project=ProjectMeta(id="t", name="T"), devices=[], connections={}
        )
        dev = DeviceConfig(
            id="d1",
            driver="net_only_test",
            name="Net",
            config={"usb_serial": "AB12CD", "port": 23, "host": "1.2.3.4"},
        )
        engine.project.devices.append(dev)
        cfg = engine.resolved_device_config(dev)["config"]
        # Numeric TCP port preserved, not rewritten to the serial path.
        assert cfg["port"] == 23
    finally:
        unregister_driver("net_only_test")


# --- DeviceManager reconnect re-resolution ----------------------------------


def test_reconnect_refreshes_moved_usb_port(monkeypatch):
    """A replug can move the adapter's OS path while the server runs; every
    reconnect attempt must follow the stable usb_serial to the current path
    instead of redialing the stale one."""
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    dm = DeviceManager(StateStore(), EventBus())
    driver = SimpleNamespace(
        config={"usb_serial": "AB12CD", "port": "/dev/ttyUSB0", "transport": "serial"},
        DRIVER_INFO={"transport": "serial"},
    )
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [_fake_port("/dev/ttyUSB1", vid=1, pid=2, serial_number="AB12CD")],
    )

    dm._refresh_usb_serial_port("disp1", driver)
    assert driver.config["port"] == "/dev/ttyUSB1"

    # Adapter unplugged entirely: stored port left as-is so the connect
    # fails with the normal serial open error.
    monkeypatch.setattr(list_ports_mod, "comports", lambda: [])
    dm._refresh_usb_serial_port("disp1", driver)
    assert driver.config["port"] == "/dev/ttyUSB1"


def test_reconnect_refresh_ignores_non_serial_devices(monkeypatch):
    """A stray usb_serial on a network device must not hijack its port
    during reconnect (mirrors the resolver gate)."""
    from server.core.device_manager import DeviceManager
    from server.core.event_bus import EventBus
    from server.core.state_store import StateStore

    dm = DeviceManager(StateStore(), EventBus())
    driver = SimpleNamespace(
        config={"usb_serial": "AB12CD", "transport": "tcp", "host": "1.2.3.4", "port": 23}
    )
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [_fake_port("/dev/ttyUSB1", vid=1, pid=2, serial_number="AB12CD")],
    )
    dm._refresh_usb_serial_port("disp1", driver)
    assert driver.config["port"] == 23
