"""Third-party request chatter stays out of the in-app log.

httpx logs every request it makes at INFO ("HTTP Request: GET http://...").
The platform's own HTTP calls are logged where they matter (a driver's
traffic per device, with its own lines), so the library's copy only buried
what people read: the Dashboard's Recent Activity filled with the Simulator
UI proxy's polls, and a device's log picked up any request whose URL named
the device. httpcore and websockets add a line per connection step or frame
at DEBUG. A library's warnings and errors still come through.
"""

from __future__ import annotations

import logging

import pytest

from openavc.utils.log_buffer import get_log_buffer
from openavc.utils.logger import get_logger


def _messages() -> list[str]:
    return [e["message"] for e in get_log_buffer().get_recent(1000)]


@pytest.fixture(autouse=True)
def _configured():
    get_logger("openavc.tests.third_party_levels")


@pytest.mark.parametrize("name,level", [
    ("httpx", logging.WARNING),
    ("httpcore", logging.WARNING),
    ("websockets", logging.INFO),
])
def test_library_loggers_are_held_to_their_level(name, level):
    assert logging.getLogger(name).getEffectiveLevel() == level


def test_a_library_request_line_does_not_reach_the_log():
    logging.getLogger("httpx").info(
        'HTTP Request: GET http://127.0.0.1:19500/api/devices/acme_amp_7 '
        '"HTTP/1.1 200 OK"'
    )
    logging.getLogger("httpcore.http11").debug("acme_amp_7 send_request_headers.started")
    logging.getLogger("websockets.client").debug("acme_amp_7 > TEXT frame")
    assert not any("acme_amp_7" in m for m in _messages())


def test_a_library_warning_still_does():
    logging.getLogger("httpx").warning("acme_amp_8 certificate could not be verified")
    assert any("acme_amp_8" in m for m in _messages())
