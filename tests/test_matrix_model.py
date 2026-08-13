"""What a matrix resolves to, which is the whole of project format 0.10.0.

The shapes here are the ones the driver corpus forced (matrix plan §2.1a), not
invented variety: a decoder with six routing planes, a frame with two ports
patched out, string ids, and a source that is an rtsp:// URL. Each of them was
unsayable in the pattern form, and each is a list entry now.
"""

from __future__ import annotations

from openavc.ui.matrix_model import (
    axis_count,
    destination_for,
    resolve_axis,
    resolve_matrix_config,
    resolve_ui,
    route_matches,
    route_value,
)


# --- The comparison every match goes through -------------------------------


def test_a_labelled_port_matches_its_number():
    """The defect the whole comparison exists for: parseInt('IN1') is NaN."""
    assert route_matches("IN2", 2)
    assert route_matches(2, "IN2")
    assert route_matches("HDMI 3", 3)


def test_identical_values_can_never_read_as_a_mismatch():
    """NaN never equals itself, so the audio badge lit on byte-identical values."""
    assert route_matches("IN1", "IN1")
    assert route_matches("Laptop", "laptop")
    assert route_matches(7, 7.0)


def test_a_name_is_not_guessed_at_and_two_numbers_are_not_one():
    assert not route_matches("Laptop", 2)
    assert not route_matches("1080p60", 1)


def test_zero_and_blank_are_unrouted_rather_than_port_zero():
    """AV gear reports 0 for an idle port; every routing driver numbers from 1."""
    assert route_value(0) is None
    assert route_value("0") is None
    assert route_value("") is None
    assert route_value(None) is None
    assert not route_matches(0, 0)


def test_a_boolean_is_a_word_not_a_number():
    """JavaScript stringifies a boolean here; Python would call True equal to 1."""
    assert route_value(True) == "true"
    assert not route_matches(True, 1)


# --- The resolved form -----------------------------------------------------


def test_a_written_out_axis_keeps_every_entry_as_written():
    config = {
        "sources": [
            {"value": 1, "label": "Apple TV", "label_key": "device.mx.input.1.name"},
            {"value": "HDMI_A", "label": "Laptop"},
        ],
        "destinations": [
            {"value": 7, "label": "Stream", "route_key": "device.enc.source",
             "route": [{"action": "macro", "macro": "start_stream"}]},
        ],
    }

    assert resolve_axis(config, "sources") == config["sources"]
    assert resolve_axis(config, "destinations") == config["destinations"]


def test_an_entry_with_no_label_is_named_by_its_position():
    """Which is what the pattern form drew, so a migrated project keeps its words."""
    config = {"sources": [{"value": 4}], "destinations": [{"value": "rx2"}]}

    assert resolve_axis(config, "sources")[0]["label"] == "In 1"
    assert resolve_axis(config, "destinations")[0]["label"] == "Out 1"


def test_a_bare_scalar_entry_means_just_this_value():
    assert resolve_axis({"sources": [1, "HDMI_A"]}, "sources") == [
        {"value": 1, "label": "In 1"},
        {"value": "HDMI_A", "label": "In 2"},
    ]


def test_an_axis_that_says_nothing_resolves_to_nothing():
    """Rather than to the phantom 4x4 the old renderer default invented."""
    assert resolve_axis({}, "sources") == []
    assert resolve_axis(None, "destinations") == []
    assert resolve_axis({"sources": "four"}, "sources") == []


def test_junk_entries_are_dropped_rather_than_drawn():
    assert resolve_axis({"sources": [None, {"label": "no value"}, 3]}, "sources") == [
        {"value": 3, "label": "In 3"},
    ]


# --- The generator form ----------------------------------------------------


def test_a_count_generator_expands_to_numbered_entries():
    config = {"destinations": {"from": {
        "count": 3, "route_key": "device.mx.output.*.input",
    }}}

    assert resolve_axis(config, "destinations") == [
        {"value": 1, "label": "Out 1", "route_key": "device.mx.output.1.input"},
        {"value": 2, "label": "Out 2", "route_key": "device.mx.output.2.input"},
        {"value": 3, "label": "Out 3", "route_key": "device.mx.output.3.input"},
    ]


def test_explicit_values_need_not_be_numbers_or_contiguous():
    """A Gefen frame's outputs are string ids; an NVX source is a URL."""
    config = {"destinations": {"from": {
        "values": ["out_a", "out_c"], "route_key": "device.gefen.*.source",
    }}}

    assert [e["value"] for e in resolve_axis(config, "destinations")] == ["out_a", "out_c"]
    assert resolve_axis(config, "destinations")[1]["route_key"] == "device.gefen.out_c.source"


def test_exclude_drops_the_ports_nobody_patched():
    """'Leave out unused inputs and outputs' is not a feature, it is a shorter list."""
    config = {"destinations": {
        "from": {"count": 8, "route_key": "device.mx.output.*.input"},
        "exclude": [7, 8],
    }}

    entries = resolve_axis(config, "destinations")
    assert [e["value"] for e in entries] == [1, 2, 3, 4, 5, 6]


def test_overrides_edit_one_entry_by_value():
    config = {"destinations": {
        "from": {"count": 3, "route_key": "device.mx.output.*.input"},
        "overrides": {"2": {"label": "Main LCD",
                            "route": [{"action": "macro", "macro": "m"}]}},
    }}

    entries = resolve_axis(config, "destinations")
    assert entries[1]["label"] == "Main LCD"
    assert entries[1]["route"] == [{"action": "macro", "macro": "m"}]
    # ...and the generated key survives the override that did not mention it.
    assert entries[1]["route_key"] == "device.mx.output.2.input"
    assert entries[0]["label"] == "Out 1"


