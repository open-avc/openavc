"""Import guard for the plugin declaration modules: ``plugin_declarations``,
``plugin_extension_rules``, ``plugin_handler_rules`` and ``plugin_source`` in
``openavc/core/``.

The plugin declaration rules and their static reader are meant to run where
OpenAVC is not installed (a plugin repository's CI, a contributor's machine),
so they may import nothing but the standard library and each other. This test
imports them in a clean subprocess and fails if any other ``openavc`` module,
or any third-party package, comes in with them.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED = {
    "openavc", "openavc.core",
    "openavc.core.plugin_declarations",
    "openavc.core.plugin_extension_rules",
    "openavc.core.plugin_handler_rules",
    "openavc.core.plugin_source",
}


def test_the_declaration_rules_import_only_the_standard_library():
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "before = set(sys.modules)\n"
        "import openavc.core.plugin_declarations\n"
        "import openavc.core.plugin_source\n"
        "added = set(sys.modules) - before\n"
        "stdlib = sys.stdlib_module_names\n"
        "for name in sorted(added):\n"
        "    if name.split('.')[0] not in stdlib:\n"
        "        print(name)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    loaded = {line for line in result.stdout.splitlines() if line}
    assert loaded <= ALLOWED, sorted(loaded - ALLOWED)
