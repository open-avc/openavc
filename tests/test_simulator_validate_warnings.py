"""Tests for simulator validator message severity.

The validator used to warn "X not in simulator initial_state — auto-gen
default may not be appropriate" with severity=warning. The supporting
comment claimed the simulator section "overrides initial_state
completely" — actually `yaml_auto._merge_simulator_section` merges
per-key, so the auto-gen default stays active for any variable not
explicitly listed. A misleading WARN made driver authors think they
had a problem to fix when they didn't.

The fix: surface the auto-gen default as `info` (not `warning`) and
update the text to read like a heads-up, not a complaint.

This file also covers the Python-simulator command-coverage warning, which
used to be a silent no-op.
"""

from pathlib import Path

from openavc.simulator.validate import (
    ValidationResult,
    _check_python_state_coverage,
    _check_state_coverage,
)
from openavc.simulator.validate import validate_python_driver


def _result() -> ValidationResult:
    """Build a minimal validator result for the unit tests."""
    return ValidationResult(driver_path="x", driver_id="x", driver_type="yaml")


def test_state_coverage_emits_info_not_warning_for_auto_gen_fallback():
    """Variable in state_variables but not in simulator.initial_state
    should produce a single info-severity issue, no warning.
    """
    state_vars = {"power": {"type": "enum", "values": ["off", "on"], "label": "P"}}
    sim_initial: dict = {}
    sim = {"initial_state": {}}  # non-empty truthy sim section, missing the key

    r = _result()
    _check_state_coverage(r, state_vars, sim_initial, sim)

    assert not r.warnings, (
        f"_check_state_coverage emitted warnings for an auto-gen fallback: "
        f"{[i.message for i in r.warnings]}"
    )
    assert len(r.infos) == 1
    msg = r.infos[0].message
    assert "auto-gen default" in msg
    # The wording should hint at the fix, not just describe the gap.
    assert "override" in msg.lower() or "initial_state" in msg


def test_state_coverage_passes_silently_when_all_vars_covered():
    """No coverage gap = no issues of any severity."""
    state_vars = {"power": {"type": "enum", "values": ["off", "on"]}}
    sim_initial = {"power": "off"}
    sim = {"initial_state": sim_initial}

    r = _result()
    _check_state_coverage(r, state_vars, sim_initial, sim)

    assert not r.errors
    assert not r.warnings
    assert not r.infos
    assert r.passed


def test_info_issues_do_not_make_result_fail():
    """`passed` is errors-only — info messages are not failures."""
    r = _result()
    r.info("state_coverage", "auto-gen default heads-up")
    assert r.passed
    assert not r.errors
    assert not r.warnings
    assert len(r.infos) == 1


def _write(path: Path, content: str) -> Path:
    """Write a temporary file for the validator regression test."""
    path.write_text(content, encoding="utf-8")
    return path


def test_python_simulator_without_command_names_is_reported(tmp_path):
    """A Python simulator that never mentions driver command names says so.

    Reports at ``info``, not ``warning``: a simulator that dispatches on raw
    protocol bytes can never mention a logical command name, so this warned
    permanently on those drivers no matter how correct they were — which
    trains an author to ignore the one channel that might say something real.
    """
    driver = _write(
        tmp_path / "sample.py",
        "class SampleDriver:\n"
        "  DRIVER_INFO = {\n"
        "    'id': 'sample',\n"
        "    'name': 'Sample',\n"
        "    'transport': 'tcp',\n"
        "    'state_variables': {},\n"
        "    'commands': {\n"
        "        'power_on': {'label': 'Power On', 'send': 'PWR ON'},\n"
        "        'power_off': {'label': 'Power Off', 'send': 'PWR OFF'},\n"
        "    },\n"
        "}\n",
    )
    _write(
        tmp_path / "sample_sim.py",
        "class SampleSimulator:\n"
        "    SIMULATOR_INFO = {\n"
        "        'driver_id': 'sample',\n"
        "        'name': 'Sample Simulator',\n"
        "        'transport': 'tcp',\n"
        "        'initial_state': {},\n"
        "    }\n\n"
        "    def handle_command(self, data):\n"
        "        return None\n",
    )

    result = validate_python_driver(driver)

    assert result.passed
    assert not result.errors
    coverage = [i for i in result.infos if i.check == 'command_coverage']
    assert coverage
    # Both are named, not just the first: one command appearing in the source
    # used to silence the check for every other command in the driver.
    assert 'power_on' in coverage[0].message
    assert 'power_off' in coverage[0].message
    assert '2 of 2' in coverage[0].message


