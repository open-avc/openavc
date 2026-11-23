"""What a scanned device is called: the heading of its Discovery card, the
name its sort reads, and the name it is added to the project with.

One rule, on the server, carried on every record a scan sends (the REST
results, the live feed, the assistant's results):

* A device identified as a driver is called by that driver's name, whatever
  model string the scan read (a media player that announced a board name
  reads as the player, and the model stays on its Model line). Where the scan
  read no manufacturer, the driver's maker is shown.
* A device identified only through a fingerprint many makers share (a
  ``cross_vendor`` one: a projector protocol, a camera beacon) keeps the
  maker and model it reported, since the driver's name says what protocol
  it speaks, not what it is; the driver's name is used only when the scan
  read no model, and its maker is never shown as the device's.
* A device nobody identified is called by its maker and model, else the
  name it gave itself, else its maker.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig, ProjectMeta
from openavc.discovery.engine import DiscoveryEngine
from openavc.discovery.result import DiscoveredDevice
from openavc.discovery.tier_matcher import evidence_mdns
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import register_driver, unregister_driver

_PLAYER_SERVICE = "_acme-player._tcp"
_SHARED_SERVICE = "_acme-shared-ctl._tcp"

# A driver for one maker's product, identified by its own announcement.
_PLAYER = {
    "id": "acme_player_heading",
    "name": "Acme Media Player",
    "manufacturer": "Acme",
    "category": "streaming",
    "transport": "tcp",
    "discovery": {"mdns": [_PLAYER_SERVICE]},
}

# A driver for a protocol many makers' equipment speaks: its fingerprint
# says the protocol, not whose product answered.
_SHARED = {
    "id": "acme_shared_heading",
    "name": "Shared Control Protocol Projector",
    "manufacturer": "Generic",
    "category": "projector",
    "transport": "tcp",
    "discovery": {"mdns": [{"service": _SHARED_SERVICE, "cross_vendor": True}]},
}


def _engine(installed=(), catalog=()) -> DiscoveryEngine:
    engine = DiscoveryEngine()
    engine.load_driver_hints_from_registry([dict(d) for d in installed])
    if catalog:
        engine._rebuild_signal_index(community_drivers=[dict(d) for d in catalog])
    return engine


def _announcing(service: str, ip: str = "192.0.2.80", **info) -> DiscoveredDevice:
    device = DiscoveredDevice(ip=ip, **info)
    device.evidence_log.append(evidence_mdns(service))
    return device


def _record(engine: DiscoveryEngine, device: DiscoveredDevice) -> dict:
    """The record every exit sends for ``device`` once the scan has matched it."""
    engine.results[device.ip] = device
    engine.rematch()
    return next(d for d in engine.get_results() if d["ip"] == device.ip)


# --- an identified device ---


def test_an_identified_device_is_called_by_its_driver_not_the_model_it_read():
    record = _record(_engine([_PLAYER]), _announcing(_PLAYER_SERVICE, model="ACX200BRD"))

    assert record["identification"]["driver_id"] == "acme_player_heading"
    assert record["name"] == "Acme Media Player"
    assert record["model"] == "ACX200BRD"


def test_the_drivers_maker_is_shown_where_the_scan_read_none():
    record = _record(_engine([_PLAYER]), _announcing(_PLAYER_SERVICE, model="ACX200BRD"))

    assert record["manufacturer"] == "Acme"


def test_a_maker_the_scan_read_stays():
    record = _record(
        _engine([_PLAYER]),
        _announcing(_PLAYER_SERVICE, manufacturer="Acme Corporation", model="ACX200BRD"),
    )

    assert record["name"] == "Acme Media Player"
    assert record["manufacturer"] == "Acme Corporation"


def test_a_driver_only_the_library_lists_names_it_too():
    """The scan suggests drivers that are not installed yet (Install & Add):
    the library's copy names the device."""
    record = _record(_engine(catalog=[_PLAYER]), _announcing(_PLAYER_SERVICE, model="ACX200BRD"))

    assert record["identification"]["driver_id"] == "acme_player_heading"
    assert record["name"] == "Acme Media Player"
    assert record["manufacturer"] == "Acme"


# --- identified through a fingerprint many makers share ---


def test_a_shared_protocol_identification_keeps_the_maker_and_model_it_reported():
    record = _record(
        _engine([_SHARED]),
        _announcing(_SHARED_SERVICE, manufacturer="Acme", model="PX-500"),
    )

    assert record["identification"]["driver_id"] == "acme_shared_heading"
    assert record["name"] == "Acme PX-500"
    assert record["manufacturer"] == "Acme"


def test_a_shared_protocol_identification_with_no_model_is_called_by_its_driver():
    record = _record(_engine([_SHARED]), _announcing(_SHARED_SERVICE))

    assert record["name"] == "Shared Control Protocol Projector"


def test_a_shared_protocol_drivers_maker_is_never_shown_as_the_devices():
    record = _record(_engine([_SHARED]), _announcing(_SHARED_SERVICE, model="PX-500"))

    assert record["manufacturer"] is None
    assert record["name"] == "PX-500"


