"""A device card's reason sentence never shows a login.

``device.<id>.offline_detail`` is published to every reader of state: the
Programmer, the panels, scripts and the cloud relay. Two ways a login reached
it: a sign-in fault quotes what the device sent after the password prompt, so
a device that echoes what it is sent put the password into the sentence; and a
host typed with a login (``admin:secret@10.0.0.5``) was printed as typed. The
log copy of the same failure was already masked; the card was not.

On an invented Acme device.
"""

from __future__ import annotations

import errno
import re
from typing import Any

import pytest

from openavc.core.connection_fault import AUTH_FAILED, classify_connection_fault
from openavc.core.device_manager import DeviceManager
from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.utils.log_redaction import get_secret_registry

_AUTH = {
    "type": "telnet_login",
    "username_prompt": "login: ",
    "password_prompt": "Password: ",
    "timeout_seconds": 0.2,
}


def _driver(**config: Any) -> Any:
    definition: dict[str, Any] = {
        "id": "acme_secure",
        "name": "Acme Secure Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "default_config": {"host": "", "port": 23},
        "config_schema": {"username": {"type": "string", "label": "Login Name"}},
        "state_variables": {"power": {"type": "string", "label": "Power"}},
        "commands": {},
        "responses": [],
        "auth": dict(_AUTH),
    }
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    cls = create_configurable_driver_class(definition)
    cfg = {"host": "10.0.0.9", "port": 23, "username": "operator", "password": "S3cretPass"}
    cfg.update(config)
    return cls("dev1", cfg, state, events)


def _card(driver: Any, exc: BaseException) -> tuple[str, str]:
    """Publish ``exc`` as the device's offline reason; return (code, sentence)."""
    manager = DeviceManager(StateStore(), EventBus())
    code = manager._set_offline_reason("dev1", driver, exc=exc)
    return code, manager.state.get("device.dev1.offline_detail")


async def _sign_in_fault(driver: Any, echo: bytes, *, failure: str | None) -> ConnectionFaultError:
    """The fault the sign-in raises when the device answers the password with
    ``echo`` (the stage after the password went out)."""
    head = b"login: Password: "
    driver._auth_buffer = bytearray(head + echo)
    driver._auth_search_pos = len(head)
    with pytest.raises(ConnectionFaultError) as caught:
        await driver._auth_wait_for(
            re.compile("OK> "),
            re.compile(failure) if failure else None,
            0.05,
            stage="success",
        )
    return caught.value


# Passwords whose quoted form differs from the typed one, or that follow a
# line break: the quote turns a newline into the letters \r\n, which sit
# against the password's first letter, and escapes a backslash or a quote.
_PASSWORDS = ["S3cretPass", "pa\\ss'word", 'say"cheese"']


@pytest.mark.parametrize("password", _PASSWORDS)
@pytest.mark.parametrize("failure", ["incorrect", None], ids=["refused", "no_confirmation"])
async def test_a_password_the_device_echoes_is_masked_on_the_card_and_in_the_fault(
    password, failure,
):
    driver = _driver(password=password)
    echo = b"\r\n" + password.encode() + b"\r\nLogin incorrect\r\n"
    exc = await _sign_in_fault(driver, echo, failure=failure)

    # The fault itself is the log line's text: masked before it is raised.
    assert password not in str(exc)
    assert repr(password)[1:-1] not in str(exc)
    assert "***" in str(exc)

    code, sentence = _card(driver, exc)
    assert code == AUTH_FAILED
    assert password not in sentence
    assert repr(password)[1:-1] not in sentence
    assert "Login incorrect" in sentence


async def test_the_sign_in_still_quotes_what_the_device_sent_around_the_password():
    driver = _driver()
    exc = await _sign_in_fault(
        driver, b"\r\nS3cretPass\r\nLogin incorrect\r\n", failure="incorrect",
    )
    assert str(exc) == (
        "login rejected by the device (matched failure pattern in "
        "'\\r\\n***\\r\\nLogin incorrect\\r\\n')"
    )


def test_a_driver_fault_carrying_the_password_is_masked_when_published():
    """Whatever wrote the sentence (a driver's own fault, a stashed one), the
    card masks this device's secrets before anyone can read it."""
    driver = _driver()
    _, sentence = _card(
        driver, ConnectionFaultError("device said S3cretPass is wrong", code="auth_failed"),
    )
    assert sentence == "device said *** is wrong"

    driver._stash_fault(AUTH_FAILED, "refused S3cretPass")
    manager = DeviceManager(StateStore(), EventBus())
    manager._set_offline_reason("dev1", driver)
    assert manager.state.get("device.dev1.offline_detail") == "refused ***"


def test_the_card_masks_the_systems_own_secrets_but_not_another_devices():
    """The card is about one device: its secrets and the project's are masked,
    and another device's password does not blank a word on it."""
    driver = _driver()
    registry = get_secret_registry()
    registry.set_project_secrets({"MeshKey99"})
    registry.set_config_secrets("dev2", {"refused"})
    _, sentence = _card(
        driver,
        ConnectionFaultError("refused with MeshKey99 and S3cretPass", code="auth_failed"),
    )
    assert sentence == "refused with *** and ***"


# --- a login typed into the address --------------------------------------------


def _refused_on(host: str, port: Any = 80) -> str:
    fault = classify_connection_fault(
        last_error="",
        exc=ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
        host=host, port=port, transport="http",
    )
    return fault.message


@pytest.mark.parametrize("host, shown", [
    ("admin:S3cretPass@10.0.0.5", "admin:***@10.0.0.5:80"),
    ("http://admin:S3cretPass@cam.local", "http://admin:***@cam.local:80"),
    ("admin:p@ss:w/rd@10.0.0.5", "admin:***@10.0.0.5:80"),
    (" admin:S3cretPass@10.0.0.5", '" admin:***@10.0.0.5":80'),
])
def test_a_login_typed_into_the_host_shows_its_password_masked(host, shown):
    message = _refused_on(host)
    assert f"Connection refused on {shown}." in message
    assert "S3cretPass" not in message and "p@ss" not in message


@pytest.mark.parametrize("host, shown", [
    ("10.0.0.5", "10.0.0.5:80"),
    ("user@10.0.0.5", "user@10.0.0.5:80"),
    ("fe80::1", "fe80::1:80"),
    ("[::1]", "[::1]:80"),
    ("http://cam.local", "http://cam.local:80"),
    (" 10.0.0.5", '" 10.0.0.5":80'),
])
def test_a_host_with_no_login_is_shown_as_dialed(host, shown):
    assert f"Connection refused on {shown}." in _refused_on(host)


def test_a_login_typed_into_the_host_is_masked_on_the_card():
    driver = _driver(host="admin:S3cretPass@10.0.0.5", password="")
    _, sentence = _card(
        driver, ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"),
    )
    assert sentence.startswith("Connection refused on admin:***@10.0.0.5:23.")
