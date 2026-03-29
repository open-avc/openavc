"""Tests for Wake-on-LAN driver."""

import importlib.util
from pathlib import Path

import pytest

from server.core.device_manager import _DRIVER_REGISTRY

if "wake_on_lan" not in _DRIVER_REGISTRY:
    pytest.skip("wake_on_lan driver not installed", allow_module_level=True)

WakeOnLANDriver = _DRIVER_REGISTRY["wake_on_lan"]

# Load helper function from the driver module in driver_repo/
_wol_path = Path(__file__).parent.parent / "driver_repo" / "wake_on_lan.py"
_spec = importlib.util.spec_from_file_location("_wol_helpers", _wol_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_magic_packet = _mod.build_magic_packet


# --- Magic packet construction ---


def test_magic_packet_format():
    """Magic packet is 102 bytes: 6 * 0xFF + 16 * MAC."""
    packet = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102
    assert packet[:6] == b"\xFF" * 6
    mac_bytes = bytes.fromhex("AABBCCDDEEFF")
    for i in range(16):
        offset = 6 + i * 6
        assert packet[offset : offset + 6] == mac_bytes


def test_magic_packet_dash_separator():
    packet = build_magic_packet("AA-BB-CC-DD-EE-FF")
    assert len(packet) == 102


def test_magic_packet_no_separator():
    packet = build_magic_packet("AABBCCDDEEFF")
    assert len(packet) == 102


def test_magic_packet_lowercase():
    packet = build_magic_packet("aa:bb:cc:dd:ee:ff")
    assert len(packet) == 102


def test_magic_packet_invalid_mac():
    with pytest.raises(ValueError):
        build_magic_packet("not:a:mac")


def test_magic_packet_too_short():
    with pytest.raises(ValueError):
        build_magic_packet("AA:BB:CC")


# --- Driver ---


@pytest.fixture
def wol_driver(state, events):
    state.set_event_bus(events)
    return WakeOnLANDriver(
        "wol1",
        {"mac_address": "AA:BB:CC:DD:EE:FF"},
        state,
        events,
    )


async def test_connect(wol_driver):
    await wol_driver.connect()
    assert wol_driver.get_state("connected") is True


async def test_disconnect(wol_driver):
    await wol_driver.connect()
    await wol_driver.disconnect()
    assert wol_driver.get_state("connected") is False


async def test_send_wake(wol_driver):
    """WoL send_command completes without error."""
    await wol_driver.connect()
    result = await wol_driver.send_command("wake")
    assert result is True
    # last_wake should be set
    assert wol_driver.get_state("last_wake") is not None


async def test_send_wake_no_mac(state, events):
    """Missing MAC address is handled gracefully."""
    state.set_event_bus(events)
    driver = WakeOnLANDriver("wol2", {}, state, events)
    await driver.connect()
    result = await driver.send_command("wake")
    assert result is None  # No packet sent
