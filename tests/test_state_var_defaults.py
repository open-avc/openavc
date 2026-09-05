"""What a declared state variable holds before the device has spoken, pinned
across every consumer that answers the question.

There are two questions here and they used to be confused for one.

**What does the runtime record?** Nothing — ``None``. A device that has not
reported a reading has no reading, and the store already distinguishes "the
driver declares this" (``has()``) from "somebody reported it" (``get()``). The
runtime used to record a typed value instead — a numeric at its declared
``min``, an enum at its first value — which nothing downstream could tell from a
reading: a projector nobody had reached published ``lamp_hours = 0`` against a
true 450, the relay shipped it to the cloud as fresh, and a monitor declaring a
minimum above zero fired on it.

**What value WOULD it hold, for a consumer that has to produce one?** That is
still ``compiled_protocol.state_var_default``, and its two callers are the
auto-generated simulator (seeding the wire values it will serve) and the
validator (working out what a variable would hold). Those used to be answered in
four places under three rules; there is one rule now, and this is the table that
made the case.
"""

import math

from openavc.drivers.base import BaseDriver
from openavc.drivers.compiled_protocol import state_var_default
from openavc.simulator.validate import _default_for_type
from openavc.simulator.yaml_auto import YAMLAutoSimulator


def _runtime(var_def):
    return BaseDriver._default_for_var_def(var_def)


def _simulator(var_def):
    """What the auto-generated simulator seeds a flat state var with."""
    info = YAMLAutoSimulator._build_info(
        {"id": "acme_widget", "state_variables": {"probe": var_def}}
    )
    return info["initial_state"]["probe"]


def _simulator_child(var_def):
    return YAMLAutoSimulator._default_child_value(var_def)


def _validator(var_def):
    return _default_for_type(var_def)


# What a value-producing consumer answers. The validator must always match the
# simulator, and the simulator's two seeding paths must always match each other,
# so those are asserted rather than tabulated.
TABLE = [
    ({"type": "number", "min": -12}, 0.0),
    ({"type": "number", "min": -80.0}, 0.0),
    ({"type": "number"}, 0.0),
    ({"type": "float", "min": -3.5}, 0.0),
    ({"type": "integer", "min": 0}, 0),
    ({"type": "integer", "min": 5}, 5),
    ({"type": "integer"}, 0),
    ({"type": "boolean"}, False),
    ({"type": "boolean", "min": 1}, False),
    ({"type": "enum", "values": ["off", "on"]}, "off"),
    ({"type": "enum", "values": []}, ""),
    ({"type": "string"}, ""),
    ({}, ""),
]


def test_default_table():
    for var_def, expected in TABLE:
        assert _simulator(var_def) == expected, var_def
        assert _simulator_child(var_def) == expected, var_def
        assert _validator(var_def) == expected, var_def


def test_the_runtime_records_no_reading_whatever_was_declared():
    """Every row of the table above, and the runtime's answer is the same one.

    This is the whole fix: a reading that was never read is not a number, not a
    word, and not an empty string. It is nothing, which is what
    ``monitors.NO_VALUE``, ``alert_monitor`` and ``condition_eval`` all already
    understand ``None`` to mean.
    """
    for var_def, _ in TABLE:
        assert _runtime(var_def) is None, var_def


def test_integer_min_rounds_up_for_a_consumer_that_needs_a_value():
    """A fractional ``min`` on an integer var rounds UP.

    Truncating would produce a value BELOW the minimum its own driver declares —
    the simulator did not, and the validator passed the authored 0.5 through
    untouched and then warned that the simulator's 1 didn't round-trip.
    """
    var_def = {"type": "integer", "min": 0.5}
    assert _simulator(var_def) == 1
    assert _simulator_child(var_def) == 1
    assert _validator(var_def) == 1

    negative = {"type": "integer", "min": -2.5}
    assert math.ceil(-2.5) == -2
    for reader in (_simulator, _simulator_child, _validator):
        assert reader(negative) == -2


def test_unusable_min_falls_back_to_zero_without_crashing():
    """A hand-edited driver with ``min: "low"`` is an authoring bug, not a
    reason to take a device's whole state down at instantiation."""
    bad_int = {"type": "integer", "min": "low"}
    bad_num = {"type": "number", "min": "low"}
    for reader in (_simulator, _simulator_child, _validator):
        assert reader(bad_int) == 0
        assert reader(bad_num) == 0.0
    assert _runtime(bad_int) is None
    assert _runtime(bad_num) is None


def test_boolean_min_is_not_a_number():
    """``min: true`` must not produce 1 — float(True) is 1.0 and would look
    like a deliberate starting value."""
    assert state_var_default({"type": "integer", "min": True}) == 0
    assert state_var_default({"type": "number", "min": True}) == 0.0


def test_float_is_a_number_alias_for_the_simulator_too():
    """``float`` is an accepted alias for ``number`` in the driver loader and
    the schema. The simulator used to seed it with '' — a string where every
    consumer expects a number — because only ``number`` was spelled in its
    branch."""
    assert _simulator({"type": "float"}) == 0.0
    assert _simulator_child({"type": "float"}) == 0.0
    assert _validator({"type": "float"}) == 0.0
