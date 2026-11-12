"""How a YAML driver writes a device setting: the three write forms.

A setting's ``write:`` block is an OSC message (``address``), an HTTP request
(``path`` / ``method``) or a line of the device's protocol (``send``). These
pin what each form sends, what it returns, and the refusals before anything
goes out, so the write can be restructured without changing any of them.

Invented device ("acme_widget") and synthetic payloads throughout.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import UnknownDeviceSettingError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.http_client import HTTPClientTransport
from openavc.transport.osc import OSCTransport
from openavc.transport.osc_codec import osc_encode_message

BASE: dict[str, Any] = {
    "id": "acme_widget",
    "name": "Acme Widget",
    "manufacturer": "Acme",
    "category": "audio",
    "version": "1.0.0",
    "transport": "tcp",
    "default_config": {"host": "", "port": 23, "zone": "Z1"},
    "state_variables": {
        "mute": {"type": "boolean", "label": "Mute"},
        "gain": {"type": "integer", "label": "Gain", "offset": -18},
        "level": {"type": "integer", "label": "Level"},
    },
    "commands": {},
    "responses": [
        {"match": r"^LEVEL=(\d+)$", "set": {"level": "$1"}},
    ],
}


def _definition(transport: str, settings: dict[str, Any], **extra: Any) -> dict[str, Any]:
    definition = copy.deepcopy(BASE)
    definition["transport"] = transport
    definition["device_settings"] = settings
    definition.update(extra)
    return definition


def _driver(definition: dict[str, Any], transport: Any):
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    driver = cls(
        "dev1", {"host": "127.0.0.1", "port": 23, "zone": "Z1"}, state, events,
    )
    driver.transport = transport
    return driver


class LineTransport:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.sent: list[bytes] = []

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class FakeOsc(OSCTransport):
    def __init__(self, connected: bool = True) -> None:  # no socket
        self._is_connected = connected
        self.sent: list[bytes] = []

    @property
    def connected(self) -> bool:
        return self._is_connected

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class FakeHttp(HTTPClientTransport):
    def __init__(self, text: str = "") -> None:  # no client
        self.requests: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.text = text

    @property
    def connected(self) -> bool:
        return True

    async def request(self, *args: Any, **kwargs: Any) -> Any:
        self.requests.append((args, kwargs))
        return SimpleNamespace(status_code=200, text=self.text)


# ── Refusals before anything is sent ──


async def test_an_undeclared_setting_is_refused() -> None:
    driver = _driver(_definition("tcp", {}), LineTransport())
    with pytest.raises(UnknownDeviceSettingError, match="Unknown device setting: gain"):
        await driver.set_device_setting("gain", 1)


async def test_a_setting_with_no_write_block_is_refused() -> None:
    driver = _driver(_definition("tcp", {"gain": {"type": "integer"}}), LineTransport())
    with pytest.raises(NotImplementedError, match="^Device setting 'gain' has no write definition$"):
        await driver.set_device_setting("gain", 1)


@pytest.mark.parametrize("write", [{"send": ""}, {"label": "nothing to send"}])
async def test_a_write_block_with_nothing_to_send_is_refused(write: dict[str, Any]) -> None:
    transport = LineTransport()
    driver = _driver(_definition("tcp", {"gain": {"write": write}}), transport)
    with pytest.raises(
        NotImplementedError,
        match="^Device setting 'gain' write definition has no address, path or send$",
    ):
        await driver.set_device_setting("gain", 1)
    assert transport.sent == []


# ── The value that goes out ──


async def test_the_state_key_variable_converts_a_value_with_no_map_word() -> None:
    settings = {
        "trim": {"state_key": "gain", "write": {"send": "TRIM {value}\\r"}},
        "gain": {"write": {"send": "GAIN {value}\\r"}},
    }
    transport = LineTransport()
    driver = _driver(_definition("tcp", settings), transport)
    await driver.set_device_setting("trim", 0)
    await driver.set_device_setting("gain", 2)
    assert transport.sent == [b"TRIM 18\r", b"GAIN 20\r"]


async def test_a_map_word_goes_out_in_place_of_the_converted_value() -> None:
    settings = {
        "gain": {"map": {"0": "MIN"}, "write": {"send": "GAIN {value}\\r"}},
    }
    transport = LineTransport()
    driver = _driver(_definition("tcp", settings), transport)
    await driver.set_device_setting("gain", 0)
    assert transport.sent == [b"GAIN MIN\r"]


# ── A line of the device's protocol ──


async def test_a_line_write_is_substituted_framed_and_returns_true() -> None:
    settings = {"gain": {"write": {"send": "{zone} GAIN {value}\\r"}}}
    definition = _definition(
        "tcp", settings,
        send_frame={"type": "length_prefix", "header": "AB", "length_size": 1},
    )
    transport = LineTransport()
    driver = _driver(definition, transport)
    assert await driver.set_device_setting("gain", 2) is True
    assert transport.sent == [b"AB\x0bZ1 GAIN 20\r"]


@pytest.mark.parametrize("transport", [None, LineTransport(connected=False)])
async def test_a_line_write_while_disconnected_is_refused(transport: Any) -> None:
    settings = {"gain": {"write": {"send": "GAIN {value}\\r"}}}
    driver = _driver(_definition("tcp", settings), transport)
    with pytest.raises(ConnectionError, match=r"^\[dev1\] Not connected$"):
        await driver.set_device_setting("gain", 2)


# ── An OSC message ──


async def test_an_osc_write_sends_the_address_and_typed_args() -> None:
    settings = {
        "gain": {"write": {"address": "/{zone}/gain", "args": [{"type": "i", "value": "{value}"}]}},
    }
    transport = FakeOsc()
    driver = _driver(_definition("osc", settings), transport)
    assert await driver.set_device_setting("gain", 2) is True
    assert transport.sent == [osc_encode_message("/Z1/gain", [("i", 20)])]


async def test_an_osc_write_with_no_args_sends_the_bare_address() -> None:
    settings = {"mute": {"write": {"address": "/mute/{value}"}}}
    transport = FakeOsc()
    driver = _driver(_definition("osc", settings), transport)
    await driver.set_device_setting("mute", True)
    assert transport.sent == [osc_encode_message("/mute/True", [])]


@pytest.mark.parametrize("transport", [None, FakeOsc(connected=False)])
async def test_an_osc_write_while_disconnected_is_refused(transport: Any) -> None:
    settings = {"gain": {"write": {"address": "/gain"}}}
    driver = _driver(_definition("osc", settings), transport)
    with pytest.raises(ConnectionError, match=r"^\[dev1\] Not connected$"):
        await driver.set_device_setting("gain", 2)


async def test_an_osc_write_on_another_transport_is_refused() -> None:
    settings = {"gain": {"write": {"address": "/gain"}}}
    transport = LineTransport()
    driver = _driver(_definition("tcp", settings), transport)
    with pytest.raises(
        ConnectionError,
        match=r"^\[dev1\] Setting 'gain' uses OSC write but transport is not OSC$",
    ):
        await driver.set_device_setting("gain", 2)
    assert transport.sent == []


async def test_an_address_write_is_osc_even_when_it_also_names_a_path() -> None:
    settings = {"gain": {"write": {"address": "/gain", "path": "/api/gain"}}}
    transport = FakeHttp()
    driver = _driver(_definition("http", settings), transport)
    with pytest.raises(ConnectionError, match="uses OSC write"):
        await driver.set_device_setting("gain", 2)
    assert transport.requests == []


# ── An HTTP request ──


async def test_an_http_json_write_posts_the_body_and_reads_the_answer() -> None:
    settings = {
        "gain": {"write": {
            "path": "/api/{zone}/gain",
            "body": '{"gain": {value}}',
            "headers": {"X-Zone": "{zone}", "X-Retry": 2},
        }},
    }
    transport = FakeHttp(text="LEVEL=7")
    driver = _driver(_definition("http", settings), transport)
    response = await driver.set_device_setting("gain", 2)
    assert transport.requests == [(
        ("POST", "/api/Z1/gain"),
        {
            "params": None, "json_body": {"gain": 20}, "content": None,
            "headers": {"X-Zone": "Z1", "X-Retry": "2"},
        },
    )]
    assert response.status_code == 200
    assert driver.get_state("level") == 7


async def test_an_http_write_with_no_body_sends_none_and_its_own_method() -> None:
    settings = {"mute": {"write": {"method": "put", "path": "/mute/{value}"}}}
    transport = FakeHttp()
    driver = _driver(_definition("http", settings), transport)
    await driver.set_device_setting("mute", False)
    assert transport.requests == [
        (("PUT", "/mute/False"), {"params": None, "json_body": None, "content": None, "headers": None}),
    ]


async def test_an_http_method_alone_makes_an_http_write_to_the_root() -> None:
    settings = {"mute": {"write": {"method": "DELETE"}}}
    transport = FakeHttp()
    driver = _driver(_definition("http", settings), transport)
    await driver.set_device_setting("mute", True)
    assert transport.requests == [
        (("DELETE", "/"), {"params": None, "json_body": None, "content": None, "headers": None}),
    ]


async def test_an_http_body_that_is_not_json_goes_as_content_and_its_answer_is_read(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A body that is not JSON (XML, form text) goes as raw content, and the
    device's answer reaches the reply rules as a JSON write's does: a device
    that answers with the new value shows it at once, not at the next poll."""
    settings = {"gain": {"write": {"path": "/gain", "body": "<gain>{value}</gain>"}}}
    transport = FakeHttp(text="LEVEL=7")
    driver = _driver(_definition("http", settings), transport)
    with caplog.at_level("DEBUG", logger="openavc.drivers.configurable"):
        response = await driver.set_device_setting("gain", 2)
    assert transport.requests == [
        (("POST", "/gain"), {
            "params": None, "json_body": None, "content": b"<gain>20</gain>", "headers": None,
        }),
    ]
    assert response.text == "LEVEL=7"
    assert driver.get_state("level") == 7
    assert "[dev1] Set device setting 'gain' = 2" in caplog.messages