def test_a_makers_driver_a_hint_puts_ahead_of_a_shared_protocol_keeps_the_model_too():
    """Only the shared fingerprint said what the device is; the maker's own
    driver leads because the MAC prefix names its maker. The device was
    still recognized through the protocol, so it reads by what it reported."""
    from openavc.discovery.tier_matcher import evidence_oui

    makers_driver = {
        "id": "acme_projector_heading",
        "name": "Acme Projector Series",
        "manufacturer": "Acme",
        "category": "projector",
        "transport": "tcp",
        "discovery": {"oui": ["00:1e:c0"]},
    }
    device = _announcing(_SHARED_SERVICE, model="PX-500", mac="00:1e:c0:aa:bb:cc")
    device.evidence_log.append(evidence_oui("00:1e:c0:aa:bb:cc"))
    record = _record(_engine([_SHARED, makers_driver]), device)

    assert record["identification"]["driver_id"] == "acme_projector_heading"
    assert record["name"] == "PX-500"


# --- a device nobody identified ---


@pytest.mark.parametrize(
    ("scanned", "expected"),
    [
        ({"manufacturer": "Acme", "model": "WX-100"}, "Acme WX-100"),
        ({"manufacturer": "Acme", "model": "Acme WX-100"}, "Acme WX-100"),
        ({"manufacturer": "Acme", "device_name": "Lobby amp"}, "Lobby amp"),
        ({"manufacturer": "Acme"}, "Acme Device"),
        ({}, "Unknown Device"),
    ],
)
def test_an_unidentified_device_is_called_by_what_the_scan_read(scanned, expected):
    record = _record(_engine([_PLAYER]), DiscoveredDevice(ip="192.0.2.81", **scanned))

    assert record["identification"]["state"] == "unknown"
    assert record["name"] == expected
    assert record["manufacturer"] == scanned.get("manufacturer")


def test_a_record_sent_before_the_match_carries_a_name():
    """The live feed sends a device as each phase finds it, before the
    matcher has run."""
    device = _announcing(_PLAYER_SERVICE, manufacturer="Acme", model="ACX200BRD")

    assert device.to_dict()["name"] == "Acme ACX200BRD"


def test_a_device_whose_driver_went_away_is_named_again_from_what_it_read():
    engine = _engine([_PLAYER])
    device = _announcing(_PLAYER_SERVICE, model="ACX200BRD")
    assert _record(engine, device)["name"] == "Acme Media Player"

    engine.load_driver_hints_from_registry([])
    record = _record(engine, device)

    assert record["identification"]["state"] == "unknown"
    assert record["name"] == "ACX200BRD"
    assert record["manufacturer"] is None


# --- the name a device added from the scan gets ---


class _PlayerDriver(BaseDriver):
    DRIVER_INFO = {
        **_PLAYER,
        "default_config": {"host": "", "port": 7000},
        "state_variables": {},
        "commands": {},
    }

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def send_command(self, command, params=None):
        return None


@pytest.fixture
def player_driver():
    register_driver(_PlayerDriver)
    yield _PlayerDriver
    unregister_driver("acme_player_heading")


def _wire_add(tmp_path, monkeypatch, discovery: DiscoveryEngine) -> Engine:
    from openavc.api import discovery as discovery_api

    discovery_api.set_discovery_engine(discovery)
    engine = Engine(str(tmp_path / "test.avc"))
    engine.project = ProjectConfig(
        project=ProjectMeta(id="t", name="Test"), devices=[], connections={},
    )
    monkeypatch.setattr("openavc.core.project_loader.save_project", lambda *a, **k: None)
    discovery_api.set_app_engine(engine)
    return engine


@pytest.fixture
def restore_discovery_engine():
    from openavc.api import discovery as discovery_api

    before, app_before = discovery_api._engine, discovery_api._app_engine
    yield
    discovery_api.set_discovery_engine(before)
    discovery_api.set_app_engine(app_before)


async def test_an_identified_device_is_added_under_its_cards_name(
    tmp_path, monkeypatch, player_driver, restore_discovery_engine,
):
    from openavc.api.discovery import AddDeviceRequest, add_device

    discovery = _engine([_PLAYER])
    device = _announcing(_PLAYER_SERVICE, model="ACX200BRD")
    heading = _record(discovery, device)["name"]
    engine = _wire_add(tmp_path, monkeypatch, discovery)

    result = await add_device(AddDeviceRequest(ip=device.ip, driver_id="acme_player_heading"))

    assert heading == "Acme Media Player"
    assert result["name"] == heading
    assert engine.project.devices[-1].name == heading


async def test_a_name_typed_for_the_add_still_wins(
    tmp_path, monkeypatch, player_driver, restore_discovery_engine,
):
    from openavc.api.discovery import AddDeviceRequest, add_device

    discovery = _engine([_PLAYER])
    device = _announcing(_PLAYER_SERVICE, model="ACX200BRD")
    _record(discovery, device)
    _wire_add(tmp_path, monkeypatch, discovery)

    result = await add_device(AddDeviceRequest(
        ip=device.ip, driver_id="acme_player_heading", name="Lobby Player",
    ))

    assert result["name"] == "Lobby Player"


