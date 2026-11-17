"""A connection test opens a serial port with the line settings a connect opens it with.

Two doors open a serial port without running a driver: the Driver Builder's
raw Send (``POST /api/driver-definitions/{id}/test-command`` with a
``command_string``) and the device page's Test Connection
(``core/device_reachability``). Each reads the baud rate, data bits, parity,
stop bits and flow control the way the connect reads them
(``drivers/transport_ladder.serial_line_settings``): the raw Send from the
draft's ``default_config`` and the Live Test panel's overrides, Test Connection
from the device's resolved config. A setting the connect refuses is refused by
both, in the connect's words, before any port is opened.

Synthetic only: an invented ``/dev/ttyACME0`` and the transports replaced.
"""

from __future__ import annotations

from typing import Any

import pytest
import serial

from openavc.api.models import TestCommandRequest
from openavc.api.routes import driver_test
from openavc.core.connection_fault import ConnectionFaultError
from openavc.core.device_reachability import check_reachability
from openavc.drivers.base import BaseDriver
from openavc.drivers.transport_ladder import serial_line_settings

PORT = "/dev/ttyACME0"

ACME_LINE = {
    "baudrate": 19200,
    "bytesize": 7,
    "parity": "Even",
    "stopbits": 2,
    "flow_control": "hardware",
}

ACME_OPENED = {
    "baudrate": 19200,
    "bytesize": 7,
    "parity": "E",
    "stopbits": 2,
    "rtscts": True,
    "xonxoff": False,
}

DEFAULT_OPENED = {
    "baudrate": 9600,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "rtscts": False,
    "xonxoff": False,
}


def _line(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: kwargs.get(key) for key in DEFAULT_OPENED}


# --- The one reading ----------------------------------------------------------


def test_the_reading_is_the_connects():
    assert serial_line_settings(ACME_LINE) == ACME_OPENED
    assert serial_line_settings({}) == DEFAULT_OPENED
    assert BaseDriver._coerce_serial_params(ACME_LINE) == tuple(ACME_OPENED.values())


def test_the_reading_refuses_in_the_connects_words():
    with pytest.raises(ConnectionFaultError, match=r"^This device's Parity setting is 'Q'") as caught:
        serial_line_settings({"parity": "Q"})
    assert caught.value.fault_code == "invalid_config"


# --- The Driver Builder's raw Send ---------------------------------------------


class _FakeSerial:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_and_wait(self, data: bytes, timeout: float = 5.0) -> bytes:
        self.sent.append(data)
        return b"OK\r"

    async def close(self) -> None:
        return None


@pytest.fixture
def opened(monkeypatch):
    """The keyword arguments each SerialTransport.create was called with."""
    calls: list[dict[str, Any]] = []

    async def create(**kwargs: Any) -> _FakeSerial:
        calls.append(kwargs)
        return _FakeSerial()

    from openavc.transport import serial_transport

    monkeypatch.setattr(serial_transport.SerialTransport, "create", staticmethod(create))
    return calls


def _raw(**fields: Any) -> TestCommandRequest:
    return TestCommandRequest(
        transport="serial", port=PORT, command_string="PWR?", **fields,
    )


def _draft(default_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "serial",
        "default_config": {"port": "", **default_config},
        "commands": {},
    }


async def test_raw_send_opens_with_the_drafts_line_settings(opened):
    result = await driver_test._test_raw(_raw(definition=_draft(ACME_LINE)))

    assert result["success"] is True, result
    assert len(opened) == 1
    assert opened[0]["port"] == PORT
    assert _line(opened[0]) == ACME_OPENED


async def test_raw_send_takes_the_panels_overrides_over_the_draft(opened):
    body = _raw(
        definition=_draft(ACME_LINE),
        config_overrides={"baudrate": "38400", "parity": "N"},
    )

    await driver_test._test_raw(body)

    assert _line(opened[0]) == {**ACME_OPENED, "baudrate": 38400, "parity": "N"}


async def test_raw_send_without_a_draft_opens_at_the_defaults(opened):
    await driver_test._test_raw(_raw())

    assert _line(opened[0]) == DEFAULT_OPENED


async def test_raw_send_refuses_a_setting_the_connect_refuses(opened):
    result = await driver_test._test_raw(_raw(definition=_draft({"parity": "Q"})))

    assert result["success"] is False
    assert result["error"].startswith("This device's Parity setting is 'Q'")
    assert opened == []


# --- Test Connection ----------------------------------------------------------


@pytest.fixture
def probed(monkeypatch):
    """The keyword arguments each serial.Serial was opened with."""
    calls: list[dict[str, Any]] = []

    class _Port:
        def __init__(self, port: str, *args: Any, **kwargs: Any) -> None:
            calls.append({"port": port, "args": args, **kwargs})

        def close(self) -> None:
            return None

    monkeypatch.setattr(serial, "Serial", _Port)
    return calls


def _device(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "widget_a",
        "driver": "acme_widget",
        "name": "Widget A",
        "config": {"transport": "serial", "port": PORT, **config},
    }


async def test_test_connection_opens_with_the_devices_line_settings(probed):
    result = await check_reachability(_device(ACME_LINE))

    assert result["success"] is True, result
    assert len(probed) == 1
    assert probed[0]["port"] == PORT
    assert _line(probed[0]) == ACME_OPENED


async def test_test_connection_refuses_a_setting_the_connect_refuses(probed):
    result = await check_reachability(_device({"bytesize": 9}))

    assert result["success"] is False
    assert result["error"].startswith("This device's Data bits setting is 9")
    assert probed == []
