"""Tests for the simulator validator's coverage of authored simulator features.

The validator's job is parity: anything the simulator runtime honors should
get pre-run feedback when it's authored wrong. These tests cover the checks
for the notifications: section, OSC address handlers and commands, the
declarative controls: schema, explicit-handler set_state:/capture templates,
and the child-entity heads-up — plus regressions for poll-coverage false
positives (each_child queries, trailing whitespace, config_derived).

All drivers here are invented (acme_widget) with synthetic payloads.
"""

import textwrap

from simulator.validate import validate_yaml_driver


def _validate(tmp_path, yaml_text):
    path = tmp_path / "acme_widget.avcdriver"
    path.write_text(textwrap.dedent(yaml_text), encoding="utf-8")
    return validate_yaml_driver(path)


def _messages(result, check, severity=None):
    issues = result.issues
    if severity:
        issues = [i for i in issues if i.severity == severity]
    return [i.message for i in issues if i.check == check]


# ── notifications: ──


def test_notification_unknown_key_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        simulator:
          initial_state: {power: false}
          notifications:
            powr: 'PWR {value}'
    """)
    msgs = _messages(r, "notifications", "warning")
    assert any("'powr'" in m for m in msgs), msgs


def test_notification_boolean_value_template_is_error(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        simulator:
          initial_state: {power: false}
          notifications:
            power: 'PWR {value}'
    """)
    msgs = _messages(r, "notifications", "error")
    assert any("{value}" in m and "boolean" in m for m in msgs), msgs


def test_notification_unquoted_boolean_keys_are_error(tmp_path):
    # Unquoted true:/false: parse as YAML booleans; the runtime str()s them
    # to 'True'/'False', which never matches the lowercase lookup.
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        simulator:
          initial_state: {power: false}
          notifications:
            power:
              true: 'PWR1'
              false: 'PWR0'
    """)
    msgs = _messages(r, "notifications", "error")
    assert len(msgs) == 2 and all("never matches" in m for m in msgs), msgs


def test_notification_round_trip_against_response_patterns(tmp_path):
    base = """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {{type: integer, min: 0, max: 100}}
        responses:
          - match: 'VOL(\\d+)'
            set: {{volume: $1}}
        simulator:
          initial_state: {{volume: 20}}
          notifications:
            volume: '{template}'
    """
    good = _validate(tmp_path, base.format(template="VOL{value}"))
    assert not _messages(good, "notifications"), _messages(good, "notifications")

    bad = _validate(tmp_path, base.format(template="LEVEL={value}"))
    msgs = _messages(bad, "notifications", "warning")
    assert any("matches no" in m for m in msgs), msgs


def test_notification_on_non_tcp_transport_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: udp
        state_variables:
          level: {type: integer}
        simulator:
          initial_state: {level: 1}
          notifications:
            level: 'LVL {value}'
    """)
    msgs = _messages(r, "notifications", "warning")
    assert any("no effect" in m for m in msgs), msgs


# ── OSC coverage ──


def test_osc_command_without_handler_is_error(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: osc
        state_variables:
          level: {type: number}
        commands:
          set_level:
            address: "/acme/level"
            args: [{type: f, value: "{level}"}]
            params:
              level: {type: number}
        simulator:
          initial_state: {level: 0.5}
    """)
    msgs = _messages(r, "command_coverage", "error")
    assert any("set_level" in m for m in msgs), msgs


def test_osc_command_covered_by_handler_or_response(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: osc
        state_variables:
          level: {type: number}
          name: {type: string}
        commands:
          set_level:
            address: "/acme/ch/{ch}/level"
            params:
              ch: {type: string}
          query_name:
            address: "/acme/name"
          renew:
            address: "/xremote"
        responses:
          - address: "/acme/ch/01/level"
            mappings: [{arg: 0, state: level, type: number}]
        simulator:
          initial_state: {level: 0.5, name: Acme}
          command_handlers:
            - address: "/acme/name"
              handler: |
                respond("/acme/name", [("s", state["name"])])
    """)
    # set_level covered via template overlap with the literal response
    # address; query_name via the handler; renew via the special /xremote.
    assert not _messages(r, "command_coverage"), _messages(r, "command_coverage")


def test_osc_poll_query_checked_and_command_names_skipped(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: osc
        state_variables:
          level: {type: number}
        commands:
          renew:
            address: "/xremote"
        polling:
          queries:
            - "renew"
            - "/acme/unanswered"
        simulator:
          initial_state: {level: 0.5}
    """)
    msgs = _messages(r, "poll_coverage", "error")
    assert len(msgs) == 1 and "/acme/unanswered" in msgs[0], msgs


def test_handler_syntax_error_is_reported(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: osc
        state_variables:
          level: {type: number}
        simulator:
          initial_state: {level: 0.5}
          command_handlers:
            - address: "/acme/*"
              handler: |
                if True
                  respond("/acme/level", [("f", 1.0)])
    """)
    msgs = _messages(r, "handler_syntax", "error")
    assert any("syntax error" in m for m in msgs), msgs


