"""Import-guard for the shared protocol-interpreter helpers.

``openavc/drivers/compiled_protocol.py`` is shared beyond the server runtime —
the device simulator (a separate process) and the driver validator import it
directly — so it must stay importable with nothing but the standard library,
``binary_helpers``, and the logging util: no driver runtime, no transport
stack, no discovery, no YAML. This test imports it in a clean subprocess and
fails if anything outside the allowed closure gets pulled in.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {
    "openavc",
    "openavc.drivers",
    "openavc.drivers.compiled_protocol",
    # What a rule's child_set: / child_list: compile to; compile_driver calls
    # it, and it is held to the same closure.
    "openavc.drivers.child_routes",
    # The driver contract's constant tables. Stdlib-only (ipaddress + re) and
    # already vendored into openavc-drivers, so it travels everywhere this
    # module does. compiled_protocol needs it for the reserved child props:
    # the child_set: compiler coerces by declared type, and the reserved keys
    # have to coerce by theirs or `online` writes the truthy string "false".
    "openavc.drivers.spec",
    "openavc.transport",
    "openavc.transport.binary_helpers",
    "openavc.utils",
    "openavc.utils.logger",
    # Pulled in by openavc.utils.logger (same closure inline_protocol has).
    "openavc.utils.log_buffer",
    # Also pulled in by openavc.utils.logger: the credential-redaction filter it
    # installs on every handler. Stdlib-only (logging + re), so it does not
    # widen what the simulator or the validator has to be able to import.
    "openavc.utils.log_redaction",
    "openavc.system_config",
}


def test_compiled_protocol_stays_pure():
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "import openavc.drivers.compiled_protocol\n"
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
    loaded = [line for line in result.stdout.splitlines() if line]
    assert set(loaded) <= ALLOWED, (
        f"compiled_protocol pulled in modules outside its purity contract: "
        f"{sorted(set(loaded) - ALLOWED)}"
    )


def _openavc_modules_loaded(module: str, *flags: str) -> set[str]:
    """The openavc modules importing ``module`` loads, in a fresh interpreter,
    plus ``third-party:<name>`` for each top-level module outside the standard
    library that the import itself pulls in (the interpreter's own start-up
    modules are left out)."""
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "before = set(sys.modules)\n"
        "import " + module + "\n"
        "names = sorted(m for m in sys.modules if m.startswith('openavc'))\n"
        "names += sorted('third-party:' + m for m in set(sys.modules) - before\n"
        "                if '.' not in m and m != 'openavc' and m not in sys.stdlib_module_names)\n"
        "print('\\n'.join(names))\n"
    )
    result = subprocess.run(
        [sys.executable, *flags, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if line}


@pytest.mark.parametrize(
    "module",
    [
        "openavc.simulator.reply_templates",
        "openavc.simulator.send_templates",
        "openavc.simulator.command_names",
    ],
)
def test_the_simulators_template_and_name_readers_stay_leaves(module):
    # The reply-text walker, the send-template inversion and the command-name
    # fallback import nothing beyond the standard library, so anything that
    # needs them can take them without the simulator's own stack. -I -S leaves
    # only the standard library importable, so a third-party import fails the
    # import itself.
    assert _openavc_modules_loaded(module, "-I", "-S") == {"openavc", "openavc.simulator", module}


def test_the_reread_queries_stay_within_the_protocol_closure():
    # The validator imports the then_query / read_back query lists; they read
    # a rule through compiled_protocol and a query line through
    # send_templates, and nothing more: no other openavc module, and nothing
    # outside the standard library (yaml included).
    loaded = _openavc_modules_loaded("openavc.simulator.reread_queries")
    allowed = ALLOWED | {
        "openavc.simulator",
        "openavc.simulator.send_templates",
        "openavc.simulator.reread_queries",
    }
    assert loaded <= allowed, sorted(loaded - allowed)
