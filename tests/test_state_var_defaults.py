"""The default value of a declared state variable, pinned across all three
consumers.

"What does a state variable hold before anything is read from the device"
used to be answered in four places under three different rules: the driver
runtime, the simulator's per-device seeding, the simulator's per-child
seeding, and the validator — whose comment claimed to mirror the simulator
and did not. The drift was latent rather than live (no shipped driver
declares a fractional integer ``min``), and the failure mode when it fires is
a validator warning on a correct driver, which is exactly what teaches an
author to stop reading warnings.

There is one rule now. This test is the table that made the case, run against
all three callers, so a fifth spelling cannot appear quietly.

The runtime/simulator difference in the ``number`` column is deliberate and
is NOT drift: a driver publishes logical values (a fader declaring min -80
starts at -80.0), while a simulator seeds the wire value it will put on the
socket. Those are different namespaces, and the driver's response rules map
between them.
"""

import math

from server.drivers.base import BaseDriver
from server.drivers.compiled_protocol import state_var_default
from simulator.validate import _default_for_type
from simulator.yaml_auto import YAMLAutoSimulator


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


# (declared var, runtime, simulator) — the validator must always match the
# simulator, and the simulator's two seeding paths must always match each
# other, so those are asserted rather than tabulated.
TABLE = [
    ({"type": "number", "min": -12}, -12.0, 0.0),
    ({"type": "number", "min": -80.0}, -80.0, 0.0),
    ({"type": "number"}, 0.0, 0.0),
    ({"type": "float", "min": -3.5}, -3.5, 0.0),
    ({"type": "integer", "min": 0}, 0, 0),
    ({"type": "integer", "min": 5}, 5, 5),
    ({"type": "integer"}, 0, 0),
    ({"type": "boolean"}, False, False),
    ({"type": "boolean", "min": 1}, False, False),
    ({"type": "enum", "values": ["off", "on"]}, "off", "off"),
    ({"type": "enum", "values": []}, "", ""),
    ({"type": "string"}, "", ""),
    ({}, "", ""),
]


def test_default_table():
    for var_def, expected_runtime, expected_sim in TABLE:
        assert _runtime(var_def) == expected_runtime, var_def
        assert _simulator(var_def) == expected_sim, var_def
        assert _simulator_child(var_def) == expected_sim, var_def
        assert _validator(var_def) == expected_sim, var_def


def test_integer_min_rounds_up_everywhere():
    """A fractional ``min`` on an integer var rounds UP, in every consumer.

    Truncating would seed the variable BELOW the minimum its own driver
    declares — the runtime used to, the simulator did not, and the validator
    passed the authored 0.5 through untouched and then warned that the
    simulator's 1 didn't round-trip.
    """
    var_def = {"type": "integer", "min": 0.5}
    assert _runtime(var_def) == 1
    assert _simulator(var_def) == 1
    assert _simulator_child(var_def) == 1
    assert _validator(var_def) == 1

    negative = {"type": "integer", "min": -2.5}
    assert math.ceil(-2.5) == -2
    for reader in (_runtime, _simulator, _simulator_child, _validator):
        assert reader(negative) == -2


def test_unusable_min_falls_back_to_zero_without_crashing():
    """A hand-edited driver with ``min: "low"`` is an authoring bug, not a
    reason to take a device's whole state down at instantiation."""
    bad_int = {"type": "integer", "min": "low"}
    bad_num = {"type": "number", "min": "low"}
    for reader in (_runtime, _simulator, _simulator_child, _validator):
        assert reader(bad_int) == 0
        assert reader(bad_num) == 0.0


def test_boolean_min_is_not_a_number():
    """``min: true`` must not seed 1 — float(True) is 1.0 and would look
    like a deliberate starting value."""
    assert state_var_default({"type": "integer", "min": True}) == 0
    assert state_var_default({"type": "number", "min": True},
                             number_from_min=True) == 0.0


def test_float_is_a_number_alias_for_the_simulator_too():
    """``float`` is an accepted alias for ``number`` in the driver loader and
    the schema. The simulator used to seed it with '' — a string where every
    consumer expects a number — because only ``number`` was spelled in its
    branch."""
    assert _simulator({"type": "float"}) == 0.0
    assert _simulator_child({"type": "float"}) == 0.0
    assert _validator({"type": "float"}) == 0.0
    assert _runtime({"type": "float", "min": 2}) == 2.0
