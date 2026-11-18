"""Tests for local USB-to-serial support.

Covers enumerating the host's serial ports for the connection picker, the
connection resolver that rewrites a USB-serial device's port from its stable
adapter serial number, and the dial that refuses a device whose adapter is not
plugged in, at every door that dials it and in the connection test.

Synthetic only — the fake ports stand in for whatever pyserial would enumerate
on a host. No real device names, no hardware dependency.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from typing import Any

import pytest
import serial
import serial.tools
import serial.tools.list_ports as list_ports_mod

import openavc.transport.serial_transport as st
from openavc.core.bridge_binding import PASSTHROUGH, ROUTE_KEY
from openavc.core.connection_fault import UNREACHABLE, ConnectionFaultError
from openavc.core.device_manager import DeviceManager
from openavc.core.device_reachability import check_reachability
from openavc.core.engine import Engine
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver


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


# --- resolve_usb_binding (called by core.device_config on every resolve) -----


def _attached(monkeypatch, *ports: tuple[str, str]) -> None:
    """The adapters plugged in now, as (path, USB serial number)."""
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [
            _fake_port(path, vid=0x0403, pid=0x6001, serial_number=serial_number)
            for path, serial_number in ports
        ],
    )


def test_usb_binding_rewrites_port_to_live_path(monkeypatch):
    _attached(monkeypatch, ("COM3", "ZZ99"), ("COM9", "AB12CD"))
    cfg = {"transport": "serial", "usb_serial": "AB12CD", "port": "COM3"}
    out = st.resolve_usb_binding(cfg)
    assert out["port"] == "COM9"
    # Original config is not mutated.
    assert cfg["port"] == "COM3"


def test_the_record_keeps_the_stored_port_when_the_adapter_is_absent(monkeypatch):
    # The resolved record says what the device is set to; it is the dial
    # (require_usb_adapter, below) that refuses an adapter that is not there.
    _attached(monkeypatch, ("COM3", "ZZ99"))
    cfg = {"transport": "serial", "usb_serial": "AB12CD", "port": "COM3"}
    assert st.resolve_usb_binding(cfg)["port"] == "COM3"


def test_usb_binding_resolves_when_transport_unset(monkeypatch):
    # A serial-default driver may carry no explicit transport; still resolve.
    _attached(monkeypatch, ("COM9", "AB12CD"))
    cfg = {"usb_serial": "AB12CD", "port": "COM3"}
    assert st.resolve_usb_binding(cfg)["port"] == "COM9"


def test_an_adapter_with_no_serial_number_never_matches_an_empty_stored_id(monkeypatch):
    # A clone exposing an empty serial is not "the adapter" of a device that
    # stored none: that device is bound to its path.
    _attached(monkeypatch, ("COM9", ""))
    cfg = {"transport": "serial", "usb_serial": "", "port": "COM3"}
    assert st.resolve_usb_binding(cfg) is cfg


def test_usb_binding_skipped_without_usb_serial(monkeypatch):
    listed = False

    def _spy():
        nonlocal listed
        listed = True
        return [_fake_port("COM9", vid=1, serial_number="AB12CD")]

    monkeypatch.setattr(list_ports_mod, "comports", _spy)
    cfg = {"transport": "serial", "port": "COM3"}
    assert st.resolve_usb_binding(cfg) == cfg
    assert not listed  # no enumeration when there's nothing to resolve


def test_usb_binding_skipped_for_network_and_bridge(monkeypatch):
    _attached(monkeypatch, ("COM9", "AB12CD"))
    # Explicit network transport — a stray usb_serial must not hijack the port.
    net = {"transport": "tcp", "usb_serial": "AB12CD", "port": 23, "host": "1.2.3.4"}
    assert st.resolve_usb_binding(net) == net
    # Bridge-bound (rewritten to the bridge's pass-through by the bridge resolver,
    # which records it).
    bridged = {
        "usb_serial": "AB12CD", "bridge": "itach", "bridge_port": "serial:1",
        ROUTE_KEY: PASSTHROUGH,
    }
    assert st.resolve_usb_binding(bridged) == bridged


def test_stray_usb_serial_on_network_device_does_not_clobber_port(monkeypatch):
    """A leftover/hand-edited usb_serial on a device whose driver is a network
    (tcp) driver must not rewrite its numeric port to a serial path, even when
    the config carries no explicit transport. The resolver consults the driver's
    declared transport so the empty-transport case no longer passes the gate."""
    from openavc.drivers.registry import register_driver, unregister_driver
    from openavc.core.project_loader import DeviceConfig, ProjectConfig, ProjectMeta
    from openavc.drivers.base import BaseDriver

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
        _attached(monkeypatch, ("/dev/ttyUSB0", "AB12CD"))
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


# --- DeviceManager: every dial follows the adapter ---------------------------


def test_a_dial_follows_a_moved_usb_port(monkeypatch):
    """A replug can move the adapter's OS path while the server runs; every
    dial must follow the stable usb_serial to the current path instead of
    redialing the stale one."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

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

    dm._follow_usb_adapter("disp1", driver)
    assert driver.config["port"] == "/dev/ttyUSB1"

    # Adapter unplugged entirely: the attempt is refused before it dials,
    # and the stored port is left as it was.
    monkeypatch.setattr(list_ports_mod, "comports", lambda: [])
    with pytest.raises(ConnectionFaultError) as refused:
        dm._follow_usb_adapter("disp1", driver)
    assert refused.value.fault_code == UNREACHABLE
    assert driver.config["port"] == "/dev/ttyUSB1"


