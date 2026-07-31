"""Type-consistency and command-coverage checks, held to one rule set.

Two things this pins.

There used to be two copies of the initial-state type check — one for YAML
drivers and a second inside the Python path — and they had drifted. The Python
copy checked ``boolean`` and ``enum`` only, so a string sitting in an
``integer`` state variable was reported for a YAML driver and silently
accepted for a Python one. Neither copy covered ``number`` or ``float``, both
of which are declarable types.

And the Python command-coverage check was gated on "did *any* command name
appear in the simulator source", so one incidental match silenced it for every
other command in the driver.
"""

from __future__ import annotations

import textwrap

import pytest

from simulator.validate import validate_python_driver


def _driver_and_sim(tmp_path, state_vars: str, initial_state: str, commands: str = "{}"):
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent(f'''
        class AcmeWidgetDriver:
            DRIVER_INFO = {{
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {state_vars},
                "commands": {commands},
            }}
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent(f'''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {{
                "driver_id": "acme_widget",
                "name": "Acme Widget Simulator",
                "transport": "tcp",
                "initial_state": {initial_state},
            }}
    '''), encoding="utf-8")
    return driver


def _messages(result, check: str) -> list[str]:
    return [i.message for i in result.issues if i.check == check]


def test_a_string_in_an_integer_variable_is_reported(tmp_path):
    """This is the case the Python path used to accept in silence."""
    path = _driver_and_sim(
        tmp_path,
        '{"temperature": {"type": "integer", "label": "Temp"}}',
        '{"temperature": "warm"}',
    )
    found = _messages(validate_python_driver(path), "type_consistency")
    assert any("temperature" in m and "integer" in m for m in found)


@pytest.mark.parametrize("declared", ["number", "float"])
def test_a_string_in_a_number_variable_is_reported(tmp_path, declared):
    """Neither copy of the check ever covered these two declarable types."""
    path = _driver_and_sim(
        tmp_path,
        f'{{"level": {{"type": "{declared}", "label": "Level"}}}}',
        '{"level": "loud"}',
    )
    found = _messages(validate_python_driver(path), "type_consistency")
    assert any("level" in m and declared in m for m in found)


def test_a_boolean_mismatch_is_still_reported(tmp_path):
    """The one case that already worked must keep working."""
    path = _driver_and_sim(
        tmp_path,
        '{"panel_lock": {"type": "boolean", "label": "Lock"}}',
        '{"panel_lock": 7}',
    )
    found = _messages(validate_python_driver(path), "type_consistency")
    assert any("panel_lock" in m and "boolean" in m for m in found)


def test_true_does_not_satisfy_an_integer_declaration(tmp_path):
    """bool is a subclass of int, so this passed without an explicit guard."""
    path = _driver_and_sim(
        tmp_path,
        '{"count": {"type": "integer", "label": "Count"}}',
        '{"count": True}',
    )
    found = _messages(validate_python_driver(path), "type_consistency")
    assert any("count" in m for m in found)


def test_a_value_outside_the_declared_enum_is_reported(tmp_path):
    path = _driver_and_sim(
        tmp_path,
        '{"mode": {"type": "enum", "label": "Mode", "values": ["a", "b"]}}',
        '{"mode": "spinning"}',
    )
    found = _messages(validate_python_driver(path), "type_consistency")
    assert any("mode" in m and "spinning" in m for m in found)


def test_a_computed_enum_says_it_could_not_be_checked(tmp_path):
    """A silent skip here reads exactly like a pass.

    The old reader dropped ``values`` entirely, so the enum branch quietly
    compared against an empty list and never fired.
    """
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        _MODES = ["a", "b"]

        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {
                    "mode": {"type": "enum", "label": "Mode", "values": _MODES},
                },
                "commands": {},
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget",
                "name": "Acme Widget Simulator",
                "transport": "tcp",
                "initial_state": {"mode": "spinning"},
            }
    '''), encoding="utf-8")
    found = _messages(validate_python_driver(driver), "type_consistency")
    assert any("computed" in m and "mode" in m for m in found)


def test_a_matching_type_is_not_reported(tmp_path):
    """Vacuity guard: a check that fires on everything proves nothing."""
    path = _driver_and_sim(
        tmp_path,
        '{"volume": {"type": "integer", "label": "Volume"},'
        ' "mode": {"type": "enum", "label": "Mode", "values": ["a"]}}',
        '{"volume": 12, "mode": "a"}',
    )
    assert _messages(validate_python_driver(path), "type_consistency") == []


def test_both_driver_formats_run_the_same_function():
    """The two copies drifted once; a second copy must fail rather than drift.

    Asserted on the source so adding a parallel implementation inside the
    Python path is what breaks, not a later behavioural difference nobody
    happens to test.
    """
    from pathlib import Path

    import simulator.validate as validate

    source = Path(validate.__file__).read_text(encoding="utf-8")
    assert source.count("def _check_initial_state_types(") == 1
    # Once from the YAML path's wrapper, once from the Python path.
    assert source.count("_check_initial_state_types(result") == 2


# ── Command coverage ──


