"""Which form a command, or a device setting's write, is sent in: one shape
table (``tests/fixtures/command_form_cases.json``) run through every reader
that decides it.

The owner is ``drivers/command_form.py`` over the form table in ``spec.py``.
Each reader below asks it, and each is run here on every case in the reader's
own words: the checker's verdict, the sender the runtime calls, the route
the Builder's dry run captures, the Live Test's summary, the simulator's
handlers and probes. The Driver Builder's ``commandForm``
(``validateDriver.ts``) runs the same file in ``commandForm.parity.test.ts``.

Invented devices ("acme_*") throughout; nothing here opens a socket.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from openavc.api.routes.driver_test import _describe_outgoing
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers import command_form as cf
from openavc.drivers.avcdriver_semantic import check_driver_definition, validate_driver_definition
from openavc.drivers.driver_loader import read_driver_file
from openavc.drivers.command_outcome import CommandOutcomeError, outcome_of
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.dry_run import capture_command_transport
from openavc.simulator import driver_sends, inline_device, push_channels, send_coverage
from openavc.simulator import osc_coverage, yaml_auto
from openavc.simulator.validation_result import ValidationResult
from openavc.simulator.setting_handlers import write_template
from openavc.simulator.side_receiver import side_send_blocks

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "command_form_cases.json").read_text(encoding="utf-8")
)["cases"]
COMMANDS = [c for c in _CASES if c["origin"] == "command"]
WRITES = [c for c in _CASES if c["origin"] == "setting_write"]


def _ids(cases: list[dict]) -> list[str]:
    return [c["name"] for c in cases]


UNIT: dict[str, Any] = {
    "id": "acme_form_unit",
    "name": "Acme Form Unit",
    "manufacturer": "Acme",
    "category": "video",
    "transport": "tcp",
    "delimiter": "\r",
    "default_config": {"host": "", "port": 23},
    "state_variables": {"level": {"type": "integer", "label": "Level"}},
    "commands": {"ping": {"label": "Ping", "send": "PING\r"}},
    "responses": [],
}


def _driver(**sections: Any):
    definition = copy.deepcopy(UNIT)
    definition.update(sections)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls("dev1", {"host": "10.0.0.5", "port": 23}, state, events)
    driver._pace_lines = False
    return driver


class Recorder:
    """One async sender, replaced: records that it was called."""

    def __init__(self, name: str, log: list[str]) -> None:
        self.name = name
        self.log = log

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.log.append(self.name)
        return True


class _Wire:
    """The device's transport: records the bytes of a line."""

    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def send(self, data: bytes) -> None:
        self.log.append("raw")


# An OSC simulator that answers no address.
_NO_ADDRESSES = osc_coverage.OscAddresses([], [], UNIT)


# ── the owner ──


@pytest.mark.parametrize("case", _CASES, ids=_ids(_CASES))
def test_the_table_names_what_each_block_declares_and_sends(case: dict) -> None:
    assert cf.declared_forms(case["block"], case["origin"]) == case["declared"]
    assert cf.command_form(case["block"], case["origin"]) == case["form"]


def test_the_table_covers_every_form_and_origin() -> None:
    forms = {name for name, _fields in cf.SEND_FORMS}
    assert {c["form"] for c in COMMANDS} - {None} == forms
    assert {c["form"] for c in WRITES} - {None} == {"osc", "http", "raw"}
    assert any(len(c["declared"]) > 1 for c in _CASES)
    assert any(not c["declared"] for c in _CASES)


def test_a_block_that_is_not_a_mapping_declares_nothing() -> None:
    assert cf.declared_forms(None) == []
    assert cf.declared_forms("PWR") == []
    assert cf.command_form(["send"]) is None


# ── the checker ──


