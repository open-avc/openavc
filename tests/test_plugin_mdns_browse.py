"""PluginAPI.mdns_browse: capability gating, validation, and result mapping."""

import pytest

from server.core.event_bus import EventBus
from server.core.plugin_api import PluginAPI, PluginPermissionError
from server.core.plugin_registry import PluginRegistry
from server.core.state_store import StateStore


def _api(capabilities):
    return PluginAPI(
        plugin_id="t",
        capabilities=capabilities,
        config={},
        registry=PluginRegistry("t"),
        state_store=StateStore(),
        event_bus=EventBus(),
        macro_engine=None,
        device_manager=None,
        platform_id="test",
    )


@pytest.mark.asyncio
async def test_mdns_browse_requires_network_listen():
    api = _api(["state_read"])
    with pytest.raises(PluginPermissionError):
        await api.mdns_browse(["_elg._tcp.local."])


@pytest.mark.asyncio
async def test_mdns_browse_validates_service_types():
    api = _api(["network_listen"])
    with pytest.raises(ValueError):
        await api.mdns_browse([])
    with pytest.raises(ValueError):
        await api.mdns_browse(["   "])


@pytest.mark.asyncio
async def test_mdns_browse_maps_results(monkeypatch):
    from server.discovery import mdns_scanner

    class FakeScanner:
        def __init__(self, control_ip="", service_types=None):
            assert service_types == ["_elg._tcp.local."]

        async def start(self, duration=10.0):
            assert duration == 2.0
            result = mdns_scanner.MDNSResult(
                ip="192.0.2.5",
                hostname="dock.local.",
                port=5343,
                service_type="_elg._tcp.local.",
                instance_name="Booth Dock",
                txt_records={"sn": "AA", "dt": "215"},
            )
            return {"192.0.2.5": result}

    monkeypatch.setattr(mdns_scanner, "MDNSScanner", FakeScanner)
    api = _api(["network_listen"])
    out = await api.mdns_browse(["_elg._tcp.local."], duration=2.0)
    assert out == [
        {
            "ip": "192.0.2.5",
            "port": 5343,
            "hostname": "dock.local.",
            "instance_name": "Booth Dock",
            "service_type": "_elg._tcp.local.",
            "txt": {"sn": "AA", "dt": "215"},
        }
    ]