def test_one_incidental_match_no_longer_silences_the_rest(tmp_path):
    """The finding: a name occurring inside an unrelated string hid the others."""
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {},
                "commands": {
                    "set_bitrate": {"label": "Set Bitrate"},
                    "set_profile": {"label": "Set Profile"},
                    "start_stream": {"label": "Start Stream"},
                    "stop_stream": {"label": "Stop Stream"},
                },
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget",
                "name": "Acme Widget Simulator",
                "transport": "tcp",
                "initial_state": {},
            }

            def handle_command(self, data):
                # The only mention of any command name, and it is incidental.
                if data == b"stream.set_bitrate":
                    return b"OK"
                return None
    '''), encoding="utf-8")
    found = _messages(validate_python_driver(driver), "command_coverage")
    assert found, "the check must still say something"
    assert "3 of 4" in found[0]
    assert "set_profile" in found[0]


def test_a_simulator_naming_every_command_is_quiet(tmp_path):
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": {},
                "commands": {"power_on": {"label": "On"}, "power_off": {"label": "Off"}},
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget", "name": "Acme Widget Simulator",
                "transport": "tcp", "initial_state": {},
            }

            def handle_command(self, data):
                if data == b"power_on":
                    return b"OK"
                if data == b"power_off":
                    return b"OK"
                return None
    '''), encoding="utf-8")
    assert _messages(validate_python_driver(driver), "command_coverage") == []


def test_command_coverage_reports_at_info_not_as_a_failure(tmp_path):
    """A binary protocol never mentions a logical command name.

    Those drivers warned permanently no matter how correct they were, which
    trains an author to ignore the one channel that might say something real.
    """
    path = _driver_and_sim(
        tmp_path, "{}", "{}", '{"power_on": {"label": "On"}}',
    )
    result = validate_python_driver(path)
    coverage = [i for i in result.issues if i.check == "command_coverage"]
    assert coverage
    assert all(i.severity == "info" for i in coverage)


def test_computed_commands_are_named_rather_than_skipped(tmp_path):
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        _CMDS = {"power_on": {"label": "On"}}

        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": {}, "commands": _CMDS,
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget", "name": "Acme Widget Simulator",
                "transport": "tcp", "initial_state": {},
            }
    '''), encoding="utf-8")
    found = _messages(validate_python_driver(driver), "command_coverage")
    assert any("computed" in m for m in found)


def test_computed_state_variables_are_named_rather_than_skipped(tmp_path):
    """The old reader always produced *something*, so this never arose.

    Now that the reader is honest, "I could not read this" has to be said out
    loud — a silent skip is indistinguishable from a clean pass.
    """
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        _VARS = {"power": {"type": "boolean"}}

        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": _VARS, "commands": {},
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget", "name": "Acme Widget Simulator",
                "transport": "tcp", "initial_state": {"power": True},
            }
    '''), encoding="utf-8")
    found = _messages(validate_python_driver(driver), "state_coverage")
    assert any("computed" in m for m in found)


# ── What the validator does with a file it cannot read ──


def test_a_module_level_driver_info_is_reported_not_crashed_on(tmp_path):
    """The runtime loads DRIVER_INFO off a BaseDriver subclass.

    A module-level dict of that name is a driver that will never load. The
    old regex reader could not tell the difference and validated it as if it
    were fine; the platform reader refuses, and a validator must turn that
    into a finding rather than a traceback.
    """
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        DRIVER_INFO = {"id": "acme_widget", "name": "Acme Widget", "transport": "tcp"}
    '''), encoding="utf-8")
    result = validate_python_driver(driver)
    assert not result.passed
    assert any("class attribute" in i.message for i in result.errors)


def test_a_driver_that_does_not_parse_is_reported_not_crashed_on(tmp_path):
    driver = tmp_path / "acme_widget.py"
    driver.write_text("class Broken:\n    DRIVER_INFO = {\n", encoding="utf-8")
    result = validate_python_driver(driver)
    assert not result.passed
    assert any("does not parse" in i.message or "syntax" in i.message.lower()
               for i in result.errors)


# ── The simulator side of the same reader problem ──


def test_a_computed_simulator_info_is_read_not_faked(tmp_path):
    """The regex fallback stored an unevaluable value as its own source text.

    So a simulator seeding ``"volume": _DEFAULT`` handed the type check the
    string ``"_DEFAULT"``, which reported that an integer state variable held
    a str — a finding about the reader, printed as a finding about the driver.
    It also could not see an ``initial_state`` built from a module constant at
    all, and reported the field missing.
    """
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": {"volume": {"type": "integer", "label": "Volume"}},
                "commands": {},
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text(textwrap.dedent('''
        _INITIAL = {"volume": 30}

        class AcmeWidgetSimulator:
            SIMULATOR_INFO = {
                "driver_id": "acme_widget",
                "name": "Acme Widget Simulator",
                "transport": "tcp",
                "initial_state": _INITIAL,
            }
    '''), encoding="utf-8")
    result = validate_python_driver(driver)
    missing = [i for i in result.errors if "missing required field" in i.message]
    assert not missing, "initial_state is declared, just not as a literal"
    assert not any(
        "is integer but initial_state value is str" in i.message
        for i in result.issues
    )


def test_a_simulator_that_does_not_parse_is_reported(tmp_path):
    driver = tmp_path / "acme_widget.py"
    driver.write_text(textwrap.dedent('''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": {}, "commands": {},
            }
    '''), encoding="utf-8")
    sim = tmp_path / "acme_widget_sim.py"
    sim.write_text("class Broken:\n    SIMULATOR_INFO = {\n", encoding="utf-8")
    result = validate_python_driver(driver)
    assert not result.passed
