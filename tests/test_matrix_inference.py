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

import json

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


def test_a_device_routed_by_one_vocabulary_and_reporting_another_gets_both():
    """The device says "Mic"; the command takes "0". Both are already declared.

    This used to be two warnings -- "value 0 reads as nothing routed" and "not
    the same vocabulary" -- because a source entry held ONE value and there was
    nowhere to put the second. There is now, and pairing them by label is the
    whole fix: send "0", match on "Mic".
    """
    found = _by_id(propose_matrices("dsp", INVERTED_PROCESSOR))
    proposal = found["input.source"]
    assert [(s["value"], s.get("report_value")) for s in proposal["sources"]] == [
        ("0", "Mic"), ("1", "Line"), ("2", "USB"),
    ]
    assert not any("value 0" in w for w in proposal["warnings"])
    assert not any("not the same vocabulary" in w for w in proposal["warnings"])


def test_a_vocabulary_that_does_not_line_up_is_still_flagged_rather_than_guessed():
    """Pairing is all-or-nothing: a partial map is a guess about which end is wrong."""
    driver = json.loads(json.dumps(INVERTED_PROCESSOR))
    driver["child_entity_types"]["input"]["state_variables"]["source"]["values"] = [
        "Microphone", "Line", "USB",
    ]
    proposal = _by_id(propose_matrices("dsp", driver))["input.source"]
    assert all(s.get("report_value") is None for s in proposal["sources"])
    assert any("not the same vocabulary" in w for w in proposal["warnings"])
    assert any("value 0" in w for w in proposal["warnings"])


def test_a_device_that_reports_what_it_accepts_gets_no_second_value():
    """One value is enough for every other driver in the corpus, and stays enough."""
    driver = json.loads(json.dumps(INVERTED_PROCESSOR))
    driver["commands"]["set_input_source"]["params"]["source"]["values"] = [
        "Mic", "Line", "USB",
    ]
    proposal = _by_id(propose_matrices("dsp", driver))["input.source"]
    assert all(s.get("report_value") is None for s in proposal["sources"])


def test_a_command_that_copies_settings_between_channels_proposes_nothing():
    """Anchoring on 'which child reports what is routed to it' is what excludes it."""
    assert propose_matrices("dsp", FALSE_POSITIVE) == []


def test_a_driver_that_declares_no_routing_proposes_nothing():
    assert propose_matrices("x", {"commands": {"power_on": {}}}) == []
    assert propose_matrices("x", None) == []


#: One declarative driver covering a whole family of frames: how many ports THIS
#: unit has is a field on the device, not a fact about the driver. The protocol
#: reaches 128 either way, so a driver that only reads its id_format offers a
#: 128x128 for a frame somebody has already told it is a 4x4.
CONFIGURED_FRAME = {
    "config_schema": {
        "input_count": {"type": "integer", "default": 0, "label": "Input Count"},
        "output_count": {"type": "integer", "default": 0, "label": "Output Count"},
    },
    "child_entity_types": {
        "output": {
            "label": "Output", "label_plural": "Outputs",
            "id_format": {"type": "integer", "min": 1, "max": 128},
            "instances": {"count_from": "output_count", "label": "Output {id}"},
            "state_variables": {"input": {"type": "integer", "min": 1, "max": 128}},
        },
        "input": {
            "label": "Input", "label_plural": "Inputs",
            "id_format": {"type": "integer", "min": 1, "max": 128},
            "instances": {"count_from": "input_count", "label": "Input {id}"},
            "state_variables": {"name": {"type": "string"}},
        },
    },
    "commands": {
        "route": {"params": {
            "output": {"type": "child_id", "child_type": "output"},
            "input": {"type": "child_id", "child_type": "input"},
        }},
    },
}


# --- Reading the live device rather than its declaration -------------------


def test_a_roster_sized_from_config_is_what_gets_offered():
    """8x4 because the device says 8x4, without it being connected.

    The platform already resolves a roster this way for the bound-child-id
    check. Reading id_format instead offered 128 sources and 128 destinations,
    every one ticked, and Apply wrote a real 128x128.
    """
    config = {"input_count": 8, "output_count": 4}
    proposal = _by_id(propose_matrices("mx", CONFIGURED_FRAME, None, config))["output.input"]

    assert [d["value"] for d in proposal["destinations"]] == [1, 2, 3, 4]
    assert [s["value"] for s in proposal["sources"]] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert proposal["destinations"][0]["route_key"] == "device.mx.output.1.input"
    # Nothing to warn about: this is the size the device was configured for,
    # which is a different thing from the size it could be.
    assert proposal["warnings"] == []
    # Still not the live roster, and it does not claim to be.
    assert proposal["from_roster"] is False


