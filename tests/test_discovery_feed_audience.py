"""Who hears a Discovery scan: the Programmer, never a panel.

Each update a scan sends carries the whole record of a device it found (its
address, MAC, serial number, firmware, open ports and what they answered),
and the same results over REST need a Programmer sign-in. A panel draws none
of it, so a panel socket open while a scan runs must receive none of it.

Through the real endpoint, on a claimed instance in the open access mode
(any panel on the network is admitted), with the discovery routes wired to
the engine's broadcast exactly as ``openavc/main.py`` wires them.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from openavc.api import discovery as discovery_api
from openavc.discovery.result import DiscoveredDevice
from tests.panel_access_helpers import PASSWORD, lan_client

AUTH = ("admin", PASSWORD)

FOUND = DiscoveredDevice(
    ip="10.0.0.2",
    mac="00:11:22:33:44:55",
    hostname="acme-widget",
    manufacturer="Acme",
    model="Widget 3000",
    serial_number="SN-0001",
    firmware="1.2.3",
    open_ports=[23, 80],
    banners={23: "Acme Widget ready"},
).to_dict()

SCAN_MESSAGES = [
    {"type": "discovery.phase", "phase": "ping_sweep", "phase_number": 1,
     "total_phases": 8, "message": "Pinging the subnet", "progress": 0.1,
     "warnings": []},
    {"type": "discovery.update", "device": FOUND, "phase": "ping_sweep",
     "progress": 0.2},
    {"type": "discovery.complete", "scan_id": "scan1", "status": "complete",
     "total_devices": 1, "duration_seconds": 1.0, "warnings": []},
]


class _AcmeScan:
    """Stands in for the discovery engine. The scan it starts reports a
    phase, one device and its end through the feed the wiring gave it, as a
    real scan does from its own task; a refresh re-matches the one device it
    holds."""

    def __init__(self) -> None:
        self.config: dict = {}
        self.results = {FOUND["ip"]: FOUND}
        self.on_update = None

    async def start_scan(self, *, subnets, extra_subnets, timeout,
                         ignore_control_interface):
        for message in SCAN_MESSAGES:
            await self.on_update(message)
        return "scan1"

    def get_status(self) -> dict:
        return {"status": "running", "subnets": ["10.0.0.0/30"],
                "started_at": 0.0, "budget_seconds": 60.0}

    def load_driver_hints_from_registry(self, drivers) -> None:
        pass

    def load_discovery_companions_from_dirs(self, dirs) -> None:
        pass

    def refold_cached_catalog(self) -> None:
        pass

    def rematch(self):
        return list(self.results.values())


@pytest.fixture
def wired(claimed_engine, access_mode, monkeypatch):
    """The discovery routes on a stand-in scan, broadcasting through the
    test engine the way main.py hands them its engine's broadcast."""
    access_mode("open")
    monkeypatch.setattr(discovery_api, "_engine", None)
    discovery_api.set_discovery_engine(_AcmeScan())
    monkeypatch.setattr(discovery_api, "_broadcast_fn", claimed_engine.broadcast_ws)
    return claimed_engine


def _basic_header() -> dict[str, str]:
    token = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    return {"authorization": f"Basic {token}"}


def _handshake(sock) -> None:
    assert sock.receive_json()["type"] == "state.snapshot"
    assert sock.receive_json()["type"] == "ui.definition"


def _next_after_a_page_change(sock) -> str:
    """The type of the socket's next message once it asks for a page:
    ``ui.navigate`` when nothing was queued for it before that answer."""
    sock.send_json({"type": "ui.page", "page_id": "main"})
    return sock.receive_json()["type"]


def test_a_scan_reaches_the_programmer_and_never_a_panel(wired):
    with lan_client().websocket_connect("/ws?client=programmer", headers=_basic_header()) as programmer, \
            lan_client().websocket_connect("/ws?client=panel") as panel:
        _handshake(programmer)
        _handshake(panel)

        resp = lan_client().post(
            "/api/discovery/scan", json={"subnets": ["10.0.0.0/30"]}, auth=AUTH,
        )
        assert resp.status_code == 200, resp.text

        assert [programmer.receive_json() for _ in SCAN_MESSAGES] == SCAN_MESSAGES
        assert _next_after_a_page_change(panel) == "ui.navigate"


def test_a_refresh_after_a_driver_change_reaches_the_programmer_and_never_a_panel(wired):
    """Installing or removing a driver re-matches every device found so far
    and sends each one again: the same record, the same audience."""
    with lan_client().websocket_connect("/ws?client=programmer", headers=_basic_header()) as programmer, \
            lan_client().websocket_connect("/ws?client=panel") as panel:
        _handshake(programmer)
        _handshake(panel)

        asyncio.run(discovery_api.refresh_all_device_matches())

        update = programmer.receive_json()
        assert update["type"] == "discovery.update"
        assert update["device"] == FOUND
        assert update["phase"] == "refresh"
        assert _next_after_a_page_change(panel) == "ui.navigate"