async def test_an_http_write_on_another_transport_is_refused() -> None:
    settings = {"gain": {"write": {"path": "/gain"}}}
    transport = LineTransport()
    driver = _driver(_definition("tcp", settings), transport)
    with pytest.raises(
        ConnectionError,
        match=r"^\[dev1\] Setting 'gain' uses HTTP write but transport is not HTTP$",
    ):
        await driver.set_device_setting("gain", 2)
    assert transport.sent == []


async def test_an_http_answer_with_no_text_is_returned_unread() -> None:
    settings = {"gain": {"write": {"path": "/gain"}}}

    class Silent(FakeHttp):
        async def request(self, *args: Any, **kwargs: Any) -> Any:
            self.requests.append((args, kwargs))
            return SimpleNamespace(status_code=204)

    transport = Silent()
    driver = _driver(_definition("http", settings), transport)
    response = await driver.set_device_setting("gain", 2)
    assert response.status_code == 204
    assert driver.get_state("level") is None


# ── Filled as a command's send is filled ──


async def test_an_http_write_takes_push_values_as_a_command_does() -> None:
    settings = {"gain": {"write": {
        "path": "/api/{push_session}/gain",
        "body": '{"gain": {value}, "cb": "{push_callback_url}"}',
    }}}
    transport = FakeHttp()
    driver = _driver(_definition("http", settings), transport)
    driver._push_session = "S9"
    driver._push_callback_url = "http://192.0.2.1/cb"
    await driver.set_device_setting("gain", 2)
    assert transport.requests == [(
        ("POST", "/api/S9/gain"),
        {
            "params": None, "json_body": {"gain": 20, "cb": "http://192.0.2.1/cb"},
            "content": None, "headers": None,
        },
    )]


