"""The command dry run reports exactly what the runtime would transmit.

The Driver Builder's wire preview used to be built by a second, TypeScript
implementation of the send path. It substituted `{name}` tokens and nothing
else, so it disagreed with the runtime about four things at once: format
specs (`{preset:02d}` previewed verbatim, sent as `05`), command_prefix and
command_suffix framing, escape decoding (`\\x02` shown as four characters,
sent as one byte), and the computed send_frame header.

The dry run removes the second implementation instead of correcting it. It
builds the command with the real ConfigurableDriver and hands it a transport
that records instead of transmitting, so the preview is the send. These tests
pin that: each framing knob shows up in the reported wire, and the bytes a
dry run reports are byte-identical to the bytes a live send puts on a socket.

Devices here are invented; the platform behaviour is the subject.
"""
from __future__ import annotations

import asyncio

import pytest

from openavc.api.models import TestCommandRequest
from openavc.api.routes.driver_test import (
    _dry_run_command,
    _test_via_configurable_driver,
)
# Aliased on import: pytest would otherwise collect the route handler itself.
from openavc.api.routes.driver_test import test_driver_command as command_endpoint


def _definition(**overrides) -> dict:
    """A minimal byte-stream driver, plus whatever the case is about."""
    base = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "transport": "tcp",
        "commands": {
            "set_preset": {
                "label": "Set Preset",
                "send": "PRESET {preset:02d}\\r",
                "params": {"preset": {"type": "integer", "min": 1, "max": 64}},
            },
        },
    }
    base.update(overrides)
    return base


def _request(definition: dict, command: str, params: dict | None = None, **kw):
    return TestCommandRequest(
        host=kw.pop("host", "192.0.2.10"),
        port=kw.pop("port", 4001),
        transport=definition.get("transport", "tcp"),
        definition=definition,
        command_name=command,
        params=params or {},
        dry_run=True,
        **kw,
    )


# --- the four things the TypeScript preview got wrong -------------------------

async def test_format_spec_is_applied():
    """`{preset:02d}` renders as the runtime formats it, not verbatim."""
    result = await _dry_run_command(
        _request(_definition(), "set_preset", {"preset": 5})
    )
    assert result["success"], result
    assert result["route"] == "raw"
    assert result["wire"] == "PRESET 05\r"


async def test_hex_format_spec_is_applied():
    definition = _definition(
        commands={
            "set_volume": {
                "label": "Set Volume",
                "send": "VOL{level:04X}\\r",
                "params": {"level": {"type": "integer"}},
            }
        }
    )
    result = await _dry_run_command(
        _request(definition, "set_volume", {"level": 255})
    )
    assert result["wire"] == "VOL00FF\r"


async def test_command_prefix_and_suffix_wrap_the_send():
    definition = _definition(command_prefix="@", command_suffix="!\\r")
    definition["commands"]["set_preset"]["send"] = "PRESET {preset:02d}"
    result = await _dry_run_command(
        _request(definition, "set_preset", {"preset": 7})
    )
    assert result["wire"] == "@PRESET 07!\r"


async def test_escape_sequences_decode_to_bytes():
    """`\\x02` is one byte on the wire, not the four characters written."""
    definition = _definition(
        commands={"wake": {"label": "Wake", "send": "\\x02WAKE\\x03", "params": {}}}
    )
    result = await _dry_run_command(_request(definition, "wake"))
    assert bytes.fromhex(result["wire_hex"]) == b"\x02WAKE\x03"


async def test_send_frame_header_is_computed():
    """A length-prefix send_frame wraps the payload with its computed length."""
    definition = _definition(
        send_frame={"type": "length_prefix", "length_bytes": 2, "header": "ISCP"},
        commands={"ping": {"label": "Ping", "send": "PING", "params": {}}},
    )
    result = await _dry_run_command(_request(definition, "ping"))
    wire = bytes.fromhex(result["wire_hex"])
    assert wire.startswith(b"ISCP"), wire
    assert wire.endswith(b"PING"), wire
    assert len(wire) > len(b"PING")