def _form_verdict(errors: list[str]) -> str:
    if any("must have 'send'" in e for e in errors):
        return "none"
    if any("but a command sends one way" in e for e in errors):
        return "several"
    return "one"


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_the_checker_refuses_a_command_that_declares_no_form_or_several(case: dict) -> None:
    definition = copy.deepcopy(UNIT)
    definition["commands"] = {"go": case["block"]}
    errors = validate_driver_definition(definition, strict=True)
    declared = case["declared"]
    expected = "none" if not declared else ("one" if len(declared) == 1 else "several")
    if expected == "none" and cf.left_empty(case["block"]):
        expected = "left empty"
        assert any(": is empty, so the command sends nothing" in e for e in errors), errors
    assert _form_verdict(errors) == ("one" if expected == "left empty" else expected), errors


@pytest.mark.parametrize("block", [
    {"address": ""}, {"send": "", "address": ""}, {"address": []}, {"ir": ""}, {"ir": False},
])
def test_an_address_or_ir_code_left_empty_is_refused_only_at_the_authors_doors(
    block: dict, tmp_path: Path,
) -> None:
    # An empty address or IR code was a form until a null or empty field
    # chose none, so a driver saved with one (the Builder's Address box
    # cleared) passed every door: an installed copy keeps loading, named.
    definition = copy.deepcopy(UNIT)
    definition["commands"] = {"go": block}
    field = "ir" if "ir" in block else "address"
    no_form = f"commands.go.{field}: is empty, so the command sends nothing"
    assert any(e.startswith(no_form) for e in validate_driver_definition(definition, strict=True))
    verdict = check_driver_definition(definition)
    assert not any("must have 'send'" in e for e in verdict.errors), verdict.errors
    assert any(e.startswith(no_form) for e in verdict.notices), verdict.notices

    path = tmp_path / "acme_form_unit.avcdriver"
    path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    read = read_driver_file(path)
    assert read.definition is not None, read.reasons
    assert any(no_form in n for n in read.notices), read.notices


@pytest.mark.parametrize("block", [{}, {"send": ""}, {"address": None}, {"udp": {}}])
def test_a_command_with_no_form_written_is_refused_at_every_door(block: dict) -> None:
    definition = copy.deepcopy(UNIT)
    definition["commands"] = {"go": block}
    assert check_driver_definition(definition).errors


def test_the_checker_still_names_a_udp_block_that_is_not_a_mapping() -> None:
    definition = copy.deepcopy(UNIT)
    definition["commands"] = {"go": {"udp": "x"}}
    errors = validate_driver_definition(definition)
    assert any("'udp' must be a mapping" in e for e in errors), errors


def test_the_checker_reads_an_osc_writes_args_only_on_an_osc_write() -> None:
    def errors(write: dict) -> list[str]:
        definition = copy.deepcopy(UNIT)
        definition["device_settings"] = {"level": {"type": "integer", "state_key": "level",
                                                   "write": write}}
        return [e for e in validate_driver_definition(definition) if "args" in e]

    bad = [{"type": "zz", "value": 1}]
    assert errors({"address": "/lvl", "args": bad})
    assert not errors({"address": "", "send": "LVL {value}", "args": bad})


# ── the runtime ──


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_the_runtime_sends_each_command_in_its_form(case: dict) -> None:
    drv = _driver(commands={"go": case["block"]})
    sent: list[str] = []
    for name in ("ir", "udp", "osc", "http"):
        attr = {"ir": "_send_ir_command", "udp": "_send_udp_side",
                "osc": "_send_osc_command", "http": "_send_http_command"}[name]
        setattr(drv, attr, Recorder(name, sent))
    drv.transport = _Wire(sent)
    drv._link_open = lambda: True
    if case["form"] is None:
        with pytest.raises(CommandOutcomeError) as caught:
            asyncio.run(drv._send_command("go"))
        assert outcome_of(caught.value) == "not_sent"
        assert sent == []
    else:
        asyncio.run(drv._send_command("go"))
        assert sent == [case["form"]]


