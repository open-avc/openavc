"""Load every platform module, and every driver in a driver library, on the
Python running this script.

Installs run more than one Python: the Linux installer takes 3.11 or newer,
the Pi image and appliance hardware run Debian's 3.13, Docker and the frozen
builds run 3.12. The test suite runs on 3.12 only, so CI runs this on the
others: a module or driver that fails to load on one of them fails here.

    python scripts/import_everything.py [path/to/openavc-drivers]

Exits non-zero, naming each file, when anything fails to load.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DRIVER_CATEGORIES = (
    "projectors", "displays", "switchers", "audio", "cameras",
    "video", "lighting", "power", "streaming", "utility",
)


def platform_modules() -> list[str]:
    sys.path.insert(0, str(ROOT))
    import openavc

    failures = []
    for info in pkgutil.walk_packages(openavc.__path__, "openavc."):
        if info.name.startswith(("openavc.web", "openavc.templates")):
            continue
        try:
            importlib.import_module(info.name)
        except Exception:
            failures.append(f"{info.name}\n{traceback.format_exc(limit=3)}")
    return failures


def driver_files(library: Path) -> tuple[int, list[str]]:
    failures = []
    count = 0
    for category in DRIVER_CATEGORIES:
        for path in sorted((library / category).glob("*.py")):
            count += 1
            name = f"_driver_{category}_{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                failures.append(f"{category}/{path.name}\n{traceback.format_exc(limit=3)}")
    return count, failures


def main() -> int:
    version = sys.version.split()[0]
    failures = platform_modules()
    print(f"Python {version}: platform modules loaded, {len(failures)} failed")
    if len(sys.argv) > 1:
        count, driver_failures = driver_files(Path(sys.argv[1]))
        print(f"Python {version}: {count} driver files loaded, {len(driver_failures)} failed")
        failures += driver_failures
    for failure in failures:
        print(f"\nFAILED {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
