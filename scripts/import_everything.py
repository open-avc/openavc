"""Load every platform module, and every driver in a driver library, on the
Python running this script.

Installs run more than one Python: the Linux installer takes 3.11 or newer
(3.14 on a host whose only Python is 3.14), the Pi image and appliance
hardware run Debian's 3.13, Docker and the frozen builds run 3.12. The test suite runs on 3.12 only, so CI runs this on the
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


def library_python_files(library: Path) -> list[Path]:
    """Every Python file in the library's category folders: drivers, simulators,
    discovery companions. Which files those are is the library's own rule, its
    scripts/catalog_files.py (standard library only), loaded by its path."""
    spec = importlib.util.spec_from_file_location(
        "_library_catalog_files", library / "scripts" / "catalog_files.py"
    )
    catalog_files = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(catalog_files)
    return [path for path in catalog_files.category_files(library) if path.suffix == ".py"]


def driver_files(library: Path) -> tuple[int, list[str]]:
    failures = []
    paths = library_python_files(library)
    for path in paths:
        category = path.parent.name
        name = f"_driver_{category}_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            failures.append(f"{category}/{path.name}\n{traceback.format_exc(limit=3)}")
    return len(paths), failures


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
