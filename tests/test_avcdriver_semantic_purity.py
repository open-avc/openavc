"""Import-guard for the driver-contract modules.

``openavc/drivers/spec.py``, ``openavc/drivers/avcdriver_semantic.py`` and the
check modules it imports (``action_checks``, ``command_checks`` (with the form rule it
asks, ``command_form``), ``condition_checks``,
``child_fault_checks``, ``child_type_checks``, ``conversion_checks``, ``declared_names``, ``discovery_block``, ``discovery_hint``, ``driver_id``, ``driver_warnings``, ``framing_checks``,
``liveness_checks``,
``param_aid_checks``, ``push_block_checks``, ``query_entry_checks``, with the reader it asks,
``query_entry``, and the driver model, ``driver_model``, with the reply-rule reading it
composes, ``reply_rule``, the placeholder fill it fills a pattern with, ``pattern_fill``, and
the repeated-option rule it reads enum lists by, ``enum_options``,
``reply_name_checks``, ``response_rule_checks`` (with the value-spec reading it asks,
``json_value_spec``), ``routing_checks``, ``serial_line``, ``setting_checks``,
``sign_in_checks``, ``state_variable_checks``, ``substitution_checks``, ``tagged_errors``,
and the number rule they read numbers through, ``usable_number``),
``openavc/drivers/child_ids.py`` (with ``roster_source``, the roster-source rule it asks), ``openavc/drivers/python_info.py`` (with ``python_source``, the source read it asks) and ``openavc/drivers/simulator_info.py`` are shared beyond the server runtime (the
community driver catalog runs the same rules in its CI, in a job that installs
no ``openavc`` package), so they must stay importable with nothing but the
standard library, each other and the two leaf rules they ask from ``utils/``
(``regex_safety``, ``version_rule``): no runtime, no transports, no discovery, no
YAML. This test imports them in a clean subprocess and fails if anything
outside the allowed closure gets pulled in.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {
    "openavc",
    "openavc.drivers",
    "openavc.drivers.spec",
    "openavc.drivers.action_checks",
    "openavc.drivers.auth_block",
    "openavc.drivers.avcdriver_semantic",
    "openavc.drivers.capture_ref",
    "openavc.drivers.capture_ref_checks",
    "openavc.drivers.child_fault_checks",
    "openavc.drivers.child_ids",
    "openavc.drivers.child_type_checks",
    "openavc.drivers.command_checks",
    "openavc.drivers.command_form",
    "openavc.drivers.condition_checks",
    "openavc.drivers.conversion_checks",
    "openavc.drivers.declared_names",
    "openavc.drivers.discovery_block",
    "openavc.drivers.discovery_hint",
    "openavc.drivers.driver_id",
    "openavc.drivers.driver_model",
    "openavc.drivers.driver_warnings",
    "openavc.drivers.enum_options",
    "openavc.drivers.framing_checks",
    "openavc.drivers.json_value_spec",
    "openavc.drivers.liveness_checks",
    "openavc.drivers.param_aid_checks",
    "openavc.drivers.pattern_fill",
    "openavc.drivers.platform_floor_checks",
    "openavc.drivers.push_block_checks",
    "openavc.drivers.python_source",
    "openavc.drivers.query_entry",
    "openavc.drivers.query_entry_checks",
    "openavc.drivers.reply_name_checks",
    "openavc.drivers.reply_rule",
    "openavc.drivers.restart_window",
    "openavc.drivers.response_rule_checks",
    "openavc.drivers.roster_source",
    "openavc.drivers.routing_checks",
    "openavc.drivers.send_shape",
    "openavc.drivers.serial_line",
    "openavc.drivers.setting_checks",
    "openavc.drivers.setting_model",
    "openavc.drivers.shape_checks",
    "openavc.drivers.sign_in_checks",
    "openavc.drivers.state_variable_checks",
    "openavc.drivers.substitution_checks",
    "openavc.drivers.tagged_errors",
    "openavc.drivers.transport_choice",
    "openavc.drivers.unknown_keys",
    "openavc.drivers.usable_number",
    "openavc.drivers.yaml_text",
    "openavc.utils",
    "openavc.utils.regex_safety",
    "openavc.utils.version_rule",
}


def _loaded_modules(import_stmt: str) -> list[str]:
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        + import_stmt + "\n"
        "names = sorted(m for m in sys.modules if m.startswith('openavc'))\n"
        "if 'yaml' in sys.modules: names.append('yaml')\n"
        "print('\\n'.join(names))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def test_spec_imports_nothing_beyond_itself():
    loaded = _loaded_modules("import openavc.drivers.spec")
    assert set(loaded) <= {"openavc", "openavc.drivers", "openavc.drivers.spec"}, loaded


def test_semantic_rules_stay_pure():
    loaded = _loaded_modules("import openavc.drivers.avcdriver_semantic")
    assert set(loaded) <= ALLOWED, (
        f"avcdriver_semantic pulled in modules outside its purity contract: "
        f"{sorted(set(loaded) - ALLOWED)}"
    )


def test_python_info_imports_nothing_beyond_itself():
    """The Python-driver reader is vendored into the community catalog and runs
    in a CI job that installs only that repo's own requirements — so it may not
    reach for the server runtime, and not for YAML either.

    It may reach the shared rules module, which is vendored beside it and held
    to the same contract: the cross-reference rules a Python driver is checked
    against are the *same functions* the YAML surface calls, and reaching them
    is the whole point. Budgeting against ``ALLOWED`` rather than a list of its
    own keeps that from becoming a back door — anything ``avcdriver_semantic``
    may not import, ``python_info`` still may not import through it. Which
    class it reads (``python_driver_class``) is vendored beside it too.
    """
    allowed = ALLOWED | {"openavc.drivers.python_info", "openavc.drivers.python_driver_class"}
    loaded = _loaded_modules("import openavc.drivers.python_info")
    assert set(loaded) <= allowed, sorted(set(loaded) - allowed)
    # Still not YAML. The reader parses Python source, and the catalog CI job
    # it runs in has no yaml dependency to lean on.
    assert "yaml" not in loaded


def test_simulator_info_imports_nothing_beyond_the_python_reader():
    """The check of a Python simulator's SIMULATOR_INFO is vendored beside the
    Python-driver reader and runs in the same catalog CI job, so it is held to
    the same budget, plus that reader."""
    allowed = ALLOWED | {
        "openavc.drivers.python_info",
        "openavc.drivers.python_driver_class",
        "openavc.drivers.simulator_info",
    }
    loaded = _loaded_modules("import openavc.drivers.simulator_info")
    assert set(loaded) <= allowed, sorted(set(loaded) - allowed)
    assert "yaml" not in loaded
