"""The password a YAML driver's sign-in types is masked in that device's log.

The redaction rule recognizes a credential by its config field's name, or by
the driver declaring the field ``secret: true``. ``auth.password_field`` can
name any field, so the sign-in registers the value it sends as a secret
itself. Invented device (``acme_widget``) and a synthetic password.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.transport.wire_log import format_wire_data
from openavc.utils.log_redaction import get_secret_registry, is_secret_key

PASSWORD = "Acme-Sign-9137"


def _definition() -> dict:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "utility",
        "version": "1.0.0",
        "transport": "tcp",
        "delimiter": "\r\n",
        "default_config": {"host": "", "port": 23, "operator": "", "site_code": ""},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "operator": {"type": "string", "label": "Operator"},
            "site_code": {"type": "string", "label": "Site code"},
        },
        "state_variables": {},
        "commands": {},
        "responses": [],
        "auth": {
            "type": "telnet_login",
            "username_prompt": "Name:",
            "username_field": "operator",
            "password_prompt": "Code:",
            "password_field": "site_code",
            "timeout_seconds": 1,
        },
    }


@pytest.mark.asyncio
async def test_the_sign_in_password_is_masked_under_any_field_name():
    assert not is_secret_key("site_code")  # the name list alone would miss it
    cls = create_configurable_driver_class(_definition())
    driver = cls(
        "acme1",
        {"host": "127.0.0.1", "port": 23, "operator": "tech", "site_code": PASSWORD},
        StateStore(),
        EventBus(),
    )
    driver.transport = AsyncMock()
    driver._auth_wait_for = AsyncMock()
    try:
        await driver._perform_auth_handshake()
        line = format_wire_data(f"{PASSWORD}\r\n".encode(), "acme1", "tx")
        assert PASSWORD not in line
        assert "***" in line
    finally:
        get_secret_registry().forget("acme1")
