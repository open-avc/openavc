"""Reading a matrix out of what a driver declares, instead of typing it again.

Every driver here is invented, and every SHAPE is one the shipped corpus forced
(matrix plan §2.1a): parameters typed to a child and parameters that are plain
integers, a routed-source property called ``input`` and one called ``source``,
six routing planes on one child entity, a plane chosen by a command parameter
rather than by a second command, sources that are an enum rather than a port
list, and a processor whose destinations are children called ``input``.

The point of every case is the same: the guess must land on the RIGHT command
and the RIGHT plane, because the failure mode is not an error -- it is a matrix
that routes video when the author asked for audio.
"""

from __future__ import annotations

from openavc.ui.matrix_inference import propose_matrices

# --- Invented drivers, one per shape the corpus forced ----------------------

#: A frame whose route command names both ends by child type. The easy case,
#: and the one the picker must get exactly right.
TYPED_FRAME = {
    "child_entity_types": {
        "output": {
            "label": "Output", "label_plural": "Outputs",
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {
                "input": {"type": "integer", "label": "Routed Input", "min": 1, "max": 4},
                "audio_input": {"type": "integer", "label": "Ex-Audio Input"},
                "signal": {"type": "boolean", "label": "Sync"},
            },
        },
        "input": {
            "label": "Input", "label_plural": "Inputs",
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {"name": {"type": "string"}, "signal": {"type": "boolean"}},
        },
    },
    "commands": {
        "route": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "child_id", "child_type": "input"},
        }},
        "audio_route": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "child_id", "child_type": "input"},
        }},
        "copy_output_edid": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "child_id", "child_type": "input"},
        }},
        "set_volume": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "level": {"type": "integer"},
        }},
    },
}

#: Half the corpus declares its route parameters as plain integers, so nothing
#: says which child type either end belongs to. It is still inferable, by name.
UNTYPED_FRAME = {
    "child_entity_types": {
        "output": {
            "label": "Output", "label_plural": "Outputs",
            "id_format": {"type": "integer", "min": 1, "max": 3},
            "state_variables": {"input": {"type": "integer", "min": 1, "max": 3}},
        },
    },
    "commands": {
        "route": {"params": {"output": {"type": "integer"}, "input": {"type": "integer"}}},
    },
}

#: One child entity carrying several independent routing planes, switched by a
#: parameter on a single command. Without the plane parameter, all six planes
#: would produce the same matrix and route video six times.
MULTI_PLANE = {
    "child_entity_types": {
        "sink": {
            "label": "Sink", "label_plural": "Sinks",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "source_video": {"type": "integer", "label": "Video Source"},
                "source_audio": {"type": "integer", "label": "Audio Source"},
                "source_usb": {"type": "integer", "label": "USB Source"},
            },
        },
        "encoder": {
            "label": "Encoder", "label_plural": "Encoders",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"online": {"type": "boolean"}},
        },
    },
    "commands": {
        "route": {"params": {
            "sink_id": {"type": "child_id", "child_type": "sink"},
            "encoder_id": {"type": "child_id", "child_type": "encoder"},
            "stream": {"type": "enum", "required": True,
                       "values": ["ALL", "VIDEO", "AUDIO", "USB"]},
        }},
    },
}

#: A processor whose destinations are children called `input`, routed from a
#: fixed enum rather than from a port list. Every naive rule gets this
#: backwards, and it is the reason nothing is applied without a person looking.
INVERTED_PROCESSOR = {
    "child_entity_types": {
        "input": {
            "label": "Channel", "label_plural": "Channels",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "source": {"type": "enum", "values": ["Mic", "Line", "USB"]},
                "level": {"type": "integer", "min": 0, "max": 100},
            },
        },
    },
    "commands": {
        "set_input_level": {"params": {
            "channel": {"type": "child_id", "child_type": "input"},
            "level": {"type": "integer"},
        }},
        "set_input_source": {"params": {
            "channel": {"type": "child_id", "child_type": "input"},
            "source": {"type": "enum", "label": "Source", "values": [
                {"value": "0", "label": "Mic"},
                {"value": "1", "label": "Line"},
                {"value": "2", "label": "USB"},
            ]},
        }},
    },
}

#: A driver with a command that takes a source and a destination and does not
#: route: it copies settings between channels. It passes every parameter-shaped
#: test there is, and nothing on it reports what is routed anywhere.
FALSE_POSITIVE = {
    "child_entity_types": {
        "channel": {
            "label": "Channel",
            "id_format": {"type": "integer", "min": 1, "max": 4},
            "state_variables": {"gain": {"type": "integer"}, "mute": {"type": "boolean"}},
        },
    },
    "commands": {
        "eq_copy": {"params": {
            "source": {"type": "child_id", "child_type": "channel"},
            "dest": {"type": "child_id", "child_type": "channel"},
        }},
    },
}


