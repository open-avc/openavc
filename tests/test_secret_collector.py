"""The one collector of a device's secret values, and the exits that ask it.

A device's secret values are what ``utils/log_redaction`` gathers for the
secret registry: a setting the driver marks secret, the connection
``password``, and a value named like a credential. A secret the driver marks
is read as its text whatever type it holds, so a PIN stored as a number is
masked like one stored as text; a value only named like a credential is
collected when it is text, so a ``secret_port`` of 2000 does not mask every
"2000". Every exit that may not carry a secret (``utils/secret_audience``)
masks what the collector gathered.

Product-agnostic: an invented device (``acme_widget``) and synthetic payloads.
"""

from __future__ import annotations

from typing import Any

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.wire_log import format_wire_data
from openavc.utils.log_redaction import (
    REDACTED,
    collect_secret_values,
    get_secret_registry,
    secret_text,
    secret_values,
)
from openavc.utils.secret_audience import Exit

PIN = 482913


@pytest.fixture(autouse=True)
def _clean_registry():
    get_secret_registry().clear()
    yield
    get_secret_registry().clear()


class _AcmePinned(BaseDriver):
    """An invented keypad whose access PIN its driver marks secret, typed as a
    number by a driver written before a secret had to be text."""

    DRIVER_INFO = {
        "id": "acme_widget_pinned",
        "name": "Acme Widget",
        "transport": "tcp",
        "config_schema": {
            "host": {"type": "string"},
            "pin": {"type": "integer", "secret": True},
        },
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params=None):  # pragma: no cover
        return None


def _pinned_driver() -> _AcmePinned:
    return _AcmePinned(
        "acme_widget", {"host": "10.0.0.5", "pin": PIN}, StateStore(), EventBus(),
    )


# --- Reading a value as text ------------------------------------------------------


@pytest.mark.parametrize(("value", "text"), [
    ("hunter22", "hunter22"), (PIN, "482913"), (4829.5, "4829.5"),
    (True, None), (None, None), ({"a": 1}, None), (["x"], None),
])
def test_a_secret_value_is_read_as_its_text(value: Any, text: str | None):
    assert secret_text(value) == text


def test_a_secret_the_driver_marks_is_collected_whatever_type_it_holds():
    schema = _AcmePinned.DRIVER_INFO["config_schema"]
    assert collect_secret_values({"host": "10.0.0.5", "pin": PIN}, schema) == {"482913"}


def test_the_connection_password_is_collected_as_text_whatever_type_it_holds():
    assert collect_secret_values({"password": 99887766}, None) == {"99887766"}


def test_a_number_only_named_like_a_credential_is_not_collected():
    # The name list is the net over what is sent away, not what makes a
    # value secret: a port number does not mask every "2000" in the log.
    assert collect_secret_values({"secret_port": 2000, "api_key": "k-77aa"}, None) == {"k-77aa"}


def test_a_report_takes_short_values_and_usernames_too():
    config = {"username": "op", "password": "abc", "pin": 12, "host": "10.0.0.5"}
    schema = {"pin": {"type": "integer", "secret": True}}
    assert secret_values(config, schema) == set()
    assert secret_values(config, schema, floor=1, usernames=True) == {"op", "abc", "12"}


# --- The exits -------------------------------------------------------------------


def test_a_numeric_pin_is_masked_in_the_device_s_traffic():
    _pinned_driver()
    line = format_wire_data(b"PIN 482913\r", device_id="acme_widget")
    assert "482913" not in line
    assert REDACTED in line


def test_a_numeric_pin_is_masked_in_every_exit_that_may_not_carry_one():
    _pinned_driver()
    registry = get_secret_registry()
    for where in (Exit.LOG, Exit.CLOUD, Exit.REPORT, Exit.SENTENCE, Exit.PROGRAMMER):
        assert registry.masked(where, "sent PIN 482913") == f"sent PIN {REDACTED}", where
    assert registry.masked(Exit.DEVICE, "PIN 482913", "acme_widget") == "PIN 482913"