def test_python_simulator_prefix_only_mentions_are_still_reported(tmp_path):
    """A prefix like `power` must not count as `power_on` coverage."""
    driver = _write(
        tmp_path / "sample.py",
        "class SampleDriver:\n"
        "  DRIVER_INFO = {\n"
        "    'id': 'sample',\n"
        "    'name': 'Sample',\n"
        "    'transport': 'tcp',\n"
        "    'state_variables': {},\n"
        "    'commands': {\n"
        "        'power_on': {'label': 'Power On', 'send': 'PWR ON'},\n"
        "    },\n"
        "}\n",
    )
    _write(
        tmp_path / "sample_sim.py",
        "class SampleSimulator:\n"
        "    SIMULATOR_INFO = {\n"
        "        'driver_id': 'sample',\n"
        "        'name': 'Sample Simulator',\n"
        "        'transport': 'tcp',\n"
        "        'initial_state': {},\n"
        "    }\n\n"
        "    def handle_command(self, data):\n"
        "        power = None\n"
        "        return power\n",
    )

    result = validate_python_driver(driver)

    assert result.passed
    assert not result.errors
    assert any(issue.check == 'command_coverage' for issue in result.infos)


# --- Python state coverage: a count, not a warning per name ------------------
#
# Same reasoning as the two checks above, found the same way. `DRIVER_INFO`
# state variables and `SIMULATOR_INFO` initial_state are different namespaces:
# the simulator seeds what goes on the wire, the driver publishes what it
# computes from that. Over the shipped corpus this warned 268 times, and every
# case inspected was a correct pair under two naming conventions
# (`power_code` / `power`) or a fan-out with nothing to seed (one `lamp_hours`
# key becoming `lamp1_hours`..`lamp8_hours`).


def test_python_state_coverage_reports_one_counted_info_not_a_warning():
    r = _result()
    _check_python_state_coverage(
        r,
        {"power": {}, "source": {}, "lamp_hours": {}},
        {"power_code": "on", "input_code": 1, "lamp": 400},
    )

    assert not r.warnings, [i.message for i in r.warnings]
    (info,) = [i for i in r.infos if i.check == "state_coverage"]
    # Both counts, because the judgement this leaves to the author is a
    # comparison: 3 seeds against 33 variables reads differently from 38.
    assert "seeds 3 state key(s)" in info.message
    assert "3 of the driver's 3 state variable(s)" in info.message


def test_python_state_coverage_is_silent_when_every_name_lines_up():
    r = _result()
    _check_python_state_coverage(r, {"power": {}}, {"power": "on", "extra": 1})
    assert not r.issues


def test_a_simulator_that_seeds_nothing_at_all_is_a_warning():
    """The one case needing no threshold: it models none of the device.

    Every threshold that looked promising over the corpus (name overlap,
    seeds-per-variable ratio) put a correct driver on the wrong side of it.
    An empty seed block needs no threshold to read.
    """
    r = _result()
    _check_python_state_coverage(r, {"power": {}, "source": {}}, {})

    (warning,) = r.warnings
    assert warning.check == "state_coverage"
    assert "models none of this device" in warning.message


def test_a_computed_initial_state_is_not_mistaken_for_an_empty_one(tmp_path):
    """`initial_state: dict(DEFAULTS)` is unreadable here, not empty.

    The reader hands back an UNEVALUATED marker, which this side used to
    collapse to `{}` silently — so a simulator whose seeds are built from a
    module constant looked exactly like one that seeds nothing. Two shipped
    drivers are written that way.
    """
    driver = _write(
        tmp_path / "sample.py",
        "class SampleDriver:\n"
        "  DRIVER_INFO = {\n"
        "    'id': 'sample',\n"
        "    'name': 'Sample',\n"
        "    'transport': 'tcp',\n"
        "    'state_variables': {'power': {'type': 'string', 'label': 'P'}},\n"
        "}\n",
    )
    _write(
        tmp_path / "sample_sim.py",
        "DEFAULTS = {'power': 'off'}\n\n"
        "class SampleSimulator:\n"
        "    SIMULATOR_INFO = {\n"
        "        'driver_id': 'sample',\n"
        "        'name': 'Sample Simulator',\n"
        "        'transport': 'tcp',\n"
        "        'initial_state': dict(DEFAULTS),\n"
        "    }\n\n"
        "    def handle_command(self, data):\n"
        "        return None\n",
    )

    result = validate_python_driver(driver)

    assert not [
        i for i in result.warnings if i.check == "state_coverage"
    ], [i.message for i in result.warnings]
    assert any(
        "initial_state is computed" in i.message
        for i in result.infos
        if i.check == "state_coverage"
    ), [i.message for i in result.infos]