def test_labels_are_indexed_by_position_before_anything_is_excluded():
    config = {"sources": {
        "from": {"count": 4, "labels": ["Apple TV", "Room PC", "Laptop", "Cam"]},
        "exclude": [2],
    }}

    assert [(e["value"], e["label"]) for e in resolve_axis(config, "sources")] == [
        (1, "Apple TV"), (3, "Laptop"), (4, "Cam"),
    ]


def test_a_generator_with_nothing_to_generate_produces_nothing():
    assert resolve_axis({"sources": {}}, "sources") == []
    assert resolve_axis({"sources": {"from": {"count": 0}}}, "sources") == []
    assert resolve_axis({"sources": {"from": {"count": "eight"}}}, "sources") == []


def test_only_the_first_star_is_substituted():
    """Matching JavaScript's String.replace with a string needle, which is what
    the pattern form has always done."""
    config = {"destinations": {"from": {"count": 1, "route_key": "a.*.b.*"}}}
    assert resolve_axis(config, "destinations")[0]["route_key"] == "a.1.b.*"


def test_a_key_with_no_star_is_one_key_every_entry_shares():
    """Legitimate: several destinations can watch one flat state variable."""
    config = {"destinations": {"from": {"count": 2, "route_key": "device.mx.usb_out"}}}
    assert {e["route_key"] for e in resolve_axis(config, "destinations")} == {
        "device.mx.usb_out"
    }


# --- The shapes the corpus forced ------------------------------------------


def test_one_decoder_carries_six_routing_planes_as_six_elements():
    """The case that settles it: the variation is cardinality, not vocabulary.

    A Chazy decoder routes video, audio, IR, RS-232, USB and CEC independently
    through one child entity. No rename reaches that -- but the plane is simply
    part of the state key, so one matrix covers one plane and needs no machinery
    at all beyond a per-destination key.
    """
    planes = ["source_video", "source_audio", "source_ir",
              "source_rs232", "source_usb", "source_cec"]
    for plane in planes:
        config = {"destinations": {"from": {
            "count": 3, "route_key": f"device.chazy.decoder.*.{plane}",
        }}}
        keys = [e["route_key"] for e in resolve_axis(config, "destinations")]
        assert keys == [f"device.chazy.decoder.{n}.{plane}" for n in (1, 2, 3)]


def test_destinations_may_live_on_different_devices_entirely():
    """The single change that makes this expressible: each destination owns its key."""
    config = {"destinations": [
        {"value": 1, "label": "Main LCD", "route_key": "device.mx.output.1.input"},
        {"value": "rtsp://10.0.0.9/live", "label": "Stream",
         "route_key": "device.enc.source"},
    ]}

    entries = resolve_axis(config, "destinations")
    assert entries[0]["route_key"].startswith("device.mx.")
    assert entries[1]["route_key"].startswith("device.enc.")


# --- What the callers ask ---------------------------------------------------


def test_axis_count_answers_for_both_forms():
    assert axis_count({"sources": {"from": {"count": 16}}}, "sources") == 16
    assert axis_count({"sources": [{"value": 1}, {"value": 2}]}, "sources") == 2
    assert axis_count({}, "destinations") == 0


def test_resolve_matrix_config_expands_both_axes_and_passes_the_rest_through():
    config = {
        "sources": {"from": {"count": 2}},
        "destinations": [{"value": 1, "label": "Main"}],
        "show_lock": False,
        "presets": [{"name": "All 1", "macro": "m"}],
    }

    resolved = resolve_matrix_config(config)

    assert len(resolved["sources"]) == 2
    assert resolved["destinations"] == [{"value": 1, "label": "Main"}]
    assert resolved["show_lock"] is False
    assert resolved["presets"] == [{"name": "All 1", "macro": "m"}]


def test_destination_for_finds_the_row_a_panel_just_routed():
    """By value, through the same comparison the crosspoints light by: a dropdown
    reads '2' out of the DOM where the project wrote 2."""
    config = {"destinations": [
        {"value": 2, "label": "Confidence", "route": [{"action": "macro", "macro": "m"}]},
        {"value": "stream", "label": "Stream"},
    ]}

    assert destination_for(config, "2")["label"] == "Confidence"
    assert destination_for(config, 2)["label"] == "Confidence"
    assert destination_for(config, "STREAM")["label"] == "Stream"
    assert destination_for(config, 99) is None


def test_resolve_ui_reaches_page_elements_and_masters_and_nothing_else():
    ui = {
        "pages": [{"id": "main", "elements": [
            {"id": "mx", "type": "matrix",
             "matrix_config": {"sources": {"from": {"count": 2}}}},
            {"id": "btn", "type": "button", "label": "Go"},
        ]}],
        "master_elements": [
            {"id": "bar", "type": "matrix",
             "matrix_config": {"destinations": {"from": {"count": 3}}}},
        ],
    }

    resolved = resolve_ui(ui)

    assert len(resolved["pages"][0]["elements"][0]["matrix_config"]["sources"]) == 2
    assert resolved["pages"][0]["elements"][1] == {
        "id": "btn", "type": "button", "label": "Go"
    }
    assert len(resolved["master_elements"][0]["matrix_config"]["destinations"]) == 3
