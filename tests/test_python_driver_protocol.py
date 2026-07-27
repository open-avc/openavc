"""A Python driver talking to a device over a real socket.

Everything else in the core suite fakes the transport. This file does not:
it starts an invented device (``tests/simulators/acme_display_simulator.py``)
on a loopback port and drives an invented driver
(``tests/drivers/acme_display.py``) against it through the platform's own TCP
transport and delimiter framing. What that covers, and nothing else in core
does end to end:

  - ``BaseDriver.connect()``'s full lifecycle, including a challenge/response
    login performed in ``_post_connect`` and an identity read in
    ``_initial_sync``;
  - ``poll()`` reading several fields, including one reply that carries two
    integers and one that packs six severities into a single token;
  - commands with and without parameters, and a command the device refuses
    because it is not awake;
  - declared state-variable types surviving the round trip.

The device is invented on purpose. A test that needed a real product's driver
to be installed could only run where the driver library happened to be
present, which in this suite is nowhere.
"""

from __future__ import annotations

import asyncio

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from tests.drivers.acme_display import AcmeDisplayDriver
from tests.simulators.acme_display_simulator import AcmeDisplaySimulator

# How long to let a reply travel loopback and be applied to state.
SETTLE = 0.15


@pytest.fixture
def core(acme_sim):
    """StateStore + EventBus wired together."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def driver(acme_sim, core):
    """Connected Acme Display driver."""
    state, events = core
    d = AcmeDisplayDriver(
        device_id="disp_test",
        config={"host": "127.0.0.1", "port": acme_sim.port, "poll_interval": 0},
        state=state,
        events=events,
    )
    await d.connect()
    yield d
    await d.disconnect()


async def test_connect(driver, core):
    state, _ = core
    assert state.get("device.disp_test.connected") is True


async def test_device_info(driver, core):
    """Identity fields are read once, in _initial_sync, and split into state."""
    state, _ = core
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.device_name") == "Acme Display Simulator"
    assert state.get("device.disp_test.manufacturer") == "Acme"
    assert state.get("device.disp_test.model") == "AX-100"
    assert state.get("device.disp_test.protocol_rev") == "2"


async def test_available_inputs(driver, core):
    """The source-list query populates available_inputs with friendly names."""
    state, _ = core
    await asyncio.sleep(SETTLE)
    available = state.get("device.disp_test.available_inputs")
    assert available is not None
    assert "vga1" in available
    assert "hdmi1" in available
    assert "net" in available


async def test_power_on(driver, core, acme_sim):
    """Power settles through an intermediate state, as real gear does."""
    state, _ = core
    await driver.send_command("power_on")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.power") == "warming"

    # Simulator warmup_time is 0.3s.
    await asyncio.sleep(0.35)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.power") == "on"


async def test_power_off(driver, core, acme_sim):
    state, _ = core
    acme_sim.power = "on"  # skip the warmup
    await driver.send_command("power_off")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.power") in ("cooling", "off")


async def test_set_input(driver, core, acme_sim):
    """A friendly input name is translated to the device's own code."""
    state, _ = core
    acme_sim.power = "on"  # must be awake to switch
    await driver.send_command("set_input", {"input": "hdmi2"})
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.input") == "hdmi2"
    assert acme_sim.source == "d2"


async def test_set_input_by_code(driver, core, acme_sim):
    """A raw device code passes through untranslated."""
    state, _ = core
    acme_sim.power = "on"
    await driver.send_command("set_input", {"input": "a2"})
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.input") == "vga2"


async def test_two_values_in_one_reply(driver, core):
    """One reply carrying two integers lands in two typed state variables."""
    state, _ = core
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.lamp_hours") == 3200
    assert state.get("device.disp_test.power_cycles") == 1


async def test_fault_status_ok(driver, core, acme_sim):
    """An all-clear status word expands into per-field severities."""
    state, _ = core
    acme_sim.fault = "000000"
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.fault_status") == "ok"
    for field in ("fan", "lamp", "temp", "cover", "filter", "other"):
        assert state.get(f"device.disp_test.fault_{field}") == "ok"