# --- the other two routes -----------------------------------------------------

async def test_osc_route_reports_the_encoded_packet():
    definition = _definition(
        transport="osc",
        commands={
            "fader": {
                "label": "Fader",
                "address": "/ch/{channel:02d}/fader",
                "args": [{"type": "f", "value": "{level}"}],
                "params": {
                    "channel": {"type": "integer"},
                    "level": {"type": "number"},
                },
            }
        },
    )
    result = await _dry_run_command(
        _request(definition, "fader", {"channel": 3, "level": 0.5}, port=8000)
    )
    assert result["success"], result
    assert result["route"] == "osc"
    # Read back from the packet the codec actually produced.
    assert result["wire"].startswith("/ch/03/fader")
    assert "0.5" in result["wire"]
    assert bytes.fromhex(result["wire_hex"]).startswith(b"/ch/03/fader\x00")


async def test_http_route_reports_target_headers_and_body():
    definition = _definition(
        transport="http",
        commands={
            "set_input": {
                "label": "Set Input",
                "method": "POST",
                "path": "/api/input/{input:02d}",
                "headers": {"Content-Type": "text/xml"},
                "body": "<Input>{input}</Input>",
                "params": {"input": {"type": "integer"}},
            }
        },
    )
    result = await _dry_run_command(
        _request(definition, "set_input", {"input": 4}, port=80)
    )
    assert result["success"], result
    assert result["route"] == "http"
    assert result["wire"] == "POST /api/input/04"
    assert result["headers"]["content-type"] == "text/xml"
    assert result["body"] == "<Input>4</Input>"


async def test_http_query_params_reach_the_request_target():
    definition = _definition(
        transport="http",
        commands={
            "status": {
                "label": "Status",
                "method": "GET",
                "path": "/api/status",
                "query_params": {"verbose": "1", "ch": "{ch}"},
                "params": {"ch": {"type": "integer"}},
            }
        },
    )
    result = await _dry_run_command(
        _request(definition, "status", {"ch": 3}, port=80)
    )
    assert result["wire"] == "GET /api/status?verbose=1&ch=3"


async def test_query_params_survive_a_non_json_body():
    """A command may carry both a text body and query params.

    The two used to be exclusive by accident: the sender returned early for a
    body that wasn't JSON, before it had built the query string, so an XML or
    form-text command silently sent no query params at all. One send site now
    handles both bodies, so declaring both works.
    """
    definition = _definition(
        transport="http",
        commands={
            "set_input": {
                "label": "Set Input",
                "method": "POST",
                "path": "/api/input",
                "query_params": {"force": "1", "ch": "{input}"},
                "body": "<Input>{input}</Input>",
                "params": {"input": {"type": "integer"}},
            }
        },
    )
    result = await _dry_run_command(
        _request(definition, "set_input", {"input": 4}, port=80)
    )
    assert result["wire"] == "POST /api/input?force=1&ch=4"
    assert result["body"] == "<Input>4</Input>"


async def test_http_json_body_is_encoded_the_way_it_is_sent():
    definition = _definition(
        transport="http",
        commands={
            "set_mute": {
                "label": "Set Mute",
                "method": "PUT",
                "path": "/api/mute",
                "body": '{"mute": {state}}',
                "params": {"state": {"type": "integer"}},
            }
        },
    )
    result = await _dry_run_command(
        _request(definition, "set_mute", {"state": 1}, port=80)
    )
    assert result["wire"] == "PUT /api/mute"
    assert result["body"] == '{"mute":1}'


# --- an HTTP request as the device gets it ------------------------------------

def _http_command(**command) -> dict:
    return _definition(
        transport="http",
        commands={"set_gain": {"label": "Set Gain", "method": "PUT", "path": "/api/gain",
                               "body": '{"gain": {level}}',
                               "params": {"level": {"type": "integer"}}, **command}},
    )


