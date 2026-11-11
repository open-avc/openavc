"""What the AI's UI checks are told about a bound ``device.*`` key, pinned.

Three questions are asked of the same key at every UI write: is the property
one the driver declares (``undeclared``), is the child id on the declared
roster (``child``), and what does the driver declare about it (``declared``,
which the range fill reads). Each answers ``None`` for "no opinion", and each
must agree with the others about which device and which child a key names.

These record what the three answer today, case by case, including where they
deliberately differ: a per-instance child schema that lacks a property is the
whole answer to ``undeclared`` but only a first look for ``declared``, which
falls back to the type's own schema.
"""

from __future__ import annotations

import json

import pytest

from openavc.cloud.tools.ui_tools import (
    _declared_state_variable,
    _undeclared_state_property,
    _unknown_child_id,
)


class _Amp:
    """An invented amplifier: flat state (one name with dots in it), a counted
    channel roster with a per-instance schema, a zone type with no roster, and
    a child type that is not a mapping."""

    DRIVER_INFO = {
        "id": "acme_amp",
        "state_variables": {
            "level": {"type": "float", "min": -60.0, "max": 6.0},
            "preset.recall.last": {"type": "string"},
            "label": "not a mapping",
        },
        "child_entity_types": {
            "channel": {
                "id_format": {"type": "integer"},
                "instances": {"count": 4},
                "state_variables": {
                    "gain": {"type": "float", "max": 0.0},
                    "mute": {"type": "boolean"},
                },
            },
            "zone": {"state_variables": {}},
            "bus": "not a mapping",
        },
    }
    config: dict = {}

    def get_child_schema(self, child_type, local_id):
        if child_type == "channel" and local_id == 1:
            return {"gain": {"type": "float", "max": 10.0}}
        if child_type == "channel" and local_id == 2:
            return {}
        return None


class _Plain:
    """Declares a child type but answers no per-instance schema at all."""

    DRIVER_INFO = {
        "id": "acme_plain",
        "child_entity_types": {
            "out": {"instances": {"count": 2}, "state_variables": {"sig": {}}},
        },
    }
    config = None


class _Bare:
    """Declares nothing."""

    DRIVER_INFO: dict = {}