def test_a_sparse_roster_from_config_keeps_the_ids_it_was_given():
    """Ports 1, 4 and 9 of a frame, because that is what is patched."""
    driver = json.loads(json.dumps(CONFIGURED_FRAME))
    driver["child_entity_types"]["output"]["instances"] = {"ids_from": "output_ids"}
    proposal = _by_id(
        propose_matrices("mx", driver, None, {"output_ids": "1, 4, 9", "input_count": 2}),
    )["output.input"]
    assert [d["value"] for d in proposal["destinations"]] == [1, 4, 9]
    assert proposal["warnings"] == []


def test_an_unfilled_count_field_is_named_instead_of_the_cable():
    """The remedy is a number in this device's settings, not a connection.

    With the field left at its default there are no ports and nothing on the
    wire will ever produce one, so "connect it and press Re-read device" sends
    the author to check a cable that is already plugged in.
    """
    proposal = _by_id(propose_matrices("mx", CONFIGURED_FRAME, None, {}))["output.input"]
    assert len(proposal["destinations"]) == 128
    (warning,) = [w for w in proposal["warnings"] if "ports this driver can have" in w]
    # Named as the Settings form spells it, not as the YAML does.
    assert "Set 'Output Count' on this device" in warning
    assert "Connect it" not in warning


def test_a_driver_that_declares_no_roster_still_says_connect_it():
    """The max fallback and its original advice, for a driver with no field to set."""
    proposal = _by_id(propose_matrices("mx", TYPED_FRAME))["output.input"]
    (warning,) = [w for w in proposal["warnings"] if "ports this driver can have" in w]
    assert "Connect it and press Re-read device" in warning


def test_registered_children_still_win_over_a_configured_roster():
    """Two ports patched on a frame configured for four is a two-row list."""
    roster = {"output": [{"local_id": 2, "local_id_padded": "2", "label": "Main LCD"}]}
    proposal = _by_id(propose_matrices(
        "mx", CONFIGURED_FRAME, roster, {"input_count": 8, "output_count": 4},
    ))["output.input"]
    assert [d["value"] for d in proposal["destinations"]] == [2]
    assert proposal["from_roster"] is True
    assert proposal["warnings"] == []


def test_a_roster_the_device_resizes_is_never_predicted_from_config():
    """`count_from_state` means the hardware settles it, so the config is a floor.

    Offering the config number as though it were the answer would be a guess
    dressed as a reading -- the same mistake in the other direction.
    """
    driver = json.loads(json.dumps(CONFIGURED_FRAME))
    driver["child_entity_types"]["output"]["instances"]["count_from_state"] = "num_outputs"
    proposal = _by_id(propose_matrices(
        "mx", driver, None, {"input_count": 8, "output_count": 4},
    ))["output.input"]
    assert len(proposal["destinations"]) == 128
    assert any("ports this driver can have" in w for w in proposal["warnings"])


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


def test_a_port_the_device_names_gets_no_invented_caption():
    """Because an invented one would outrank the name, once it arrives.

    A caption is a stored name and a stored name is what the panel draws first
    (`_entryLabel`), so "Sink 1" written here would sit on a tile for good and
    the endpoint's real name -- typed into the rack an hour later -- would never
    show. The renderer captions an unnamed row instead.
    """
    driver = {
        **MULTI_PLANE,
        "child_entity_types": {
            **MULTI_PLANE["child_entity_types"],
            "sink": {
                **MULTI_PLANE["child_entity_types"]["sink"],
                "state_variables": {
                    **MULTI_PLANE["child_entity_types"]["sink"]["state_variables"],
                    "name": {"type": "string"},
                },
            },
        },
    }
    roster = {"sink": [{"local_id": 1, "local_id_padded": "1", "label": ""}]}
    (dest,) = _by_id(propose_matrices("mx", driver, roster))[
        "sink.source_video"]["destinations"]
    assert "label" not in dest
    assert dest["label_key"] == "device.mx.sink.1.name"


def test_a_port_the_device_cannot_name_still_gets_one():
    """The other half: no live key, so a caption here is the only one there is."""
    roster = {"output": [{"local_id": 3, "local_id_padded": "3", "label": ""}]}
    (dest,) = _by_id(propose_matrices("mx", TYPED_FRAME, roster))[
        "output.input"]["destinations"]
    assert dest["label"] == "Output 1"
    assert "label_key" not in dest