def _by_id(proposals: list[dict]) -> dict[str, dict]:
    return {p["id"]: p for p in proposals}


# --- What it finds ---------------------------------------------------------


def test_a_frame_proposes_one_matrix_per_routing_plane():
    """Two properties reporting a routed source are two matrices, not one."""
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert set(found) == {"output.input", "output.audio_input"}


def test_each_plane_gets_its_own_command_not_the_first_one_that_fits():
    """The failure this scoring exists for: routing video where audio was asked.

    Both commands take the same two child ids, so structure alone cannot tell
    them apart -- and picking the first would send every ex-audio route down
    the video command.
    """
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["command"] == "route"
    assert found["output.audio_input"]["command"] == "audio_route"


def test_a_command_that_takes_both_ends_and_does_not_route_is_not_chosen():
    """copy_output_edid takes an output and an input, and copies an EDID."""
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["command"] != "copy_output_edid"


def test_each_destination_gets_its_own_route_key():
    """The whole of 0.10.0: a destination owns its key, there is no pattern."""
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    keys = [d["route_key"] for d in found["output.input"]["destinations"]]
    assert keys == [f"device.mx.output.{i}.input" for i in (1, 2, 3, 4)]


def test_the_audio_plane_watches_the_audio_property():
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    keys = [d["route_key"] for d in found["output.audio_input"]["destinations"]]
    assert keys[0] == "device.mx.output.1.audio_input"


def test_sources_come_from_the_child_type_the_command_names():
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert [s["value"] for s in found["output.input"]["sources"]] == [1, 2, 3, 4]


def test_a_declared_name_property_becomes_a_live_label_key():
    """A port the device names is worth binding; the platform `label` is not --
    that one is the project's own name, which the author is editing here."""
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["sources"][0]["label_key"] == "device.mx.input.1.name"
    assert "label_key" not in found["output.input"]["destinations"][0]


def test_the_route_action_is_built_ready_to_bind():
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["route"] == [{
        "action": "device.command", "device": "mx", "command": "route",
        "params": {"output": "$output", "input": "$input"},
    }]


# --- The shapes that break a naive rule ------------------------------------


def test_plain_integer_parameters_are_still_inferable_but_less_certain():
    """Half the corpus declares no child type on either end of its route."""
    found = _by_id(propose_matrices("mx", UNTYPED_FRAME))
    assert found["output.input"]["command"] == "route"
    assert found["output.input"]["confidence"] == "medium"


def test_a_typed_frame_is_high_confidence():
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["confidence"] == "high"


def test_six_planes_on_one_child_each_send_their_own_plane():
    """One command, one plane parameter. Without the fill, every plane routes video."""
    found = _by_id(propose_matrices("mx", MULTI_PLANE))
    assert set(found) == {"sink.source_video", "sink.source_audio", "sink.source_usb"}
    assert found["sink.source_video"]["route"][0]["params"]["stream"] == "VIDEO"
    assert found["sink.source_audio"]["route"][0]["params"]["stream"] == "AUDIO"
    assert found["sink.source_usb"]["route"][0]["params"]["stream"] == "USB"


def test_a_plane_nobody_can_fill_in_is_named_rather_than_left_to_fail():
    """A required parameter with nothing to say is a command the device refuses."""
    driver = {
        "child_entity_types": {"sink": {
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"source": {"type": "integer"}},
        }},
        "commands": {"route": {"params": {
            "sink": {"type": "child_id", "child_type": "sink"},
            "input": {"type": "integer"},
            "signal": {"type": "enum", "required": True, "values": ["A", "B"]},
        }}},
    }
    warnings = propose_matrices("mx", driver)[0]["warnings"]
    assert any("'signal'" in w and "refused" in w for w in warnings)


def test_a_processor_routes_from_an_enum_and_says_which_way_round_it_is():
    """Destinations called `input` are correct here and wrong on a switcher."""
    found = _by_id(propose_matrices("dsp", INVERTED_PROCESSOR))
    proposal = found["input.source"]
    assert proposal["command"] == "set_input_source"
    assert [s["label"] for s in proposal["sources"]] == ["Mic", "Line", "USB"]
    assert any("route TO" in w for w in proposal["warnings"])


def test_a_source_value_of_zero_is_flagged_because_a_panel_reads_it_as_unrouted():
    """0 means "nothing is routed" to every renderer, so that crosspoint is dead."""
    found = _by_id(propose_matrices("dsp", INVERTED_PROCESSOR))
    assert any("value 0" in w for w in found["input.source"]["warnings"])


