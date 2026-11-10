"""Import-guard for the driver-contract modules.

``openavc/drivers/spec.py``, ``openavc/drivers/avcdriver_semantic.py`` and the
check modules it imports (``action_checks``, ``command_checks``, ``condition_checks``,
``child_type_checks``, ``declared_names``, ``framing_checks``, ``liveness_checks``,
``param_aid_checks``, ``push_block_checks``, ``query_entry_checks``,
``response_rule_checks``, ``routing_checks``, ``sign_in_checks``,
``state_variable_checks``),
``openavc/drivers/child_ids.py`` and ``openavc/drivers/python_info.py`` are shared beyond the server runtime (the
community driver catalog runs the same rules in its CI, in a job that installs
no ``openavc`` package), so they must stay importable with nothing but the
standard library and each other: no runtime, no transports, no discovery, no
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
    "openavc.drivers.avcdriver_semantic",
    "openavc.drivers.child_ids",
    "openavc.drivers.child_type_checks",
    "openavc.drivers.command_checks",
    "openavc.drivers.condition_checks",
    "openavc.drivers.declared_names",
    "openavc.drivers.framing_checks",
    "openavc.drivers.liveness_checks",
    "openavc.drivers.param_aid_checks",
    "openavc.drivers.push_block_checks",
    "openavc.drivers.query_entry_checks",
    "openavc.drivers.response_rule_checks",
    "openavc.drivers.routing_checks",
    "openavc.drivers.sign_in_checks",
    "openavc.drivers.state_variable_checks",
    "openavc.utils",
    "openavc.utils.regex_safety",
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
    may not import, ``python_info`` still may not import through it.
    """
    allowed = ALLOWED | {"openavc.drivers.python_info"}
    loaded = _loaded_modules("import openavc.drivers.python_info")
    assert set(loaded) <= allowed, sorted(set(loaded) - allowed)
    # Still not YAML. The reader parses Python source, and the catalog CI job
    # it runs in has no yaml dependency to lean on.
    assert "yaml" not in loaded
