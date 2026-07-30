"""Tests for ``python -m server.drivers.check`` — the standalone contract check.

The command's whole value is that it defines no rules of its own: it is a front
end over the functions the save doors, the runtime loader and the community
catalog already call. So the load-bearing tests here are the ones that pin the
message text against those functions directly — if the checker ever grows its
own copy of a rule, the wording drifts and they fail.

The rest cover what the command must never do quietly: skip a file it cannot
read, or pass over a value it could not evaluate.

All drivers here are invented (acme_widget) with synthetic payloads.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from server.drivers.avcdriver_semantic import unknown_key_errors
from server.drivers.check import check_driver_file, main, scan_for_drivers
from server.drivers.driver_loader import validate_driver_definition
from server.drivers.python_info import python_driver_info_issues

CLEAN_YAML = """\
    id: acme_widget
    name: Acme Widget
    transport: tcp
    state_variables:
      power:
        type: boolean
        label: Power
    commands:
      power_on:
        label: Power On
        send: "PWR ON\\r"
"""

CLEAN_PYTHON = '''\
    from server.drivers.base import BaseDriver


    class AcmeWidgetDriver(BaseDriver):
        DRIVER_INFO = {
            "id": "acme_widget",
            "name": "Acme Widget",
            "transport": "tcp",
            "state_variables": {
                "power": {"type": "boolean", "label": "Power"},
            },
            "commands": {
                "power_on": {"label": "Power On"},
            },
        }
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# ── A clean driver says nothing ──


def test_clean_yaml_driver_is_silent_and_exits_zero(tmp_path, capsys):
    driver = _write(tmp_path / "acme_widget.avcdriver", CLEAN_YAML)
    assert main([str(driver)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_clean_python_driver_is_silent_and_exits_zero(tmp_path, capsys):
    driver = _write(tmp_path / "acme_widget.py", CLEAN_PYTHON)
    assert main([str(driver)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# ── Same wording as the doors it fronts ──


def test_python_unknown_key_message_is_the_shared_rule_verbatim(tmp_path):
    """The checker must not paraphrase. Its message for a misspelled key is
    ``unknown_key_errors``' message — the same string catalog CI prints and the
    same one the Builder's save returns."""
    driver = _write(
        tmp_path / "acme_widget.py",
        CLEAN_PYTHON.replace('"label": "Power On"', '"labl": "Power On"'),
    )
    result = check_driver_file(driver)

    info = {
        "id": "acme_widget",
        "name": "Acme Widget",
        "transport": "tcp",
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {"power_on": {"labl": "Power On"}},
    }
    assert result.errors == unknown_key_errors(info)
    assert result.errors == [
        "commands.power_on: unknown key 'labl' (did you mean 'label'?)"
    ]


def test_yaml_errors_are_the_save_door_verdict_verbatim(tmp_path):
    """The YAML half is ``validate_driver_definition(..., strict=True)`` — the
    call the Builder's save, the import route and catalog CI all make."""
    text = CLEAN_YAML.replace("state_variables:", "state_varibles:")
    driver = _write(tmp_path / "acme_widget.avcdriver", text)
    import yaml

    driver_def = yaml.safe_load(driver.read_text(encoding="utf-8"))

    result = check_driver_file(driver)
    assert result.errors == validate_driver_definition(driver_def, strict=True)
    assert any("unknown key 'state_varibles'" in e for e in result.errors)
    assert any("did you mean 'state_variables'?" in e for e in result.errors)


def test_structural_message_is_the_loaders_rule_verbatim(tmp_path):
    """The structural rules are the ones ``driver_loader`` logs at load."""
    driver = _write(
        tmp_path / "acme_widget.py",
        CLEAN_PYTHON.replace(
            '"commands": {',
            '"actions": [{"kind": "command"}],\n            "commands": {',
        ),
    )
    result = check_driver_file(driver)
    expected = python_driver_info_issues({"actions": [{"kind": "command"}]})
    # The wording is the SHARED rule's. python_info used to carry its own copy
    # of the actions checks — narrower, and worded differently — so the same
    # broken driver was described one way by the catalog and another by the
    # Builder. It delegates to ``validate_actions`` now, and both say this.
    assert expected == ["actions[0]: missing required 'id' (non-empty string)"]
    assert expected[0] in result.errors


