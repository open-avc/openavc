"""The tray app and the menu bar app, loaded by path for the tests.

installer/tray.py and installer/menubar.py are standalone scripts, each frozen
on its own by PyInstaller, that import their shared reader
(installer/status_reader.py) from their own folder. Loaded here by path, that
folder is not on sys.path, so the reader is loaded first under its own name.
The menu bar is loaded with a stand-in for rumps, which exists only on macOS:
the functions under test never touch it, only the class body names it.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "installer"


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_status_reader() -> types.ModuleType:
    """installer/status_reader.py, the one both scripts import."""
    if "status_reader" not in sys.modules:
        sys.modules["status_reader"] = _load("status_reader", INSTALLER / "status_reader.py")
    return sys.modules["status_reader"]


def load_tray(name: str) -> types.ModuleType:
    load_status_reader()
    return _load(name, INSTALLER / "tray.py")


def load_menubar(name: str) -> types.ModuleType:
    load_status_reader()
    rumps = types.ModuleType("rumps")
    rumps.App = type("App", (), {})
    rumps.MenuItem = rumps.Timer = object
    saved = sys.modules.get("rumps")
    sys.modules["rumps"] = rumps
    try:
        return _load(name, INSTALLER / "menubar.py")
    finally:
        if saved is None:
            del sys.modules["rumps"]
        else:
            sys.modules["rumps"] = saved