def test_a_dial_ignores_usb_serial_on_non_serial_devices(monkeypatch):
    """A stray usb_serial on a network device must not hijack its port
    when it is dialled (mirrors the resolver gate)."""
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore

    dm = DeviceManager(StateStore(), EventBus())
    driver = SimpleNamespace(
        config={"usb_serial": "AB12CD", "transport": "tcp", "host": "1.2.3.4", "port": 23}
    )
    monkeypatch.setattr(
        list_ports_mod,
        "comports",
        lambda: [_fake_port("/dev/ttyUSB1", vid=1, pid=2, serial_number="AB12CD")],
    )
    dm._follow_usb_adapter("disp1", driver)
    assert driver.config["port"] == 23


# --- An adapter that is not plugged in is never dialled ---------------------
#
# A device bound to adapter A (serial AB12CD) last dialled /dev/ttyUSB0. A is
# unplugged, and the OS gives that name to adapter B (serial ZZ99). Opening
# the stored name would reach B's equipment, so every door that dials the
# device refuses instead, with a reason that keeps the device retrying until A
# is back.

USB_DRIVER = "acme_usb_widget"


class _UsbWidget(BaseDriver):
    """Opens no port: records the path it was asked to dial."""

    DRIVER_INFO: dict[str, Any] = {
        "id": USB_DRIVER,
        "name": "Acme USB Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "serial",
        "default_config": {},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }
    dialed: list[str] = []

    async def connect(self) -> None:
        type(self).dialed.append(self.config.get("port"))
        self._connected = True
        self.state.set(f"device.{self.device_id}.connected", True, source="driver")

    async def disconnect(self) -> None:
        self._connected = False

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None

    async def stop_polling(self) -> None:
        return None


@pytest.fixture
async def usb_dm():
    register_driver(_UsbWidget)
    _UsbWidget.dialed = []
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    yield dm
    for device_id in list(dm._reconnect_tasks):
        await dm._cancel_reconnect(device_id)
    unregister_driver(USB_DRIVER)


def _widget_a(port: str = "/dev/ttyUSB0") -> dict[str, Any]:
    return {
        "id": "widget_a", "driver": USB_DRIVER, "name": "Widget A",
        "config": {"transport": "serial", "usb_serial": "AB12CD", "port": port},
    }