def test_output_is_path_qualified(tmp_path, capsys):
    driver = _write(
        tmp_path / "acme_widget.py",
        CLEAN_PYTHON.replace('"label": "Power On"', '"labl": "Power On"'),
    )
    assert main([str(driver)]) == 1
    err = capsys.readouterr().err
    assert (
        f"{driver}: error: commands.power_on: unknown key 'labl' "
        f"(did you mean 'label'?)" in err
    )


def test_printed_paths_use_native_separators(tmp_path):
    """A Windows author must get ``drivers\\acme_widget.py``, not ``C:/...``.

    ``as_posix()`` reads fine on macOS and is wrong on Windows: nothing there
    emits that form and an editor's problem matcher will not match it, which
    defeats the point of a ``path: error: message`` format. On POSIX the two
    are identical, so this pins the function rather than the separator — a
    re-introduced ``as_posix()`` fails on Windows and is at least legible here.
    """
    from server.drivers.check import _display_path

    nested = tmp_path / "drivers" / "acme_widget.py"
    assert _display_path(nested, tmp_path) == str(Path("drivers/acme_widget.py"))
    assert _display_path(nested, Path(tmp_path.anchor) / "elsewhere") == str(nested)


# ── A file it cannot read is an error, not a skip ──


def test_python_without_driver_info_is_an_error(tmp_path, capsys):
    driver = _write(tmp_path / "acme_widget.py", "POWER = 1\n")
    assert main([str(driver)]) == 1
    err = capsys.readouterr().err
    assert "no DRIVER_INFO class attribute found" in err
    assert "1 file(s) checked, 1 with errors, 1 unreadable" in err


def test_python_syntax_error_is_an_error(tmp_path, capsys):
    driver = _write(tmp_path / "acme_widget.py", "class A:\n    DRIVER_INFO = {\n")
    assert main([str(driver)]) == 1
    err = capsys.readouterr().err
    assert "syntax error" in err
    assert not check_driver_file(driver).readable


def test_module_level_driver_info_is_reported_not_skipped(tmp_path):
    """A DRIVER_INFO outside a class body is how the runtime loses a driver:
    it looks for a BaseDriver subclass and reads the attribute off the class.
    The checker must say so rather than treat the file as unremarkable."""
    driver = _write(tmp_path / "acme_widget.py", "DRIVER_INFO = {'id': 'acme'}\n")
    result = check_driver_file(driver)
    assert not result.readable
    assert "inside a class body" in result.errors[0]


def test_unsupported_extension_is_an_error(tmp_path):
    other = _write(tmp_path / "notes.txt", "hello\n")
    result = check_driver_file(other)
    assert not result.readable
    assert "unsupported file type" in result.errors[0]


def test_malformed_yaml_is_an_error(tmp_path):
    driver = _write(tmp_path / "acme_widget.avcdriver", "id: [unclosed\n")
    result = check_driver_file(driver)
    assert not result.readable
    assert "could not read as YAML" in result.errors[0]


def test_non_mapping_yaml_is_an_error(tmp_path):
    driver = _write(tmp_path / "acme_widget.avcdriver", "- one\n- two\n")
    result = check_driver_file(driver)
    assert not result.readable
    assert result.errors == ["top-level YAML must be a mapping"]


# ── Coverage is stated, never implied ──


def test_computed_values_are_named_in_a_note(tmp_path):
    driver = _write(
        tmp_path / "acme_widget.py",
        '''\
        BUILT_AT_IMPORT = {"power": {"type": "boolean", "label": "Power"}}


        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": dict(BUILT_AT_IMPORT),
            }
''',
    )
    result = check_driver_file(driver)
    assert result.ok
    assert result.notes
    assert "state_variables" in result.notes[0]
    assert "could not be read from the source" in result.notes[0]


def test_class_dependent_checks_are_named_not_assumed(tmp_path):
    driver = _write(
        tmp_path / "acme_widget.py",
        CLEAN_PYTHON.replace(
            '"commands": {',
            '"device_settings": {"volume": {"type": "integer"}},\n'
            '            "commands": {',
        ),
    )
    result = check_driver_file(driver)
    assert result.ok
    note = " ".join(result.notes)
    assert "need the loaded driver class" in note
    assert "set_device_setting" in note