async def test_http_preview_carries_the_headers_the_connection_adds():
    """The request the device gets carries the connection's default headers,
    the body's Content-Type and the sign-in header, not only the command's
    own headers. The token itself is masked, as the traffic view masks it."""
    result = await _dry_run_command(_request(
        _http_command(), "set_gain", {"level": 3}, port=80,
        config_overrides={
            "auth_type": "bearer", "token": "tok-9f8e7d6c",
            "default_headers": {"X-Api-Version": "2"},
        },
    ))
    assert result["success"], result
    assert result["wire"] == "PUT /api/gain"
    headers = result["headers"]
    assert headers["x-api-version"] == "2"
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer ***"
    assert headers["host"] == "192.0.2.10"
    assert "tok-9f8e7d6c" not in str(result)


async def test_an_api_key_header_is_shown_with_its_key_masked():
    result = await _dry_run_command(_request(
        _http_command(), "set_gain", {"level": 3}, port=80,
        config_overrides={
            "auth_type": "api_key", "api_key_header": "X-Device-Key",
            "api_key": "key-5a4b3c2d",
        },
    ))
    assert result["success"], result
    assert result["headers"]["x-device-key"] == "***"
    assert "key-5a4b3c2d" not in str(result)


async def test_a_basic_sign_in_header_is_shown_masked():
    """Basic sign-in is added as the request is sent, not when it is built."""
    result = await _dry_run_command(_request(
        _http_command(), "set_gain", {"level": 3}, port=80,
        config_overrides={"auth_type": "basic", "username": "admin", "password": "pw-1a2b3c4d"},
    ))
    assert result["success"], result
    assert result["headers"]["authorization"] == "Basic ***"
    assert "pw-1a2b3c4d" not in str(result)


async def test_http_preview_names_the_port_it_connects_to():
    """The Host header is the device's address and port, as connecting
    builds them: https with no port set connects to 443, so the header
    carries no port; a port that is not the scheme's own is named."""
    implied = await _dry_run_command(_request(
        _http_command(), "set_gain", {"level": 1}, port="",
        config_overrides={"ssl": True},
    ))
    assert implied["success"], implied
    assert implied["headers"]["host"] == "192.0.2.10"
    named = await _dry_run_command(_request(_http_command(), "set_gain", {"level": 1}, port=8080))
    assert named["headers"]["host"] == "192.0.2.10:8080"


async def test_an_http_preview_builds_with_an_unfilled_connection_panel():
    result = await _dry_run_command(
        _request(_http_command(), "set_gain", {"level": 2}, host="", port="")
    )
    assert result["success"], result
    assert result["wire"] == "PUT /api/gain"
    assert "host" not in result["headers"]


@pytest.mark.parametrize("host", ["10.0.0.5:80", "[::1", "10.0.0.5:"])
async def test_an_http_preview_builds_while_the_address_is_being_typed(host: str):
    """A host box that does not hold an address yet leaves the address out of
    the preview, as an empty one does: the command's wire form still shows."""
    result = await _dry_run_command(
        _request(_http_command(), "set_gain", {"level": 2}, host=host, port=80)
    )
    assert result["success"], result
    assert result["wire"] == "PUT /api/gain"
    assert "host" not in result["headers"]


async def test_an_http_preview_builds_with_a_port_no_connection_takes():
    result = await _dry_run_command(
        _request(_http_command(), "set_gain", {"level": 2}, port=70000)
    )
    assert result["success"], result
    assert result["wire"] == "PUT /api/gain"
    assert "host" not in result["headers"]


async def test_a_serial_draft_previews_its_http_shaped_command():
    """The panel names the mismatch; the dry run still shows what the
    command would build."""
    definition = {**_http_command(), "transport": "serial"}
    body = _request(definition, "set_gain", {"level": 2}, host="", port="COM3")
    result = await _dry_run_command(body)
    assert result["success"], result
    assert result["wire"] == "PUT /api/gain"


