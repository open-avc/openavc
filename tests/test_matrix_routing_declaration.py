"""A driver saying where its own routing lives, instead of being guessed at.

Every shape here is one the shipped corpus forced (matrix plan §2.1a and the
Phase 5 sweep), and every case is a place the guess is WRONG rather than merely
absent -- which is the bar for declaring anything at all:

* a routing command needing a fixed extra parameter no property name supplies,
  so the guessed action goes out short a required argument and is refused;
* a device that routes itself and has no destination child, which the guess
  cannot see at all;
* properties that merely read like routing -- a clip indicator, a priority
  mode, a list of aliases -- which propose matrices whose crosspoints mean
  nothing, and which only the driver can rule out.

The declaration REPLACES the guess. That is most of its value, and it is why
these tests care as much about what stops being proposed as about what starts.
"""

from __future__ import annotations

from openavc.drivers.avcdriver_semantic import routing_block_errors
from openavc.ui.matrix_inference import propose_matrices

# --- Invented drivers, one per shape the corpus forced ----------------------

#: One command covering several planes, told which by a parameter whose value
#: is nowhere in the property name. The guess fills that parameter in from the
#: property (`source_video` -> VIDEO) and cannot when the property is a plain
#: `source`, so the route it proposes is missing a required argument.
UNNAMEABLE_PLANE = {
    "child_entity_types": {
        "sink": {
            "label": "Sink", "label_plural": "Sinks",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"source": {"type": "integer", "label": "Source"}},
        },
        "encoder": {
            "label": "Encoder", "label_plural": "Encoders",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {"online": {"type": "boolean"}},
        },
    },
    "commands": {
        "switch": {"params": {
            "sink_id": {"type": "child_id", "child_type": "sink"},
            "encoder_id": {"type": "child_id", "child_type": "encoder"},
            "signal": {"type": "enum", "required": True,
                       "values": ["ALL", "VIDEO", "IR"]},
        }},
    },
}

#: An endpoint that IS one end of a route: it shows one thing at a time, has no
#: destination child to enumerate, and its routing command addresses the device
#: rather than a port on it. The guess needs a command naming both ends and so
#: proposes nothing whatsoever.
SELF_ROUTING_ENDPOINT = {
    "state_variables": {
        "video_source": {"type": "enum", "label": "Video Source",
                         "values": ["None", "Input1", "Input2", "Stream"]},
        "stream_location": {"type": "string", "label": "Stream URL"},
    },
    "commands": {
        "set_video_source": {"params": {
            "source": {"type": "enum", "required": True,
                       "values": ["None", "Input1", "Input2", "Stream"]},
        }},
        "route_stream": {"params": {
            "encoder": {"type": "string", "required": True,
                        "label": "Encoder (IP or stream URL)"},
        }},
    },
}

#: An amplifier whose channels carry a real source selector alongside four
#: properties that only READ like one. Three are ruled out by their shape --
#: a yes/no cannot be a source, and a mode or a list is a fact ABOUT the
#: routing -- but `dante_audio_source` cannot be: it is an enum, it is named
#: exactly like a routed source, and it chooses between Dante and analogue
#: rather than between encoders. Only the driver can say that one is not a
#: route, which is the whole argument for letting it.
NOISY_AMPLIFIER = {
    "child_entity_types": {
        "channel": {
            "label": "Channel", "label_plural": "Channels",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "primary_source": {"type": "string", "label": "Primary Source"},
                "dante_audio_source": {"type": "enum", "values": ["DANTE", "NATIVE"],
                                       "label": "Dante Audio Source"},
                "input_clip": {"type": "boolean", "label": "Input Clip"},
                "input_mode": {"type": "enum", "values": ["Override", "Backup"],
                               "label": "Priority Mode"},
                "source_list": {"type": "string", "label": "Routed Sources"},
            },
        },
    },
    "commands": {
        "set_primary_source": {"params": {
            "channel": {"type": "child_id", "child_type": "channel"},
            "source": {"type": "enum", "values": ["Analog 1", "Analog 2", "Dante 1"]},
        }},
        "set_dante_audio_source": {"params": {
            "channel": {"type": "child_id", "child_type": "channel"},
            "source": {"type": "enum", "values": ["DANTE", "NATIVE"]},
        }},
    },
}

