"""Import-guard for the shared protocol-interpreter helpers.

``server/drivers/compiled_protocol.py`` is shared beyond the server runtime —
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
    "server",
    "server.drivers",
    "server.drivers.compiled_protocol",
    "server.transport",
    "server.transport.binary_helpers",
    "server.utils",
    "server.utils.logger",
    # Pulled in by server.utils.logger (same closure inline_protocol has).
    "server.utils.log_buffer",
    "server.system_config",
}


def test_compiled_protocol_stays_pure():
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        "import server.drivers.compiled_protocol\n"
        "names = sorted(m for m in sys.modules if m.startswith('server'))\n"
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