def test_a_port_the_device_is_not_reaching_is_flagged_and_said_out_loud():
    """A roster can list a port that is gone.

    An MXNet CBOX keeps an endpoint in its database after it leaves the rack and
    then refuses every route to it in its own words. Offered here it looked
    exactly like the ones that are plugged in, so it got ticked, and the
    destination it drew could never route.
    """
    roster = {
        "sink": [
            {"local_id": 1, "local_id_padded": "1", "label": "Lobby", "online": True},
            {"local_id": 2, "local_id_padded": "2", "label": "Boardroom",
             "online": False},
        ],
        "encoder": [{"local_id": 1, "local_id_padded": "1", "label": "Apple TV",
                     "online": True}],
    }
    proposal = _by_id(propose_matrices("mx", MULTI_PLANE, roster))["sink.source_video"]
    assert [d.get("offline") for d in proposal["destinations"]] == [None, True]
    assert any("Not answering right now: Boardroom" in w for w in proposal["warnings"])


def test_a_roster_that_says_nothing_about_presence_flags_nothing():
    """`online` absent means nobody asked -- a declared or ranged roster."""
    proposal = _by_id(propose_matrices("mx", MULTI_PLANE))["sink.source_video"]
    assert all("offline" not in d for d in proposal["destinations"])
    assert not any("Not answering" in w for w in proposal["warnings"])


def test_a_proposal_names_its_command_the_way_the_rest_of_the_ide_does():
    """`command` is a wire id; the author sees the label everywhere else."""
    driver = {
        **TYPED_FRAME,
        "commands": {
            **TYPED_FRAME["commands"],
            "route": {**TYPED_FRAME["commands"]["route"],
                      "label": "Route Source to Display"},
        },
    }
    proposal = _by_id(propose_matrices("mx", driver))["output.input"]
    assert proposal["command"] == "route"
    assert proposal["command_label"] == "Route Source to Display"
    assert "Route Source to Display" in proposal["why"]
    # And the wire names of the two ends are NOT in it: an integrator cannot act
    # on which parameter is which, and for a declared driver there is nothing
    # there for them to confirm.
    assert "'output'" not in proposal["why"]
    assert "'input'" not in proposal["why"]


def test_a_command_with_no_label_falls_back_to_its_own_name():
    proposal = _by_id(propose_matrices("mx", TYPED_FRAME))["output.input"]
    assert proposal["command_label"] == "route"


def test_the_audio_plane_is_paired_when_audio_has_its_own_command():
    """Audio-follow-video needs a second action list, and it is already here.

    The Builder's answer used to be a warning telling the author to hand-author
    it in the Bindings tab -- for a command the picker was holding.
    """
    found = _by_id(propose_matrices("mx", TYPED_FRAME))
    assert found["output.input"]["audio_plane_id"] == "output.audio_input"
    # And the audio plane does not follow itself.
    assert found["output.audio_input"]["audio_plane_id"] is None


def test_one_command_covering_every_plane_is_not_paired():
    """It needs a combined value on its own plane parameter, not two sends.

    Every one-command multi-plane driver in the corpus accepts one (MXNet
    `stream: all`, Chazy and Darwin `signal: ALL`), so firing the same command
    twice would be the wrong answer offered in place of the right one.
    """
    found = _by_id(propose_matrices("mx", MULTI_PLANE))
    assert all(p["audio_plane_id"] is None for p in found.values())


def test_two_planes_on_one_property_answer_to_different_ids():
    """A combined mode watches the same property as the plane it contains.

    The picker keys its options by id and finds the chosen one by it, so a
    collision means picking "Video" and silently getting "All streams".
    """
    driver = {
        **MULTI_PLANE,
        "routing": {
            "destination_child_type": "sink", "source_child_type": "encoder",
            "command": "route",
            "planes": [
                {"label": "All streams", "route_property": "source_video",
                 "params": {"stream": "ALL"}},
                {"label": "Video", "route_property": "source_video",
                 "params": {"stream": "VIDEO"}},
            ],
        },
    }
    proposals = propose_matrices("mx", driver)
    assert [p["id"] for p in proposals] == [
        "sink.source_video", "sink.source_video.sinks_video",
    ]
    assert len({p["id"] for p in proposals}) == 2


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