def test_no_class_dependent_note_when_nothing_triggers_one(tmp_path):
    driver = _write(tmp_path / "acme_widget.py", CLEAN_PYTHON)
    assert check_driver_file(driver).notes == []


# ── Directory scanning ──


def test_directory_scan_skips_companions_and_helpers(tmp_path):
    _write(tmp_path / "acme_widget.py", CLEAN_PYTHON)
    _write(tmp_path / "acme_widget_sim.py", "DRIVER_INFO = {'id': 'sim'}\n")
    _write(tmp_path / "acme_widget_discovery.py", "DRIVER_INFO = {'id': 'd'}\n")
    _write(tmp_path / "_helper.py", "DRIVER_INFO = {'id': 'h'}\n")
    _write(tmp_path / "acme_widget.avcdriver", CLEAN_YAML)

    scan = scan_for_drivers(tmp_path)
    assert {p.name for p in scan.files} == {"acme_widget.py", "acme_widget.avcdriver"}
    assert scan.skipped == []


def test_directory_scan_reports_skipped_test_modules(tmp_path, capsys):
    """A driver test declares ``DRIVER_INFO: dict = {}`` on its stub base class,
    which is a real declaration. Skipping those silently would make a repo's own
    test suite look like dozens of broken drivers — or, worse, make the skip
    itself invisible."""
    _write(tmp_path / "acme_widget.py", CLEAN_PYTHON)
    _write(tmp_path / "tests" / "test_acme_widget.py", "DRIVER_INFO = {'id': 'x'}\n")
    _write(tmp_path / "conftest.py", "DRIVER_INFO = {'id': 'y'}\n")

    scan = scan_for_drivers(tmp_path)
    assert {p.name for p in scan.files} == {"acme_widget.py"}
    assert {p.name for p in scan.skipped} == {"test_acme_widget.py", "conftest.py"}

    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 file(s) checked, 0 with errors, 0 unreadable" in out
    assert "2 file(s) declaring a DRIVER_INFO were skipped" in out


def test_named_file_is_always_checked_even_in_a_skipped_location(tmp_path, capsys):
    """Naming a path and being told nothing is the failure this command removes."""
    driver = _write(tmp_path / "tests" / "acme_widget.py", "POWER = 1\n")
    assert main([str(driver)]) == 1
    assert "no DRIVER_INFO class attribute found" in capsys.readouterr().err


# ── simulator.validate runs the contract check first ──


def test_simulator_validate_reports_contract_errors_above_parity(tmp_path, capsys):
    """An author who only knows the documented command still meets the
    contract — and meets it in the same words, above the parity findings."""
    import pytest

    from simulator.validate import main as validate_main

    driver = _write(
        tmp_path / "acme_widget.py",
        CLEAN_PYTHON.replace('"label": "Power On"', '"labl": "Power On"'),
    )

    with pytest.raises(SystemExit) as exit_info:
        validate_main([str(driver)])
    assert exit_info.value.code == 1

    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    issue_lines = [ln for ln in lines if ln.startswith("  ")]
    assert issue_lines, lines
    assert issue_lines[0] == (
        "  ERROR [contract] commands.power_on: unknown key 'labl' "
        "(did you mean 'label'?)"
    )
    # Verbatim, not paraphrased: the same string check_driver_file produced.
    assert check_driver_file(driver).errors[0] in issue_lines[0]


def test_simulator_validate_stays_green_on_a_clean_driver(tmp_path, capsys):
    import pytest

    from simulator.validate import main as validate_main

    driver = _write(tmp_path / "acme_widget.py", CLEAN_PYTHON)
    with pytest.raises(SystemExit) as exit_info:
        validate_main([str(driver)])
    assert exit_info.value.code == 0
    assert "[contract]" not in capsys.readouterr().out


def test_missing_path_exits_nonzero(tmp_path, capsys):
    assert main([str(tmp_path / "nope.py")]) == 1
    assert "no such file or directory" in capsys.readouterr().err


def test_directory_with_no_drivers_exits_nonzero(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    assert main([str(tmp_path / "empty")]) == 1
    assert "no driver files found" in capsys.readouterr().err