# ── controls: ──


def test_controls_unknown_type_is_error(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          count: {type: integer, min: 0, max: 10}
        simulator:
          initial_state: {count: 0}
          controls:
            - type: number
              key: count
              min: 0
              max: 10
    """)
    msgs = _messages(r, "controls", "error")
    assert any("unknown control type 'number'" in m for m in msgs), msgs


def test_controls_missing_required_fields_are_errors(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
          source: {type: string}
        simulator:
          initial_state: {volume: 10, source: hdmi1}
          controls:
            - type: slider
              key: volume
              min: 0
            - type: select
              key: source
            - type: matrix
              inputs: 4
              outputs: 2
    """)
    msgs = _messages(r, "controls", "error")
    assert any("'max'" in m for m in msgs), msgs
    assert any("'options'" in m for m in msgs), msgs
    assert any("'state_pattern'" in m for m in msgs), msgs


def test_controls_unknown_state_key_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        simulator:
          initial_state: {volume: 10}
          controls:
            - type: slider
              key: volums
              min: 0
              max: 100
    """)
    msgs = _messages(r, "controls", "warning")
    assert any("'volums'" in m for m in msgs), msgs


def test_controls_group_recurses_and_presets_need_count_or_names(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          preset: {type: integer, min: 1, max: 8}
        simulator:
          initial_state: {preset: 1}
          controls:
            - type: group
              label: Presets
              controls:
                - type: presets
                  key: preset
                - type: bogus
                  key: preset
    """)
    warn = _messages(r, "controls", "warning")
    err = _messages(r, "controls", "error")
    assert any("neither 'count' nor 'names'" in m for m in warn), warn
    assert any("controls[0].controls[1]" in m and "bogus" in m for m in err), err


def test_valid_controls_pass_clean(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: enum, values: [on, off]}
          volume: {type: integer, min: 0, max: 100}
          mute: {type: boolean}
        simulator:
          initial_state: {power: off, volume: 10, mute: false, out1_input: 1, meter_1: 0}
          controls:
            - type: power
              key: power
            - type: slider
              key: volume
              min: 0
              max: 100
            - type: toggle
              key: mute
              label: Mute
            - type: matrix
              inputs: 4
              outputs: 2
              state_pattern: "out{output}_input"
            - type: meters
              channels: 2
              key_pattern: "meter_{ch}"
    """)
    assert not _messages(r, "controls"), _messages(r, "controls")


# ── explicit handlers (set_state / capture refs) ──


def test_set_state_unknown_target_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        commands:
          set_volume:
            send: "VOL {volume}\\r"
            params:
              volume: {type: integer}
        simulator:
          initial_state: {volume: 10}
          command_handlers:
            - receive: 'VOL (\\d+)'
              respond: 'VOL={1}'
              set_state: {volum: '{1}'}
    """)
    msgs = _messages(r, "set_state", "warning")
    assert any("'volum'" in m for m in msgs), msgs


def test_out_of_range_capture_ref_is_error(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        commands:
          set_volume:
            send: "VOL {volume}\\r"
            params:
              volume: {type: integer}
        simulator:
          initial_state: {volume: 10}
          command_handlers:
            - receive: 'VOL (\\d+)'
              respond: 'VOL={2}'
              set_state: {volume: '{2}'}
    """)
    msgs = _messages(r, "set_state", "error")
    assert len(msgs) == 2 and all("{2}" in m for m in msgs), msgs


def test_unknown_state_ref_in_respond_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        commands:
          get_volume:
            send: "VOL?\\r"
        simulator:
          initial_state: {volume: 10}
          command_handlers:
            - receive: 'VOL\\?'
              respond: 'VOL={state.volum}'
    """)
    msgs = _messages(r, "set_state", "warning")
    assert any("state.volum" in m for m in msgs), msgs


def test_set_state_ignored_alongside_handler_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        commands:
          get_volume:
            send: "VOL?\\r"
        simulator:
          initial_state: {volume: 10}
          command_handlers:
            - match: 'VOL\\?'
              set_state: {volume: '5'}
              handler: |
                respond('VOL=' + str(state['volume']))
    """)
    msgs = _messages(r, "command_handlers", "warning")
    assert any("ignored" in m for m in msgs), msgs


def test_invalid_handler_regex_is_error(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          volume: {type: integer, min: 0, max: 100}
        simulator:
          initial_state: {volume: 10}
          command_handlers:
            - receive: 'VOL ([0-9+'
              respond: 'OK'
    """)
    msgs = _messages(r, "command_handlers", "error")
    assert any("invalid regex" in m for m in msgs), msgs


# ── child entities ──