ROSTER = {
    "sink": [{"local_id": 1, "local_id_padded": "001", "label": "Lobby"},
             {"local_id": 2, "local_id_padded": "002", "label": "Bar"}],
    "encoder": [{"local_id": 1, "local_id_padded": "001", "label": "Apple TV"},
                {"local_id": 2, "local_id_padded": "002", "label": "Cable"}],
}


def _by_property(proposals):
    return {p["route_property"]: p for p in proposals}


# --- The fixed extra parameter ---------------------------------------------


def test_a_plane_the_property_cannot_name_is_guessed_without_its_parameter():
    """The defect the declaration exists for: a route that will be refused."""
    (guessed,) = propose_matrices("dev", UNNAMEABLE_PLANE, ROSTER)
    assert guessed["command"] == "switch"
    assert "signal" not in guessed["route"][0]["params"]
    assert any("'signal'" in w for w in guessed["warnings"])


def test_declaring_the_parameter_puts_it_on_the_route():
    declared = {**UNNAMEABLE_PLANE, "routing": {
        "destination_child_type": "sink",
        "source_child_type": "encoder",
        "command": "switch",
        "planes": [{"label": "Source", "route_property": "source",
                    "params": {"signal": "ALL"}}],
    }}
    (proposal,) = propose_matrices("dev", declared, ROSTER)
    assert proposal["route"][0]["params"] == {
        "sink_id": "$output", "encoder_id": "$input", "signal": "ALL",
    }
    # Nothing is left for the author to settle, so nothing is said.
    assert proposal["warnings"] == []


def test_a_fixed_parameter_belongs_to_its_own_plane_only():
    """A signal inherited onto the next plane would route the wrong signal."""
    declared = {**UNNAMEABLE_PLANE, "routing": {
        "destination_child_type": "sink",
        "command": "switch",
        "planes": [
            {"label": "Video", "route_property": "source",
             "params": {"signal": "VIDEO"}},
        ],
    }}
    (proposal,) = propose_matrices("dev", declared, ROSTER)
    assert proposal["route"][0]["params"]["signal"] == "VIDEO"


def test_a_declared_plane_still_reports_a_parameter_it_left_unfilled():
    declared = {**UNNAMEABLE_PLANE, "routing": {
        "destination_child_type": "sink",
        "command": "switch",
        "planes": [{"route_property": "source"}],
    }}
    (proposal,) = propose_matrices("dev", declared, ROSTER)
    assert any("'signal'" in w for w in proposal["warnings"])


# --- The device that routes itself -----------------------------------------


def test_an_endpoint_with_no_destination_child_is_not_guessable():
    assert propose_matrices("nvx", SELF_ROUTING_ENDPOINT, {}) == []


def test_declaring_it_makes_the_device_its_own_destination():
    declared = {**SELF_ROUTING_ENDPOINT, "routing": {"planes": [
        {"label": "Video", "route_property": "video_source",
         "command": "set_video_source", "source_param": "source"},
    ]}}
    (proposal,) = propose_matrices("nvx", declared, {})
    assert proposal["destinations"] == [
        {"value": "nvx", "label": "nvx", "route_key": "device.nvx.video_source"},
    ]
    # The command addresses the device, so the route carries a source and no
    # destination -- and says so rather than inventing a port parameter.
    assert proposal["route"][0]["params"] == {"source": "$input"}
    assert [s["value"] for s in proposal["sources"]] == [
        "None", "Input1", "Input2", "Stream",
    ]
    assert proposal["from_roster"] is True


def test_a_self_routing_plane_with_no_enumerable_sources_says_so():
    """An encoder named by IP or rtsp:// URL cannot be listed, and must not pretend."""
    declared = {**SELF_ROUTING_ENDPOINT, "routing": {"planes": [
        {"label": "Stream", "route_property": "stream_location",
         "command": "route_stream", "source_param": "encoder"},
    ]}}
    (proposal,) = propose_matrices("nvx", declared, {})
    assert proposal["sources"] == []
    assert any("does not say what this can be routed from" in w
               for w in proposal["warnings"])


# --- Replacing the guess rather than joining it ----------------------------


def test_a_selector_named_like_a_source_is_proposed_when_nothing_says_otherwise():
    """Nothing about its shape says it is not a route, so the guess offers it."""
    guessed = _by_property(propose_matrices("amp", NOISY_AMPLIFIER, {}))
    assert "dante_audio_source" in guessed


