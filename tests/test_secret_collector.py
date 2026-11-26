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


# --- A device on its way out --------------------------------------------------------


class _AcmeLogout(BaseDriver):
    """An invented device that signs out with its password as it disconnects."""

    DRIVER_INFO = {
        "id": "acme_widget_logout",
        "name": "Acme Widget",
        "transport": "tcp",
        "config_schema": {"host": {"type": "string"}, "password": {"type": "string"}},
        "state_variables": {},
        "commands": {},
    }
    lines: list[str] = []

    async def connect(self) -> None:
        self.set_state("connected", True)

    async def disconnect(self) -> None:
        password = str(self.config.get("password", ""))
        _AcmeLogout.lines.append(
            format_wire_data(f"LOGOUT {password}\r".encode(), device_id=self.device_id),
        )
        self.set_state("connected", False)

    async def send_command(self, command: str, params=None):  # pragma: no cover
        return None


@pytest.fixture
async def manager():
    from openavc.core.device_manager import DeviceManager
    from openavc.drivers.registry import _DRIVER_REGISTRY

    _DRIVER_REGISTRY[_AcmeLogout.DRIVER_INFO["id"]] = _AcmeLogout
    _AcmeLogout.lines = []
    state, events = StateStore(), EventBus()
    state.set_event_bus(events)
    dm = DeviceManager(state, events)
    yield dm
    await dm.disconnect_all()
    _DRIVER_REGISTRY.pop(_AcmeLogout.DRIVER_INFO["id"], None)


def _logout_device(password: str) -> dict[str, Any]:
    return {
        "id": "acme_widget", "driver": "acme_widget_logout", "name": "Acme Widget",
        "config": {"host": "10.0.0.5", "password": password},
    }


async def test_a_removed_device_s_password_is_masked_in_its_last_traffic(manager):
    await manager.add_device(_logout_device("Retiring-Pass-31"))
    await manager.remove_device("acme_widget")
    assert _AcmeLogout.lines == [f"LOGOUT {REDACTED}"]
    # Once it is gone, its password masks nothing.
    assert get_secret_registry().masked(Exit.LOG, "Retiring-Pass-31") == "Retiring-Pass-31"


async def test_a_replaced_device_s_old_password_is_masked_in_its_last_traffic(manager):
    await manager.add_device(_logout_device("Old-Pass-4471"))
    await manager.update_device("acme_widget", _logout_device("New-Pass-8812"))
    assert _AcmeLogout.lines == [f"LOGOUT {REDACTED}"]
    registry = get_secret_registry()
    assert registry.masked(Exit.LOG, "New-Pass-8812") == REDACTED
    assert registry.masked(Exit.LOG, "Old-Pass-4471") == "Old-Pass-4471"


# --- A state value on its way out ---------------------------------------------------

STREAM = "rtsp://op:streampw55@10.0.0.7/live"


@pytest.mark.parametrize(("key", "value", "shown"), [
    ("device.acme_widget.auth_token", "tok-998877", REDACTED),
    ("device.acme_widget.stream_url", STREAM, "rtsp://op:***@10.0.0.7/live"),
    ("device.acme_widget.power", "on", "on"),
    ("device.acme_widget.username", "operator", "operator"),
    ("device.acme_widget.level", 42, 42),
    ("device.acme_widget.has_password", True, True),
])
def test_a_state_value_leaves_for_the_cloud_masked(key: str, value: Any, shown: Any):
    from openavc.cloud.state_relay import cloud_state_value

    assert cloud_state_value(key, value) == shown


def test_a_registered_secret_in_a_state_value_is_masked_for_the_cloud():
    from openavc.cloud.state_relay import cloud_state_value

    get_secret_registry().add_runtime_secret("acme_widget", "sess-41a9c3")
    assert cloud_state_value("device.acme_widget.last_reply", "OK sess-41a9c3") == (
        f"OK {REDACTED}"
    )


@pytest.mark.asyncio
async def test_the_state_relay_sends_a_token_masked():
    from unittest.mock import MagicMock

    from openavc.cloud.state_relay import StateRelay

    store = StateStore()
    relay = StateRelay(MagicMock(), store)
    store.set("device.acme_widget.auth_token", "tok-998877")
    relay._on_state_change("device.acme_widget.auth_token", None, "tok-998877", "device.acme_widget")
    queued = [entry for batch in relay._batches.values() for entry in batch]
    assert [entry["value"] for entry in queued] == [REDACTED]


def test_the_state_log_line_masks_a_stream_address_s_password(caplog):
    import logging

    store = StateStore()
    with caplog.at_level(logging.DEBUG, logger="openavc.core.state_store"):
        store.set("device.acme_widget.stream_url", STREAM)
    lines = "\n".join(r.getMessage() for r in caplog.records if r.name == "openavc.core.state_store")
    assert "streampw55" not in lines
    assert "rtsp://op:***@10.0.0.7/live" in lines


def test_driver_state_on_this_system_is_given_as_it_is():
    from openavc.utils.log_redaction import state_value_as

    get_secret_registry().add_runtime_secret("acme_widget", "streampw55")
    assert state_value_as(Exit.DRIVER_STATE, "device.acme_widget.auth_token", "tok-998877") == "tok-998877"
    assert state_value_as(Exit.DRIVER_STATE, "device.acme_widget.stream_url", STREAM) == STREAM
    assert state_value_as(Exit.LOG, "device.acme_widget.stream_url", STREAM) == (
        "rtsp://op:***@10.0.0.7/live"
    )


# --- A config shown away from this system --------------------------------------------


def test_a_plugin_s_config_reaches_the_assistant_with_every_secret_masked():
    from openavc.cloud.tools.plugin_tools import _masked

    # The plugin's login, saved here and registered, quoted again in a field
    # nothing names like a credential.
    get_secret_registry().set_project_secrets({"Bridge-Pass-1"})
    config = {"password": "Bridge-Pass-1", "notes": "fallback login Bridge-Pass-1", "port": 1883}
    assert _masked(config)["config"] == {
        "password": REDACTED, "notes": f"fallback login {REDACTED}", "port": 1883,
    }


def test_a_plugin_config_sent_back_as_it_was_read_keeps_its_saved_text():
    from openavc.cloud.tools.plugin_tools import _masked
    from openavc.utils.log_redaction import keep_masked_secrets

    get_secret_registry().set_project_secrets({"Bridge-Pass-1"})
    stored = {"password": "Bridge-Pass-1", "notes": "fallback login Bridge-Pass-1"}
    read = _masked(stored)["config"]
    restored, unresolved = keep_masked_secrets({**read, "port": 1884}, stored, where=Exit.CLOUD)
    assert unresolved == []
    assert restored == {**stored, "port": 1884}


def test_an_orphaned_device_s_details_mask_every_secret():
    from openavc.core.device_info import _orphan_config

    get_secret_registry().set_project_secrets({"Orph-Pass-61"})
    config = {"host": "10.0.0.8", "password": "Orph-Pass-61", "notes": "spare Orph-Pass-61",
              "stream": "rtsp://op:streampw55@10.0.0.8/live"}
    assert _orphan_config(config) == {
        "host": "10.0.0.8", "password": REDACTED, "notes": f"spare {REDACTED}",
        "stream": "rtsp://op:***@10.0.0.8/live",
    }
