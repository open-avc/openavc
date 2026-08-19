"""What the auto-generated simulator offers a person to click.

A driver declares what its values ARE; the simulator has to turn that into
something usable. Two things it was getting wrong, both invisible until a
driver with real ranges arrived:

  * every ``number`` got a 0.1 step, so a 0-5000 W reading became a slider with
    50,000 stops -- unplaceable, and a write per pixel on the way past;
  * a child's properties were sent as bare names, so a boolean mute arrived
    untyped and drew as a text box you typed "true" into.
"""

import pytest

from openavc.simulator.yaml_auto import YAMLAutoSimulator

MAX_STOPS = 500


def stops(control):
    return (control["max"] - control["min"]) / control["step"]


def build(state_variables=None, child_entity_types=None):
    definition = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": state_variables or {},
    }
    if child_entity_types:
        definition["child_entity_types"] = child_entity_types
    return YAMLAutoSimulator("acme_widget", config={}, driver_def=definition)


def controls_for(state_variables):
    return {c.get("key"): c for c in build(state_variables).SIMULATOR_INFO["controls"]}


# ── Slider resolution ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "v_min,v_max,expected_step",
    [
        (0.0, 300.0, 1),        # line voltage: volts, not tenths of a volt
        (0.0, 5000.0, 10),      # power draw: was 50,000 stops
        (0.0, 100.0, 0.2),      # current
        (-80.0, 0.0, 0.2),      # dBFS threshold
        (0.0, 1.0, 0.01),       # a small range keeps fine resolution
        (0.0, 10.0, 0.02),
    ],
)
def test_an_invented_step_is_one_a_person_would_have_picked(v_min, v_max, expected_step):
    control = controls_for({
        "reading": {"type": "number", "min": v_min, "max": v_max},
    })["reading"]
    assert control["step"] == pytest.approx(expected_step)
    assert stops(control) <= MAX_STOPS


def test_a_declared_step_is_the_authors_call_and_is_left_alone():
    """Even a fine one. The driver knows what the device resolves to."""
    control = controls_for({
        "gain": {"type": "number", "min": -80.0, "max": 10.0, "step": 0.1},
    })["gain"]
    assert control["step"] == 0.1
    # And this is deliberately allowed to exceed the cap.
    assert stops(control) > MAX_STOPS


def test_integer_sliders_never_get_a_fractional_step():
    control = controls_for({
        "tone_hz": {"type": "integer", "min": 20, "max": 20000},
    })["tone_hz"]
    assert isinstance(control["step"], int)
    assert control["step"] >= 1
    assert stops(control) <= MAX_STOPS


def test_a_small_integer_range_still_steps_by_one():
    control = controls_for({
        "wait_min": {"type": "integer", "min": 1, "max": 240},
    })["wait_min"]
    assert control["step"] == 1


def test_a_degenerate_range_does_not_divide_by_zero():
    control = controls_for({"stuck": {"type": "number", "min": 5.0, "max": 5.0}})["stuck"]
    assert control["step"] > 0


def test_the_step_is_free_of_binary_float_dust():
    """0.05 must not arrive as 0.05000000000000001 -- it reaches an input's
    `step` attribute, and the readout's precision is derived from its text."""
    for v_max in (10.0, 20.0, 50.0, 200.0):
        step = controls_for({"r": {"type": "number", "min": 0.0, "max": v_max}})["r"]["step"]
        assert len(str(float(step)).split(".")[-1]) <= 4, (v_max, step)


def test_a_variable_with_no_range_is_still_a_readout_not_a_slider():
    control = controls_for({"uptime": {"type": "number"}})["uptime"]
    assert control["type"] == "indicator"


# ── Child property types ─────────────────────────────────────────────────


CHILD_TYPES = {
    "channel": {
        "label": "Channel",
        "id_format": {"type": "integer", "min": 1, "max": 4},
        "instances": {"count": 4},
        "state_variables": {
            "mute": {"type": "boolean", "label": "Mute", "control": True},
            "fader": {"type": "number", "label": "Output Level", "min": -80.0,
                      "max": 0.0, "step": 0.5, "unit": "dB", "control": True},
            "name": {"type": "string", "label": "Channel Name"},
            "mode": {"type": "enum", "label": "Output Mode",
                     "values": ["HiZ-70V", "LoZ"]},
        },
    },
}


def child_info():
    # to_info_dict, not SIMULATOR_INFO: the roster is per-instance (it depends
    # on this device's config), so it is built where the UI asks for it.
    sim = build(child_entity_types=CHILD_TYPES)
    return sim.to_info_dict()["children"]["channel"]


def test_each_child_property_carries_the_type_the_driver_declared():
    defs = child_info()["prop_defs"]
    assert defs["mute"]["type"] == "boolean"
    assert defs["name"]["type"] == "string"
    assert defs["mode"]["type"] == "enum"
    assert defs["mode"]["values"] == ["HiZ-70V", "LoZ"]


def test_a_numeric_child_property_carries_its_range_so_it_can_be_a_fader():
    fader = child_info()["prop_defs"]["fader"]
    assert (fader["min"], fader["max"], fader["step"]) == (-80.0, 0.0, 0.5)
    assert fader["unit"] == "dB"
    assert fader["label"] == "Output Level"


def test_absent_facts_are_omitted_rather_than_sent_as_null():
    """The UI treats a missing bound as "not bounded". A null would have to be
    special-cased at every reader instead."""
    name = child_info()["prop_defs"]["name"]
    assert set(name) == {"type", "label"}


def test_the_plain_name_list_is_still_sent():
    """The device panel uses it to tell child state from device state, so
    adding the types must not take it away."""
    info = child_info()
    assert info["props"] == list(info["prop_defs"])
    assert "mute" in info["props"]
