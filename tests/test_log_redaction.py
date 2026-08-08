"""Credential redaction in the device log.

Device protocols put logins on the wire in the clear, transport TX/RX is pinned
to DEBUG into the in-memory ring buffer whatever the configured log level, and
that buffer is served by ``GET /api/logs/recent`` and offered as a download. So
a driver that authenticates used to publish its password to anyone who could
read the log.

Two halves are pinned here: the shared TX/RX formatter (every transport routes
through one function, and that function masks the device's known credentials),
and the log filter that covers lines a driver writes itself. Plus the reuse
pins — the rule exists once and three doors call it, so a fourth copy fails CI
rather than drifting.

Product-agnostic throughout: an invented device (``acme_widget``) and synthetic
payloads, never a shipped driver.
"""

from __future__ import annotations

import inspect
import logging

import pytest

from openavc.core.state_store import StateStore
from openavc.core.event_bus import EventBus
from openavc.drivers.base import BaseDriver
from openavc.transport.wire_log import format_wire_data
from openavc.utils.log_redaction import (
    MIN_SECRET_LEN,
    SecretRedactionFilter,
    SecretRegistry,
    collect_secret_values,
    compile_secret_pattern,
    get_secret_registry,
    is_secret_key,
    redact_config,
    redact_text,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test starts and ends with an empty process-wide registry."""
    registry = get_secret_registry()
    registry.clear()
    yield registry
    registry.clear()


# --------------------------------------------------------------------------
# The formatter — one function, and every transport routes through it
# --------------------------------------------------------------------------


def test_printable_payload_is_returned_as_text():
    assert format_wire_data(b"  PWR ON\r\n  ") == "PWR ON"


def test_binary_payload_is_returned_as_hex():
    assert format_wire_data(b"\x00\x01\xff") == "0001ff"


def test_non_printable_ascii_falls_through_to_hex():
    assert format_wire_data(b"\x07\x08") == "0708"


def test_every_transport_formats_through_the_shared_function():
    """No transport may keep a private copy of the formatting rule.

    There were three byte-identical copies of ``_format_data`` before this
    consolidation. A future one would silently escape redaction — the copy
    would format the bytes and never consult the registry — so the check is
    that each transport's formatter body is a delegation, not a reimplementation.
    """
    from openavc.transport import serial_transport, tcp, udp

    formatters = {
        "tcp": tcp.TCPTransport._format_data,
        "serial": serial_transport.SerialTransport._format_data,
        "udp": udp._format_data,
    }
    for name, func in formatters.items():
        source = inspect.getsource(func)
        assert "format_wire_data" in source, (
            f"{name}._format_data no longer delegates to the shared formatter"
        )
        assert ".hex()" not in source, (
            f"{name}._format_data reimplements the formatting rule; it must "
            f"call format_wire_data so redaction cannot be bypassed"
        )


# --------------------------------------------------------------------------
# Redaction of known credential values
# --------------------------------------------------------------------------


def test_config_credential_is_masked_in_wire_traffic(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    assert format_wire_data(b"login(admin,hunter2)", "acme_1") == "login(admin,***)"


def test_redaction_is_scoped_to_the_device_that_owns_the_secret(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    # A second device's traffic is not masked by the first device's password —
    # over-redacting an unrelated device's log would be its own defect.
    assert format_wire_data(b"login(admin,hunter2)", "acme_2") == "login(admin,hunter2)"


def test_traffic_with_no_device_is_formatted_but_not_redacted(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    # A discovery probe or connection test belongs to no device, so there is no
    # credential set to redact against.
    assert format_wire_data(b"login(admin,hunter2)") == "login(admin,hunter2)"


def test_credential_inside_a_binary_frame_is_masked_as_hex(clean_registry):
    """A binary protocol logs hex, so the ASCII form never appears in the line."""
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    frame = b"\x02" + b"hunter2" + b"\x03"
    assert format_wire_data(frame, "acme_1") == "02***03"


def test_a_blank_password_does_not_blank_the_log(clean_registry):
    clean_registry.set_config_secrets("acme_1", {""})
    assert format_wire_data(b"PWR ON", "acme_1") == "PWR ON"


def test_a_very_short_credential_is_ignored(clean_registry):
    """Masking "12" everywhere would destroy the log it is meant to protect."""
    clean_registry.set_config_secrets("acme_1", {"12"})
    assert format_wire_data(b"VOL 12", "acme_1") == "VOL 12"


def test_a_credential_is_not_matched_inside_a_longer_word(clean_registry):
    """The bug this rule was written for: community "public" broke "republic"."""
    clean_registry.set_config_secrets("acme_1", {"public"})
    assert format_wire_data(b"republic", "acme_1") == "republic"
    assert format_wire_data(b"get public", "acme_1") == "get ***"


def test_a_credential_with_punctuation_edges_still_matches(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"p@ss!"})
    assert format_wire_data(b"auth p@ss! ok", "acme_1") == "auth *** ok"


def test_the_longer_of_two_overlapping_credentials_wins(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"secretvalue", "secretvalue123"})
    assert format_wire_data(b"key=secretvalue123", "acme_1") == "key=***"


# --------------------------------------------------------------------------
# Which config fields are credentials
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["password", "Password", "api_key", "passphrase", "token", "username"]
)
def test_conventional_credential_names_are_recognised(key):
    assert is_secret_key(key) is True


@pytest.mark.parametrize("key", ["host", "port", "user_label", "poll_interval"])
def test_ordinary_config_names_are_not_credentials(key):
    assert is_secret_key(key) is False


def test_a_declared_secret_field_is_collected_whatever_it_is_called():
    """`secret: true` is the driver saying so; the name list is only a fallback."""
    config = {"site_code": "9931-ALPHA", "host": "10.0.0.5"}
    schema = {"site_code": {"type": "string", "secret": True}}
    assert collect_secret_values(config, schema) == {"9931-ALPHA"}


def test_conventional_names_are_collected_without_a_schema():
    config = {"password": "hunter2", "host": "10.0.0.5", "port": 23}
    assert collect_secret_values(config, None) == {"hunter2"}


def test_short_and_non_string_config_values_are_never_collected():
    config = {"password": "", "api_key": "ab", "token": None, "secret_port": 2000}
    assert collect_secret_values(config, None) == set()


def test_redact_config_masks_values_but_keeps_unset_fields_visible():
    masked = redact_config({"password": "hunter2", "token": "", "host": "10.0.0.5"})
    assert masked == {"password": "***", "token": "", "host": "10.0.0.5"}


# --------------------------------------------------------------------------
# Runtime secrets — BaseDriver.redact_in_log
# --------------------------------------------------------------------------


class _AcmeDriver(BaseDriver):
    """An invented device whose login returns a session token."""

    DRIVER_INFO = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "config_schema": {
            "password": {"type": "string", "secret": True},
            "site_code": {"type": "string", "secret": True},
        },
        "state_variables": {},
        "commands": {},
    }

    async def send_command(self, command: str, params=None):  # pragma: no cover
        return None


def _make_driver(config):
    return _AcmeDriver("acme_1", config, StateStore(), EventBus())


def test_a_driver_registers_its_config_credentials_on_construction(clean_registry):
    _make_driver({"host": "10.0.0.5", "password": "hunter2", "site_code": "ALPHA-99"})
    assert clean_registry.secrets_for("acme_1") == {"hunter2", "ALPHA-99"}
    assert format_wire_data(b"login(admin,hunter2)", "acme_1") == "login(admin,***)"


def test_redact_in_log_masks_a_token_the_device_issued(clean_registry):
    driver = _make_driver({"host": "10.0.0.5", "password": "hunter2"})
    payload = b'{"result":{"session":"a91f-77c2-de10"}}'
    assert format_wire_data(payload, "acme_1") == payload.decode()

    driver.redact_in_log("a91f-77c2-de10")
    assert format_wire_data(payload, "acme_1") == '{"result":{"session":"***"}}'


def test_redact_in_log_ignores_a_trivially_short_value(clean_registry):
    driver = _make_driver({"host": "10.0.0.5"})
    driver.redact_in_log("ok")
    assert format_wire_data(b"status ok", "acme_1") == "status ok"


def test_a_runtime_token_survives_a_config_re_resolve(clean_registry):
    """A reconnect re-registers config secrets; it must not drop the token."""
    driver = _make_driver({"host": "10.0.0.5", "password": "hunter2"})
    driver.redact_in_log("a91f-77c2-de10")
    driver._register_config_secrets()
    assert "a91f-77c2-de10" in clean_registry.secrets_for("acme_1")


def test_an_edited_password_stops_masking_the_old_one(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    clean_registry.set_config_secrets("acme_1", {"newpass9"})
    assert format_wire_data(b"was hunter2", "acme_1") == "was hunter2"
    assert format_wire_data(b"now newpass9", "acme_1") == "now ***"


def test_forgetting_a_device_stops_masking_its_credential(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    clean_registry.forget("acme_1")
    assert format_wire_data(b"login(admin,hunter2)", "acme_1") == "login(admin,hunter2)"


# --------------------------------------------------------------------------
# The filter — lines a driver writes itself, which no formatter sees
# --------------------------------------------------------------------------


def _record(message: str, *args) -> logging.LogRecord:
    return logging.LogRecord(
        name="openavc.drivers.acme_widget",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def test_filter_masks_a_credential_a_driver_logged_itself(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    record = _record("Authenticating as admin/hunter2")
    assert SecretRedactionFilter().filter(record) is True
    assert record.getMessage() == "Authenticating as admin/***"


def test_filter_masks_a_credential_delivered_through_record_args(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    record = _record("Authenticating with %s", "hunter2")
    SecretRedactionFilter().filter(record)
    assert record.getMessage() == "Authenticating with ***"


def test_filter_leaves_a_record_with_no_credential_untouched(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    record = _record("Connected to 10.0.0.5:23")
    SecretRedactionFilter().filter(record)
    assert record.getMessage() == "Connected to 10.0.0.5:23"


def test_filter_is_idempotent_across_handlers(clean_registry):
    """One record passes the console, file and buffer handlers in turn."""
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    record = _record("token hunter2")
    filt = SecretRedactionFilter()
    for _ in range(3):
        filt.filter(record)
    assert record.getMessage() == "token ***"


def test_filter_never_drops_a_record(clean_registry):
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    assert SecretRedactionFilter().filter(_record("hunter2")) is True


def test_filter_masks_any_devices_credential(clean_registry):
    """The filter has no device context, so it draws on every registered secret."""
    clean_registry.set_config_secrets("acme_1", {"hunter2"})
    clean_registry.set_config_secrets("acme_2", {"otherpass"})
    record = _record("cross-device otherpass")
    SecretRedactionFilter().filter(record)
    assert record.getMessage() == "cross-device ***"


def test_filter_is_installed_on_every_live_handler():
    """Console, file and in-memory buffer must all carry it.

    The buffer is the one that matters most — it is what GET /api/logs/recent
    and the Log view's Download serve — and it is the one a filter attached to
    a logger rather than a handler would miss.
    """
    from openavc.utils import logger as logger_module
    from openavc.utils.log_buffer import BufferHandler

    logger_module._configure_root()
    root = logging.getLogger()

    # Only the handlers OpenAVC installs — the test runner injects its own.
    installed = [
        h
        for h in root.handlers
        if h is logger_module._console_handler
        or h is logger_module._file_handler
        or isinstance(h, BufferHandler)
    ]
    assert any(isinstance(h, BufferHandler) for h in installed), (
        "the in-memory buffer handler is not installed — it is what "
        "GET /api/logs/recent and the Log view's Download serve"
    )
    assert logger_module._console_handler in installed
    for handler in installed:
        assert any(
            isinstance(f, SecretRedactionFilter) for f in handler.filters
        ), f"{type(handler).__name__} has no secret-redaction filter"


# --------------------------------------------------------------------------
# The read door — a secret registered after the line was already logged
# --------------------------------------------------------------------------


def _buffer_with(message: str):
    from openavc.utils.log_buffer import LogBuffer, LogEntry

    buffer = LogBuffer()
    buffer.append(
        LogEntry(
            timestamp=0.0,
            level="DEBUG",
            source="openavc.transport.tcp",
            category="device",
            message=message,
            device="acme_1",
        )
    )
    return buffer


def test_a_line_already_in_the_buffer_is_redacted_when_read(clean_registry):
    """The frame that DELIVERS a runtime token is logged before the driver has
    seen it, so ``redact_in_log`` cannot mask it at write time. This is the door
    that serves that buffer — GET /api/logs/recent and the Log view's Download.
    """
    buffer = _buffer_with("[acme_1] RX: TOKEN a91f-77c2-de10")
    assert buffer.get_recent(10)[0]["message"].endswith("a91f-77c2-de10")

    clean_registry.add_runtime_secret("acme_1", "a91f-77c2-de10")
    assert buffer.get_recent(10)[0]["message"] == "[acme_1] RX: TOKEN ***"


def test_reading_the_buffer_is_unchanged_when_nothing_is_registered(clean_registry):
    buffer = _buffer_with("[acme_1] RX: PWR ON")
    assert buffer.get_recent(10)[0]["message"] == "[acme_1] RX: PWR ON"


def test_reading_the_buffer_does_not_rewrite_the_stored_entry(clean_registry):
    """Redaction on read must not mutate the buffer — the count/filter logic
    and the WS subscribers both hold the same LogEntry objects."""
    buffer = _buffer_with("[acme_1] RX: TOKEN a91f-77c2-de10")
    clean_registry.add_runtime_secret("acme_1", "a91f-77c2-de10")
    buffer.get_recent(10)
    assert buffer._entries[0].message == "[acme_1] RX: TOKEN a91f-77c2-de10"


# --------------------------------------------------------------------------
# Reuse pins — the rule exists once
# --------------------------------------------------------------------------


def test_device_manager_masks_configs_with_the_shared_rule():
    """A second credential-name list is what this consolidation removed."""
    from openavc.core import device_manager

    assert device_manager._redact_config is redact_config


def test_the_cloud_log_tool_masks_with_the_shared_rule():
    """The cloud AI's view of a log line and the log itself agree on "secret"."""
    from openavc.cloud.tools import system_tools

    assert system_tools._redact_log_message is redact_text
    assert system_tools._looks_secret is is_secret_key


def test_min_secret_length_is_one_constant():
    from openavc.cloud.tools import system_tools

    assert system_tools.MIN_SECRET_LEN is MIN_SECRET_LEN


# --------------------------------------------------------------------------
# Registry mechanics
# --------------------------------------------------------------------------


def test_an_empty_registry_compiles_no_pattern():
    assert compile_secret_pattern([]) is None
    assert compile_secret_pattern(["ab", ""]) is None


def test_redact_text_is_a_no_op_without_secrets():
    assert redact_text("PWR ON", set()) == "PWR ON"


def test_registry_reports_whether_it_holds_anything():
    registry = SecretRegistry()
    assert registry.has_secrets() is False
    registry.set_config_secrets("acme_1", {"hunter2"})
    assert registry.has_secrets() is True
    registry.forget("acme_1")
    assert registry.has_secrets() is False


def test_registry_pattern_cache_follows_a_runtime_addition():
    """A cached per-device pattern must not outlive the secret set it was built
    from — the token registered after the first TX line still gets masked."""
    registry = SecretRegistry()
    registry.set_config_secrets("acme_1", {"hunter2"})
    assert registry.redact_for("acme_1", "tok abcd1234") == "tok abcd1234"
    registry.add_runtime_secret("acme_1", "abcd1234")
    assert registry.redact_for("acme_1", "tok abcd1234") == "tok ***"
