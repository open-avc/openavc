"""Finding a plugin's code in its folder and importing it.

Each plugin imports as its own package, ``plugin_<folder>``, whose ``__path__``
is the plugin's folder. A multi-file plugin's modules import each other
relatively (``from . import helper``) and land in ``sys.modules`` under that
package, so two plugins that both ship a ``helper.py`` never replace each
other's, and no plugin folder goes on ``sys.path``. An import that fails takes
back every module it added, so a broken file cannot leave half a plugin behind
for the next one. The startup scan (``PluginLoader.scan_plugins``) and an
install (``plugin_installer._register_installed_plugin``) both import through
here; each picks its own candidate files.

``.deps`` is the folder plugin dependencies are installed into. It goes on the
END of ``sys.path``, so a package a plugin brought can never shadow the
standard library or one of OpenAVC's own; the native-library half is
``plugin_native_deps.inject_native_lib_paths``.

Logs as ``openavc.core.plugin_loader``: the Programmer's plugin page shows
that logger's lines beside the plugin's own.
"""

import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from openavc.utils.logger import get_logger

log = get_logger("openavc.core.plugin_loader")


def _in_package(module_name: str, package_name: str) -> bool:
    return module_name == package_name or module_name.startswith(package_name + ".")


def purge_plugin_modules(package_name: str) -> None:
    """Remove a plugin's package and all its submodules from sys.modules."""
    for name in [m for m in sys.modules if _in_package(m, package_name)]:
        sys.modules.pop(name, None)


def exec_plugin_in_package(filepath: Path, plugin_dir: Path) -> ModuleType | None:
    """Import a plugin file inside its folder's package, ``plugin_<folder>``.

    ``__init__.py`` is the package itself; any other file is a module of it,
    under a parent package made here when it is not loaded yet. On any
    exception every module of the package this call added is removed again.
    Returns the executed module, or ``None`` if the file has no importable spec.
    """
    package_name = f"plugin_{plugin_dir.name}"
    before = set(sys.modules)
    try:
        if filepath.name == "__init__.py":
            spec = importlib.util.spec_from_file_location(
                package_name, filepath,
                submodule_search_locations=[str(plugin_dir)],
            )
            return _exec_spec(package_name, spec)
        _ensure_parent_package(package_name, plugin_dir)
        # A plain module, not a package: its relative imports resolve through
        # the parent's __path__, and passing submodule_search_locations here
        # would make __spec__.parent disagree with __package__.
        sub_name = f"{package_name}.{filepath.stem}"
        return _exec_spec(sub_name, importlib.util.spec_from_file_location(sub_name, filepath))
    except BaseException:
        for name in set(sys.modules) - before:
            if _in_package(name, package_name):
                sys.modules.pop(name, None)
        raise


def _ensure_parent_package(package_name: str, plugin_dir: Path) -> None:
    if package_name in sys.modules:
        return
    pkg_spec = importlib.machinery.ModuleSpec(package_name, None, is_package=True)
    pkg = importlib.util.module_from_spec(pkg_spec)
    pkg.__path__ = [str(plugin_dir)]
    sys.modules[package_name] = pkg


def _exec_spec(name: str, spec) -> ModuleType | None:
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def plugin_class_in(module: ModuleType, plugin_id: str | None = None) -> type | None:
    """The first class in ``module`` (by attribute name) whose PLUGIN_INFO is a
    dict with an id, or, given ``plugin_id``, with that id."""
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and _declares_plugin(attr, plugin_id):
            return attr
    return None


def _declares_plugin(cls: type, plugin_id: str | None) -> bool:
    info = getattr(cls, "PLUGIN_INFO", None)
    if not isinstance(info, dict):
        return False
    if plugin_id is None:
        return "id" in info
    return info.get("id") == plugin_id


def plugin_folders(plugin_repo_dir: Path) -> list[Path]:
    """The folders of a plugin repo that may hold a plugin, in name order.

    A name starting with ``.`` or ``_`` is skipped (``.deps`` among them).
    """
    return [
        entry for entry in sorted(plugin_repo_dir.iterdir())
        if entry.is_dir() and not entry.name.startswith((".", "_"))
    ]


def find_plugin_class(plugin_dir: Path) -> type | None:
    """Import a plugin folder's candidate files in turn; the first plugin class found.

    The candidates: ``__init__.py``, then ``<folder>_plugin.py``, then every
    other ``.py`` in name order except those starting with ``_``. A file that
    holds no plugin class has its modules purged before the next one runs; a
    file that raises is logged and the next one tried.
    """
    for filepath in _candidate_files(plugin_dir):
        plugin_class = _load_plugin_class(filepath, plugin_dir)
        if plugin_class:
            return plugin_class
    return None


def _candidate_files(plugin_dir: Path) -> list[Path]:
    first = [plugin_dir / "__init__.py", plugin_dir / f"{plugin_dir.name}_plugin.py"]
    candidates = [path for path in first if path.exists()]
    candidates.extend(
        py_file for py_file in sorted(plugin_dir.glob("*.py"))
        if not py_file.name.startswith("_") and py_file not in candidates
    )
    return candidates


def _load_plugin_class(filepath: Path, plugin_dir: Path) -> type | None:
    try:
        module = exec_plugin_in_package(filepath, plugin_dir)
        if module is None:
            return None
        plugin_class = plugin_class_in(module)
        if plugin_class is not None:
            return plugin_class
        purge_plugin_modules(f"plugin_{plugin_dir.name}")
    except Exception:  # Catch-all: exec_module runs arbitrary plugin code
        log.exception(f"Error loading plugin file {filepath}")
    return None


def add_deps_to_import_path(deps_dir: Path) -> None:
    """Append ``.deps`` to ``sys.path`` once, if it exists (never in front:
    it holds packages plugins need, not replacements for ours)."""
    deps_path = str(deps_dir)
    if os.path.isdir(deps_path) and deps_path not in sys.path:
        sys.path.append(deps_path)


def make_deps_loadable(deps_dir: Path) -> None:
    """Put an existing ``.deps`` where Python and the native-library loader look."""
    if not deps_dir.is_dir():
        return
    add_deps_to_import_path(deps_dir)
    # Imported here: plugin_native_deps brings the download stack with it,
    # which importing the loader does not otherwise need.
    from openavc.core.plugin_native_deps import inject_native_lib_paths

    inject_native_lib_paths(deps_dir)
