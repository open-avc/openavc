"""Import-guard for the driver-contract modules.

``server/drivers/spec.py``, ``server/drivers/avcdriver_semantic.py`` and
``server/drivers/python_info.py`` are shared beyond the server runtime (the
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
    "server",
    "server.drivers",
    "server.drivers.spec",
    "server.drivers.avcdriver_semantic",
    "server.utils",
    "server.utils.regex_safety",
}


def _loaded_modules(import_stmt: str) -> list[str]:
    code = (
        "import sys\n"
        "sys.path.insert(0, r'" + str(REPO_ROOT) + "')\n"
        + import_stmt + "\n"
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
    return [line for line in result.stdout.splitlines() if line]


def test_spec_imports_nothing_beyond_itself():
    loaded = _loaded_modules("import server.drivers.spec")
    assert set(loaded) <= {"server", "server.drivers", "server.drivers.spec"}, loaded


def test_semantic_rules_stay_pure():
    loaded = _loaded_modules("import server.drivers.avcdriver_semantic")
    assert set(loaded) <= ALLOWED, (
        f"avcdriver_semantic pulled in modules outside its purity contract: "
        f"{sorted(set(loaded) - ALLOWED)}"
    )


def test_python_info_imports_nothing_beyond_itself():
    """The Python-driver reader is vendored into the community catalog and runs
    in a CI job that installs only that repo's own requirements — so it may not
    reach for the server runtime, and not for YAML either."""
    loaded = _loaded_modules("import server.drivers.python_info")
    assert set(loaded) <= {
        "server",
        "server.drivers",
        "server.drivers.python_info",
    }, loaded