def test_a_reported_vocabulary_that_differs_from_the_commanded_one_is_flagged():
    """The device says "Mic"; the command takes "0". Routing works, feedback does not."""
    found = _by_id(propose_matrices("dsp", INVERTED_PROCESSOR))
    assert any("not the same vocabulary" in w for w in found["input.source"]["warnings"])


def test_a_command_that_copies_settings_between_channels_proposes_nothing():
    """Anchoring on 'which child reports what is routed to it' is what excludes it."""
    assert propose_matrices("dsp", FALSE_POSITIVE) == []


def test_a_driver_that_declares_no_routing_proposes_nothing():
    assert propose_matrices("x", {"commands": {"power_on": {}}}) == []
    assert propose_matrices("x", None) == []


# --- Reading the live device rather than its declaration -------------------


def test_registered_children_win_over_the_declared_range():
    """A 1..4 frame with two ports patched is a two-row list, not a 4x4."""
    roster = {
        "output": [
            {"local_id": 2, "local_id_padded": "2", "label": "Main LCD"},
            {"local_id": 7, "local_id_padded": "7", "label": ""},
        ],
        "input": [{"local_id": 3, "local_id_padded": "3", "label": "Laptop"}],
    }
    found = _by_id(propose_matrices("mx", TYPED_FRAME, roster))
    destinations = found["output.input"]["destinations"]
    assert [d["value"] for d in destinations] == [2, 7]
    assert destinations[0]["label"] == "Main LCD"
    # An unnamed port still gets a caption, from what its type is called.
    assert destinations[1]["label"] == "Output 2"
    assert destinations[1]["route_key"] == "device.mx.output.7.input"


def test_a_string_keyed_port_set_cannot_be_guessed_and_says_so():
    """Names come from the device. Inventing them is worse than an empty list."""
    driver = {
        "child_entity_types": {"output": {
            "id_format": {"type": "string"},
            "state_variables": {"input": {"type": "string"}},
        }},
        "commands": {"route": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "string"},
        }}},
    }
    proposal = propose_matrices("mx", driver)[0]
    assert proposal["destinations"] == []
    assert any("has not told the system which ports" in w for w in proposal["warnings"])


def test_padded_child_ids_reach_the_route_key_and_the_plain_id_reaches_the_value():
    """The key is addressed by the padded id; the command takes the plain one."""
    roster = {"output": [{"local_id": 3, "local_id_padded": "003", "label": ""}]}
    found = _by_id(propose_matrices("mx", TYPED_FRAME, roster))
    destination = found["output.input"]["destinations"][0]
    assert destination["value"] == 3
    assert destination["route_key"] == "device.mx.output.003.input"


def test_the_most_confident_proposal_comes_first():
    found = propose_matrices("mx", {**TYPED_FRAME, **{"commands": {
        "route": {"params": {"output": {"type": "integer"}, "input": {"type": "integer"}}},
        "audio_route": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "child_id", "child_type": "input"},
        }},
    }}})
    assert found[0]["id"] == "output.audio_input"
    assert found[0]["confidence"] == "high"
    assert found[1]["confidence"] == "medium"


def test_a_declared_range_standing_in_for_a_roster_says_so():
    """The difference between "a 16x16 frame" and "the four outputs this unit has".

    A configured but unconnected switcher registers no children, so the list is
    the widest thing the driver can be -- which draws a perfectly convincing
    16x16 for a 4x4 sitting on the bench, and said nothing about it.
    """
    proposal = _by_id(propose_matrices("mx", TYPED_FRAME))["output.input"]
    assert proposal["from_roster"] is False
    assert any("ports this driver can have" in w for w in proposal["warnings"])

    roster = {"output": [{"local_id": 1, "local_id_padded": "1", "label": "Main"}]}
    live = _by_id(propose_matrices("mx", TYPED_FRAME, roster))["output.input"]
    assert live["from_roster"] is True
    assert live["warnings"] == []


def test_the_main_plane_is_offered_before_the_side_ones():
    """A frame's video route, not its extracted-audio matrix.

    Alphabetical order put `audio_input` first, so the picker opened on the
    plane almost nobody wants and the one everybody came for was one dropdown
    away.
    """
    assert [p["id"] for p in propose_matrices("mx", TYPED_FRAME)] == [
        "output.input", "output.audio_input",
    ]
    assert [p["route_property"] for p in propose_matrices("mx", MULTI_PLANE)] == [
        "source_video", "source_audio", "source_usb",
    ]
