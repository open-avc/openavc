"""One reading of an HTTP text line: a request written as one line of text
(``METHOD /path body``), wherever a driver writes one.

A command's ``send:`` on HTTP, the poll, start-up and follow-up lines, a
``read_back:`` query, the Driver Builder's raw Send and the simulator
validator's sample all read a line the same way (``transport/http_line``).
The runtime's lines go through the dry run's recording transport, so a
recorded request is what the device would get. A command's own ``read_back:
true`` is a GET of its path, never read as a line.

Invented devices ("acme_*") and synthetic payloads throughout.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from openavc.api.models import TestCommandRequest
from openavc.api.routes import driver_test
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.avcdriver_semantic import validate_driver_definition
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.dry_run import capture_http_transport
from openavc.simulator.driver_sends import query_line
from openavc.transport.http_line import METHODS, body_parts, read_http_line

# --- the reading ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("GET /api/status", ("GET", "/api/status", "")),
        ('POST /api/power {"on": true}', ("POST", "/api/power", '{"on": true}')),
        ('PUT /api/volume {"level": 50}', ("PUT", "/api/volume", '{"level": 50}')),
        ("DELETE /api/preset/1", ("DELETE", "/api/preset/1", "")),
        ("HEAD /", ("HEAD", "/", "")),
        ("OPTIONS /api", ("OPTIONS", "/api", "")),
        ("PATCH /api/x <a/>", ("PATCH", "/api/x", "<a/>")),
        # A method in any case, upper case on the wire.
        ("post /api/x", ("POST", "/api/x", "")),
        # A bare path is a GET; JSON after it makes it a POST of that JSON.
        ("/api/status", ("GET", "/api/status", "")),
        ('/api/x {"a": 1}', ("POST", "/api/x", '{"a": 1}')),
        ("/api/x [1, 2]", ("POST", "/api/x", "[1, 2]")),
        ("/api/x and more", ("GET", "/api/x", "")),
        # The path starts with a slash in every form.
        ("status", ("GET", "/status", "")),
        ("GET api/status", ("GET", "/api/status", "")),
        ("GET", ("GET", "/", "")),
        ("", ("GET", "/", "")),
        ("  GET /api/status \r\n", ("GET", "/api/status", "")),
    ],
)
def test_a_line_reads_as_its_method_path_and_body(text: str, expected: tuple[str, str, str]) -> None:
    line = read_http_line(text)
    assert (line.method, line.path, line.body) == expected


def test_a_space_ends_the_path() -> None:
    """A value with a space filled into a line splits it there: a path that
    needs one writes it encoded."""
    assert read_http_line("/api/preset/Main Room") == ("GET", "/api/preset/Main", "")
    assert read_http_line("/api/preset/Main%20Room") == ("GET", "/api/preset/Main%20Room", "")


def test_the_methods_are_the_ones_a_line_may_name() -> None:
    assert METHODS == ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")


def test_a_body_goes_as_json_when_it_parses_else_as_its_text() -> None:
    assert body_parts('{"on": true}') == ({"on": True}, None)
    assert body_parts("<on/>") == (None, b"<on/>")
    assert body_parts("power=on") == (None, b"power=on")


# --- the driver's own lines ---


def _acme_rest(**extra: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {
        "id": "acme_rest_unit",
        "name": "Acme REST Unit",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "http",
        "default_config": {"poll_interval": 0},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {
            "set_power": {
                "label": "Set Power",
                "method": "PUT",
                "path": "/api/power",
                "body": '{"on": true}',
                "read_back": 'POST /api/power/query {"what": "power"}',
            },
            "rename": {
                "label": "Rename",
                "method": "PUT",
                "path": "/api/name/{name}",
                "params": {"name": {"type": "string", "required": True, "label": "Name"}},
                "read_back": True,
            },
        },
        "responses": [{"match": r"^power=(\w+)$", "set": {"power": "$1"}}],
    }
    definition.update(extra)
    return definition


def _build(definition: dict[str, Any]) -> Any:
    assert validate_driver_definition(definition, strict=True) == []
    cls = create_configurable_driver_class(definition)
    config = {**definition["default_config"], "host": "127.0.0.1", "port": 80}
    return cls(device_id="acme1", config=config, state=StateStore(), events=EventBus())


async def _requests(definition: dict[str, Any], run: Any) -> list[tuple[str, str, str]]:
    """What ``run(driver)`` puts on the wire: each request's method, target
    and body."""
    drv = _build(definition)
    capture = await capture_http_transport(drv)
    drv.transport = capture
    try:
        await run(drv)
    finally:
        await capture.close()
    return [(r["method"], r["target"], r["body"]) for r in capture.requests]


async def test_a_poll_line_naming_a_method_goes_out_with_it() -> None:
    definition = _acme_rest(polling={"queries": ['POST /api/status {"all": true}', "/api/power"]})
    sent = await _requests(definition, lambda drv: drv.poll())
    assert sent == [("POST", "/api/status", '{"all":true}'), ("GET", "/api/power", "")]


async def test_a_start_up_line_naming_a_method_goes_out_with_it() -> None:
    definition = _acme_rest(on_connect=["PUT /api/subscribe <events/>", "api/hello"])
    sent = await _requests(definition, lambda drv: drv._run_on_connect())
    assert sent == [("PUT", "/api/subscribe", "<events/>"), ("GET", "/api/hello", "")]


async def test_a_follow_up_line_naming_a_method_goes_out_with_it() -> None:
    rules = [{"match": r"^power=(\w+)$", "set": {"power": "$1"}, "then_query": ["POST /api/detail"]}]
    definition = _acme_rest(responses=rules)

    async def answer(drv: Any) -> None:
        await drv.on_data_received(b"power=on")
        await asyncio.gather(*list(drv._follow_up_rounds.values()))

    sent = await _requests(definition, answer)
    assert sent == [("POST", "/api/detail", "")]


async def test_a_read_back_query_naming_a_method_goes_out_with_it() -> None:
    async def press(drv: Any) -> None:
        await drv.send_command("set_power", {})
        await asyncio.gather(*list(drv._read_back_tasks))

    sent = await _requests(_acme_rest(), press)
    assert sent == [
        ("PUT", "/api/power", '{"on":true}'),
        ("POST", "/api/power/query", '{"what":"power"}'),
    ]


async def test_a_command_reading_itself_back_gets_its_whole_path() -> None:
    """``read_back: true`` reads the command's own path, not a line: a value
    with a space in it stays in the path."""
    async def press(drv: Any) -> None:
        await drv.send_command("rename", {"name": "Main Room"})
        await asyncio.gather(*list(drv._read_back_tasks))

    sent = await _requests(_acme_rest(), press)
    assert sent == [("PUT", "/api/name/Main%20Room", ""), ("GET", "/api/name/Main%20Room", "")]


async def test_a_send_line_is_read_the_same_way() -> None:
    """A command's ``send:`` on HTTP, the reading's first caller."""
    commands = {"wake": {"label": "Wake", "send": "post api/wake"}}
    definition = _acme_rest(commands=commands)
    sent = await _requests(definition, lambda drv: drv.send_command("wake", {}))
    assert sent == [("POST", "/api/wake", "")]


# --- the simulator validator ---


@pytest.mark.parametrize(
    ("text", "dispatched"),
    [
        ("/api/status", "GET /api/status"),
        ("api/status", "GET /api/status"),
        ("get /api/status", "GET /api/status"),
        ("HEAD /api/status", "HEAD /api/status"),
        ('POST /api/status {"all": true}', 'POST /api/status|{"all":true}'),
        ("PUT /api/x <a/>", "PUT /api/x|<a/>"),
    ],
)
def test_the_validator_samples_a_line_as_the_simulator_receives_it(text: str, dispatched: str) -> None:
    """``METHOD /path``, then ``|`` and the body as it arrives: the HTTP
    simulator's command line (``yaml_auto.respond_http``)."""
    assert query_line(text, {}, "http") == dispatched