async def test_an_http_preview_sends_nothing_and_records_nothing(monkeypatch):
    """The stand-in is the device's own HTTP client with only the network
    taken away: nothing leaves, and the device's traffic view gets nothing."""
    import httpx

    from openavc.core import device_traffic

    async def _explode(*args, **kwargs):
        raise AssertionError("a dry run must not send a request")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _explode)
    recorded: list[str] = []
    monkeypatch.setattr(
        device_traffic._recorder, "record",
        lambda device_id, *a, **k: recorded.append(device_id),
    )
    result = await _dry_run_command(_request(_http_command(), "set_gain", {"level": 2}, port=80))
    assert result["success"], result
    assert recorded == []


# --- what a dry run must not do -----------------------------------------------

async def test_dry_run_opens_no_transport(monkeypatch):
    """No socket is created — the capture stands in before anything connects."""
    from openavc.transport.tcp import TCPTransport

    async def _explode(*args, **kwargs):
        raise AssertionError("a dry run must not open a transport")

    monkeypatch.setattr(TCPTransport, "create", _explode)
    result = await _dry_run_command(
        _request(_definition(), "set_preset", {"preset": 1})
    )
    assert result["success"], result


async def test_dry_run_is_not_rate_limited():
    """Back-to-back previews are the point; the 2s limit guards devices."""
    body = _request(_definition(), "set_preset", {"preset": 1})
    for _ in range(5):
        result = await command_endpoint("acme_widget", body)
        assert result["success"], result


async def test_dry_run_does_not_consume_the_live_send_allowance():
    """A preview must not make the next real send 429."""
    from openavc.api import _engine

    _engine._test_endpoint_last_call.pop("test_command:acme_rate", None)
    await command_endpoint(
        "acme_rate", _request(_definition(), "set_preset", {"preset": 1})
    )
    assert "test_command:acme_rate" not in _engine._test_endpoint_last_call


async def test_dry_run_tolerates_an_unfilled_connection_panel():
    """Previewing while the port box is still empty is normal authoring."""
    body = _request(_definition(), "set_preset", {"preset": 9}, host="", port="")
    result = await _dry_run_command(body)
    assert result["success"], result
    assert result["wire"] == "PRESET 09\r"


async def test_unknown_command_is_reported_plainly():
    result = await _dry_run_command(_request(_definition(), "nope"))
    assert not result["success"]
    assert "nope" in result["error"]


async def test_invalid_definition_is_reported_plainly():
    body = _request({"id": "broken", "commands": {"x": {"send": "A"}}}, "x")
    body.definition["state_variables"] = "not a mapping"
    result = await _dry_run_command(body)
    assert not result["success"]
    assert result["error"]


async def test_a_command_with_nothing_to_send_previews_blank():
    definition = _definition(
        commands={"todo": {"label": "Not written yet", "send": "", "params": {}}}
    )
    result = await _dry_run_command(_request(definition, "todo"))
    assert result["success"], result
    assert result["wire"] == ""


async def test_param_validation_still_applies():
    """The dry run is the real send path, so a bad value fails the same way."""
    result = await _dry_run_command(
        _request(_definition(), "set_preset", {"preset": 999})
    )
    assert not result["success"]
    assert result["error"]


async def test_param_wire_map_is_applied():
    """A param's `map:` translation reaches the preview, as it reaches the wire."""
    definition = _definition(
        commands={
            "select": {
                "label": "Select",
                "send": "SEL{source}\\r",
                "params": {
                    "source": {
                        "type": "enum",
                        "values": ["hdmi1", "hdmi2"],
                        "map": {"hdmi1": "A", "hdmi2": "B"},
                    }
                },
            }
        }
    )
    result = await _dry_run_command(
        _request(definition, "select", {"source": "hdmi2"})
    )
    assert result["wire"] == "SELB\r"


