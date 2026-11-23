"""An HTTP command or setting write the device answers with an error status
did not finish.

A status of 400 and up is the device saying no: a 4xx is a request it could
not accept (a wrong path, a refused login, a bad value, busy), a 5xx one it
took and could not finish. Either raises the command's outcome
(``command_outcome``), after the answer has been read and the command's
read-back scheduled, so the state shows what the device holds. A poll line is
not a command a person sent: its answer still goes to the credential check,
whatever its status.

The generated simulator answers a command it knows with nothing to say as an
empty 200, and keeps 404 for a line nothing matches.

Invented device ("acme_widget") and synthetic payloads throughout.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.command_outcome import CommandOutcomeError, answered_outcome
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.simulator.yaml_auto import YAMLAutoSimulator
from openavc.transport.http_client import HTTPClientTransport, HTTPResponse

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "http",
    "default_config": {"host": "", "port": 80},
    "state_variables": {
        "level": {"type": "integer", "label": "Level"},
        "gain": {"type": "integer", "label": "Gain"},
    },
    "commands": {
        "power_on": {"label": "Power On", "method": "POST", "path": "/api/power"},
        "query_level": {"label": "Query Level", "method": "GET", "path": "/api/level"},
    },
    "device_settings": {
        "gain": {
            "type": "integer", "label": "Gain", "state_key": "gain",
            "write": {"method": "PUT", "path": "/api/gain", "body": '{"gain": {value}}'},
        },
    },
    "responses": [
        {"match": r"LEVEL=(\d+)", "set": {"level": "$1"}},
    ],
}


class FakeHttp(HTTPClientTransport):
    """Answers every request with the status and text set on it."""

    def __init__(self, status: int = 200, text: str = "") -> None:  # no client
        self.status = status
        self.text = text
        self.requests: list[tuple[str, str]] = []

    @property
    def connected(self) -> bool:
        return True

    async def request(self, method: str, path: str, **_: Any) -> HTTPResponse:
        self.requests.append((method, path))
        return HTTPResponse(
            status_code=self.status, headers={}, text=self.text,
            ok=200 <= self.status < 300,
        )

    async def get(self, path: str, **kwargs: Any) -> HTTPResponse:
        return await self.request("GET", path, **kwargs)


def _driver(transport: FakeHttp, **extra: Any):
    definition = copy.deepcopy(BASE)
    definition.update(extra)
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    driver = cls("dev1", {"host": "127.0.0.1", "port": 80}, state, events)
    driver.transport = transport
    return driver


# ── The rule ──


@pytest.mark.parametrize(
    "status,outcome",
    [
        (200, None), (204, None), (302, None), (399, None),
        (400, "rejected"), (401, "rejected"), (404, "rejected"), (409, "rejected"),
        (499, "rejected"), (500, "failed"), (503, "failed"), (599, "failed"),
        (None, None), ("500", None), (True, None),
    ],
)
def test_the_status_decides_the_outcome(status: Any, outcome: str | None) -> None:
    assert answered_outcome(status) == outcome


# ── A command ──


@pytest.mark.parametrize(
    "status,outcome", [(400, "rejected"), (401, "rejected"), (404, "rejected"), (500, "failed")],
)
async def test_a_command_answered_with_an_error_status_did_not_finish(
    status: int, outcome: str,
) -> None:
    driver = _driver(FakeHttp(status))
    with pytest.raises(CommandOutcomeError) as caught:
        await driver.send_command("power_on")
    assert caught.value.outcome == outcome


@pytest.mark.parametrize("status", [200, 204, 302])
async def test_a_command_answered_below_400_is_done(status: int) -> None:
    driver = _driver(FakeHttp(status))
    response = await driver.send_command("power_on")
    assert response.status_code == status


async def test_a_refused_commands_answer_is_still_read_and_read_back() -> None:
    transport = FakeHttp(409, text="LEVEL=7")
    driver = _driver(transport)
    driver._definition["commands"]["power_on"]["read_back"] = ["query_level"]
    read_backs: list[str] = []
    driver._read_back = lambda command, *_: read_backs.append(command)
    with pytest.raises(CommandOutcomeError):
        await driver.send_command("power_on")
    assert driver.get_state("level") == 7
    assert read_backs == ["power_on"]


# ── A device setting ──


async def test_a_setting_write_answered_with_an_error_status_did_not_finish() -> None:
    transport = FakeHttp(500, text="LEVEL=3")
    driver = _driver(transport)
    with pytest.raises(CommandOutcomeError) as caught:
        await driver.set_device_setting("gain", 5)
    assert caught.value.outcome == "failed"
    assert transport.requests == [("PUT", "/api/gain")]
    assert driver.get_state("level") == 3


async def test_a_setting_write_answered_below_400_is_done() -> None:
    driver = _driver(FakeHttp(204))
    response = await driver.set_device_setting("gain", 5)
    assert response.status_code == 204


# ── A poll line is not a command a person sent ──


@pytest.mark.parametrize("line", ["query_level", "/api/level"])
async def test_a_poll_lines_error_answer_goes_to_the_credential_check(line: str) -> None:
    driver = _driver(FakeHttp(404))
    await driver._dispatch_query(line)
    assert line in driver._http_answered


async def test_a_poll_line_naming_a_command_still_tallies_a_refused_login() -> None:
    driver = _driver(FakeHttp(401))
    driver._open_http_cycle(True)
    await driver._dispatch_query("query_level")
    assert driver._http_cycle == [1, 1]


# ── The generated simulator ──


def _simulator(**extra: Any) -> YAMLAutoSimulator:
    definition = copy.deepcopy(BASE)
    definition.update(extra)
    return YAMLAutoSimulator(device_id="sim1", config={}, driver_def=definition)


async def test_the_simulator_answers_a_write_it_carried_out_with_an_empty_200() -> None:
    sim = _simulator()
    response = await sim.respond_http(None, "PUT", "/api/gain", {}, '{"gain": 5}')
    assert (response.status, response.text) == (200, "")
    assert sim.get_state("gain") == 5


async def test_the_simulator_answers_a_template_handler_with_no_reply_with_an_empty_200() -> None:
    sim = _simulator(simulator={"command_handlers": [
        {"receive": r"POST /api/power", "set_state": {"level": "1"}},
    ]})
    response = await sim.respond_http(None, "POST", "/api/power", {}, "")
    assert (response.status, response.text) == (200, "")


async def test_the_simulator_still_answers_404_for_a_path_nothing_matches() -> None:
    sim = _simulator()
    response = await sim.respond_http(None, "POST", "/api/nowhere", {}, "")
    assert response.status == 404


async def test_a_script_handler_that_answers_nothing_still_plays_a_refusal() -> None:
    sim = _simulator(simulator={"command_handlers": [
        {"match": r"POST /api/power", "handler": "state['level'] = 2\n"},
    ]})
    response = await sim.respond_http(None, "POST", "/api/power", {}, "")
    assert response.status == 404


def test_a_byte_link_command_with_nothing_to_say_stays_silent() -> None:
    definition = copy.deepcopy(BASE)
    definition.update(
        transport="tcp", default_config={"host": "", "port": 23},
        commands={"power_on": {"label": "Power On", "send": "PWR 1\r", "sets": {"gain": 1}}},
        device_settings={},
    )
    sim = YAMLAutoSimulator(device_id="sim1", config={}, driver_def=definition)
    assert sim.handle_command(b"PWR 1") is None
    assert sim.get_state("gain") == 1
