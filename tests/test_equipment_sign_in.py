"""A product with OpenAVC built in is asked for the sign-in it needs.

Its address, username and password are required settings, so Add Device,
Edit Device, a device edit and the AI's device tools ask for any left blank,
the way they ask for any driver's required field. A device saved without one
says on its card which ones are not set, not the ones it already has; the
card's banner says where to set them, as it does for any refused login.
"""
from __future__ import annotations

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.required_config import missing_required_fields
from openavc.core.state_store import StateStore
from openavc.drivers.base import ConnectionFaultError
from openavc.drivers.equipment import EquipmentDriver

SCHEMA = EquipmentDriver.DRIVER_INFO["config_schema"]
DEFAULTS = EquipmentDriver.DRIVER_INFO["default_config"]


def test_the_address_and_sign_in_are_required():
    assert missing_required_fields(SCHEMA, dict(DEFAULTS), "network") == [
        ("host", "Host"), ("username", "Username"), ("password", "Password"),
    ]


def test_a_discovered_product_is_asked_only_for_its_sign_in():
    config = {**DEFAULTS, "host": "10.0.0.9"}
    assert missing_required_fields(SCHEMA, config, "network") == [
        ("username", "Username"), ("password", "Password"),
    ]
    config.update(username="engineer", password="secret")
    assert missing_required_fields(SCHEMA, config, "network") == []


@pytest.mark.parametrize(
    "config, sentence",
    [
        ({"host": "10.0.0.9"},
         "This product's username and password are not set."),
        ({"host": "10.0.0.9", "username": "engineer"},
         "This product's password is not set."),
        ({"username": "engineer", "password": "secret"},
         "This product's host is not set."),
        ({},
         "This product's host, username and password are not set."),
    ],
)
async def test_the_card_names_only_what_is_missing(config, sentence):
    driver = EquipmentDriver("equipment", {**DEFAULTS, **config}, StateStore(), EventBus())
    with pytest.raises(ConnectionFaultError) as caught:
        await driver.connect()
    assert str(caught.value) == sentence
    assert caught.value.fault_code == "auth_failed"