def test_dry_run_round_trips_over_http():
    """The flag survives the request model and the route, not just the helper.

    No lifespan here on purpose: a dry run asks nothing of the engine, so it
    answers whether or not one is running.
    """
    from fastapi.testclient import TestClient

    from openavc.main import app

    client = TestClient(app)
    payload = {
        "host": "192.0.2.10",
        "port": 4001,
        "transport": "tcp",
        "definition": _definition(),
        "command_name": "set_preset",
        "params": {"preset": 5},
        "dry_run": True,
    }
    first = client.post(
        "/api/driver-definitions/acme_widget/test-command", json=payload
    )
    assert first.status_code == 200, first.text
    assert first.json()["wire"] == "PRESET 05\r"

    # Immediately again: previews are not throttled the way sends are.
    second = client.post(
        "/api/driver-definitions/acme_widget/test-command", json=payload
    )
    assert second.status_code == 200, second.text


def test_dry_run_over_http_requires_a_command():
    from fastapi.testclient import TestClient

    from openavc.main import app

    client = TestClient(app)
    response = client.post(
        "/api/driver-definitions/acme_widget/test-command",
        json={"host": "1.2.3.4", "port": 1, "transport": "tcp", "dry_run": True},
    )
    assert response.status_code == 422, response.text


# --- the equality that matters ------------------------------------------------

async def test_dry_run_bytes_equal_a_live_send_over_a_real_socket():
    """The preview is not merely plausible: it is byte-identical to the send.

    Runs the same command twice through the same endpoint helper — once as a
    dry run, once live against a loopback server that records what arrives —
    and compares the bytes. Covers every framing knob at once.
    """
    definition = _definition(
        command_prefix="\\x02",
        command_suffix="\\x03",
        commands={
            "set_preset": {
                "label": "Set Preset",
                "send": "PRESET {preset:02d}",
                "params": {"preset": {"type": "integer", "min": 1, "max": 64}},
            }
        },
    )

    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        data = await reader.read(256)
        if data:
            received.append(data)
        writer.write(b"OK\r")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        dry = await _dry_run_command(
            _request(definition, "set_preset", {"preset": 5}, host="127.0.0.1",
                     port=port)
        )
        live_body = TestCommandRequest(
            host="127.0.0.1",
            port=port,
            transport="tcp",
            definition=definition,
            command_name="set_preset",
            params={"preset": 5},
            timeout=1.0,
        )
        await _test_via_configurable_driver(live_body)
    finally:
        server.close()
        await server.wait_closed()

    assert received, "the live send never reached the loopback server"
    assert bytes.fromhex(dry["wire_hex"]) == received[0]
    assert received[0] == b"\x02PRESET 05\x03"


@pytest.mark.parametrize(
    "template,params,expected",
    [
        ("PRESET {preset:02d}\\r", {"preset": 5}, "PRESET 05\r"),
        ("ADDR {addr:04X}\\r", {"addr": 255}, "ADDR 00FF\r"),
        # An empty string substitutes as empty; the old preview left the
        # placeholder standing.
        ("NAME {label}\\r", {"label": ""}, "NAME \r"),
        # An unknown token is left alone, exactly as the runtime leaves it.
        ("RAW {unknown}\\r", {}, "RAW {unknown}\r"),
    ],
)
async def test_substitution_cases_the_old_preview_disagreed_on(
    template: str, params: dict, expected: str
):
    definition = _definition(
        commands={
            "case": {
                "label": "Case",
                "send": template,
                "params": {
                    name: {"type": "integer" if isinstance(v, int) else "string"}
                    for name, v in params.items()
                },
            }
        }
    )
    result = await _dry_run_command(_request(definition, "case", params))
    assert result["success"], result
    assert result["wire"] == expected
