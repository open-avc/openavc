"""Tests for installer/openavc.spec hidden imports coverage (A27).

PyInstaller's static analysis only catches module-level imports. Function-
level `from server.transport.X import ...` calls (used to keep startup cost
low for optional transports) are invisible to it, so the modules must be
listed in `hiddenimports` or they're missing from the frozen build.

Before A27, OSC transport and codec were used function-level all over
`drivers/configurable.py`, `simulator/yaml_auto.py`, `simulator/osc_simulator.py`,
and `drivers/base.py`, but neither module was in the spec — every OSC
device crashed the moment the runtime tried to import it.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPEC_PATH = REPO_ROOT / "installer" / "openavc.spec"


def _hidden_imports() -> set[str]:
    """Extract the `hiddenimports = [...]` literal list from the spec.

    Done as plain text parsing because the spec references PyInstaller
    symbols (Analysis, PYZ, etc.) that aren't importable in a test env.
    """
    src = SPEC_PATH.read_text(encoding="utf-8")
    m = re.search(r"hiddenimports\s*=\s*\[(.*?)\n\]", src, re.DOTALL)
    assert m, "Could not locate hiddenimports list in openavc.spec"
    block = m.group(1)
    return set(re.findall(r"['\"]([\w.]+)['\"]", block))


def test_osc_modules_are_hidden_imports():
    """OSC transport + codec + simulators must be declared. All four are
    used function-level so PyInstaller cannot find them automatically.
    """
    hidden = _hidden_imports()
    required = {
        "server.transport.osc",
        "server.transport.osc_codec",
        "simulator.osc_simulator",
        "simulator.udp_simulator",
    }
    missing = required - hidden
    assert not missing, f"openavc.spec is missing OSC hidden imports: {missing}"


def test_all_function_level_transport_imports_are_declared():
    """Any `from server.transport.X import …` used inside a function body
    must be in `hiddenimports`. Catches new transports added with the same
    deferred-import pattern that bit OSC.
    """
    hidden = _hidden_imports()

    # Modules that are well-known to be unrelated test infrastructure
    skip_dirs = {"tests", "node_modules", ".git", "dist", "build"}

    pattern = re.compile(r"from\s+(server\.transport\.\w+)\s+import", re.MULTILINE)
    referenced = set()
    for py in REPO_ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        referenced.update(pattern.findall(text))

    missing = referenced - hidden
    assert not missing, (
        f"openavc.spec is missing transport hidden imports used in source: {missing}"
    )