def test_child_entity_driver_without_sim_section_warns(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        child_entity_types:
          output:
            label: Output
            id_format: {type: integer}
            instances: {count: 4}
    """)
    msgs = _messages(r, "child_entities", "warning")
    assert any("no\nsimulator" in m or "no simulator" in m for m in msgs), msgs


def test_child_entity_driver_with_sim_section_gets_info(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        child_entity_types:
          output:
            label: Output
            id_format: {type: integer}
            instances: {count: 4}
        simulator:
          initial_state: {power: false}
    """)
    assert not _messages(r, "child_entities", "warning")
    msgs = _messages(r, "child_entities", "info")
    assert any("not auto-generated" in m for m in msgs), msgs


# ── poll-coverage regressions ──


def test_each_child_poll_query_uses_sample_child_id(tmp_path):
    """each_child queries expand per child at runtime; the validator checks
    them with a sample id instead of erroring on the literal {child_id}."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        child_entity_types:
          output:
            label: Output
            id_format: {type: integer}
            instances: {count: 4}
        polling:
          queries:
            - {each_child: output, send: "{child_id}STAT\\r"}
        simulator:
          initial_state: {power: false}
          command_handlers:
            - receive: '(\\d+)STAT'
              respond: '{1}STAT=1'
    """)
    assert not _messages(r, "poll_coverage"), _messages(r, "poll_coverage")


def test_uncovered_each_child_poll_query_still_errors(tmp_path):
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        child_entity_types:
          output:
            label: Output
            id_format: {type: integer}
            instances: {count: 4}
        polling:
          queries:
            - {each_child: output, send: "{child_id}STAT\\r"}
        simulator:
          initial_state: {power: false}
          command_handlers:
            - receive: 'OTHER'
              respond: 'OK'
    """)
    msgs = _messages(r, "poll_coverage", "error")
    assert any("1STAT" in m for m in msgs), msgs


def test_poll_query_with_trailing_space_matches_stripping_runtime(tmp_path):
    """The simulator strips incoming lines before matching, so a poll query
    with trailing whitespace before its \\r must not be flagged."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          fw: {type: string}
        polling:
          queries:
            - 'g_fw O 00 NC \\r'
        simulator:
          initial_state: {fw: '1.0'}
          command_handlers:
            - match: 'g_fw O 00 NC'
              handler: |
                respond('g_fw 00 NC ' + state['fw'])
    """)
    assert not _messages(r, "poll_coverage"), _messages(r, "poll_coverage")


def test_child_id_params_sample_as_integers(tmp_path):
    """A child_id command param must sample as an integer (runtime auto
    patterns capture digits), so the auto handler covers it."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: tcp
        state_variables:
          power: {type: boolean}
        child_entity_types:
          output:
            label: Output
            id_format: {type: integer}
            instances: {count: 4}
        commands:
          route:
            send: "{input}*{output}!\\r"
            params:
              input: {type: integer}
              output: {type: child_id}
        simulator:
          initial_state: {power: false}
    """)
    assert not _messages(r, "command_coverage"), _messages(r, "command_coverage")


def test_config_derived_resolves_in_poll_queries(tmp_path):
    """config_derived values substitute into poll queries the way the driver
    runtime computes them (empty refs collapse the derived value to '')."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: osc
        default_config:
          zone_id: ""
        config_derived:
          zp: "/zone/{zone_id}"
        state_variables:
          level: {type: number}
        polling:
          queries:
            - "{zp}/level"
        simulator:
          initial_state: {level: 0.5}
          command_handlers:
            - address: "/level"
              handler: |
                respond("/level", [("f", state["level"])])
    """)
    assert not _messages(r, "poll_coverage"), _messages(r, "poll_coverage")


def test_http_raw_path_poll_query_matches_get_prefixed_handler(tmp_path):
    """An HTTP driver polling a raw path is dispatched by the simulator as the
    synthesized line "GET /path?query" — the validator must compare handlers
    against that form, not the bare path, or every raw-path poll reads as
    uncovered (false error)."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: http
        state_variables:
          level: {type: integer}
        commands:
          query_level:
            method: GET
            path: "/getxml"
            query_params:
              location: "/Status/Level"
        polling:
          queries:
            - "/getxml?location=/Status/Level"
        responses:
          - match: '<Level>(\\d+)</Level>'
            set: {level: "$1"}
        simulator:
          initial_state: {level: 5}
          command_handlers:
            - match: '^GET /getxml\\?location=/Status/Level\\b.*'
              handler: |
                respond(f'<Level>{state["level"]}</Level>')
    """)
    assert not _messages(r, "poll_coverage", "error"), _messages(r, "poll_coverage")


def test_http_raw_path_poll_query_without_handler_still_errors(tmp_path):
    """The GET-prefix fix must not blind the check: an unanswered raw path is
    still a real coverage error."""
    r = _validate(tmp_path, """\
        id: acme_widget
        transport: http
        state_variables:
          level: {type: integer}
        polling:
          queries:
            - "/getxml?location=/Status/Unanswered"
        simulator:
          initial_state: {level: 5}
    """)
    msgs = _messages(r, "poll_coverage", "error")
    assert len(msgs) == 1 and "/Status/Unanswered" in msgs[0], msgs
