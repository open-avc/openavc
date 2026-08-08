"""Tests for the OSC platform additions that back the QLab driver:

  - ``json_path`` on response mappings (pull a value out of a JSON string
    carried in an OSC/regex response arg)
  - ``config_derived`` (declarative computed config, e.g. an optional address
    prefix)
  - OSC over TCP framed with SLIP (RFC 1055)

Per the platform test policy these exercise the runtime with INVENTED devices
("acme_*") and synthetic payloads — no real product or captured fixture is
involved. A regression test pins that a positional-arg OSC response (no
``json_path``) is unchanged.
"""

import asyncio

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import (
    ConfigurableDriver,
    create_configurable_driver_class,
)
from openavc.transport.frame_parsers import SlipFrameParser, slip_encode
from openavc.transport.osc import OSCTransport
from openavc.transport.osc_codec import osc_decode_message, osc_encode_message


def _make_driver(definition, config=None, device_id="dev1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, config or {}, state, events)


# ===========================================================================
# json_path — OSC responses (the QLab /reply {"data": ...} shape)
# ===========================================================================

_JSON_DEF = {
    "id": "acme_reply_box",
    "name": "Acme Reply Box",
    "transport": "osc",
    "commands": {},
    "state_variables": {
        "title": {"type": "string", "label": "Title"},
        "running": {"type": "boolean", "label": "Running"},
        "raw_level": {"type": "number", "label": "Raw Level"},
    },
    "responses": [
        {"address": "/reply*/title", "mappings": [
            {"arg": 0, "json_path": "data", "state": "title", "type": "string"}]},
        {"address": "/reply*/nested", "mappings": [
            {"arg": 0, "json_path": "data.name", "state": "title", "type": "string"}]},
        {"address": "/reply*/running", "mappings": [
            {"arg": 0, "json_path": "data", "state": "running", "type": "boolean"}]},
        # Positional, no json_path — must behave exactly as before.
        {"address": "/acme/level", "mappings": [
            {"arg": 0, "state": "raw_level", "type": "float"}]},
    ],
}


@pytest.mark.asyncio
async def test_json_path_extracts_top_level_value():
    drv = _make_driver(_JSON_DEF)
    msg = osc_encode_message(
        "/reply/workspace/ABC/title",
        [("s", '{"status":"ok","data":"Intro Music"}')],
    )
    await drv.on_data_received(msg)
    assert drv.get_state("title") == "Intro Music"


@pytest.mark.asyncio
async def test_json_path_walks_nested_key():
    drv = _make_driver(_JSON_DEF)
    msg = osc_encode_message(
        "/reply/nested", [("s", '{"data":{"name":"Cue 5"}}')]
    )
    await drv.on_data_received(msg)
    assert drv.get_state("title") == "Cue 5"


@pytest.mark.asyncio
async def test_json_path_list_yields_length_as_boolean():
    drv = _make_driver(_JSON_DEF)
    # Non-empty array -> truthy "anything running?".
    await drv.on_data_received(
        osc_encode_message("/reply/running", [("s", '{"data":["c1","c2"]}')])
    )
    assert drv.get_state("running") is True
    # Empty array -> falsy.
    await drv.on_data_received(
        osc_encode_message("/reply/running", [("s", '{"data":[]}')])
    )
    assert drv.get_state("running") is False


@pytest.mark.asyncio
async def test_json_path_invalid_json_is_skipped():
    drv = _make_driver(_JSON_DEF)
    # Seed a known value, then feed garbage — the mapping is skipped, not
    # overwritten with a wrong value.
    await drv.on_data_received(
        osc_encode_message("/reply/title", [("s", '{"data":"Keep Me"}')])
    )
    assert drv.get_state("title") == "Keep Me"
    await drv.on_data_received(
        osc_encode_message("/reply/title", [("s", "this is not json")])
    )
    assert drv.get_state("title") == "Keep Me"


@pytest.mark.asyncio
async def test_json_path_missing_key_is_skipped():
    drv = _make_driver(_JSON_DEF)
    await drv.on_data_received(
        osc_encode_message("/reply/title", [("s", '{"data":"First"}')])
    )
    # JSON valid but has no "data" key — skip rather than wipe state.
    await drv.on_data_received(
        osc_encode_message("/reply/title", [("s", '{"status":"ok"}')])
    )
    assert drv.get_state("title") == "First"


@pytest.mark.asyncio
async def test_positional_osc_arg_unchanged_without_json_path():
    """Regression: an OSC response with no json_path still reads the arg
    positionally by type, exactly as before the feature."""
    drv = _make_driver(_JSON_DEF)
    await drv.on_data_received(
        osc_encode_message("/acme/level", [("f", 0.42)])
    )
    assert drv.get_state("raw_level") == pytest.approx(0.42)


# ===========================================================================
# json_path — regex/text responses (shared path; TCP/HTTP JSON replies)
# ===========================================================================

_REGEX_JSON_DEF = {
    "id": "acme_json_lines",
    "name": "Acme JSON Lines",
    "transport": "tcp",
    "commands": {},
    "state_variables": {"label": {"type": "string", "label": "Label"}},
    "responses": [
        {"match": r"(\{.*\})", "mappings": [
            {"group": 1, "json_path": "data", "state": "label", "type": "string"}]},
    ],
}


@pytest.mark.asyncio
async def test_json_path_on_regex_capture_group():
    drv = _make_driver(_REGEX_JSON_DEF)
    await drv.on_data_received(b'PREFIX {"status":"ok","data":"Zone A"} SUFFIX')
    assert drv.get_state("label") == "Zone A"


# ===========================================================================
# config_derived — optional address prefix
# ===========================================================================

_WS_DEF = {
    "id": "acme_ws_box",
    "name": "Acme Workspace Box",
    "transport": "osc",
    "config_derived": {"ws": "/workspace/{workspace_id}"},
    "default_config": {"workspace_id": ""},
    "commands": {"go": {"address": "{ws}/go"}},
    "responses": [],
    "state_variables": {},
}


def test_config_derived_present_when_field_set():
    drv = _make_driver(_WS_DEF, {"workspace_id": "ABC123"})
    assert drv.config["ws"] == "/workspace/ABC123"
    # And it flows into command address substitution.
    assert ConfigurableDriver._safe_substitute("{ws}/go", drv.config) == \
        "/workspace/ABC123/go"


def test_config_derived_blank_when_field_empty():
    drv = _make_driver(_WS_DEF, {"workspace_id": ""})
    assert drv.config["ws"] == ""
    assert ConfigurableDriver._safe_substitute("{ws}/go", drv.config) == "/go"


def test_config_derived_blank_when_field_missing():
    # workspace_id not supplied at all (falls back to default "").
    drv = _make_driver(_WS_DEF, {})
    assert drv.config["ws"] == ""


def test_no_config_derived_leaves_config_untouched():
    drv = _make_driver(_JSON_DEF, {"host": "1.2.3.4"})
    assert "ws" not in drv.config


# ===========================================================================
# OSC over TCP with SLIP framing
# ===========================================================================


async def _start_slip_osc_echo_server():
    """A localhost TCP server that SLIP-deframes incoming OSC and replies with
    one SLIP-framed OSC packet per message received.

    Returns ``(port, received, aclose)``. Call ``await aclose()`` to tear it
    down. We deliberately do not use ``asyncio.Server.wait_closed()``: on
    Python 3.12 it hangs forever when the client connection has already drained
    before it is awaited (a CPython bug the stdlib's own docstring records being
    mis-fixed across 3.11/3.12.0/3.12.1; fixed in 3.13). Instead we close the
    accepted connections ourselves and await each ``StreamWriter.wait_closed()``,
    which is sound on every version.
    """
    received: list[bytes] = []
    conns: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        conns.append(writer)
        parser = SlipFrameParser()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                for msg in parser.feed(data):
                    received.append(msg)
                    reply = osc_encode_message(
                        "/reply/version", [("s", '{"data":"5.4.5"}')]
                    )
                    writer.write(slip_encode(reply))
                    await writer.drain()
        except (ConnectionError, OSError):
            pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def aclose():
        server.close()
        for writer in conns:
            writer.close()
        for writer in conns:
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    return port, received, aclose


@pytest.mark.asyncio
async def test_osc_over_tcp_slip_round_trip():
    port, received, aclose = await _start_slip_osc_echo_server()
    got: list[bytes] = []
    transport = OSCTransport(
        host="127.0.0.1", port=port, on_data=lambda d: got.append(d), tcp=True
    )
    try:
        await transport.open()
        assert transport.connected
        await transport.send_message("/workspace/ABC/go")
        # Let the server receive + reply and our reader deliver the frame.
        for _ in range(50):
            if got:
                break
            await asyncio.sleep(0.02)

        # The server received our SLIP-framed /go, deframed cleanly.
        assert any(
            osc_decode_message(m)[0] == "/workspace/ABC/go" for m in received
        )
        # We received the server's SLIP-framed reply, deframed by our parser.
        assert got, "no reply received over TCP+SLIP"
        addr, args = osc_decode_message(got[0])
        assert addr == "/reply/version"
        assert args == [("s", '{"data":"5.4.5"}')]
    finally:
        await transport.close()
        await aclose()


@pytest.mark.asyncio
async def test_osc_tcp_verify_true_when_connected():
    port, _received, aclose = await _start_slip_osc_echo_server()
    transport = OSCTransport(host="127.0.0.1", port=port, tcp=True)
    try:
        await transport.open()
        assert await transport.verify(timeout=1.0) is True
    finally:
        await transport.close()
        await aclose()
