"""The names Discovery shows beside a found device's ports.

Every record carries its ports, each with the name shown beside it and the
project device that holds it (``result.identity_of``), so the card, the
export and the assistant all read one answer.

A device identified as a driver names a port by that driver when the driver
declares it, else by what usually listens there (SSH, HTTP, VNC), else not at
all: a port another driver declares never carries that driver's name, which
read as a second identity. A device nobody identified keeps the name of the
drivers that declare the port, a hint of what it may be: a port only one
declares carries that driver's name, one several declare their shared maker,
both names, or how many. A port a project device holds is listed though the
scan never connects to it, marked in use by that device.
"""

from __future__ import annotations

import pytest

from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.tier_matcher import evidence_mdns


def _driver(driver_id, name, manufacturer, *, mdns=None, probe_port=None, port_open=()):
    discovery: dict = {}
    if mdns:
        discovery["mdns"] = [mdns]
    if probe_port is not None:
        discovery["tcp_probe"] = {"port": probe_port, "send_ascii": "?\r", "expect": "ok"}
    if port_open:
        discovery["port_open"] = list(port_open)
    return {
        "id": driver_id, "name": name, "manufacturer": manufacturer,
        "category": "utility", "transport": "tcp", "discovery": discovery,
    }


ACME_WIDGET = _driver("acme_widget", "Acme Widget", "Acme",
                      mdns="_acme-widget._tcp", port_open=[17300])
ZENITH = _driver("zenith_projector", "Zenith Projector", "Zenith", port_open=[4352, 5900])


def _engine(installed=(), catalog=()) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([dict(d) for d in installed])
    if catalog:
        engine._rebuild_signal_index(community_drivers=[dict(d) for d in catalog])
    return engine


def _found(engine: DiscoveryEngine, *, ports=(), mdns=None, held=None, ip="10.0.0.5"):
    device = engine._get_or_create(ip)
    device.open_ports = list(ports)
    if mdns:
        device.evidence_log.append(evidence_mdns(mdns))
    for port, holder in (held or {}).items():
        device.port_probes[port] = {"in_use_by": holder}
    return device


def _labels(record: dict) -> dict[int, str | None]:
    return {p["port"]: p["label"] for p in record["ports"]}


# --- A device identified as a driver -----------------------------------------


def test_a_port_another_driver_declares_reads_generic_on_an_identified_device():
    engine = _engine([ACME_WIDGET, ZENITH])
    device = _found(engine, ports=[17300, 4352, 5900, 23, 8000], mdns="_acme-widget._tcp")
    engine.identify(device)
    assert device.identification.driver_id == "acme_widget"
    assert _labels(device.to_dict()) == {
        23: "Telnet",
        4352: None,
        5900: "VNC",
        8000: None,
        17300: "Acme Widget",
    }


def test_a_port_two_models_share_names_the_one_the_device_was_identified_as():
    pro = _driver("acme_widget_pro", "Acme Widget Pro", "Acme",
                  mdns="_acme-widget-pro._tcp", port_open=[17300])
    engine = _engine([ACME_WIDGET, pro])
    device = _found(engine, ports=[17300], mdns="_acme-widget-pro._tcp")
    engine.identify(device)
    assert _labels(device.to_dict()) == {17300: "Acme Widget Pro"}


def test_an_identified_device_never_takes_another_drivers_name_when_its_own_has_none():
    nameless = _driver("acme_widget", "", "Acme", mdns="_acme-widget._tcp")
    engine = _engine([nameless, ZENITH])
    device = _found(engine, ports=[4352], mdns="_acme-widget._tcp")
    engine.identify(device)
    assert device.identification.state.value == "identified"
    assert device.identified_by is None
    assert _labels(device.to_dict()) == {4352: None}


def test_its_drivers_port_listed_only_in_the_catalog_still_names_it():
    installed = _driver("acme_widget", "Acme Widget", "Acme", mdns="_acme-widget._tcp")
    catalog = {**installed, "ports": [17300]}
    engine = _engine([installed], catalog=[catalog])
    device = _found(engine, ports=[17300], mdns="_acme-widget._tcp")
    engine.identify(device)
    assert _labels(device.to_dict()) == {17300: "Acme Widget"}


# --- A device nobody identified ----------------------------------------------


def test_an_unidentified_device_keeps_the_declaring_drivers_name():
    engine = _engine([ACME_WIDGET, ZENITH])
    device = _found(engine, ports=[4352, 5900, 17300, 8000])
    engine.identify(device)
    assert device.identification.state.value != "identified"
    assert _labels(device.to_dict()) == {
        4352: "Zenith Projector",
        5900: "Zenith Projector",
        8000: None,
        17300: "Acme Widget",
    }


