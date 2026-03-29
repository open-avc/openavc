"""Tests for vMix driver with the simulator."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from server.core.event_bus import EventBus
from server.core.state_store import StateStore
from tests.simulators.vmix_simulator import VMixSimulator

# Load the vMix driver module from driver_repo path via the community driver
_vmix_path = Path(__file__).parent.parent.parent / "openavc-drivers" / "video" / "vmix.py"
if not _vmix_path.exists():
    pytest.skip("openavc-drivers repo not available", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("_vmix_driver", _vmix_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_parse_vmix_frame = _mod._parse_vmix_frame
_XML_BODY_PREFIX = _mod._XML_BODY_PREFIX
VMixDriver = _mod.VMixDriver


# --- Frame parser unit tests ---


def test_parse_normal_crlf():
    """Normal CRLF-delimited message."""
    msg, remaining = _parse_vmix_frame(b"FUNCTION OK\r\n")
    assert msg == b"FUNCTION OK"
    assert remaining == b""


def test_parse_incomplete():
    """Incomplete message (no CRLF) returns None."""
    msg, remaining = _parse_vmix_frame(b"FUNCTION OK")
    assert msg is None
    assert remaining == b"FUNCTION OK"


def test_parse_multiple_messages():
    """Multiple CRLF messages in one buffer."""
    buffer = b"FUNCTION OK\r\nTALLY OK 1200\r\n"
    msg1, remaining = _parse_vmix_frame(buffer)
    assert msg1 == b"FUNCTION OK"
    msg2, remaining = _parse_vmix_frame(remaining)
    assert msg2 == b"TALLY OK 1200"
    assert remaining == b""


def test_parse_xml_response():
    """XML response with length-prefixed body."""
    xml_body = b"<vmix><recording>True</recording></vmix>"
    header = f"XML {len(xml_body)}\r\n".encode()
    buffer = header + xml_body

    msg, remaining = _parse_vmix_frame(buffer)
    assert msg is not None
    assert msg.startswith(_XML_BODY_PREFIX)
    body = msg[len(_XML_BODY_PREFIX):]
    assert body == xml_body
    assert remaining == b""


def test_parse_incomplete_xml():
    """Incomplete XML body — parser waits for more data."""
    xml_body = b"<vmix><recording>True</recording></vmix>"
    header = f"XML {len(xml_body)}\r\n".encode()
    # Send only half the body
    buffer = header + xml_body[:10]

    msg, remaining = _parse_vmix_frame(buffer)
    assert msg is None
    assert remaining == buffer


def test_parse_invalid_xml_length():
    """Non-numeric XML length treated as normal message."""
    buffer = b"XML notanumber\r\n"
    msg, remaining = _parse_vmix_frame(buffer)
    assert msg == b"XML notanumber"
    assert remaining == b""


def test_parse_mixed_messages():
    """Mix of normal and XML messages in one buffer."""
    xml_body = b"<vmix/>"
    buffer = b"TALLY OK 12\r\n" + f"XML {len(xml_body)}\r\n".encode() + xml_body + b"FUNCTION OK\r\n"

    msg1, remaining = _parse_vmix_frame(buffer)
    assert msg1 == b"TALLY OK 12"

    msg2, remaining = _parse_vmix_frame(remaining)
    assert msg2.startswith(_XML_BODY_PREFIX)
    assert msg2[len(_XML_BODY_PREFIX):] == xml_body

    msg3, remaining = _parse_vmix_frame(remaining)
    assert msg3 == b"FUNCTION OK"
    assert remaining == b""


# --- Simulator fixture ---


@pytest.fixture
async def vmix_sim():
    """Running vMix simulator on a test port."""
    sim = VMixSimulator(port=18099)
    await sim.start()
    yield sim
    await sim.stop()


@pytest.fixture
def core():
    """StateStore + EventBus wired together."""
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return state, events


@pytest.fixture
async def driver(vmix_sim, core):
    """Connected vMix driver."""
    state, events = core
    d = VMixDriver(
        device_id="vmix_test",
        config={
            "host": "127.0.0.1",
            "port": 18099,
            "poll_interval": 0,
            "subscribe_tally": True,
            "subscribe_acts": False,
        },
        state=state,
        events=events,
    )
    await d.connect()
    await asyncio.sleep(0.1)  # Let tally subscription arrive
    yield d
    await d.disconnect()


# --- Integration tests ---


async def test_connect(driver, core):
    """Driver connects and receives initial tally."""
    state, _ = core
    assert state.get("device.vmix_test.connected") is True


async def test_initial_tally(driver, core):
    """After connect, tally subscription provides active/preview."""
    state, _ = core
    # Simulator starts with active=1, preview=2
    assert state.get("device.vmix_test.active") == 1
    assert state.get("device.vmix_test.preview") == 2


async def test_cut(driver, core, vmix_sim):
    """Cut swaps active and preview."""
    state, _ = core
    await driver.send_command("cut")
    await asyncio.sleep(0.15)
    # After cut: active becomes 2, preview becomes 1
    assert state.get("device.vmix_test.active") == 2
    assert state.get("device.vmix_test.preview") == 1


async def test_fade(driver, core, vmix_sim):
    """Fade swaps active and preview."""
    state, _ = core
    await driver.send_command("fade")
    await asyncio.sleep(0.15)
    assert state.get("device.vmix_test.active") == 2
    assert state.get("device.vmix_test.preview") == 1


async def test_preview_input(driver, core, vmix_sim):
    """Preview input changes preview."""
    state, _ = core
    await driver.send_command("preview_input", {"input": "3"})
    await asyncio.sleep(0.15)
    assert state.get("device.vmix_test.preview") == 3


async def test_tally_subscription(driver, core, vmix_sim):
    """Tally updates push after input change."""
    state, _ = core
    await driver.send_command("cut_direct", {"input": "3"})
    await asyncio.sleep(0.15)
    assert state.get("device.vmix_test.active") == 3
    assert state.get("device.vmix_test.tally.3") == 1


async def test_recording(driver, core, vmix_sim):
    """Start and stop recording via XML poll."""
    state, _ = core
    await driver.send_command("start_recording")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.2)
    assert state.get("device.vmix_test.recording") is True

    await driver.send_command("stop_recording")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.2)
    assert state.get("device.vmix_test.recording") is False


async def test_streaming(driver, core, vmix_sim):
    """Start and stop streaming."""
    state, _ = core
    await driver.send_command("start_streaming")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.2)
    assert state.get("device.vmix_test.streaming") is True

    await driver.send_command("stop_streaming")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.2)
    assert state.get("device.vmix_test.streaming") is False


async def test_set_volume(driver, core, vmix_sim):
    """Set volume on an input."""
    state, _ = core
    result = await driver.send_command("set_volume", {"input": "1", "value": 50})
    assert result == "FUNCTION OK"
    # Volume update is in simulator state, verify via XML poll
    await driver.poll()
    await asyncio.sleep(0.2)
    # The XML poll doesn't include volume in our simple XML, but the command succeeded
    assert vmix_sim.inputs[0]["volume"] == 50


async def test_overlay(driver, core, vmix_sim):
    """Overlay input in/out."""
    await driver.send_command("overlay_input_in", {"input": "3", "value": 1})
    await asyncio.sleep(0.1)
    assert vmix_sim.overlays["1"] == 3

    await driver.send_command("overlay_input_off", {"value": 1})
    await asyncio.sleep(0.1)
    assert vmix_sim.overlays["1"] == 0


async def test_xml_poll(driver, core, vmix_sim):
    """XML poll retrieves full state."""
    state, _ = core
    await driver.poll()
    await asyncio.sleep(0.3)
    assert state.get("device.vmix_test.version") == "29.0.0.1"
    assert state.get("device.vmix_test.input_count") == 4
    assert state.get("device.vmix_test.input.1.title") == "Camera 1"
    assert state.get("device.vmix_test.input.4.type") == "Video"


async def test_set_text(driver, core, vmix_sim):
    """SetText command sends correctly."""
    result = await driver.send_command("set_text", {
        "input": "1",
        "selectedName": "Title",
        "value": "Hello World",
    })
    assert result == "FUNCTION OK"


async def test_raw_function(driver, core, vmix_sim):
    """raw_function sends arbitrary vMix function."""
    result = await driver.send_command("raw_function", {
        "function": "PreviewInput",
        "query": "Input=4",
    })
    assert result == "FUNCTION OK"
    await asyncio.sleep(0.15)
    state, _ = core
    assert state.get("device.vmix_test.preview") == 4


async def test_disconnect(driver, core):
    """Disconnect cleans up state."""
    state, _ = core
    await driver.disconnect()
    assert state.get("device.vmix_test.connected") is False


async def test_fade_to_black(driver, core, vmix_sim):
    """Fade to black toggle."""
    await driver.send_command("fade_to_black")
    await asyncio.sleep(0.1)
    await driver.poll()
    await asyncio.sleep(0.2)
    state, _ = core
    assert state.get("device.vmix_test.fadeToBlack") is True
