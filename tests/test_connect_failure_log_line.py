"""A failed connect's log line says why, even when the exception does not.

The device manager logged "Failed to connect '<id>': <exception>", and an
exception with no text of its own (a timeout; a product with OpenAVC built in
that did not answer) left the line ending in a colon, in the device page's
Device Log and the server log, while the card on the same page gave the
reason. The line now falls back to the card's sentence. An invented device.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.drivers.registry import _DRIVER_REGISTRY


class _AcmeSilent(BaseDriver):
    DRIVER_INFO: dict[str, Any] = {
        "id": "acme_silent_connect",
        "name": "Acme Silent",
        "transport": "tcp",
        "default_config": {"port": 5500},
        "state_variables": {},
        "commands": {},
    }
    failure: BaseException = TimeoutError()

    async def connect(self) -> None:
        raise type(self).failure

    async def send_command(self, command: str, params: dict | None = None) -> Any:
        return None


async def _failed_lines(
    caplog, failure: BaseException, *, retry_after: float = 60.0, press_reconnect: bool = False,
) -> tuple[list[str], str]:
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    dm._reconnect_delay = lambda device_id, attempt: retry_after
    _AcmeSilent.failure = failure
    _DRIVER_REGISTRY["acme_silent_connect"] = _AcmeSilent
    try:
        with caplog.at_level(logging.WARNING, logger="openavc"):
            await dm.add_device({
                "id": "w1", "driver": "acme_silent_connect", "name": "W1",
                "config": {"host": "192.0.2.10", "port": 5500},
            })
            await asyncio.sleep(0.05)
            if press_reconnect:
                await dm.reconnect_device("w1")
                await asyncio.sleep(0.05)
        lines = [r.getMessage() for r in caplog.records if "failed" in r.getMessage().lower()]
        return lines, str(state.get("device.w1.offline_detail") or "")
    finally:
        await dm._cancel_reconnect("w1")
        _DRIVER_REGISTRY.pop("acme_silent_connect", None)


async def _failed_line(caplog, failure: BaseException) -> tuple[str, str]:
    lines, detail = await _failed_lines(caplog, failure)
    first = [line for line in lines if "Failed to connect 'w1'" in line]
    assert len(first) == 1, lines
    return first[0], detail


async def test_a_failure_with_no_text_says_what_the_card_says(caplog):
    line, detail = await _failed_line(caplog, TimeoutError())
    assert detail
    assert line == f"Failed to connect 'w1': {detail}"


async def test_a_failure_with_its_own_text_still_says_that(caplog):
    line, _detail = await _failed_line(caplog, ConnectionRefusedError("refused by 192.0.2.10:5500"))
    assert line == "Failed to connect 'w1': refused by 192.0.2.10:5500"


async def test_the_retries_and_a_reconnect_press_say_it_too(caplog):
    lines, detail = await _failed_lines(
        caplog, TimeoutError(), retry_after=0.001, press_reconnect=True,
    )
    assert detail
    assert f"[w1] Reconnect failed: {detail}" in lines
    assert f"Reconnect failed for w1: {detail}" in lines
    assert not [line for line in lines if line.endswith(": ")], lines