def test_a_port_two_models_share_is_named_by_their_maker():
    pro = _driver("acme_widget_pro", "Acme Widget Pro", "Acme", port_open=[17300])
    engine = _engine([ACME_WIDGET, pro])
    assert _labels(_found(engine, ports=[17300]).to_dict()) == {17300: "Acme"}


def test_a_port_two_makers_share_names_both():
    other = _driver("zenith_box", "Zenith Box", "Zenith", port_open=[17300])
    engine = _engine([ACME_WIDGET, other])
    assert _labels(_found(engine, ports=[17300]).to_dict()) == {
        17300: "Acme Widget or Zenith Box",
    }


def test_a_port_many_makers_share_says_how_many():
    makers = [_driver(f"maker{n}_box", f"Maker{n} Box", f"Maker{n}", port_open=[4100])
              for n in range(4)]
    engine = _engine(makers)
    assert _labels(_found(engine, ports=[4100]).to_dict()) == {4100: "4 drivers"}


def test_a_catalog_driver_counts_once_beside_its_installed_copy():
    catalog = [
        {**ACME_WIDGET, "ports": [17300]},
        {**_driver("acme_dsp", "Acme DSP", "Acme"), "ports": [1702]},
    ]
    engine = _engine([ACME_WIDGET], catalog=catalog)
    assert _labels(_found(engine, ports=[17300, 1702]).to_dict()) == {
        1702: "Acme DSP",
        17300: "Acme Widget",
    }


@pytest.mark.parametrize("port,name", [(22, "SSH"), (23, "Telnet"), (80, "HTTP"),
                                       (443, "HTTPS"), (8080, "HTTP alt")])
def test_the_generic_ports_keep_their_generic_names(port, name):
    engine = _engine([_driver("acme_widget", "Acme Widget", "Acme", probe_port=port)])
    assert _labels(_found(engine, ports=[port]).to_dict()) == {port: name}


# --- A port a project device holds -------------------------------------------


def test_a_held_port_is_listed_in_use_by_its_device():
    engine = _engine([ACME_WIDGET])
    device = _found(engine, ports=[23], mdns="_acme-widget._tcp",
                    held={17300: "Lobby Widget"})
    engine.identify(device)
    record = device.to_dict()
    assert record["ports"] == [
        {"port": 23, "label": "Telnet", "in_use_by": None},
        {"port": 17300, "label": "Acme Widget", "in_use_by": "Lobby Widget"},
    ]
    # What the scan connected to is unchanged: the held port was never dialled.
    assert record["open_ports"] == [23]


def test_a_device_whose_only_port_is_held_lists_it():
    engine = _engine([ACME_WIDGET])
    record = _found(engine, held={17300: "Lobby Widget"}).to_dict()
    assert record["ports"] == [
        {"port": 17300, "label": "Acme Widget", "in_use_by": "Lobby Widget"},
    ]


# --- When the names are read --------------------------------------------------


def test_a_record_sent_before_the_match_carries_the_names():
    engine = _engine([ZENITH])
    assert _labels(_found(engine, ports=[4352]).to_dict()) == {4352: "Zenith Projector"}


def test_a_rematch_after_a_driver_is_installed_names_its_port():
    engine = _engine([ACME_WIDGET])
    device = _found(engine, ports=[4352])
    engine.identify(device)
    assert _labels(device.to_dict()) == {4352: None}
    engine.load_driver_hints_from_registry([dict(ACME_WIDGET), dict(ZENITH)])
    [record] = engine.rematch()
    assert _labels(record) == {4352: "Zenith Projector"}


# --- The export lists them as the card does -----------------------------------


@pytest.fixture
def restore_discovery_engine():
    from openavc.api import discovery as discovery_api

    before = discovery_api._engine
    yield
    discovery_api.set_discovery_engine(before)


async def test_the_export_lists_each_port_with_its_name_and_a_held_one(restore_discovery_engine):
    from openavc.api import discovery as discovery_api

    engine = _engine([ACME_WIDGET, ZENITH])
    device = _found(engine, ports=[23, 4352, 8000], mdns="_acme-widget._tcp",
                    held={17300: "Lobby Widget"})
    engine.identify(device)
    discovery_api.set_discovery_engine(engine)

    lines = (await discovery_api.export_results()).splitlines()

    assert "    Ports: 23 (Telnet), 4352, 8000, 17300 (Acme Widget, in use by Lobby Widget)" in lines


async def test_the_results_carry_no_table_of_port_names_beside_the_devices(restore_discovery_engine):
    from openavc.api import discovery as discovery_api

    engine = _engine([ZENITH])
    _found(engine, ports=[4352])
    discovery_api.set_discovery_engine(engine)

    results = await discovery_api.get_results()

    assert "port_labels" not in results and "port_drivers" not in results
    assert results["devices"][0]["ports"] == [
        {"port": 4352, "label": "Zenith Projector", "in_use_by": None},
    ]
