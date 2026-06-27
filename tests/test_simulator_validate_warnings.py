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

from simulator.validate import ValidationResult, _check_state_coverage
from simulator.validate import validate_python_driver


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


def test_python_simulator_without_command_names_gets_a_warning(tmp_path):
    """A Python simulator that never mentions driver command names should
    surface a warning instead of passing silently.
    """
    driver = _write(
        tmp_path / "sample.py",
        "DRIVER_INFO = {\n"
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
    assert any(issue.check == 'command_coverage' for issue in result.warnings)
    assert 'power_on' in result.warnings[0].message or 'power_off' in result.warnings[0].message


def test_python_simulator_prefix_only_mentions_still_warn(tmp_path):
    """A prefix like `power` must not count as `power_on` coverage."""
    driver = _write(
        tmp_path / "sample.py",
        "DRIVER_INFO = {\n"
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
    assert any(issue.check == 'command_coverage' for issue in result.warnings)
