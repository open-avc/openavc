"""The DRIVER_INFO a YAML definition becomes, exactly.

``create_configurable_driver_class`` copies the definition's fields the
platform reads off a live driver onto DRIVER_INFO. These pin the defaults,
which fields are copied, the order they land in (the device listing and the
driver API serialise it as built), and the class it returns.
``test_driver_field_reaches_runtime.py`` checks the contract's side: that no
declared field is silently dropped.

Invented device ("acme_widget") throughout.
"""

from __future__ import annotations

from openavc.drivers.configurable import ConfigurableDriver, create_configurable_driver_class


def test_a_bare_definition_gets_the_defaults() -> None:
    cls = create_configurable_driver_class({})
    assert cls.__name__ == "ConfigurableDriver_unknown"
    assert cls.__bases__ == (ConfigurableDriver,)
    assert list(cls.DRIVER_INFO.items()) == [
        ("id", "unknown"),
        ("name", "unknown"),
        ("manufacturer", "Generic"),
        ("category", "utility"),
        ("version", "1.0.0"),
        ("author", "Community"),
        ("description", ""),
        ("transport", "tcp"),
        ("default_config", {}),
        ("config_schema", {}),
        ("state_variables", {}),
        ("commands", {}),
    ]


def test_every_copied_field_lands_in_its_place() -> None:
    copied = [
        "help", "protocols", "discovery", "device_settings", "compatible_models",
        "child_entity_types", "bridge", "push", "transports", "inline_protocol",
        "ir_codes", "routing", "actions", "quick_actions", "web_ui", "delimiter",
    ]
    definition = {key: f"<{key}>" for key in reversed(copied)}
    definition.update({
        "id": "acme_widget", "name": "Acme Widget", "manufacturer": "Acme",
        "category": "audio", "version": "2.1.0", "author": "Acme Co",
        "description": "A widget.", "transport": "http",
        "default_config": {"port": 80}, "config_schema": {"port": {"type": "integer"}},
        "state_variables": {"level": {"type": "integer"}},
        "commands": {"go": {"label": "Go", "send": "GO"}},
        "on_connect": ["GO"],
    })
    cls = create_configurable_driver_class(definition)
    assert cls.__name__ == "ConfigurableDriver_acme_widget"
    assert cls._definition is definition
    info = cls.DRIVER_INFO
    assert list(info) == [
        "id", "name", "manufacturer", "category", "version", "author", "description",
        "transport", "default_config", "config_schema", "state_variables",
        *copied[:12], "commands", *copied[12:],
    ]
    assert {key: info[key] for key in copied} == {key: f"<{key}>" for key in copied}
    assert info["manufacturer"] == "Acme" and info["version"] == "2.1.0"
    assert info["commands"] == {"go": {"label": "Go", "params": {}}}
    assert "on_connect" not in info


def test_the_info_shares_the_definitions_tables() -> None:
    definition = {
        "id": "acme_widget",
        "default_config": {"port": 80},
        "state_variables": {
            "level": {"type": "integer", "help": "How loud."},
            "mute": {"type": "boolean"},
        },
    }
    level = definition["state_variables"]["level"]
    mute = definition["state_variables"]["mute"]
    info = create_configurable_driver_class(definition).DRIVER_INFO
    assert info["default_config"] is definition["default_config"]
    assert info["state_variables"] is definition["state_variables"]
    # A variable with help is replaced by an equal copy; one without is kept.
    assert info["state_variables"]["level"] == level
    assert info["state_variables"]["level"] is not level
    assert info["state_variables"]["mute"] is mute