async def test_a_line_write_takes_push_values_as_a_command_does() -> None:
    settings = {"gain": {"write": {"send": "GAIN {value} {push_callback_url}\r"}}}
    transport = LineTransport()
    driver = _driver(_definition("tcp", settings), transport)
    driver._push_callback_url = "http://192.0.2.1/cb"
    await driver.set_device_setting("gain", 2)
    assert transport.sent == [b"GAIN 20 http://192.0.2.1/cb\r"]


@pytest.mark.parametrize("transport_type, write, where", [
    ("http", {"path": "/gain/{zonee}"}, "path"),
    ("http", {"path": "/gain", "body": '{"z": "{zonee}"}'}, "body"),
    ("tcp", {"send": "GAIN {zonee} {value}\r"}, "send"),
    ("osc", {"address": "/gain/{zonee}"}, "address"),
])
async def test_a_placeholder_nothing_fills_is_warned_about_as_a_commands_is(
    transport_type: str, write: dict[str, Any], where: str, caplog: pytest.LogCaptureFixture,
) -> None:
    transport = {"http": FakeHttp, "tcp": LineTransport, "osc": FakeOsc}[transport_type]()
    driver = _driver(_definition(transport_type, {"gain": {"write": write}}), transport)
    with caplog.at_level("WARNING", logger="openavc.drivers.configurable"):
        await driver.set_device_setting("gain", 2)
    assert (
        f"[dev1] device_settings.gain.write.{where}: '{{zonee}}' was not substituted"
    ) in caplog.text