async def test_fault_status_warnings(driver, core, acme_sim):
    """A mixed status word expands per field and summarizes the problems."""
    state, _ = core
    acme_sim.fault = "102010"  # fan:warning, temp:error, filter:warning
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.fault_fan") == "warning"
    assert state.get("device.disp_test.fault_lamp") == "ok"
    assert state.get("device.disp_test.fault_temp") == "error"
    assert state.get("device.disp_test.fault_cover") == "ok"
    assert state.get("device.disp_test.fault_filter") == "warning"
    assert state.get("device.disp_test.fault_other") == "ok"
    summary = state.get("device.disp_test.fault_status")
    assert "fan:warning" in summary
    assert "temp:error" in summary
    assert "filter:warning" in summary


async def test_mute_video(driver, core, acme_sim):
    state, _ = core
    acme_sim.power = "on"
    await driver.send_command("mute_video")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.mute_video") is True

    await driver.send_command("unmute_video")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.mute_video") is False


async def test_mute_audio(driver, core, acme_sim):
    state, _ = core
    acme_sim.power = "on"
    await driver.send_command("mute_audio")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.mute_audio") is True


async def test_mute_all(driver, core, acme_sim):
    """One command whose reply updates two state variables at once."""
    state, _ = core
    acme_sim.power = "on"
    await driver.send_command("mute_all")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.mute_video") is True
    assert state.get("device.disp_test.mute_audio") is True

    await driver.send_command("unmute_all")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.mute_video") is False
    assert state.get("device.disp_test.mute_audio") is False


async def test_poll_skips_awake_only_fields_when_off(driver, core, acme_sim):
    """Polling a sleeping device does not ask for fields it would refuse."""
    state, _ = core
    acme_sim.power = "off"
    await driver.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_test.power") == "off"
    # The device saw the always-available fields and none of the awake-only
    # ones — an ERR_STATE per field per poll cycle is the bug this prevents.
    assert "PWR" in acme_sim.queries
    assert "SRC" not in acme_sim.queries
    assert "MUTEV" not in acme_sim.queries


async def test_disconnect(driver, core):
    state, _ = core
    await driver.disconnect()
    assert state.get("device.disp_test.connected") is False


# --- Login ------------------------------------------------------------------


@pytest.fixture
async def acme_sim_auth():
    """Simulator that demands a login before it answers anything."""
    sim = AcmeDisplaySimulator(
        port=0, warmup_time=0.3, cooldown_time=0.2, password="test123"
    )
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
async def driver_auth(acme_sim_auth):
    """Driver connected through the login handshake."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    d = AcmeDisplayDriver(
        device_id="disp_auth",
        config={
            "host": "127.0.0.1",
            "port": acme_sim_auth.port,
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
    """The handshake in _post_connect completes before connected is declared."""
    d, state = driver_auth
    assert state.get("device.disp_auth.connected") is True


async def test_auth_command(driver_auth, acme_sim_auth):
    """Commands work once the session is authenticated."""
    d, state = driver_auth
    await d.send_command("power_on")
    await asyncio.sleep(0.1)
    await d.poll()
    await asyncio.sleep(SETTLE)
    assert state.get("device.disp_auth.power") == "warming"


async def test_auth_wrong_password_fails_the_connect(acme_sim_auth):
    """A rejected login aborts connect() rather than reporting connected.

    _post_connect raising is what has to tear the attempt down: the socket
    opened fine, so nothing below the handshake would have noticed.
    """
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    d = AcmeDisplayDriver(
        device_id="disp_bad",
        config={
            "host": "127.0.0.1",
            "port": acme_sim_auth.port,
            "password": "wrong",
            "poll_interval": 0,
        },
        state=state,
        events=events,
    )
    with pytest.raises(ConnectionError):
        await d.connect()
    assert d.connected is False
    assert d.transport is None