@pytest.mark.parametrize("block", [{"udp": "x"}, {"ir": "x"}, {"send": "PWR1\r", "udp": ["x"]}])
def test_a_side_send_block_that_is_not_a_mapping_is_not_sent(block: dict) -> None:
    drv = _driver(commands={"go": block})
    with pytest.raises(CommandOutcomeError) as caught:
        asyncio.run(drv._send_command("go"))
    assert outcome_of(caught.value) == "not_sent"


def test_an_empty_path_beside_a_line_sends_the_line_on_a_tcp_device() -> None:
    drv = _driver(commands={"go": {"send": "PWR1\r", "path": ""}})
    wire: list[bytes] = []

    class _Tcp:
        async def send(self, data: bytes) -> None:
            wire.append(data)

    drv.transport = _Tcp()
    drv._link_open = lambda: True
    assert asyncio.run(drv._send_command("go")) is True
    assert wire == [b"PWR1\r"]


@pytest.mark.parametrize("case", WRITES, ids=_ids(WRITES))
def test_the_runtime_writes_each_setting_in_its_form(case: dict) -> None:
    drv = _driver()
    writer = drv._setting_writer(case["block"])
    taken = {
        None: None,
        "_write_setting_osc": "osc",
        "_write_setting_http": "http",
        "_write_setting_line": "raw",
    }[getattr(writer, "__name__", None)]
    assert taken == case["form"]


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_read_back_true_reads_an_osc_or_http_command_itself(case: dict) -> None:
    drv = _driver()
    if case["form"] == "http" and not isinstance(case["block"].get("path") or "/", str):
        pytest.skip("a path that is not text is the shape walk's to name")
    kinds = [query[0] for _key, query in drv._read_back_itself("go", case["block"], {})]
    assert kinds == ([case["form"]] if case["form"] in ("osc", "http") else [])


# ── the Builder's dry run and the Live Test ──


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_the_dry_run_captures_the_route_the_runtime_takes(case: dict) -> None:
    drv = _driver(commands={"go": case["block"]})

    async def capture() -> str:
        captured = await capture_command_transport(drv, case["block"])
        client = getattr(captured.transport, "_client", None)
        if client is not None:
            await client.aclose()
        return captured.route

    expected = case["form"] if case["form"] in ("osc", "http", "udp") else "raw"
    assert asyncio.run(capture()) == expected


def test_the_dry_run_of_a_udp_block_beside_a_path_opens_no_real_socket() -> None:
    block = {"path": "/x", "udp": {"port": 9, "payload": "HELLO"}}
    drv = _driver(commands={"go": block})
    opened: list[Any] = []

    async def real_open(local_addr: str | None = None) -> Any:
        opened.append(local_addr)
        raise AssertionError("the dry run opened a real UDP socket")

    drv._open_udp = real_open

    async def capture() -> Any:
        captured = await capture_command_transport(drv, block)
        await drv._send_udp_side("go", block, {})
        return captured

    captured = asyncio.run(capture())
    assert captured.route == "udp"
    assert opened == []
    assert captured.udp is not None and captured.udp.sent


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_the_live_tests_summary_is_written_in_the_commands_form(case: dict) -> None:
    block = case["block"]
    summary = _describe_outgoing(block, {}, {})
    if case["form"] == "osc":
        assert summary.startswith("OSC ")
    elif case["form"] == "http":
        assert summary.split(" ")[0] in ("GET", "POST")
    elif case["form"] == "raw":
        assert summary == block["send"]
    else:
        assert summary == ""


def test_the_live_tests_summary_of_a_line_is_the_line_on_any_transport() -> None:
    assert _describe_outgoing({"send": "/status"}, {"transport": "http"}, {}) == "/status"


def test_the_live_tests_summary_reads_a_null_method_or_path_as_absent() -> None:
    assert _describe_outgoing({"path": "/x", "method": None}, {}, {}) == "GET /x"
    assert _describe_outgoing({"path": "", "method": "post"}, {}, {}) == "POST /"


# ── the simulator ──