def test_declaring_the_real_plane_drops_the_ones_that_only_look_like_it():
    declared = {**NOISY_AMPLIFIER, "routing": {"planes": [
        {"label": "Source", "destination_child_type": "channel",
         "route_property": "primary_source", "command": "set_primary_source"},
    ]}}
    proposals = propose_matrices("amp", declared, {})
    assert [p["route_property"] for p in proposals] == ["primary_source"]


def test_a_boolean_is_never_a_routed_source():
    """Two shipped drivers carry a boolean clip and presence read-out."""
    guessed = _by_property(propose_matrices("amp", NOISY_AMPLIFIER, {}))
    assert "input_clip" not in guessed


def test_a_mode_a_type_and_a_list_are_never_a_routed_source():
    guessed = _by_property(propose_matrices("amp", NOISY_AMPLIFIER, {}))
    assert "input_mode" not in guessed
    assert "source_list" not in guessed


def test_declared_planes_keep_the_order_the_driver_wrote_them_in():
    declared = {**UNNAMEABLE_PLANE, "routing": {
        "destination_child_type": "sink",
        "command": "switch",
        "planes": [
            {"label": "Second", "route_property": "source",
             "params": {"signal": "IR"}},
        ],
    }}
    labels = [p["label"] for p in propose_matrices("dev", declared, ROSTER)]
    assert labels == ["Sinks · Second"]


def test_a_declaration_says_it_is_a_declaration():
    declared = {**UNNAMEABLE_PLANE, "routing": {
        "destination_child_type": "sink", "command": "switch",
        "planes": [{"route_property": "source", "params": {"signal": "ALL"}}],
    }}
    (proposal,) = propose_matrices("dev", declared, ROSTER)
    assert proposal["why"].startswith("The driver declares this.")
    assert proposal["confidence"] == "high"


def test_a_block_with_no_usable_plane_falls_back_to_the_guess():
    """A broken block is refused at the authoring door; a running instance is
    better served by the guess than by nothing."""
    declared = {**UNNAMEABLE_PLANE, "routing": {"planes": [{"label": "no property"}]}}
    assert [p["route_property"] for p in propose_matrices("dev", declared, ROSTER)] == [
        "source",
    ]


# --- The warning a declaration answers, and the ones it does not -----------


#: Destinations that are children called `input`, which every naive rule gets
#: backwards -- and which the two shipped Atlona processors really are.
INVERTED_PROCESSOR = {
    "child_entity_types": {
        "input": {
            "label": "Channel", "label_plural": "Channels",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "source": {"type": "enum", "values": ["Mic", "Line"]},
            },
        },
    },
    "commands": {
        "set_input_source": {"params": {
            "channel": {"type": "child_id", "child_type": "input"},
            "source": {"type": "enum", "values": [
                {"value": "0", "label": "Mic"}, {"value": "1", "label": "Line"},
            ]},
        }},
    },
}

_DECLARED_PROCESSOR = {**INVERTED_PROCESSOR, "routing": {"planes": [
    {"label": "Source", "destination_child_type": "input",
     "route_property": "source", "command": "set_input_source",
     "destination_param": "channel", "source_param": "source"},
]}}


def test_the_guess_asks_whether_children_called_input_are_really_destinations():
    (guessed,) = propose_matrices("dsp", INVERTED_PROCESSOR, {})
    assert any("usually names a source" in w for w in guessed["warnings"])


def test_a_declaration_settles_that_question_and_the_warning_goes():
    (proposal,) = propose_matrices("dsp", _DECLARED_PROCESSOR, {})
    assert not any("usually names a source" in w for w in proposal["warnings"])


def test_a_declaration_does_not_settle_what_the_device_REPORTS():
    """Structure is declarable; vocabulary is not, and a declaration says nothing
    about it either way.

    This device accepts "0" and reports "Mic". What answers that is the source
    entry carrying both -- which the DRIVER already declared twice, once as the
    command's options and once as the property's values -- and it is read off
    the same declaration whether or not there is a `routing:` block above it.
    """
    (declared,) = propose_matrices("dsp", _DECLARED_PROCESSOR, {})
    (guessed,) = propose_matrices("dsp", INVERTED_PROCESSOR, {})
    both = [(s["value"], s.get("report_value")) for s in declared["sources"]]
    assert both == [(s["value"], s.get("report_value")) for s in guessed["sources"]]
    assert both[0] == ("0", "Mic")
    assert all(sent != reported for sent, reported in both)