async def test_the_assistants_results_carry_the_cards_name(restore_discovery_engine):
    """The assistant's get_discovery_results answers with the scan's own
    records, so a device it adds can take the name its card shows."""
    from openavc.api import discovery as discovery_api
    from openavc.cloud.tools.system_tools import SystemToolsMixin

    discovery = _engine([_PLAYER])
    _record(discovery, _announcing(_PLAYER_SERVICE, model="ACX200BRD"))
    discovery_api.set_discovery_engine(discovery)

    answer = await SystemToolsMixin._get_discovery_results(MagicMock(), {})

    assert [d["name"] for d in answer["devices"]] == ["Acme Media Player"]


# --- the export names it as the card does ---


async def _exported(discovery: DiscoveryEngine, ip: str) -> list[str]:
    """The export's lines for the device at ``ip``: its heading line, then
    its detail lines."""
    from openavc.api import discovery as discovery_api

    discovery_api.set_discovery_engine(discovery)
    lines = (await discovery_api.export_results()).splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(ip))
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].startswith("    ")), len(lines))
    return lines[start:end]


async def test_the_export_heads_an_identified_device_by_its_cards_name(restore_discovery_engine):
    discovery = _engine([_PLAYER])
    record = _record(discovery, _announcing(_PLAYER_SERVICE, model="ACX200BRD"))

    heading, *details = await _exported(discovery, record["ip"])

    assert heading.endswith("  Acme Media Player")
    assert "    Manufacturer: Acme" in details
    assert "    Model: ACX200BRD" in details


@pytest.mark.parametrize(
    ("scanned", "expected"),
    [
        ({"manufacturer": "Acme", "model": "WX-100"}, "Acme WX-100"),
        ({"manufacturer": "Acme", "model": "Acme WX-100"}, "Acme WX-100"),
        ({"manufacturer": "Acme", "device_name": "Lobby amp"}, "Lobby amp"),
        ({}, "Unknown Device"),
    ],
)
async def test_the_export_heads_an_unidentified_device_by_its_cards_name(
    scanned, expected, restore_discovery_engine,
):
    discovery = _engine([_PLAYER])
    record = _record(discovery, DiscoveredDevice(ip="192.0.2.81", **scanned))

    heading, *_ = await _exported(discovery, record["ip"])

    assert record["name"] == expected
    assert heading.endswith(f"  {expected}")


# --- the audit's network check names it as the card does ---


def _network_check(discovery: DiscoveryEngine, device_info: dict, *evidence):
    """A network check whose observations are ``device_info`` and
    ``evidence``, its verdict reached: the device record it would show."""
    from openavc.audit.footprint import NetworkCheck

    check = NetworkCheck(discovery, "192.0.2.90", "192.0.2.90", source_ip="")
    check._build_evidence()
    device = check.footprint.device
    for key, value in device_info.items():
        setattr(device, key, value)
    device.evidence_log.extend(evidence)
    check._verdict()
    return check


def test_the_audit_names_an_identified_device_as_its_card_does():
    discovery = _engine([_PLAYER])
    scanned = _record(discovery, _announcing(_PLAYER_SERVICE, model="ACX200BRD"))

    check = _network_check(discovery, {"model": "ACX200BRD"}, evidence_mdns(_PLAYER_SERVICE))
    audited = check.footprint.device.to_dict()

    assert check.footprint.verdict["identification"]["driver_id"] == "acme_player_heading"
    for key in ("name", "manufacturer", "model", "category"):
        assert audited[key] == scanned[key], key
    assert audited["name"] == "Acme Media Player"


def test_the_audit_names_a_shared_protocol_device_by_what_it_reported():
    discovery = _engine([_SHARED])

    check = _network_check(discovery, {"model": "PX-500"}, evidence_mdns(_SHARED_SERVICE))
    audited = check.footprint.device.to_dict()

    assert audited["name"] == "PX-500"
    assert audited["manufacturer"] is None


async def test_the_audit_report_says_only_what_the_device_reported():
    """The card shows the identifying driver's maker where the device named
    none; the report's "What the device reported" never does."""
    from openavc.audit.report import build_report
    from openavc.audit.session import AuditManager, AuditOptions, AuditTarget

    discovery = _engine([_PLAYER])
    check = _network_check(discovery, {"model": "ACX200BRD"}, evidence_mdns(_PLAYER_SERVICE))
    assert check.footprint.device.to_dict()["manufacturer"] == "Acme"
    manager = AuditManager(None)
    session = await manager.start(
        AuditTarget(address="192.0.2.90", ip="192.0.2.90"), AuditOptions(),
    )
    session.footprint = check.footprint
    try:
        reported = build_report(session)["device"]["reported"]
    finally:
        await manager.shutdown()

    assert reported["manufacturer"] is None
    assert reported["model"] == "ACX200BRD"
