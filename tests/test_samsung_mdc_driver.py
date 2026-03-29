"""Tests for Samsung MDC driver."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from server.core.device_manager import _DRIVER_REGISTRY
from tests.simulators.samsung_mdc_simulator import SamsungMDCSimulator

if "samsung_mdc" not in _DRIVER_REGISTRY:
    pytest.skip("samsung_mdc driver not installed", allow_module_level=True)

SamsungMDCDriver = _DRIVER_REGISTRY["samsung_mdc"]

# Load helper functions from the driver module in driver_repo/
_mdc_path = Path(__file__).parent.parent / "driver_repo" / "samsung_mdc.py"
_spec = importlib.util.spec_from_file_location("_samsung_mdc_helpers", _mdc_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_build_mdc_frame = _mod._build_mdc_frame
_parse_mdc_frame = _mod._parse_mdc_frame


# --- Frame helpers ---


def test_build_frame_power_on():
    frame = _build_mdc_frame(0x11, 1, bytes([1]))
    assert frame[0] == 0xAA  # Header
    assert frame[1] == 0x11  # Command
    assert frame[2] == 1     # Display ID
    assert frame[3] == 1     # Data length
    assert frame[4] == 1     # Data: power on


def test_build_frame_checksum():
    frame = _build_mdc_frame(0x11, 1, bytes([1]))
    # Checksum = (0x11 + 0x01 + 0x01 + 0x01) & 0xFF = 0x14
    expected_cs = (0x11 + 0x01 + 0x01 + 0x01) & 0xFF
    assert frame[-1] == expected_cs


def test_parse_frame_complete():
    frame = _build_mdc_frame(0x11, 1, bytes([1]))
    result, remaining = _parse_mdc_frame(frame)
    assert result is not None
    assert result[0] == 0x11  # Command
    assert remaining == b""


def test_parse_frame_incomplete():
    result, remaining = _parse_mdc_frame(b"\xAA\x11")
    assert result is None
    assert remaining == b"\xAA\x11"


def test_parse_frame_no_header():
    # When no 0xAA found, parser discards garbage data
    result, remaining = _parse_mdc_frame(b"\x00\x01\x02")
    assert result is None
    assert remaining == b""


def test_parse_frame_multiple():
    frame1 = _build_mdc_frame(0x11, 1, bytes([1]))
    frame2 = _build_mdc_frame(0x12, 1, bytes([50]))
    data = frame1 + frame2

    msg1, rest = _parse_mdc_frame(data)
    assert msg1 is not None
    msg2, rest = _parse_mdc_frame(rest)
    assert msg2 is not None
    assert rest == b""


# --- Driver with simulator ---


@pytest.fixture
async def mdc_sim():
    """Running Samsung MDC simulator."""
    sim = SamsungMDCSimulator(port=15150)
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def mdc_driver(mdc_sim, state, events):
    """Connected Samsung MDC driver."""
    state.set_event_bus(events)
    driver = SamsungMDCDriver(
        "display1",
        {"host": "127.0.0.1", "port": 15150, "display_id": 1, "poll_interval": 0},
        state,
        events,
    )
    await driver.connect()
    yield driver
    await driver.disconnect()


async def test_connect(mdc_driver, state):
    assert mdc_driver.get_state("connected") is True


async def test_power_on(mdc_driver, state):
    await mdc_driver.send_command("power_on")
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("power") == "on"


async def test_power_off(mdc_driver, state):
    await mdc_driver.send_command("power_on")
    await asyncio.sleep(0.1)
    await mdc_driver.send_command("power_off")
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("power") == "off"


async def test_set_volume(mdc_driver, state):
    await mdc_driver.send_command("set_volume", {"level": 42})
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("volume") == 42


async def test_mute(mdc_driver, state):
    await mdc_driver.send_command("mute_on")
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("mute") is True

    await mdc_driver.send_command("mute_off")
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("mute") is False


async def test_set_input(mdc_driver, state):
    await mdc_driver.send_command("set_input", {"input": "hdmi2"})
    await asyncio.sleep(0.2)
    assert mdc_driver.get_state("input") == "hdmi2"


async def test_disconnect(mdc_driver, state):
    await mdc_driver.disconnect()
    assert mdc_driver.get_state("connected") is False