class _Devices:
    """Records every driver asked for, in order; ``hot`` raises."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self._drivers = {"amp": _Amp(), "amp.rack": _Bare(), "plain": _Plain()}

    def get_driver(self, device_id):
        self.asked.append(device_id)
        if device_id == "hot":
            raise RuntimeError("driver is on fire")
        return self._drivers.get(device_id)


# What the engine passes: every project device id, longest first.
IDS = ["amp.spare", "amp.rack", "plain", "gone", "amp", "hot"]

FLAT = {"level", "preset.recall.last", "label"}
LEVEL = {"type": "float", "min": -60.0, "max": 6.0}
TYPE_GAIN = {"type": "float", "max": 0.0}
INSTANCE_GAIN = {"type": "float", "max": 10.0}
MUTE = {"type": "boolean"}
ROSTER = {"1", "2", "3", "4"}

# key, undeclared, child, declared, drivers asked (each question asks once)
CASES = [
    ("device.amp.level", None, None, LEVEL, ["amp"]),
    ("device.amp.levl", FLAT, None, None, ["amp"]),
    ("device.amp.online", None, None, None, ["amp"]),
    # The platform writes it for every device, and this list does not have it.
    ("device.amp.web_ui_url", FLAT, None, None, ["amp"]),
    ("device.amp.label", None, None, None, ["amp"]),
    ("device.amp.preset.recall.last", None, None, {"type": "string"}, ["amp"]),
    ("device.amp.preset.recall.next", FLAT, None, None, ["amp"]),
    ("device.amp.channel.1.gain", None, None, INSTANCE_GAIN, ["amp"]),
    # The instance schema is the whole answer to one, a first look for the other.
    ("device.amp.channel.1.mute", {"gain"}, None, MUTE, ["amp"]),
    ("device.amp.channel.2.mute", None, None, MUTE, ["amp"]),
    ("device.amp.channel.3.volume", {"gain", "mute"}, None, None, ["amp"]),
    ("device.amp.channel.7.gain", None, ROSTER, TYPE_GAIN, ["amp"]),
    ("device.amp.channel.x.gain", None, ROSTER, TYPE_GAIN, ["amp"]),
    ("device.amp.channel.1.gain.db", {"gain"}, None, None, ["amp"]),
    ("device.amp.zone.1.vol", None, None, None, ["amp"]),
    ("device.amp.bus.1.x", FLAT, None, None, ["amp"]),
    ("device.amp.", FLAT, None, None, ["amp"]),
    # Two parts after the id is a flat name, even when the first is a child type.
    ("device.amp.channel.1", FLAT, None, None, ["amp"]),
    ("device.amp.foo.bar", FLAT, None, None, ["amp"]),
    ("device.amp.rack.level", None, None, None, ["amp.rack"]),
    # A longer id with no driver is the answer; the shorter id is never tried.
    ("device.amp.spare.level", None, None, None, ["amp.spare"]),
    ("device.plain.out.1.sig", None, None, {}, ["plain"]),
    ("device.plain.out.3.sig", None, {"1", "2"}, {}, ["plain"]),
    ("device.plain.out.1.nope", {"sig"}, None, None, ["plain"]),
    ("device.plain.level", None, None, None, ["plain"]),
    ("device.gone.level", None, None, None, ["gone"]),
    ("device.hot.level", None, None, None, ["hot"]),
    ("device.nobody.level", None, None, None, []),
    ("device.amp", None, None, None, []),
    ("state.amp.level", None, None, None, []),
]


def _shown(value):
    return json.dumps(sorted(value) if isinstance(value, set) else value)


@pytest.mark.parametrize(
    ("key", "undeclared", "child", "declared", "asked"), CASES,
    ids=[c[0] for c in CASES],
)
def test_each_question_answers_a_key_as_it_does_today(key, undeclared, child, declared, asked):
    answers = {}
    for name, question in (
        ("undeclared", _undeclared_state_property),
        ("child", _unknown_child_id),
        ("declared", _declared_state_variable),
    ):
        devices = _Devices()
        answers[name] = (question(devices, IDS, key), devices.asked)
    assert answers["undeclared"][0] == undeclared
    assert answers["child"][0] == child
    assert answers["declared"][0] == declared
    # Sets compare without order; the shown form catches a key order change.
    assert [_shown(a[0]) for a in answers.values()] == [
        _shown(undeclared), _shown(child), _shown(declared),
    ]
    assert answers["undeclared"][1] == asked
    assert answers["declared"][1] == asked
    assert answers["child"][1] == asked


def test_the_first_id_that_prefixes_the_key_wins_in_the_order_given() -> None:
    """The caller sorts longest first; handed the short one first, the key
    is read as the short device's dotted property."""
    devices = _Devices()
    assert _undeclared_state_property(devices, ["amp", "amp.rack"], "device.amp.rack.level") == FLAT
    assert devices.asked == ["amp"]
    devices = _Devices()
    assert _undeclared_state_property(devices, ["amp.rack", "amp"], "device.amp.rack.level") is None
    assert devices.asked == ["amp.rack"]


@pytest.mark.parametrize("key", [42, None, ["device.amp.level"]])
def test_a_key_that_is_not_text_says_nothing_and_asks_nothing(key) -> None:
    for question in (_undeclared_state_property, _unknown_child_id, _declared_state_variable):
        devices = _Devices()
        assert question(devices, IDS, key) is None
        assert devices.asked == []


def test_no_device_manager_says_nothing() -> None:
    for question in (_undeclared_state_property, _unknown_child_id, _declared_state_variable):
        assert question(None, IDS, "device.amp.level") is None


def test_the_declared_entry_is_the_drivers_own_object() -> None:
    """The range fill reads the declaration itself, never a copy of it."""
    devices = _Devices()
    entry = _declared_state_variable(devices, IDS, "device.amp.level")
    assert entry is _Amp.DRIVER_INFO["state_variables"]["level"]
    entry = _declared_state_variable(devices, IDS, "device.amp.channel.7.gain")
    assert entry is _Amp.DRIVER_INFO["child_entity_types"]["channel"]["state_variables"]["gain"]
