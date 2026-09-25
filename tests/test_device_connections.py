"""``device_connections`` / ``devices_at_host``: which project devices a
connection to an address would meet, read where each device really connects.

Asked by a device audit before it sends anything to an address, and by the
Driver Builder's test panel (``tests/test_driver_test_panel_conflict.py``).
"""

from types import SimpleNamespace

from openavc.core.device_config import device_connections, devices_at_host
from openavc.core.project_loader import DeviceConfig
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


class _AcmeBase(BaseDriver):
    DRIVER_INFO = {
        "id": "acme_conn_tcp",
        "name": "Acme TCP",
        "manufacturer": "Acme",
        "category": "utility",
        "transport": "tcp",
        "default_config": {"port": 7000},
        "commands": {},
        "state_variables": {},
        "config_schema": {},
    }


class _AcmeSerial(_AcmeBase):
    DRIVER_INFO = {**_AcmeBase.DRIVER_INFO, "id": "acme_conn_serial", "transport": "serial",
                   "default_config": {"port": "/dev/ttyUSB3"}}


class _AcmeHttp(_AcmeBase):
    DRIVER_INFO = {**_AcmeBase.DRIVER_INFO, "id": "acme_conn_http", "transport": "http",
                   "default_config": {"port": 80}}


class _AcmeBridge(_AcmeBase):
    DRIVER_INFO = {
        **_AcmeBase.DRIVER_INFO,
        "id": "acme_conn_bridge",
        "default_config": {"port": 4998},
        "bridge": {"ports": [
            {"id": "serial1", "kind": "serial", "passthrough_port": 4999},
            {"id": "ir1", "kind": "ir"},
        ]},
    }


for _cls in (_AcmeBase, _AcmeSerial, _AcmeHttp, _AcmeBridge):
    _DRIVER_REGISTRY[_cls.DRIVER_INFO["id"]] = _cls


def _project(*devices, connections=None):
    return SimpleNamespace(devices=list(devices), connections=connections or {})


def test_every_enabled_device_is_read_where_it_connects():
    project = _project(
        DeviceConfig(id="a", driver="acme_conn_tcp", name="A", config={"host": "10.0.0.5"}),
        DeviceConfig(id="b", driver="acme_conn_http", name="B", config={"host": "10.0.0.5"}),
        DeviceConfig(id="c", driver="acme_conn_serial", name="C", config={}),
        DeviceConfig(id="d", driver="acme_conn_tcp", name="D", config={"host": "10.0.0.5"},
                     enabled=False),
    )
    conns = {c.device_id: c for c in device_connections(project)}
    assert set(conns) == {"a", "b", "c"}
    assert (conns["a"].transport, conns["a"].host, conns["a"].port) == ("tcp", "10.0.0.5", 7000)
    assert (conns["b"].transport, conns["b"].port) == ("http", 80)
    assert (conns["c"].transport, conns["c"].port) == ("serial", "/dev/ttyUSB3")


def test_devices_at_host_takes_every_name_for_the_host():
    """An audit of a host name also finds the devices saved by address."""
    project = _project(
        DeviceConfig(id="by_ip", driver="acme_conn_tcp", name="By IP", config={"host": "10.0.0.5"}),
        DeviceConfig(id="by_name", driver="acme_conn_http", name="By Name",
                     config={"host": "Widget.local."}),
        DeviceConfig(id="other", driver="acme_conn_tcp", name="Other", config={"host": "10.0.0.6"}),
        DeviceConfig(id="serial", driver="acme_conn_serial", name="Serial", config={}),
    )
    found = devices_at_host(project, ["widget.local", "10.0.0.5"])
    assert sorted(c.device_id for c in found) == ["by_ip", "by_name"]


def test_a_bridged_serial_device_is_at_its_bridges_host():
    project = _project(
        DeviceConfig(id="gc", driver="acme_conn_bridge", name="Bridge",
                     config={"host": "10.0.0.60"}),
        DeviceConfig(id="disp", driver="acme_conn_serial", name="Display",
                     config={"bridge": "gc", "bridge_port": "serial1"}),
        DeviceConfig(id="tv", driver="acme_conn_tcp", name="IR TV",
                     config={"bridge": "gc", "bridge_port": "ir1"}),
    )
    found = {c.device_id: c for c in devices_at_host(project, ["10.0.0.60"])}
    # The IR device holds no connection of its own: it sends through the
    # bridge's, which the bridge itself answers for.
    assert set(found) == {"gc", "disp"}
    assert (found["disp"].transport, found["disp"].port, found["disp"].bridge) == (
        "tcp", 4999, "gc",
    )
    assert found["gc"].bridge == ""


def test_a_port_that_is_not_a_number_reads_as_none():
    project = _project(
        DeviceConfig(id="ok", driver="acme_conn_tcp", name="OK", config={"host": "10.0.0.5"}),
        DeviceConfig(id="odd", driver="acme_conn_tcp", name="Odd", config={"host": "10.0.0.5",
                     "port": "not-a-port"}),
    )
    conns = {c.device_id: c for c in device_connections(project)}
    assert conns["odd"].port is None
    assert conns["ok"].port == 7000