def test_a_declaration_does_not_settle_which_ports_are_really_there():
    (proposal,) = propose_matrices("dsp", _DECLARED_PROCESSOR, {})
    assert proposal["from_roster"] is False
    assert any("not the ones this device has" in w for w in proposal["warnings"])


# --- What the authoring gate refuses ---------------------------------------

_BASE = {
    "child_entity_types": {
        "output": {"state_variables": {"input": {"type": "integer"}}},
        "input": {"state_variables": {}},
    },
    "state_variables": {"video_source": {"type": "enum"}},
    "commands": {"route": {"params": {
        "output": {"type": "child_id", "child_type": "output"},
        "input": {"type": "integer"},
        "signal": {"type": "enum", "required": True},
    }}},
}


def _errors(block):
    return routing_block_errors({**_BASE, "routing": block})


def test_a_correct_declaration_is_accepted():
    assert _errors({"destination_child_type": "output", "command": "route",
                    "planes": [{"route_property": "input"}]}) == []


def test_a_driver_with_no_routing_block_is_not_checked():
    assert routing_block_errors(_BASE) == []


def test_a_child_type_that_does_not_exist_is_named_with_the_nearest_one():
    (error,) = _errors({"destination_child_type": "outputs", "command": "route",
                        "planes": [{"route_property": "input"}]})
    assert "'outputs' is not a declared child_entity_type" in error
    assert "did you mean 'output'?" in error


def test_a_property_that_is_not_on_that_child_type_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "inpu"}]})
    assert "route_property 'inpu' is not a state variable of child type 'output'" in error


def test_a_property_that_is_not_on_the_device_is_refused():
    (error,) = _errors({"command": "route",
                        "planes": [{"route_property": "video_sauce",
                                    "source_param": "input"}]})
    assert "is not a state variable of this driver" in error


def test_a_command_that_does_not_exist_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "rout",
                        "planes": [{"route_property": "input"}]})
    assert "command 'rout' is not a declared command" in error


def test_a_parameter_that_is_not_on_that_command_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "input",
                                    "source_param": "inp"}]})
    assert "source_param 'inp' is not a parameter of command 'route'" in error


def test_a_fixed_parameter_that_is_not_on_that_command_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "input",
                                    "params": {"signl": "X"}}]})
    assert "params.signl is not a parameter of command 'route'" in error


def test_a_fixed_value_that_would_overwrite_the_routed_source_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "input",
                                    "source_param": "input",
                                    "params": {"input": 3}}]})
    assert "already the route's source" in error


def test_fixed_parameters_with_nothing_to_send_them_on_are_refused():
    (error,) = _errors({"destination_child_type": "output",
                        "planes": [{"route_property": "input",
                                    "params": {"signal": "X"}}]})
    assert "needs a 'command' to send them on" in error


def test_a_block_with_no_planes_is_refused():
    (error,) = _errors({"destination_child_type": "output"})
    assert "needs a non-empty 'planes' list" in error


def test_a_plane_with_no_route_property_is_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"label": "Video"}]})
    assert "missing required 'route_property'" in error


def test_two_planes_watching_one_property_are_refused():
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "input"},
                                   {"route_property": "input"}]})
    assert "already declared by routing.planes[0]" in error


def test_two_planes_on_one_property_routing_it_differently_are_allowed():
    """A combined mode is a second plane, not a duplicate of the first.

    An AVoIP decoder switches every stream with one command and tells them apart
    with a parameter -- and that parameter also takes a combined value (MXNet
    `stream: all`, Chazy and Darwin `signal: ALL`). So the plane that routes
    everything and the plane that routes video alone both report `source_video`
    and send nothing alike. Refusing the pair on the property alone refused the
    combined plane, which is the one an integrator reaches for first: a video-only
    matrix on that gear leaves audio, USB and serial on the previous source with
    nothing on the panel to say so.
    """
    assert _errors({"destination_child_type": "output", "command": "route",
                    "planes": [{"label": "All", "route_property": "input",
                                "params": {"signal": "ALL"}},
                               {"label": "Video", "route_property": "input",
                                "params": {"signal": "VIDEO"}}]}) == []


