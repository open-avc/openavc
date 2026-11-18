"""The Driver Builder's raw probe reads its text before it opens anything.

The command and the reply delimiter are written with backslash escapes
(``\\r``, ``\\x03``). One the probe cannot read (a truncated ``\\x``) is
refused with a 422 naming it, before a socket or a serial port is
opened: a device that serves one connection at a time is never taken from
its driver for a probe that could not have been sent. An invented device
throughout; nothing is dialled.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from openavc.api import models
from openavc.api.routes import driver_test

# What follows the text in each refusal, as a person reads it.
REMEDY = (
    "an incomplete backslash escape. Write a byte as \\x and two hex digits, "
    "such as \\x0D, and a backslash itself as \\\\."
)


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    """Every transport the probe opens, by kind."""
    from openavc.transport import serial_transport, tcp, udp

    seen: list[str] = []

    async def create_tcp(**kwargs: Any):
        seen.append("tcp")
        raise ConnectionError("not dialled in this test")

    async def create_serial(**kwargs: Any):
        seen.append("serial")
        raise OSError("not opened in this test")

    async def open_udp(self) -> None:
        seen.append("udp")
        raise OSError("not opened in this test")

    monkeypatch.setattr(tcp.TCPTransport, "create", staticmethod(create_tcp))
    monkeypatch.setattr(serial_transport.SerialTransport, "create", staticmethod(create_serial))
    monkeypatch.setattr(udp.UDPTransport, "open", open_udp)
    return seen


def _probe(transport: str, **fields: Any) -> models.TestCommandRequest:
    port = "/dev/ttyACME0" if transport == "serial" else 4000
    return models.TestCommandRequest(**{
        "host": "192.0.2.50", "port": port, "transport": transport, "command_string": "PWR?",
        **fields,
    })


@pytest.mark.parametrize("transport", ["tcp", "serial", "udp"])
async def test_a_command_it_cannot_read_is_refused_before_anything_opens(opened, transport):
    with pytest.raises(HTTPException) as refused:
        await driver_test._test_raw(_probe(transport, command_string="PWR\\x"))

    assert refused.value.status_code == 422
    assert refused.value.detail == "The command 'PWR\\x' has " + REMEDY
    assert opened == []


@pytest.mark.parametrize("transport", ["tcp", "serial"])
async def test_a_delimiter_it_cannot_read_is_refused_before_anything_opens(opened, transport):
    with pytest.raises(HTTPException) as refused:
        await driver_test._test_raw(_probe(transport, delimiter="\\x"))

    assert refused.value.status_code == 422
    assert refused.value.detail == "The message delimiter '\\x' has " + REMEDY
    assert opened == []


@pytest.mark.parametrize("transport", ["tcp", "serial", "udp"])
async def test_text_it_can_read_goes_on_to_open(opened, transport):
    answer = await driver_test._test_raw(_probe(transport, command_string="PWR?\\r"))

    assert opened == [transport]
    assert answer["success"] is False
