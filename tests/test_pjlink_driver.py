"""Tests for PJLink driver with the simulator."""

import asyncio

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from server.core.device_manager import _DRIVER_REGISTRY
from tests.simulators.pjlink_simulator import PJLinkSimulator

if "pjlink_class1" not in _DRIVER_REGISTRY:
    pytest.skip("pjlink_class1 driver not installed", allow_module_level=True)

PJLinkDriver = _DRIVER_REGISTRY["pjlink_class1"]


@pytest.fixture
def core(pjlink_sim):
    """StateStore + EventBus wired together."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def driver(pjlink_sim, core):
    """Connected PJLink driver."""
    state, events = core
    d = PJLinkDriver(
        device_id="proj_test",
        config={"host": "127.0.0.1", "port": pjlink_sim.port, "poll_interval": 0},
        state=state,
        events=events,
    )
    await d.connect()
    yield d
    await d.disconnect()


async def test_connect(driver, core):
    state, _ = core
    assert state.get("device.proj_test.connected") is True


async def test_device_info(driver, core):
    """Device info (name, manufacturer, product, class, inputs) queried on connect."""
    state, _ = core
    # Give time for info query responses to arrive
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.projector_name") == "PJLink Simulator"
    assert state.get("device.proj_test.manufacturer") == "OpenAVC"
    assert state.get("device.proj_test.product_name") == "PJLink Sim v1.0"
    assert state.get("device.proj_test.pjlink_class") == "1"


async def test_available_inputs(driver, core):
    """INST query populates available_inputs."""
    state, _ = core
    await asyncio.sleep(0.15)
    available = state.get("device.proj_test.available_inputs")
    assert available is not None
    assert "rgb1" in available
    assert "digital1" in available
    assert "network1" in available


async def test_power_on(driver, core, pjlink_sim):
    state, _ = core
    await driver.send_command("power_on")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.power") == "warming"

    # Wait for warmup to complete (simulator warmup_time=0.3s)
    await asyncio.sleep(0.35)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.power") == "on"


async def test_power_off(driver, core, pjlink_sim):
    state, _ = core
    pjlink_sim.power = 1  # Skip warmup
    await driver.send_command("power_off")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.power") in ("cooling", "off")


async def test_set_input(driver, core, pjlink_sim):
    """Input switching works when projector is on."""
    state, _ = core
    pjlink_sim.power = 1  # Must be on to switch input
    await driver.send_command("set_input", {"input": "hdmi2"})
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.input") == "digital2"


async def test_set_input_by_code(driver, core, pjlink_sim):
    """Input switching works with raw PJLink codes."""
    state, _ = core
    pjlink_sim.power = 1
    await driver.send_command("set_input", {"input": "12"})
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.input") == "rgb2"


async def test_lamp_hours(driver, core):
    state, _ = core
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.lamp_hours") == 3200
    assert state.get("device.proj_test.lamp_count") == 1


async def test_error_status_ok(driver, core, pjlink_sim):
    """Error status parsed correctly when all clear."""
    state, _ = core
    pjlink_sim.error_status = "000000"
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.error_status") == "ok"
    assert state.get("device.proj_test.error_fan") == "ok"
    assert state.get("device.proj_test.error_lamp") == "ok"
    assert state.get("device.proj_test.error_temp") == "ok"
    assert state.get("device.proj_test.error_cover") == "ok"
    assert state.get("device.proj_test.error_filter") == "ok"
    assert state.get("device.proj_test.error_other") == "ok"


async def test_error_status_warnings(driver, core, pjlink_sim):
    """Error status parsed correctly with warnings/errors."""
    state, _ = core
    pjlink_sim.error_status = "102010"  # fan:warning, temp:error, filter:warning
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.error_fan") == "warning"
    assert state.get("device.proj_test.error_lamp") == "ok"
    assert state.get("device.proj_test.error_temp") == "error"
    assert state.get("device.proj_test.error_cover") == "ok"
    assert state.get("device.proj_test.error_filter") == "warning"
    assert state.get("device.proj_test.error_other") == "ok"
    summary = state.get("device.proj_test.error_status")
    assert "fan:warning" in summary
    assert "temp:error" in summary
    assert "filter:warning" in summary


async def test_mute_video(driver, core, pjlink_sim):
    state, _ = core
    pjlink_sim.power = 1
    await driver.send_command("mute_video")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.mute_video") is True

    await driver.send_command("unmute_video")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.mute_video") is False


async def test_mute_audio(driver, core, pjlink_sim):
    state, _ = core
    pjlink_sim.power = 1
    await driver.send_command("mute_audio")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.mute_audio") is True


async def test_mute_all(driver, core, pjlink_sim):
    state, _ = core
    pjlink_sim.power = 1
    await driver.send_command("mute_all")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.mute_video") is True
    assert state.get("device.proj_test.mute_audio") is True

    await driver.send_command("unmute_all")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.mute_video") is False
    assert state.get("device.proj_test.mute_audio") is False


async def test_poll_skips_input_when_off(driver, core, pjlink_sim):
    """Polling should not produce ERR2 warnings when projector is off."""
    state, _ = core
    # Projector starts off — poll should skip INPT/AVMT
    pjlink_sim.power = 0
    await driver.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_test.power") == "off"
    # No ERR2 warnings — input should remain at its default (None or unchanged)


async def test_disconnect(driver, core):
    state, _ = core
    await driver.disconnect()
    assert state.get("device.proj_test.connected") is False


# --- Authentication tests ---


@pytest.fixture
async def pjlink_sim_auth():
    """PJLink simulator with authentication enabled."""
    sim = PJLinkSimulator(
        port=14353, warmup_time=0.3, cooldown_time=0.2, password="test123"
    )
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def driver_auth(pjlink_sim_auth):
    """Connected PJLink driver with authentication."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    d = PJLinkDriver(
        device_id="proj_auth",
        config={
            "host": "127.0.0.1",
            "port": 14353,
            "password": "test123",
            "poll_interval": 0,
        },
        state=state,
        events=events,
    )
    await d.connect()
    yield d, state
    await d.disconnect()


async def test_auth_connect(driver_auth):
    """Driver connects with correct password."""
    d, state = driver_auth
    assert state.get("device.proj_auth.connected") is True


async def test_auth_power_on(driver_auth, pjlink_sim_auth):
    """Commands work with authentication."""
    d, state = driver_auth
    await d.send_command("power_on")
    await asyncio.sleep(0.1)
    await d.poll()
    await asyncio.sleep(0.15)
    assert state.get("device.proj_auth.power") == "warming"