async def _until(check, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not check():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


async def test_an_unplugged_adapter_is_not_dialled_at_the_name_another_adapter_took(
    usb_dm, monkeypatch,
):
    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"))
    monkeypatch.setattr(usb_dm, "_reconnect_delay", lambda *_: 0.01)

    await usb_dm.add_device(_widget_a())

    assert _UsbWidget.dialed == []
    assert usb_dm.state.get("device.widget_a.connected") is False
    assert usb_dm.state.get("device.widget_a.offline_reason") == UNREACHABLE
    detail = usb_dm.state.get("device.widget_a.offline_detail")
    assert "AB12CD" in detail and "not plugged in" in detail

    # Absence, not a setting to fix: the loop keeps trying, and dials nothing
    # while the adapter is away.
    await asyncio.sleep(0.2)
    task = usb_dm._reconnect_tasks.get("widget_a")
    assert task is not None and not task.done()
    assert usb_dm.state.get("device.widget_a.reconnect_failed") is not True
    assert _UsbWidget.dialed == []

    # Plugged back in, under a new name: the next attempt follows it.
    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"), ("/dev/ttyUSB1", "AB12CD"))
    await _until(lambda: usb_dm.state.get("device.widget_a.connected") is True)
    assert _UsbWidget.dialed == ["/dev/ttyUSB1"]


async def _reconnect(dm):
    await dm.reconnect_device("widget_a")


async def _resume(dm):
    await dm.resume_device("widget_a")


async def _in_place(dm):
    # A setup action's reconnect raises its failure to the action.
    with pytest.raises(ConnectionFaultError):
        await dm.reconnect_in_place("widget_a", dm.get_driver("widget_a"))


@pytest.mark.parametrize("door", [_reconnect, _resume, _in_place], ids=lambda d: d.__name__)
async def test_every_door_that_dials_refuses_an_unplugged_adapter(usb_dm, monkeypatch, door):
    _attached(monkeypatch, ("/dev/ttyUSB0", "AB12CD"))
    await usb_dm.add_device(_widget_a())
    assert _UsbWidget.dialed == ["/dev/ttyUSB0"]

    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"))
    await door(usb_dm)

    assert _UsbWidget.dialed == ["/dev/ttyUSB0"]
    if door is not _in_place:
        assert usb_dm.state.get("device.widget_a.offline_reason") == UNREACHABLE


@pytest.mark.parametrize("door", [_reconnect, _resume], ids=lambda d: d.__name__)
async def test_every_door_that_dials_follows_a_moved_adapter(usb_dm, monkeypatch, door):
    _attached(monkeypatch, ("/dev/ttyUSB0", "AB12CD"))
    await usb_dm.add_device(_widget_a())

    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"), ("/dev/ttyUSB1", "AB12CD"))
    await door(usb_dm)

    assert _UsbWidget.dialed == ["/dev/ttyUSB0", "/dev/ttyUSB1"]


# --- require_usb_adapter: the rule each dial asks ----------------------------


def _bound(port: Any = "/dev/ttyUSB0", **extra: Any) -> dict[str, Any]:
    return {"transport": "serial", "usb_serial": "AB12CD", "port": port, **extra}


def test_another_adapter_at_the_stored_name_is_refused(monkeypatch):
    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"))
    with pytest.raises(ConnectionFaultError) as refused:
        st.require_usb_adapter(_bound())
    assert refused.value.fault_code == UNREACHABLE
    assert "AB12CD" in str(refused.value)


def test_nothing_at_the_stored_name_is_refused(monkeypatch):
    _attached(monkeypatch)
    with pytest.raises(ConnectionFaultError):
        st.require_usb_adapter(_bound())


def test_a_port_that_reports_no_serial_number_is_dialled(monkeypatch):
    # Nothing can tell the adapter there from the bound one.
    _attached(monkeypatch, ("/dev/ttyUSB0", ""))
    cfg = _bound()
    assert st.require_usb_adapter(cfg) is cfg


def test_the_adapter_is_followed_to_its_new_name(monkeypatch):
    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"), ("/dev/ttyUSB1", "AB12CD"))
    cfg = _bound()
    assert st.require_usb_adapter(cfg)["port"] == "/dev/ttyUSB1"
    assert cfg["port"] == "/dev/ttyUSB0"


def test_the_stored_name_is_dialled_when_this_host_cannot_list_its_ports(monkeypatch):
    # No list of ports to compare with is not the same as no adapter.
    monkeypatch.delattr(serial.tools, "list_ports")
    monkeypatch.setitem(sys.modules, "serial.tools.list_ports", None)
    cfg = _bound()
    assert st.require_usb_adapter(cfg) is cfg


def test_the_stored_name_is_dialled_when_listing_the_ports_fails(monkeypatch):
    """A listing that raises is the same fact as none: the stored name is
    dialled, and the record that names it reads without an error."""
    def broken():
        raise OSError("the port list could not be read")

    monkeypatch.setattr(list_ports_mod, "comports", broken)
    cfg = _bound()
    assert st.require_usb_adapter(cfg) is cfg
    assert st.resolve_usb_binding(cfg) is cfg


@pytest.mark.parametrize(
    "cfg",
    [
        # Simulating: the redirect points the device at the simulator over TCP
        # and leaves usb_serial in place.
        _bound(port=19001, transport="tcp", host="127.0.0.1"),
        {"usb_serial": "AB12CD", "bridge": "itach", "bridge_port": "serial:1",
         ROUTE_KEY: PASSTHROUGH},
        {"transport": "serial", "port": "/dev/ttyUSB0"},
    ],
    ids=["simulated", "bridged", "not_bound"],
)
def test_a_device_not_dialled_through_a_bound_adapter_is_left_alone(monkeypatch, cfg):
    _attached(monkeypatch)
    assert st.require_usb_adapter(cfg) is cfg


# --- Test Connection asks the same rule --------------------------------------


async def test_test_connection_does_not_open_the_port_another_adapter_took(monkeypatch):
    _attached(monkeypatch, ("/dev/ttyUSB0", "ZZ99"))
    opened: list[str] = []
    monkeypatch.setattr(serial, "Serial", lambda port, *a, **k: opened.append(port))
    resolved = {
        "id": "widget_a", "driver": USB_DRIVER, "name": "Widget A",
        "config": _bound(),
    }

    result = await check_reachability(resolved)

    assert result["success"] is False
    assert "AB12CD" in result["error"] and "not plugged in" in result["error"]
    assert opened == []