def test_the_validator_writes_a_json_body_as_the_client_does() -> None:
    """The body in the sample is the bytes the HTTP client sends for it."""
    value = {"a": [1, 2], "b": "é"}
    sent = httpx.Request("POST", "http://acme.local/x", json=value).content.decode("utf-8")
    assert query_line(f"POST /x {json.dumps(value)}", {}, "http") == f"POST /x|{sent}"


def test_the_validator_leaves_a_line_on_another_transport_alone() -> None:
    assert query_line("GET STATUS\\r", {}, "tcp") == "GET STATUS"


# --- the Driver Builder's raw Send ---


@pytest.fixture()
def probe_wire(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """The raw Send's requests, answered with an empty 200."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    monkeypatch.setattr(
        driver_test, "device_transport", lambda url, verify: httpx.MockTransport(answer),
    )
    return seen


async def _probe(command_string: str) -> dict[str, Any]:
    body = TestCommandRequest(
        host="10.0.0.5", port=80, transport="http", command_string=command_string,
    )
    return await driver_test._test_http_raw(body)


async def test_the_raw_send_names_the_method_the_line_does(probe_wire: list[httpx.Request]) -> None:
    result = await _probe("HEAD /api/status")
    assert result["sent"] == "HEAD http://10.0.0.5:80/api/status"
    assert [(r.method, r.url.path) for r in probe_wire] == [("HEAD", "/api/status")]


async def test_the_raw_send_sends_a_body_as_the_body(probe_wire: list[httpx.Request]) -> None:
    result = await _probe('POST /api/power {"on": true}')
    assert result["success"] is True
    assert result["sent"] == 'POST http://10.0.0.5:80/api/power {"on": true}'
    (request,) = probe_wire
    assert (request.method, request.url.path) == ("POST", "/api/power")
    assert json.loads(request.content) == {"on": True}
    assert request.headers["content-type"] == "application/json"


async def test_the_raw_send_reads_a_bare_path_as_a_get(probe_wire: list[httpx.Request]) -> None:
    result = await _probe("status")
    assert result["sent"] == "GET http://10.0.0.5:80/status"
    assert [(r.method, r.url.path) for r in probe_wire] == [("GET", "/status")]
