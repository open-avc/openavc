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

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {
    "openavc",
    "openavc.drivers",
    "openavc.drivers.compiled_protocol",
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