def test_two_planes_routing_one_property_the_same_way_are_still_refused():
    """The refinement above must not let the real duplicate back through."""
    (error,) = _errors({"destination_child_type": "output", "command": "route",
                        "planes": [{"route_property": "input",
                                    "params": {"signal": "ALL"}},
                                   {"route_property": "input",
                                    "params": {"signal": "ALL"}}]})
    assert "routing it the same way are the same matrix twice" in error


def test_a_property_built_at_runtime_is_not_reported_as_missing():
    """Three shipped Python drivers build their child properties at
    construction time, so a file reader sees a marker rather than a mapping.
    That is not a missing property."""
    driver = {
        "child_entity_types": {"decoder": {"state_variables": "<unevaluated>"}},
        "commands": {"route": {"params": {
            "decoder_id": {"type": "child_id", "child_type": "decoder"},
            "encoder_id": {"type": "child_id", "child_type": "decoder"},
        }}},
        "routing": {"destination_child_type": "decoder", "command": "route",
                    "planes": [{"route_property": "source_video"}]},
    }
    assert routing_block_errors(driver) == []


# --- Reaching the picker from a .avcdriver file -----------------------------


#: The same declaration, written the way an author writes one: in the YAML
#: file, alongside the child types and the command it names.
ROUTED_FRAME_DEFINITION = {
    "id": "acme_frame",
    "name": "Acme Frame",
    "transport": "tcp",
    "child_entity_types": {
        "output": {
            "label": "Output", "label_plural": "Outputs",
            "id_format": {"type": "integer", "min": 1, "max": 2},
            "state_variables": {
                "input": {"type": "integer", "label": "Source"},
                "mode": {"type": "string", "label": "Scaling Mode"},
            },
        },
    },
    "commands": {
        "route": {
            "label": "Route",
            "send": "R{out},{inp}\r",
            "params": {
                "out": {"type": "child_id", "child_type": "output"},
                "inp": {"type": "integer", "min": 1, "max": 4},
            },
        },
        "set_mode": {
            "label": "Set Mode",
            "send": "M{out},{mode}\r",
            "params": {
                "out": {"type": "child_id", "child_type": "output"},
                "mode": {"type": "string"},
            },
        },
    },
    "routing": {
        "destination_child_type": "output",
        "command": "route",
        "destination_param": "out",
        "source_param": "inp",
        "planes": [{"label": "Video", "route_property": "input"}],
    },
}


def _live_driver_info(definition):
    """What the picker actually reads: DRIVER_INFO off the built class.

    Not the file. `GET /api/ui/matrix-proposals` asks the LIVE driver, because
    three shipped drivers build their child properties at construction time
    and a file reader sees an empty driver.
    """
    from openavc.drivers.configurable import create_configurable_driver_class

    return create_configurable_driver_class(definition).DRIVER_INFO


def test_a_declaration_in_the_file_reaches_the_driver_the_picker_reads():
    """The gap this closes: a declaration can be valid, published in the
    schema, offered by the Driver Builder and read by the inference, and still
    do nothing, because the YAML runtime never carried it onto the driver.
    Every test above hands `propose_matrices` a dict directly, so none of them
    crosses the one boundary an author's file has to."""
    assert _live_driver_info(ROUTED_FRAME_DEFINITION)["routing"] == \
        ROUTED_FRAME_DEFINITION["routing"]


def test_the_declaration_settles_the_plane_a_yaml_driver_offers():
    """And it has to change the answer, or carrying it means nothing."""
    info = _live_driver_info(ROUTED_FRAME_DEFINITION)
    (proposal,) = propose_matrices("frame", info, {})
    assert proposal["route_property"] == "input"
    assert proposal["command"] == "route"
    assert proposal["why"].startswith("The driver declares this.")


def test_a_yaml_driver_that_declares_nothing_is_still_guessed_at():
    """Purely additive: dropping the block puts the driver back on the guess,
    which reaches the same plane here and offers the scaling mode besides."""
    silent = {k: v for k, v in ROUTED_FRAME_DEFINITION.items() if k != "routing"}
    info = _live_driver_info(silent)
    assert "routing" not in info
    proposals = propose_matrices("frame", info, {})
    assert "input" in [p["route_property"] for p in proposals]
    assert not any(p["why"].startswith("The driver declares this.") for p in proposals)