@pytest.mark.parametrize("case", WRITES, ids=_ids(WRITES))
def test_the_simulator_answers_a_setting_write_in_its_form(case: dict) -> None:
    line = write_template(case["block"])
    form = case["form"]
    if form == "http":
        assert line is not None and line.split(" ")[0] in ("POST", "PUT", "PATCH", "GET")
    elif form == "raw":
        assert line == case["block"]["send"]
    else:
        assert line is None


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_the_simulator_reads_each_command_in_its_form(case: dict) -> None:
    block = case["block"]
    form = case["form"]
    udp = block.get("udp")
    payload = udp.get("payload") if form == "udp" and isinstance(udp, dict) else None
    sent = block.get("send") if form == "raw" else None

    # The simulator's handlers are built from a line or a datagram's payload;
    # the validator samples lines only.
    assert (yaml_auto._framed_send(block, "") or None) == (sent or payload)
    assert (send_coverage._framed_send(block, "") or None) == (sent or None)

    probes = driver_sends._command_probes({"go": block}, UNIT)
    assert bool(probes) == (form == "raw" and isinstance(sent, str) and bool(sent))

    blocks = side_send_blocks({"commands": {"go": block}})
    assert ("go" in blocks) == (form == "udp" and isinstance(udp, dict))

    assert inline_device._is_http_poll({**block, "poll": True}) == (form == "http")

    unanswered = ValidationResult("acme_form_unit.avcdriver", "acme_form_unit", "yaml")
    if form != "osc" or isinstance(block.get("address"), str):
        osc_coverage.check_osc_command_coverage(unanswered, {"go": block}, _NO_ADDRESSES, UNIT)
        assert bool(unanswered.issues) == (form == "osc")

    matcher = push_channels._registration_matcher({"go": block}, "go")
    if form == "http":
        assert matcher is not None
        assert matcher.match(f"{(block.get('method') or 'GET').upper()} {block.get('path') or '/'}")
    elif form == "raw":
        assert (matcher is not None) == bool(sent)
    else:
        assert matcher is None


@pytest.mark.parametrize("case", COMMANDS, ids=_ids(COMMANDS))
def test_a_devices_own_polled_command_is_polled_in_its_form(case: dict) -> None:
    from openavc.drivers.inline_merge import _inline_poll_queries

    block = {**case["block"], "poll": True}
    line = block.get("send") if case["form"] == "raw" else None
    assert _inline_poll_queries({"go": block}) == [line if isinstance(line, str) else "go"]


# ── an HTTP request's own fields ──


class _Http:
    """An HTTP connection: records each request's method and path."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    async def request(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.requests.append((method, path))
        return None


@pytest.mark.parametrize("req, sent", [
    ({"path": "/x", "method": None}, ("GET", "/x")),
    ({"path": "/x", "method": ""}, ("GET", "/x")),
    ({"path": "", "method": "post"}, ("POST", "/")),
    ({"path": None, "method": "put"}, ("PUT", "/")),
])
def test_a_null_or_empty_method_or_path_is_the_requests_default(req: dict, sent: tuple) -> None:
    drv = _driver()
    drv.transport = _Http()
    asyncio.run(drv._http_request("go", "commands.go", req, {}))
    assert drv.transport.requests == [sent]


@pytest.mark.parametrize("method", [None, ""])
def test_a_settings_http_write_posts_when_its_method_is_null_or_empty(method: Any) -> None:
    from openavc.transport.http_client import HTTPClientTransport

    drv = _driver(device_settings={"level": {"type": "integer", "state_key": "level",
                                             "write": {"path": "/v", "method": method}}})
    http = _Http()
    drv.transport = HTTPClientTransport.__new__(HTTPClientTransport)
    drv.transport.request = http.request
    drv._setting_written = lambda key, value: None
    asyncio.run(drv._write_setting_http("level", 5, {"path": "/v", "method": method}, {}))
    assert http.requests == [("POST", "/v")]
