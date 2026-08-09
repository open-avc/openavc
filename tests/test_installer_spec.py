"""Tests for installer/openavc.spec hidden imports coverage (A27).

PyInstaller's static analysis only catches module-level imports. Function-
level `from openavc.transport.X import ...` calls (used to keep startup cost
low for optional transports) are invisible to it, so the modules must be
listed in `hiddenimports` or they're missing from the frozen build.

Before A27, OSC transport and codec were used function-level all over
`drivers/configurable.py`, `openavc/simulator/yaml_auto.py`, `openavc/simulator/osc_simulator.py`,
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
        "openavc.transport.osc",
        "openavc.transport.osc_codec",
        "openavc.simulator.osc_simulator",
        "openavc.simulator.udp_simulator",
    }
    missing = required - hidden
    assert not missing, f"openavc.spec is missing OSC hidden imports: {missing}"


def test_all_function_level_transport_imports_are_declared():
    """Any `from openavc.transport.X import …` used inside a function body
    must be in `hiddenimports`. Catches new transports added with the same
    deferred-import pattern that bit OSC.
    """
    hidden = _hidden_imports()

    # Not platform source: test infrastructure, build output, and `data/` --
    # the runtime data dir, where a dev box keeps the community drivers it has
    # installed. A driver's imports say nothing about what the frozen bundle
    # must declare, and reading them made this test pass or fail depending on
    # which drivers happened to be installed on the machine running it.
    skip_dirs = {"tests", "node_modules", ".git", "dist", "build", "data", ".venv", "venv"}

    # The package name here has to track the platform's. It said `server.` for a
    # while after the move to `openavc.`, and because nothing in the tree matched
    # any more, `referenced` came back empty and the assertion below passed
    # without looking at anything -- which is the one failure this test cannot
    # afford, since a green run is what says a new transport is safely bundled.
    pattern = re.compile(r"from\s+(openavc\.transport\.\w+)\s+import", re.MULTILINE)
    referenced = set()
    for py in REPO_ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in py.parts):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        referenced.update(pattern.findall(text))

    # Guard the guard: if the scan finds nothing, the assertion below is
    # satisfied by an empty set and reports success without having checked a
    # single import. That is exactly how this test went quiet after the package
    # move, so prove the scan still reaches real code before trusting its verdict.
    assert referenced, (
        "Found no function-level `from openavc.transport.X import` anywhere in "
        f"{REPO_ROOT} -- the scan is broken (wrong package name or over-eager "
        "skip_dirs), not the source. Fix the scan; a pass here means nothing."
    )

    missing = referenced - hidden
    assert not missing, (
        f"openavc.spec is missing transport hidden imports used in source: {missing}"
    )


def _data_dests() -> set[str]:
    """Extract the bundle-destination strings from the `datas = [...]` list.

    Same plain-text approach as `_hidden_imports` — the spec isn't importable
    outside PyInstaller.
    """
    src = SPEC_PATH.read_text(encoding="utf-8")
    m = re.search(r"^datas\s*=\s*\[(.*?)\n\]", src, re.DOTALL | re.MULTILINE)
    assert m, "Could not locate datas list in openavc.spec"
    return set(re.findall(r",\s*['\"]([^'\"]+)['\"]\)", m.group(1)))


def test_resource_dirs_are_bundled():
    """Every resource directory the frozen runtime resolves under APP_DIR
    (sys._MEIPASS) must be in the spec's datas, or the feature it backs goes
    silently missing on Windows/macOS installs while Docker/Pi/Linux (which
    copy openavc/ wholesale) keep it. openavc/templates is the case that bit:
    project_library.ensure_starter_projects silently no-ops without it, so
    installed builds had an empty starter-project library.
    """
    dests = _data_dests()
    required = {
        "openavc/templates",
        "openavc/drivers/definitions",
        "openavc/themes",
        "openavc/web/panel",
        "projects/default",
    }
    missing = required - dests
    assert not missing, f"openavc.spec datas is missing resource dirs: {missing}"
