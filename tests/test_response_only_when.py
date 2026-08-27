"""A response rule gated on state: ``only_when:``.

The device this exists for keeps answering a query whose answer only one of its
modes is acting on. A frame with an extracted-audio matrix reports a stored
per-output audio assignment whether or not the audio ports are taking from it,
so in its bind-to-video modes that number describes nothing anybody can hear --
and published unguarded, every reader believes it: a panel names a source, an
alert fires on it, a script branches on it.

Platform feature, invented device, synthetic frames -- no real product, driver
file or captured fixture is involved.
"""

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.configurable import create_configurable_driver_class
from openavc.drivers.driver_loader import validate_driver_definition


def _make_driver(definition: dict, device_id: str = "widget_1"):
    cls = create_configurable_driver_class(definition)
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return cls(device_id, {}, state, events)


def _definition(only_when: object) -> dict:
    return {
        "id": "acme_widget",
        "name": "Acme Widget",
        "manufacturer": "Acme",
        "category": "switcher",
        "transport": "tcp",
        "state_variables": {
            "audio_mode": {"type": "integer", "label": "Audio Mode"},
            "audio_source": {"type": "integer", "label": "Audio Source"},
        },
        "responses": [
            {"match": r"^MODE(\d+)$", "set": {"audio_mode": "$1"}},
            {
                "match": r"^AUDIO SRC (\d+)$",
                "only_when": only_when,
                "set": {"audio_source": "$1"},
            },
        ],
    }


GATE = {"key": "audio_mode", "operator": "equals", "value": "2"}

# What a declared integer state variable holds before anything reports one. A
# skipped rule leaves this standing; it is not the same as "the device said 0",
# which is exactly why the gate matters more than a default ever could.
SEEDED = 0


def _audio_source(drv):
    return drv.state.get(f"device.{drv.device_id}.audio_source")


@pytest.mark.asyncio
async def test_the_rule_applies_while_the_gate_is_open() -> None:
    drv = _make_driver(_definition(GATE))
    await drv.on_data_received(b"MODE2\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == 4


@pytest.mark.asyncio
async def test_the_rule_is_skipped_while_the_gate_is_shut() -> None:
    drv = _make_driver(_definition(GATE))
    await drv.on_data_received(b"MODE0\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    # Still the value the platform seeds a declared integer with, because the
    # rule never ran. Seeded, not absent: a declared state variable exists from
    # construction so a bound label never draws a blank.
    assert _audio_source(drv) == SEEDED


@pytest.mark.asyncio
async def test_a_shut_gate_leaves_the_previous_value_standing() -> None:
    """Skipped, not cleared.

    A driver that blanked the variable every time the mode changed would make
    "we are not reporting this" indistinguishable from "the device says
    nothing is routed", which is the confusion the gate exists to end.
    """
    drv = _make_driver(_definition(GATE))
    await drv.on_data_received(b"MODE2\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    await drv.on_data_received(b"MODE0\r\n")
    await drv.on_data_received(b"AUDIO SRC 7\r\n")
    assert _audio_source(drv) == 4


@pytest.mark.asyncio
async def test_the_gate_reopens_without_a_reconnect() -> None:
    drv = _make_driver(_definition(GATE))
    await drv.on_data_received(b"MODE0\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    await drv.on_data_received(b"MODE2\r\n")
    await drv.on_data_received(b"AUDIO SRC 7\r\n")
    assert _audio_source(drv) == 7


@pytest.mark.asyncio
async def test_no_gate_applies_the_rule() -> None:
    drv = _make_driver(_definition(None))
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        {"all": [{"key": "audio_mode", "operator": "equals", "value": "2"}]},
        {
            "any": [
                {"key": "audio_mode", "operator": "equals", "value": "9"},
                {"key": "audio_mode", "operator": "gt", "value": 1},
            ]
        },
        {"any": [{"key": "audio_mode", "operator": "equals", "value": "2"}]},
    ],
    ids=["all-group", "any-group-second-matches", "any-group"],
)
async def test_condition_groups_are_honored(gate: dict) -> None:
    drv = _make_driver(_definition(gate))
    await drv.on_data_received(b"MODE2\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == 4


@pytest.mark.asyncio
async def test_a_gate_on_a_key_nothing_has_written_is_shut() -> None:
    """No value is not the value asked for. The device has not said what mode
    it is in, so a rule that only makes sense in one of them does not apply."""
    drv = _make_driver(_definition(GATE))
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == SEEDED


@pytest.mark.asyncio
async def test_a_malformed_gate_opens_rather_than_closing() -> None:
    """A driver whose condition is nonsense should report what it always
    reported. Going silent on the one state variable somebody was watching is
    the worse failure, and the validator is where the typo gets named."""
    drv = _make_driver(_definition({"operator": "eq", "value": "2"}))  # no key
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == 4


@pytest.mark.asyncio
async def test_a_shut_gate_lets_a_later_rule_have_the_frame() -> None:
    """First-match-wins counts only rules that actually apply -- otherwise a
    gate would silently shadow the rule beneath it."""
    definition = _definition(GATE)
    definition["state_variables"]["fallback"] = {
        "type": "integer", "label": "Fallback",
    }
    definition["responses"].append(
        {"match": r"^AUDIO SRC (\d+)$", "set": {"fallback": "$1"}}
    )
    drv = _make_driver(definition)
    await drv.on_data_received(b"MODE0\r\n")
    await drv.on_data_received(b"AUDIO SRC 4\r\n")
    assert _audio_source(drv) == SEEDED
    assert drv.state.get(f"device.{drv.device_id}.fallback") == 4


def test_the_validator_names_a_bad_gate() -> None:
    definition = _definition({"key": "audio_mode", "operator": "sorta_equals"})
    errors = validate_driver_definition(definition, strict=True)
    assert any("sorta_equals" in e for e in errors), errors


def test_a_well_formed_gate_validates() -> None:
    assert validate_driver_definition(_definition(GATE), strict=True) == []
