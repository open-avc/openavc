"""The transport table (``drivers/spec.py`` ``TRANSPORT_TRAITS``) is THE answer
to what a transport is and can do.

Each row is held complete, the platform's connection builders and simulator
servers are held to the rows, and the lists the schema and the checks' messages
read are held to the values they had before they became views of the table.
"""
from __future__ import annotations

import importlib

import pytest

from openavc.drivers import spec
from openavc.drivers.spec import TRANSPORT_TRAITS, TransportTraits

# What each view read before it became a column of the table, in order: the
# schema's enums, the checks' messages and the IDE's pickers print these.
_VIEWS_AS_THEY_WERE = {
    "YAML_TRANSPORTS": ("tcp", "serial", "udp", "http", "osc", "bridge"),
    "PYTHON_ONLY_TRANSPORTS": ("ssh", "mqtt", "snmp"),
    "INTERCHANGEABLE_TRANSPORTS": ("tcp", "serial", "udp", "http", "osc"),
    "AUTH_TRANSPORTS": ("tcp", "serial"),
    "LIVENESS_TRANSPORTS": ("tcp", "serial", "udp", "osc"),
    "SILENCE_CHECK_TRANSPORTS": ("tcp", "udp", "osc"),
    "LINE_GAP_TRANSPORTS": ("tcp", "serial"),
    "PORT_OPEN_TRANSPORTS": ("tcp", "http"),
}

# The simulator server each `simulator` value names, as its module and class.
_SIMULATOR_CLASSES = {
    "tcp": ("openavc.simulator.tcp_simulator", "TCPSimulator"),
    "udp": ("openavc.simulator.udp_simulator", "UDPSimulator"),
    "http": ("openavc.simulator.http_simulator", "HTTPSimulator"),
    "osc": ("openavc.simulator.osc_simulator", "OSCSimulator"),
    "mqtt": ("openavc.simulator.mqtt_simulator", "MQTTSimulator"),
    "snmp": ("openavc.simulator.snmp_simulator", "SNMPSimulator"),
}

_LINE_FORMS = {"raw", "osc", "http", None}


@pytest.mark.parametrize("view, values", sorted(_VIEWS_AS_THEY_WERE.items()))
def test_each_view_reads_what_it_did(view, values):
    assert getattr(spec, view) == values


def test_the_table_has_a_row_for_every_transport_a_driver_may_declare():
    assert tuple(TRANSPORT_TRAITS) == spec.YAML_TRANSPORTS + spec.PYTHON_ONLY_TRANSPORTS
    transport_field = spec.FIELDS["transport"]
    assert tuple(transport_field["python_enum"]) == tuple(TRANSPORT_TRAITS)


@pytest.mark.parametrize("name", list(TRANSPORT_TRAITS))
def test_each_row_answers_every_column(name):
    row = TRANSPORT_TRAITS[name]
    assert isinstance(row, TransportTraits)
    for column in TransportTraits._fields:
        value = getattr(row, column)
        if column == "simulator":
            assert value is None or value in _SIMULATOR_CLASSES, (name, value)
        elif column == "line_form":
            assert value in _LINE_FORMS, (name, value)
        else:
            assert isinstance(value, bool), (name, column, value)


def test_a_listed_transport_is_one_a_driver_may_declare():
    for name, row in TRANSPORT_TRAITS.items():
        if row.interchangeable:
            assert row.yaml, name


def test_every_transport_the_platform_connects_over_has_a_builder():
    """A device opens its connection through the ladder's builders; the only
    row with none is the bridge sentinel, whose commands ride a bridge."""
    from openavc.drivers.transport_ladder import _BUILDERS

    opens_its_own = {name for name, row in TRANSPORT_TRAITS.items() if row.simulator is not None}
    assert set(_BUILDERS) == opens_its_own
    assert set(TRANSPORT_TRAITS) - opens_its_own == {"bridge"}


@pytest.mark.parametrize("server", sorted(_SIMULATOR_CLASSES))
def test_each_simulator_server_the_table_names_exists(server):
    module_name, class_name = _SIMULATOR_CLASSES[server]
    module = importlib.import_module(module_name)
    assert isinstance(getattr(module, class_name), type)


def test_a_stand_in_is_named_only_for_a_transport_the_simulator_does_not_serve():
    stood_in = {
        name: spec.simulator_stand_in(name) for name in TRANSPORT_TRAITS
        if spec.simulator_stand_in(name) is not None
    }
    assert stood_in == {"serial": "tcp", "ssh": "tcp"}
    assert spec.simulator_stand_in("bridge") is None
    assert spec.simulator_stand_in("nonsense") is None
    assert spec.simulator_stand_in(None) is None


def test_an_unknown_name_has_no_row():
    assert spec.transport_traits("nonsense") is None
    assert spec.transport_traits(None) is None
    assert spec.transport_traits(["tcp"]) is None
    assert spec.transport_traits("tcp") is TRANSPORT_TRAITS["tcp"]
