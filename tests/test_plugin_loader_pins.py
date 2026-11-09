"""Pins for the plugin loader's own rules, result for result.

What a scan of the plugin folder finds and imports (the candidate order, the
package namespace, the purge of a file with no plugin class, ``.deps`` on the
import and library search paths); every refusal a manifest check gives, word
for word, and the order the checks run in; the extensions the panel and the
IDE receive; and the order of the start and stop sequences, including the
guarded ``stop()`` on both teardown paths and the router hooks.
"""

import asyncio
import logging
import os
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openavc.core import plugin_loader as loader_module
from openavc.core.event_bus import EventBus
from openavc.core.plugin_extensions import sanitize_state_pattern, sanitize_token_list
from openavc.core.plugin_import import exec_plugin_in_package as _exec_plugin_in_package
from openavc.core.plugin_loader import (
    PLUGIN_STOP_TIMEOUT,
    PluginLoader,
    _PLUGIN_CLASS_REGISTRY,
    get_platform_id,
    register_plugin_class,
    validate_extensions,
    validate_macro_actions,
    validate_script_api,
)
from openavc.core.plugin_api import PluginAPI
from openavc.core.plugin_registry import PluginRegistry
from openavc.core.state_store import StateStore
from openavc.version import __version__

LOGGER = "openavc.core.plugin_loader"


def _msgs(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno >= level]


def _recs(caplog, level):
    return [r for r in caplog.records if r.name == LOGGER and r.levelno >= level]


@pytest.fixture(autouse=True)
def _clean_registry_and_modules():
    saved = dict(_PLUGIN_CLASS_REGISTRY)
    before = set(sys.modules)
    yield
    _PLUGIN_CLASS_REGISTRY.clear()
    _PLUGIN_CLASS_REGISTRY.update(saved)
    for name in set(sys.modules) - before:
        if name.startswith("plugin_acme"):
            sys.modules.pop(name, None)


@pytest.fixture
def loader():
    state = StateStore()
    events = EventBus()
    state.set_event_bus(events)
    return PluginLoader(state, events, MagicMock(), MagicMock())


def _info(**over):
    info = {
        "id": "acme_widget", "name": "Acme Widget", "version": "1.0.0",
        "author": "Acme", "description": "A widget.", "category": "utility",
        "license": "MIT",
    }
    info.update(over)
    return info


def _cls(info=None, **attrs):
    body = {"PLUGIN_INFO": _info() if info is None else info}
    body.update(attrs)
    return type("AcmeWidgetPlugin", (), body)


def _plugin_src(pid, *, cls="AcmePlugin", head=""):
    body = textwrap.dedent(
        f"""
        class {cls}:
            PLUGIN_INFO = {{"id": "{pid}", "name": "Acme", "version": "1",
                           "author": "a", "description": "d",
                           "category": "utility", "license": "MIT"}}
        """
    )
    return f"{head}\n{body}"


# ──── Platform identity ────


@pytest.mark.parametrize("system, machine, expected", [
    ("Windows", "AMD64", "win_x64"),
    ("Linux", "x86_64", "linux_x64"),
    ("Linux", "aarch64", "linux_arm64"),
    ("Linux", "arm64", "linux_arm64"),
    ("Darwin", "arm64", "unknown"),
])
def test_platform_id(monkeypatch, system, machine, expected):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert get_platform_id() == expected


# ──── Scanning the plugin folder ────


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = tmp_path / "plugin_repo"
    root.mkdir()
    return root


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_missing_folder_finds_nothing(loader, tmp_path, caplog):
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        assert loader.scan_plugins(tmp_path / "absent") == {}
    assert f"Plugin repo directory not found: {tmp_path / 'absent'}" in caplog.text


def test_scan_finds_a_package_a_named_file_and_a_fallback(loader, repo, caplog):
    _write(repo / "acme_pkg" / "__init__.py", _plugin_src("acme_pkg"))
    _write(repo / "acme_named" / "acme_named_plugin.py", _plugin_src("acme_named"))
    _write(repo / "acme_loose" / "whatever.py", _plugin_src("acme_loose"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        found = loader.scan_plugins(repo)
    assert sorted(found) == ["acme_loose", "acme_named", "acme_pkg"]
    for pid, cls in found.items():
        assert _PLUGIN_CLASS_REGISTRY[pid] is cls
    assert found["acme_pkg"].__module__ == "plugin_acme_pkg"
    assert found["acme_named"].__module__ == "plugin_acme_named.acme_named_plugin"
    assert found["acme_loose"].__module__ == "plugin_acme_loose.whatever"
    assert f"Discovered 3 plugins from {repo}" in caplog.text


def test_dot_underscore_and_plain_files_are_skipped(loader, repo):
    _write(repo / ".acme_hidden" / "__init__.py", _plugin_src("acme_hidden"))
    _write(repo / "_acme_private" / "__init__.py", _plugin_src("acme_private"))
    _write(repo / "acme_file.py", _plugin_src("acme_file"))
    _write(repo / "acme_ok" / "__init__.py", _plugin_src("acme_ok"))
    assert list(loader.scan_plugins(repo)) == ["acme_ok"]


def test_folders_are_scanned_in_name_order(loader, repo):
    for name in ("acme_c", "acme_a", "acme_b"):
        _write(repo / name / "__init__.py", _plugin_src(name))
    assert list(loader.scan_plugins(repo)) == ["acme_a", "acme_b", "acme_c"]


def test_the_package_init_wins_over_the_named_file(loader, repo):
    _write(repo / "acme_dual" / "__init__.py", _plugin_src("acme_from_init"))
    _write(repo / "acme_dual" / "acme_dual_plugin.py", _plugin_src("acme_from_named"))
    assert list(loader.scan_plugins(repo)) == ["acme_from_init"]


def test_the_named_file_wins_over_an_earlier_name(loader, repo):
    _write(repo / "acme_order" / "aaa.py", _plugin_src("acme_from_aaa"))
    _write(repo / "acme_order" / "acme_order_plugin.py", _plugin_src("acme_from_named"))
    assert list(loader.scan_plugins(repo)) == ["acme_from_named"]


def test_other_files_go_in_name_order_and_private_ones_are_skipped(loader, repo):
    _write(repo / "acme_many" / "_first.py", _plugin_src("acme_from_private"))
    _write(repo / "acme_many" / "b.py", _plugin_src("acme_from_b"))
    _write(repo / "acme_many" / "a.py", _plugin_src("acme_from_a"))
    assert list(loader.scan_plugins(repo)) == ["acme_from_a"]


def test_a_class_needs_a_dict_plugin_info_with_an_id(loader, repo):
    _write(
        repo / "acme_shapes" / "__init__.py",
        textwrap.dedent(
            """
            class NoId:
                PLUGIN_INFO = {"name": "x"}
            class NotDict:
                PLUGIN_INFO = ["id"]
            not_a_class = SimpleNamespace = None
            """
        ),
    )
    _write(repo / "acme_shapes" / "real.py", _plugin_src("acme_real"))
    assert list(loader.scan_plugins(repo)) == ["acme_real"]


def test_a_file_with_no_plugin_class_is_purged_before_the_next(loader, repo):
    _write(repo / "acme_purge" / "helper.py", "VALUE = 1\n")
    _write(
        repo / "acme_purge" / "aaa.py",
        "from . import helper\nNOTHING = helper.VALUE\n",
    )
    _write(
        repo / "acme_purge" / "zzz.py",
        _plugin_src("acme_purge", head="import sys\nSEEN = sorted(m for m in sys.modules if m.startswith('plugin_acme_purge'))"),
    )
    found = loader.scan_plugins(repo)
    module = sys.modules[found["acme_purge"].__module__]
    assert module.SEEN == ["plugin_acme_purge", "plugin_acme_purge.zzz"]


def test_a_raising_file_is_logged_and_the_next_candidate_loads(loader, repo, caplog):
    _write(repo / "acme_raise" / "aaa.py", "raise RuntimeError('acme broke')\n")
    _write(repo / "acme_raise" / "bbb.py", _plugin_src("acme_after_raise"))
    _write(repo / "acme_later" / "__init__.py", _plugin_src("acme_later"))
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        found = loader.scan_plugins(repo)
    assert sorted(found) == ["acme_after_raise", "acme_later"]
    records = _recs(caplog, logging.ERROR)
    assert [r.getMessage() for r in records] == [
        f"Error loading plugin file {repo / 'acme_raise' / 'aaa.py'}"
    ]
    assert records[0].exc_info[0] is RuntimeError


def test_a_folder_whose_load_raises_is_logged_and_the_scan_goes_on(loader, repo, monkeypatch, caplog):
    _write(repo / "acme_bad" / "__init__.py", _plugin_src("acme_bad"))
    _write(repo / "acme_good" / "__init__.py", _plugin_src("acme_good"))
    real = loader_module.register_plugin_class

    def picky(cls):
        if cls.PLUGIN_INFO["id"] == "acme_bad":
            raise ValueError("no")
        real(cls)

    monkeypatch.setattr(loader_module, "register_plugin_class", picky)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        found = loader.scan_plugins(repo)
    assert list(found) == ["acme_good"]
    assert "Failed to load plugin from acme_bad" in caplog.text


def test_siblings_import_inside_the_plugins_own_package(loader, repo):
    for name in ("acme_one", "acme_two"):
        _write(repo / name / "helper.py", f"WHO = '{name}'\n")
        _write(
            repo / name / f"{name}_plugin.py",
            _plugin_src(name, head="from .helper import WHO"),
        )
    found = loader.scan_plugins(repo)
    assert sys.modules[found["acme_one"].__module__].WHO == "acme_one"
    assert sys.modules[found["acme_two"].__module__].WHO == "acme_two"
    assert "plugin_acme_one.helper" in sys.modules
    assert "plugin_acme_two.helper" in sys.modules
    assert "helper" not in sys.modules


def test_a_failed_import_rolls_back_only_what_it_added(repo):
    plugin_dir = repo / "acme_roll"
    _write(plugin_dir / "helper.py", "X = 1\n")
    _write(plugin_dir / "main.py", "from . import helper\nraise KeyError('acme')\n")
    sys.modules["plugin_acme_roll_other"] = SimpleNamespace()
    try:
        with pytest.raises(KeyError):
            _exec_plugin_in_package(plugin_dir / "main.py", plugin_dir)
        assert not [m for m in sys.modules if m.startswith("plugin_acme_roll.")]
        assert "plugin_acme_roll" not in sys.modules
        assert "plugin_acme_roll_other" in sys.modules
    finally:
        sys.modules.pop("plugin_acme_roll_other", None)


def test_a_failed_package_init_rolls_back_too(repo):
    plugin_dir = repo / "acme_rollpkg"
    _write(plugin_dir / "__init__.py", "from . import helper\nraise KeyError('acme')\n")
    _write(plugin_dir / "helper.py", "X = 1\n")
    with pytest.raises(KeyError):
        _exec_plugin_in_package(plugin_dir / "__init__.py", plugin_dir)
    assert not [m for m in sys.modules if m.startswith("plugin_acme_rollpkg")]


def test_an_existing_parent_package_is_reused(repo):
    plugin_dir = repo / "acme_reuse"
    _write(plugin_dir / "main.py", "X = 2\n")
    parent = SimpleNamespace(__path__=[str(plugin_dir)])
    sys.modules["plugin_acme_reuse"] = parent
    module = _exec_plugin_in_package(plugin_dir / "main.py", plugin_dir)
    assert sys.modules["plugin_acme_reuse"] is parent
    assert module.X == 2
    assert module.__name__ == "plugin_acme_reuse.main"
    assert module.__package__ == "plugin_acme_reuse"


def test_the_package_init_is_the_package(repo):
    plugin_dir = repo / "acme_initpkg"
    _write(plugin_dir / "__init__.py", "from .helper import X\n")
    _write(plugin_dir / "helper.py", "X = 3\n")
    module = _exec_plugin_in_package(plugin_dir / "__init__.py", plugin_dir)
    assert module is sys.modules["plugin_acme_initpkg"]
    assert module.X == 3
    assert module.__path__ == [str(plugin_dir)]


def test_deps_are_appended_to_the_import_path_once(loader, repo, monkeypatch):
    (repo / ".deps").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    deps = str(repo / ".deps")
    loader.scan_plugins(repo)
    loader.scan_plugins(repo)
    assert sys.path.count(deps) == 1
    assert sys.path[-1] == deps


def test_no_deps_folder_leaves_the_paths_alone(loader, repo, monkeypatch):
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/acme")
    before = list(sys.path)
    loader.scan_plugins(repo)
    assert sys.path == before
    assert os.environ["LD_LIBRARY_PATH"] == "/opt/acme"


def test_deps_go_in_front_of_the_linux_library_path(loader, repo, monkeypatch):
    (repo / ".deps").mkdir()
    deps = str(repo / ".deps")
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/acme")
    loader.scan_plugins(repo)
    loader.scan_plugins(repo)
    assert os.environ["LD_LIBRARY_PATH"] == f"{deps}:/opt/acme"


def test_deps_become_the_whole_linux_library_path_when_it_is_empty(loader, repo, monkeypatch):
    (repo / ".deps").mkdir()
    monkeypatch.setattr("platform.system", lambda: "Linux")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    loader.scan_plugins(repo)
    assert os.environ["LD_LIBRARY_PATH"] == str(repo / ".deps")


def test_deps_join_the_windows_dll_search(loader, repo, monkeypatch):
    (repo / ".deps").mkdir()
    deps = str(repo / ".deps")
    added = []
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", "/acme/bin")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    loader.scan_plugins(repo)
    loader.scan_plugins(repo)
    assert added == [deps, deps]
    assert os.environ["PATH"] == deps + os.pathsep + "/acme/bin"
    assert "LD_LIBRARY_PATH" not in os.environ


def test_a_refused_dll_directory_does_not_stop_the_scan(loader, repo, monkeypatch):
    (repo / ".deps").mkdir()
    _write(repo / "acme_win" / "__init__.py", _plugin_src("acme_win"))

    def refuse(_path):
        raise OSError("acme refused")

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(os, "add_dll_directory", refuse, raising=False)
    monkeypatch.setenv("PATH", "/acme/bin")
    assert list(loader.scan_plugins(repo)) == ["acme_win"]
    assert os.environ["PATH"] == "/acme/bin"


# ──── Manifest checks ────


def test_a_valid_manifest_passes(loader):
    assert loader.validate_manifest(_cls()) == (True, "")


@pytest.mark.parametrize("cls, message", [
    (type("NoInfo", (), {}), "Missing or invalid PLUGIN_INFO dict"),
    (type("ListInfo", (), {"PLUGIN_INFO": ["id"]}), "Missing or invalid PLUGIN_INFO dict"),
])
def test_no_manifest_dict(loader, cls, message):
    assert loader.validate_manifest(cls) == (False, message)


def _without(*keys):
    info = _info()
    for key in keys:
        info.pop(key)
    return info


@pytest.mark.parametrize("info, message", [
    (_without("author", "license"), "Missing required fields: ['author', 'license']"),
    (_info(license="GPL-3.0"), "License 'GPL-3.0' is not MIT-compatible"),
    (_info(category="bogus"),
     "Invalid category 'bogus', must be one of "
     "['control_surface', 'integration', 'sensor', 'utility']"),
    (_info(capabilities=["state_read", "fly", "dig"]), "Unknown capabilities: ['dig', 'fly']"),
    (_info(guest_alias=5),
     "Invalid guest_alias '5': must be one lowercase URL segment (letters, digits, "
     "hyphens, underscores; max 32 chars)"),
    (_info(guest_alias="Acme"),
     "Invalid guest_alias 'Acme': must be one lowercase URL segment (letters, digits, "
     "hyphens, underscores; max 32 chars)"),
    (_info(guest_alias="a" * 33),
     f"Invalid guest_alias '{'a' * 33}': must be one lowercase URL segment (letters, "
     "digits, hyphens, underscores; max 32 chars)"),
    (_info(guest_alias="api", capabilities=["guest_endpoints"]),
     "guest_alias 'api' is a reserved platform path"),
    (_info(guest_alias="acme"), "guest_alias requires the guest_endpoints capability"),
    (_info(network_ports=[{"port": 9000, "protocol": "tcp", "reason": "x"}]),
     "network_ports requires the network_listen capability"),
    (_info(platforms=["win_x64"]),
     "Plugin not compatible with current platform 'linux_x64'. Supported: ['win_x64']"),
    (_info(min_openavc_version="999.0.0"),
     f"Plugin requires OpenAVC v999.0.0 or later (current: v{__version__})"),
])
def test_each_manifest_refusal(loader, info, message):
    loader._platform_id = "linux_x64"
    assert loader.validate_manifest(_cls(info)) == (False, message)


def test_a_bad_ports_declaration_is_refused_as_plugin_ports_words_it(loader):
    from openavc.core.plugin_ports import validate_declaration

    bad = [{"port": "nope"}]
    ok, expected = validate_declaration(bad)
    assert not ok
    info = _info(network_ports=bad, capabilities=["network_listen"])
    assert loader.validate_manifest(_cls(info)) == (False, expected)


@pytest.mark.parametrize("info", [
    _info(license="  Apache-2.0 "),
    _info(guest_alias="acme-guest_1", capabilities=["guest_endpoints"]),
    _info(network_ports=[]),
    _info(network_ports=[{"port": 9000, "protocol": "tcp", "reason": "x"}],
          capabilities=["network_listen"]),
    _info(platforms=["all"]),
    _info(platforms=["linux_x64"]),
    _info(min_openavc_version="not a version"),
    _info(min_openavc_version="0.0.1"),
    _info(min_openavc_version=""),
])
def test_manifests_that_pass(loader, info):
    loader._platform_id = "linux_x64"
    assert loader.validate_manifest(_cls(info)) == (True, "")


def test_a_missing_packaging_skips_the_version_check(loader, monkeypatch):
    monkeypatch.setitem(sys.modules, "packaging.version", None)
    info = _info(min_openavc_version="999.0.0")
    assert loader.validate_manifest(_cls(info)) == (True, "")


def test_the_class_level_declarations_are_checked_after_the_manifest(loader):
    sync = _cls(CONFIG_SCHEMA=["x"])
    assert loader.validate_manifest(sync) == (False, "CONFIG_SCHEMA must be a dict")
    assert loader.validate_manifest(_cls(MACRO_ACTIONS=[])) == (
        False, "MACRO_ACTIONS invalid: MACRO_ACTIONS must be a dict")
    assert loader.validate_manifest(_cls(SCRIPT_API=[])) == (
        False, "SCRIPT_API invalid: SCRIPT_API must be a dict")
    assert loader.validate_manifest(_cls(EXTENSIONS=[])) == (
        False, "EXTENSIONS invalid: EXTENSIONS must be a dict")


def test_a_malformed_config_secrets_is_refused_as_plugin_secrets_words_it(loader):
    from openavc.core import plugin_secrets

    schema = {"key": {"type": "string"}}
    expected = plugin_secrets.config_secrets_problem("nope", schema)
    assert expected
    cls = _cls(CONFIG_SCHEMA=schema, CONFIG_SECRETS="nope")
    assert loader.validate_manifest(cls) == (False, expected)


def test_a_row_secret_is_warned_about_and_still_loads(loader, caplog):
    from openavc.core import plugin_secrets

    schema = {
        "rows": {
            "type": "mapping_list",
            "item_schema": {"token": {"type": "string", "secret": True}},
        },
    }
    wheres = list(plugin_secrets.unsupported_row_secrets(schema))
    assert wheres
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert loader.validate_manifest(_cls(CONFIG_SCHEMA=schema)) == (True, "")
    assert _msgs(caplog, logging.WARNING) == [
        f"Plugin 'acme_widget': {where} is marked secret, but a mapping_list row "
        f"has no id to save a login under, so it stays in the project"
        for where in wheres
    ]


def test_the_checks_run_in_their_order(loader):
    """Fix one problem at a time; each answer is the next check's."""
    loader._platform_id = "linux_x64"
    info = _info(
        license="GPL", category="bogus", capabilities=["fly"], guest_alias="api",
        network_ports=[{"port": 9000, "protocol": "tcp", "reason": "x"}],
        platforms=["win_x64"], min_openavc_version="999.0.0",
    )
    attrs = {"CONFIG_SCHEMA": [], "CONFIG_SECRETS": "nope", "MACRO_ACTIONS": [],
             "SCRIPT_API": [], "EXTENSIONS": []}
    fixes = [
        ("license", "MIT"), ("category", "utility"), ("capabilities", ["guest_endpoints"]),
        ("guest_alias", "acme"), ("network_ports", []), ("platforms", ["all"]),
        ("min_openavc_version", None),
    ]
    answers = []
    for key, value in fixes:
        answers.append(loader.validate_manifest(_cls(dict(info), **attrs))[1])
        if value is None:
            info.pop(key)
        else:
            info[key] = value
    for attr in list(attrs):
        answers.append(loader.validate_manifest(_cls(dict(info), **attrs))[1])
        attrs.pop(attr)
    answers.append(loader.validate_manifest(_cls(dict(info), **attrs))[1])
    assert answers == [
        "License 'GPL' is not MIT-compatible",
        "Invalid category 'bogus', must be one of ['control_surface', 'integration', 'sensor', 'utility']",
        "Unknown capabilities: ['fly']",
        "guest_alias 'api' is a reserved platform path",
        "network_ports requires the network_listen capability",
        "Plugin not compatible with current platform 'linux_x64'. Supported: ['win_x64']",
        f"Plugin requires OpenAVC v999.0.0 or later (current: v{__version__})",
        "CONFIG_SCHEMA must be a dict",
        "CONFIG_SECRETS must be a dict",
        "MACRO_ACTIONS invalid: MACRO_ACTIONS must be a dict",
        "SCRIPT_API invalid: SCRIPT_API must be a dict",
        "EXTENSIONS invalid: EXTENSIONS must be a dict",
        "",
    ]


def test_platform_compatibility(loader):
    loader._platform_id = "linux_x64"
    assert loader.is_platform_compatible(_cls())
    assert loader.is_platform_compatible(_cls(_info(platforms=["all"])))
    assert loader.is_platform_compatible(_cls(_info(platforms=["linux_x64", "win_x64"])))
    assert not loader.is_platform_compatible(_cls(_info(platforms=["win_x64"])))
    assert not loader.is_platform_compatible(_cls(_info(platforms=[])))
    assert loader.is_platform_compatible(type("Bare", (), {}))


# ──── MACRO_ACTIONS ────


async def _go(self, **params):
    return None


def _sync(self):
    return None


_ACTION_CLASS = type("Acme", (), {"go": _go, "sync_fn": _sync})


def _action(**spec):
    base = {"handler": "go", "params": []}
    base.update(spec)
    return base


@pytest.mark.parametrize("actions, message", [
    ([], "MACRO_ACTIONS must be a dict"),
    ({5: {}}, "action key 5 must be a string"),
    ({"other.go": _action()}, "action 'other.go' must be prefixed with the plugin id ('acme_widget.')"),
    ({"acme_widget.Go": _action()},
     "action 'acme_widget.Go' suffix must be lowercase letters, digits, or underscores (got 'Go')"),
    ({"acme_widget.": _action()},
     "action 'acme_widget.' suffix must be lowercase letters, digits, or underscores (got '')"),
    ({"acme_widget.go": []}, "action 'acme_widget.go' spec must be a dict"),
    ({"acme_widget.go": {}}, "action 'acme_widget.go' missing 'handler' (method name)"),
    ({"acme_widget.go": {"handler": 5}}, "action 'acme_widget.go' missing 'handler' (method name)"),
    ({"acme_widget.go": _action(handler="nope")},
     "action 'acme_widget.go' handler 'nope' not found on plugin class"),
    ({"acme_widget.go": _action(handler="sync_fn")},
     "action 'acme_widget.go' handler 'sync_fn' must be an async method"),
    ({"acme_widget.go": _action(params={})}, "action 'acme_widget.go' params must be a list"),
    ({"acme_widget.go": _action(params=["x"])}, "action 'acme_widget.go' param[0] must be a dict"),
    ({"acme_widget.go": _action(params=[{"type": "text"}])},
     "action 'acme_widget.go' param[0] missing 'key'"),
    ({"acme_widget.go": _action(params=[{"key": 5, "type": "text"}])},
     "action 'acme_widget.go' param[0] missing 'key'"),
    ({"acme_widget.go": _action(params=[{"key": "a", "type": "text"}, {"key": "a", "type": "text"}])},
     "action 'acme_widget.go' has duplicate param key 'a'"),
    ({"acme_widget.go": _action(params=[{"key": "a", "type": "blob"}])},
     "action 'acme_widget.go' param 'a' has invalid type 'blob' (allowed: "
     "['boolean', 'device_ref', 'float', 'integer', 'macro_ref', 'select', 'state_key', 'text'])"),
    ({"acme_widget.go": _action(params=[{"key": "a", "type": "select"}])},
     "action 'acme_widget.go' select param 'a' needs either 'options' (list) or "
     "'options_source' (state key)"),
    ({"acme_widget.go": _action(params=[{"key": "a", "type": "select", "options": "x"}])},
     "action 'acme_widget.go' select param 'a' needs either 'options' (list) or "
     "'options_source' (state key)"),
    ({"acme_widget.ok": _action(), "acme_widget.bad": _action(handler="nope")},
     "action 'acme_widget.bad' handler 'nope' not found on plugin class"),
])
def test_each_macro_action_refusal(actions, message):
    assert validate_macro_actions(actions, "acme_widget", _ACTION_CLASS) == (False, message)


@pytest.mark.parametrize("actions", [
    {},
    {"acme_widget.go": {"handler": "go"}},
    {"acme_widget.go_2": _action(params=[
        {"key": "a", "type": "select", "options": []},
        {"key": "b", "type": "select", "options_source": "plugin.acme_widget.list"},
        {"key": "c", "type": "integer"},
    ])},
])
def test_macro_actions_that_pass(actions):
    assert validate_macro_actions(actions, "acme_widget", _ACTION_CLASS) == (True, "")


# ──── SCRIPT_API ────


async def _async_fn(self):
    return None


_SCRIPT_CLASS = type("Acme", (), {"async_fn": _async_fn, "sync_fn": _sync})


@pytest.mark.parametrize("api, plugin_id, message", [
    ([], "acme_widget", "SCRIPT_API must be a dict"),
    ({}, "acme-widget",
     "plugin id 'acme-widget' is not a valid Python identifier — SCRIPT_API can't be "
     "exposed under openavc.plugins"),
    ({5: {}}, "acme_widget", "method key 5 must be a string"),
    ({"_hidden": {}}, "acme_widget",
     "method '_hidden' is not a valid identifier (lowercase start, letters/digits/underscores only)"),
    ({"Go": {}}, "acme_widget",
     "method 'Go' is not a valid identifier (lowercase start, letters/digits/underscores only)"),
    ({"register": {"handler": "async_fn"}}, "acme_widget", "method name 'register' is reserved"),
    ({"go": []}, "acme_widget", "method 'go' spec must be a dict"),
    ({"go": {}}, "acme_widget", "method 'go' missing 'handler' (method name)"),
    ({"go": {"handler": 3}}, "acme_widget", "method 'go' missing 'handler' (method name)"),
    ({"go": {"handler": "nope"}}, "acme_widget", "method 'go' handler 'nope' not found on plugin class"),
    ({"go": {"handler": "async_fn", "sync": True}}, "acme_widget",
     "method 'go' marked sync=True but handler 'async_fn' is async"),
    ({"go": {"handler": "sync_fn"}}, "acme_widget",
     "method 'go' handler 'sync_fn' is not async — add 'sync': True to SCRIPT_API entry if intentional"),
])
def test_each_script_api_refusal(api, plugin_id, message):
    assert validate_script_api(api, plugin_id, _SCRIPT_CLASS) == (False, message)


def test_script_api_that_passes():
    api = {
        "go": {"handler": "async_fn"},
        "now": {"handler": "sync_fn", "sync": True},
        "clearAll": {"handler": "async_fn", "sync": False},
    }
    assert validate_script_api(api, "acme_widget", _SCRIPT_CLASS) == (True, "")
    assert validate_script_api({}, "acme_widget", _SCRIPT_CLASS) == (True, "")


# ──── EXTENSIONS ────


@pytest.mark.parametrize("ext, message", [
    ([], "EXTENSIONS must be a dict"),
    ({"widgets": []},
     "unknown extension type 'widgets' (allowed: ['context_actions', 'device_panels', "
     "'panel_elements', 'status_cards', 'views'])"),
    ({"views": {}}, "extension type 'views' must be a list"),
    ({"views": ["x"]}, "views[0] must be a dict"),
    ({"views": [{"label": "x"}]}, "views[0] missing 'id' (string)"),
    ({"views": [{"id": 5}]}, "views[0] missing 'id' (string)"),
    ({"panel_elements": [{"id": "x"}]}, "panel_elements[0] missing 'type' (string)"),
    ({"views": [{"id": "main"}, {"id": "main"}]}, "views has duplicate id 'main'"),
    ({"panel_elements": [{"type": "meter"}, {"type": "meter"}]},
     "panel_elements has duplicate type 'meter'"),
])
def test_each_extensions_refusal(ext, message):
    assert validate_extensions(ext, "acme_widget", object) == (False, message)


def test_extensions_that_pass():
    ext = {
        "views": [{"id": "main"}, {"id": "other"}],
        "device_panels": [{"id": "main"}],
        "status_cards": [],
        "context_actions": [{"id": "a"}],
        "panel_elements": [{"type": "meter"}],
    }
    assert validate_extensions(ext, "acme_widget", object) == (True, "")


# ──── What the panel and the IDE receive ────


def _running(loader, pid, extensions, **info):
    cls = type(f"Ext_{pid}", (), {"PLUGIN_INFO": _info(id=pid, **info)})
    if extensions is not _NO_EXT:
        cls.EXTENSIONS = extensions
    loader._instances[pid] = cls()
    return cls


_NO_EXT = object()


def test_the_extensions_each_type_receives(loader, caplog):
    _running(loader, "acme_a", {
        "views": [{"id": "main", "label": "Main", "state_pattern": "plugin.acme_a.*"}],
        "device_panels": [{"id": "dp", "state_pattern": "device.*"}],
        "status_cards": [{"id": "card", "state_pattern": "   "}, {"id": "card2", "state_pattern": ""}],
        "context_actions": [{"id": "ctx", "state_pattern": 7}, {"label": "no id"}, {"label": "no id"}],
        "panel_elements": [{
            "type": "meter",
            "sandbox_permissions": ["allow-forms", "allow-top-navigation", "allow-forms", 3],
            "allow_features": "fullscreen",
            "state_pattern": " plugin.acme_a.level ",
        }],
    }, name="Acme A", capabilities=["state_read"])
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = loader.get_all_extensions()
    base = {"plugin_id": "acme_a", "plugin_name": "Acme A"}
    assert result == {
        "views": [{"id": "main", "label": "Main", "state_pattern": "plugin.acme_a.*", **base}],
        "device_panels": [{"id": "dp", "state_pattern": "plugin.acme_a.*", **base}],
        "status_cards": [{"id": "card", "state_pattern": "plugin.acme_a.*", **base},
                         {"id": "card2", "state_pattern": "plugin.acme_a.*", **base}],
        "context_actions": [
            {"id": "ctx", "state_pattern": "plugin.acme_a.*", **base},
            {"label": "no id", **base},
            {"label": "no id", **base},
        ],
        "panel_elements": [{
            "type": "meter",
            "sandbox_permissions": ["allow-forms"],
            "allow_features": [],
            "state_pattern": "plugin.acme_a.level",
            "capabilities": ["state_read"],
            **base,
        }],
    }
    assert list(result) == ["views", "device_panels", "status_cards", "context_actions", "panel_elements"]
    assert _msgs(caplog, logging.WARNING) == [
        "Plugin 'acme_a' state_pattern 'device.*' escapes its namespace; rewriting to 'plugin.acme_a.*'",
        "Plugin 'acme_a' state_pattern is not a string (7); using default 'plugin.acme_a.*'",
        "Plugin 'acme_a' sandbox_permissions requests unsupported token 'allow-top-navigation'; "
        "ignoring (allowed: ['allow-forms', 'allow-modals', 'allow-popups', 'allow-same-origin'])",
        "Plugin 'acme_a' sandbox_permissions contains non-string token 3; ignoring",
        "Plugin 'acme_a' allow_features is not a list ('fullscreen'); ignoring",
    ]


def test_the_first_plugin_to_claim_an_id_keeps_it(loader, caplog):
    _running(loader, "acme_first", {"views": [{"id": "main"}], "panel_elements": [{"type": "meter"}]})
    _running(loader, "acme_second", {"views": [{"id": "main"}, {"id": "own"}],
                                     "panel_elements": [{"type": "meter"}],
                                     "status_cards": [{"id": "main"}]})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = loader.get_all_extensions()
    assert [(e["plugin_id"], e["id"]) for e in result["views"]] == [
        ("acme_first", "main"), ("acme_second", "own")]
    assert [e["plugin_id"] for e in result["panel_elements"]] == ["acme_first"]
    assert [(e["plugin_id"], e["id"]) for e in result["status_cards"]] == [("acme_second", "main")]
    assert _msgs(caplog, logging.WARNING) == [
        "Plugin 'acme_second' views 'main' duplicates an already-registered id; skipping",
        "Plugin 'acme_second' panel_elements 'meter' duplicates an already-registered id; skipping",
    ]


def test_malformed_extensions_are_skipped_and_the_rest_still_served(loader, caplog):
    _running(loader, "acme_none", _NO_EXT)
    _running(loader, "acme_list", ["views"])
    _running(loader, "acme_mixed", {"views": "main", "status_cards": ["x", {"id": "ok"}]})
    broken = type("Broken", (), {"EXTENSIONS": {"views": [{"id": "lost"}]}})
    loader._instances["acme_broken"] = broken()
    _running(loader, "acme_last", {"views": [{"id": "last"}]})
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = loader.get_all_extensions()
    assert [(e["plugin_id"], e["id"]) for e in result["views"]] == [("acme_last", "last")]
    assert [(e["plugin_id"], e["id"]) for e in result["status_cards"]] == [("acme_mixed", "ok")]
    messages = _msgs(caplog, logging.WARNING)
    assert messages == [
        "Plugin 'acme_list' EXTENSIONS is not a dict (list); skipping",
        "Plugin 'acme_mixed' EXTENSIONS['views'] is not a list; skipping",
        "Plugin 'acme_mixed' status_cards entry is not a dict; skipping",
        "Failed to collect extensions for plugin 'acme_broken'",
    ]
    failed = [r for r in caplog.records if r.getMessage().startswith("Failed to collect")]
    assert failed[0].levelno == logging.ERROR and failed[0].exc_info[0] is AttributeError


def test_a_plugin_with_unknown_extension_types_serves_only_the_known_ones(loader):
    _running(loader, "acme_extra", {"widgets": [{"id": "w"}], "views": [{"id": "v"}]})
    result = loader.get_all_extensions()
    assert [e["id"] for e in result["views"]] == ["v"]
    assert "widgets" not in result


@pytest.mark.parametrize("extensions, expected", [
    (_NO_EXT, []),
    ([], []),
    ({"views": [{"id": "x"}]}, []),
    ({"panel_elements": {}}, []),
    ({"panel_elements": [
        "x",
        {"type": "a", "sandbox_permissions": ["allow-popups", "allow-forms", "allow-bogus"]},
        {"type": "b", "sandbox_permissions": ["allow-forms", "allow-same-origin"]},
        {"type": "c"},
    ]}, ["allow-popups", "allow-forms", "allow-same-origin"]),
])
def test_the_sandbox_grant_a_plugin_is_listed_with(loader, extensions, expected):
    cls = _running(loader, "acme_sb", extensions)
    register_plugin_class(cls)
    assert loader.list_plugins()[0]["sandbox_permissions"] == expected
    assert loader.get_plugin_info("acme_sb")["sandbox_permissions"] == expected


def test_the_sanitizers_alone():
    tokens = sanitize_token_list
    allowed = frozenset({"a", "b"})
    assert tokens(None, allowed, "p", "f") == []
    assert tokens(("a",), allowed, "p", "f") == []
    assert tokens(["b", "a", "b", "z", None], allowed, "p", "f") == ["b", "a"]
    pattern = sanitize_state_pattern
    assert pattern("plugin.p.x", "p", "plugin.p.") == "plugin.p.x"
    assert pattern("  plugin.p.{device_id}  ", "p", "plugin.p.") == "plugin.p.{device_id}"
    assert pattern("", "p", "plugin.p.") == "plugin.p.*"
    assert pattern(None, "p", "plugin.p.") == "plugin.p.*"
    assert pattern("plugin.px.y", "p", "plugin.p.") == "plugin.p.*"


# ──── Listing and describing a plugin ────


def test_a_listed_plugin_and_its_details(loader):
    cls = type("Acme", (), {
        "PLUGIN_INFO": _info(platforms=["all"], capabilities=["state_read"], guest_alias="acme",
                             usage="Use it.", dependencies=["acme-lib"]),
        "CONFIG_SCHEMA": {"a": {"type": "string"}},
        "EXTENSIONS": {"views": [{"id": "v"}]},
        "SURFACE_LAYOUT": {"rows": 1},
        "MACRO_ACTIONS": {"acme_widget.go": {"handler": "go", "label": "Go", "params": []}},
        "SCRIPT_API": {"go": {"handler": "go", "doc": "Goes."}},
    })
    register_plugin_class(cls)
    assert loader.list_plugins() == [{
        "plugin_id": "acme_widget", "name": "Acme Widget", "version": "1.0.0",
        "author": "Acme", "description": "A widget.", "category": "utility",
        "status": "stopped", "platforms": ["all"], "capabilities": ["state_read"],
        "sandbox_permissions": [], "guest_alias": "acme", "installed": True,
        "compatible": True,
    }]
    assert loader.get_plugin_info("acme_widget") == {
        "plugin_id": "acme_widget", "name": "Acme Widget", "version": "1.0.0",
        "author": "Acme", "description": "A widget.", "usage": "Use it.",
        "category": "utility", "license": "MIT", "status": "stopped",
        "platforms": ["all"], "capabilities": ["state_read"], "sandbox_permissions": [],
        "dependencies": ["acme-lib"], "installed": True, "compatible": True,
        "has_config_schema": True, "has_surface_layout": True, "has_extensions": True,
        "has_macro_actions": True, "has_script_api": True,
        "config_schema": {"a": {"type": "string"}},
        "extensions": {"views": [{"id": "v"}]},
        "surface_layout": {"rows": 1},
        "macro_actions": {"acme_widget.go": {"label": "Go", "params": []}},
        "script_api": {"go": {"doc": "Goes."}},
    }


def test_a_bare_plugin_and_its_details(loader):
    cls = type("Bare", (), {"PLUGIN_INFO": {"id": "acme_bare"},
                            "CONFIG_SCHEMA": {}, "EXTENSIONS": {}, "MACRO_ACTIONS": {}})
    register_plugin_class(cls)
    loader._status["acme_bare"] = "error"
    loader._errors["acme_bare"] = "Broke."
    assert loader.list_plugins()[0] == {
        "plugin_id": "acme_bare", "name": "acme_bare", "version": "", "author": "",
        "description": "", "category": "", "status": "error", "platforms": ["all"],
        "capabilities": [], "sandbox_permissions": [], "guest_alias": "",
        "installed": True, "compatible": True, "error": "Broke.",
    }
    assert loader.get_plugin_info("acme_bare") == {
        "plugin_id": "acme_bare", "name": "acme_bare", "version": "", "author": "",
        "description": "", "usage": "", "category": "", "license": "", "status": "error",
        "platforms": ["all"], "capabilities": [], "sandbox_permissions": [],
        "dependencies": [], "installed": True, "compatible": True,
        "has_config_schema": True, "has_surface_layout": False, "has_extensions": True,
        "has_macro_actions": False, "has_script_api": False, "error": "Broke.",
    }


def test_missing_and_incompatible_plugins_are_listed_after_the_installed(loader):
    register_plugin_class(_cls())
    loader._missing_plugins["acme_gone"] = {"plugin_id": "acme_gone", "config": {"a": 1},
                                            "reason": "Not here"}
    loader._missing_plugins["acme_widget"] = {"plugin_id": "acme_widget", "reason": "dup"}
    loader._incompatible_plugins["acme_far"] = {"plugin_id": "acme_far", "supported_platforms": ["win_x64"]}
    loader._incompatible_plugins["acme_gone"] = {"plugin_id": "acme_gone"}
    listed = loader.list_plugins()
    assert [p["plugin_id"] for p in listed] == ["acme_widget", "acme_gone", "acme_far"]
    assert listed[1] == {"plugin_id": "acme_gone", "name": "acme_gone", "status": "missing",
                         "installed": False, "compatible": True, "missing_reason": "Not here"}
    assert listed[2] == {"plugin_id": "acme_far", "name": "acme_far", "status": "incompatible",
                         "installed": True, "compatible": False,
                         "supported_platforms": ["win_x64"]}
    assert loader.get_plugin_info("acme_gone") == {
        "plugin_id": "acme_gone", "status": "missing", "installed": False,
        "config": {"a": 1}, "reason": "Not here"}
    assert loader.get_plugin_info("acme_nowhere") is None


def test_the_builder_lists_from_running_plugins(loader):
    cls = type("Acme", (), {
        "PLUGIN_INFO": _info(),
        "MACRO_ACTIONS": {
            "acme_widget.go": {"handler": "go", "label": "Go", "description": "Goes.",
                               "icon": "play", "params": [{"key": "a"}]},
            "acme_widget.bare": {"handler": "go"},
        },
        "SCRIPT_API": {"go": {"handler": "go", "doc": "Goes."}, "now": {"handler": "x", "sync": 1}},
    })
    loader._instances["acme_widget"] = cls()
    loader._instances["acme_plain"] = type("Plain", (), {"PLUGIN_INFO": {"id": "acme_plain"}})()
    assert loader.get_all_macro_actions() == [
        {"action_type": "acme_widget.go", "plugin_id": "acme_widget", "plugin_name": "Acme Widget",
         "label": "Go", "params": [{"key": "a"}], "description": "Goes.", "icon": "play"},
        {"action_type": "acme_widget.bare", "plugin_id": "acme_widget", "plugin_name": "Acme Widget",
         "label": "acme_widget.bare", "params": []},
    ]
    assert loader.get_all_script_api() == [
        {"plugin_id": "acme_widget", "plugin_name": "Acme Widget", "method": "go", "sync": False,
         "doc": "Goes."},
        {"plugin_id": "acme_widget", "plugin_name": "Acme Widget", "method": "now", "sync": True},
    ]


def test_validate_plugins_sorts_what_the_project_names(loader):
    register_plugin_class(_cls())
    register_plugin_class(_cls(_info(id="acme_far", name="Acme Far", platforms=["win_x64"])))
    loader._platform_id = "linux_x64"
    loader._status["acme_widget"] = "running"
    assert loader.validate_plugins({"acme_widget": {}, "acme_far": {}, "acme_gone": {}}) == {
        "available": [{"plugin_id": "acme_widget", "plugin_name": "Acme Widget",
                       "version": "1.0.0", "status": "running"}],
        "missing": [{"plugin_id": "acme_gone", "affected_config": True}],
        "platform_warnings": [{
            "plugin_id": "acme_far", "current_platform": "linux_x64",
            "supported_platforms": ["win_x64"],
            "message": "Plugin 'Acme Far' is not compatible with linux_x64",
        }],
    }


# ──── Start and stop, in order ────


class _Recorder:
    def __init__(self, loader, monkeypatch):
        self.calls = []
        self.loader = loader
        macros = loader._macros
        macros.register_plugin_action.side_effect = lambda *a: self.calls.append(("register_action", a[0]))
        macros.unregister_plugin_actions.side_effect = lambda pid: self.calls.append(("unregister_actions", pid))
        loader._events.on("plugin.*", self._event)
        loader.sync_declared_ports = lambda: self.calls.append(("sync_ports", None))
        cleanup = PluginRegistry.cleanup
        cancel = PluginAPI._cancel_all_tasks

        async def recorded_cleanup(registry, state, events):
            self.calls.append(("registry_cleanup", registry.plugin_id))
            await cleanup(registry, state, events)

        def recorded_cancel(api):
            self.calls.append(("cancel_api_tasks", api._plugin_id))
            cancel(api)

        monkeypatch.setattr(PluginRegistry, "cleanup", recorded_cleanup)
        monkeypatch.setattr(PluginAPI, "_cancel_all_tasks", recorded_cancel)

    async def _event(self, event, payload):
        self.calls.append(("event", event))

    def hooks(self, *, fail=()):
        def hook(name):
            def fn(*args):
                self.calls.append((name, args))
                if name in fail:
                    raise RuntimeError(f"{name} broke")
            return fn

        self.loader.set_router_hooks(hook("mount"), hook("unmount"),
                                     hook("mount_guest"), hook("unmount_guest"))


@pytest.fixture
def rec(loader, monkeypatch):
    return _Recorder(loader, monkeypatch)


def _lifecycle_plugin(rec, *, start_raises=None, stop=None, router=False, guest=False, panel_paths=("/p",)):
    async def go(self):
        return None

    async def start(self, api):
        rec.calls.append(("start", None))
        if router:
            api._registry.http_router = "router"
            api._registry.panel_ext_paths = list(panel_paths)
        if guest:
            api._registry.guest_router = "guest"
        if start_raises:
            raise start_raises

    async def stop_fn(self):
        rec.calls.append(("stop", None))
        if stop == "raise":
            raise RuntimeError("stop broke")
        if stop == "hang":
            await asyncio.sleep(10)

    return type("AcmeLife", (), {
        "PLUGIN_INFO": _info(capabilities=["guest_endpoints"], guest_alias="acme"),
        "MACRO_ACTIONS": {"acme_widget.go": {"handler": "go"}},
        "go": go, "start": start, "stop": stop_fn,
    })


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_a_start_in_order(loader, rec):
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec, router=True, guest=True))
    assert await loader.start_plugin("acme_widget") is True
    await _settle()
    assert rec.calls == [
        ("start", None),
        ("register_action", "acme_widget.go"),
        ("mount", ("acme_widget", "router", ["/p"], None)),
        ("mount_guest", ("acme_widget", "guest", "acme")),
        ("event", "plugin.started"),
        ("sync_ports", None),
    ]
    assert loader.get_plugin_status("acme_widget") == "running"


async def test_a_failed_mount_is_logged_and_the_start_stands(loader, caplog, rec):
    rec.hooks(fail=("mount", "mount_guest"))
    register_plugin_class(_lifecycle_plugin(rec, router=True, guest=True))
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert await loader.start_plugin("acme_widget") is True
    assert _msgs(caplog, logging.ERROR) == [
        "Failed to mount HTTP router for plugin 'acme_widget'",
        "Failed to mount guest HTTP router for plugin 'acme_widget'",
    ]
    assert loader.is_running("acme_widget")


async def test_no_router_means_no_mount_call(loader, rec):
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec))
    assert await loader.start_plugin("acme_widget") is True
    assert not [c for c in rec.calls if c[0].startswith("mount")]


async def test_a_failed_start_in_order(loader, caplog, rec):
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec, start_raises=ValueError("acme said no")))
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert await loader.start_plugin("acme_widget") is False
    await _settle()
    assert rec.calls == [
        ("start", None),
        ("unregister_actions", "acme_widget"),
        ("stop", None),
        ("registry_cleanup", "acme_widget"),
        ("event", "plugin.error"),
    ]
    assert loader._errors["acme_widget"] == "acme said no"
    assert _msgs(caplog, logging.ERROR) == [
        "Plugin 'acme_widget' failed to start: ValueError: acme said no",
    ]
    assert _recs(caplog, logging.ERROR)[0].exc_info[0] is ValueError


@pytest.mark.parametrize("stop, message", [
    ("raise", "Plugin 'acme_widget' stop() raised while cleaning up a failed start"),
    ("hang", f"Plugin 'acme_widget' stop() timed out after {0.05}s while cleaning up a failed start"),
])
async def test_a_failed_starts_stop_is_bounded_and_worded(loader, caplog, monkeypatch, stop, message, rec):
    monkeypatch.setattr(loader_module, "PLUGIN_STOP_TIMEOUT", 0.05)
    register_plugin_class(_lifecycle_plugin(rec, start_raises=ValueError("no"), stop=stop))
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert await loader.start_plugin("acme_widget") is False
    records = _recs(caplog, logging.ERROR)
    assert [r.getMessage() for r in records] == [
        "Plugin 'acme_widget' failed to start: ValueError: no", message,
    ]
    assert records[1].levelno == logging.ERROR
    assert (records[1].exc_info is not None) is (stop == "raise")


async def test_a_stop_in_order(loader, rec):
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec, router=True))
    await loader.start_plugin("acme_widget")
    await _settle()
    rec.calls.clear()
    await loader.stop_plugin("acme_widget")
    await _settle()
    assert rec.calls == [
        ("unregister_actions", "acme_widget"),
        ("stop", None),
        ("registry_cleanup", "acme_widget"),
        ("cancel_api_tasks", "acme_widget"),
        ("unmount", ("acme_widget",)),
        ("unmount_guest", ("acme_widget",)),
        ("event", "plugin.stopped"),
        ("sync_ports", None),
    ]
    assert loader.get_plugin_status("acme_widget") == "stopped"


@pytest.mark.parametrize("stop, message, traced", [
    ("raise", "Plugin 'acme_widget' stop() raised an exception", True),
    ("hang", "Plugin 'acme_widget' stop() timed out after 0.05s — continuing teardown", False),
])
async def test_a_stops_stop_is_bounded_and_worded(loader, caplog, monkeypatch, stop, message, traced, rec):
    monkeypatch.setattr(loader_module, "PLUGIN_STOP_TIMEOUT", 0.05)
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec, stop=stop))
    await loader.start_plugin("acme_widget")
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await loader.stop_plugin("acme_widget")
    records = _recs(caplog, logging.ERROR)
    assert [r.getMessage() for r in records] == [message]
    assert (records[0].exc_info is not None) is traced
    assert ("unmount", ("acme_widget",)) in rec.calls
    assert loader.get_plugin_status("acme_widget") == "stopped"


async def test_a_failed_unmount_is_logged_and_the_stop_finishes(loader, caplog, rec):
    rec.hooks(fail=("unmount", "unmount_guest"))
    register_plugin_class(_lifecycle_plugin(rec))
    await loader.start_plugin("acme_widget")
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        await loader.stop_plugin("acme_widget")
    await _settle()
    assert _msgs(caplog, logging.ERROR) == [
        "Failed to unmount HTTP router for plugin 'acme_widget'",
        "Failed to unmount guest HTTP router for plugin 'acme_widget'",
    ]
    assert rec.calls[-2:] == [("event", "plugin.stopped"), ("sync_ports", None)]


async def test_stopping_a_plugin_that_never_ran_still_reports_stopped(loader, rec):
    rec.hooks()
    await loader.stop_plugin("acme_ghost")
    await _settle()
    assert rec.calls == [
        ("unregister_actions", "acme_ghost"),
        ("unmount", ("acme_ghost",)),
        ("unmount_guest", ("acme_ghost",)),
        ("event", "plugin.stopped"),
        ("sync_ports", None),
    ]
    assert loader.get_plugin_status("acme_ghost") == "stopped"


async def test_a_start_for_an_unregistered_class_is_refused_quietly(loader, caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert await loader.start_plugin("acme_nothing") is False
    assert "Plugin 'acme_nothing' class not found in registry" in caplog.text
    assert loader.get_plugin_status("acme_nothing") == "unknown"


async def test_a_success_resets_the_failure_streak(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec))
    await loader.start_plugin("acme_widget")
    api = loader._apis["acme_widget"]
    for _ in range(loader_module.MAX_CALLBACK_FAILURES - 1):
        api._failure_reporter()
    assert loader._callback_failures["acme_widget"] == loader_module.MAX_CALLBACK_FAILURES - 1
    api._success_reporter()
    assert "acme_widget" not in loader._callback_failures
    for _ in range(loader_module.MAX_CALLBACK_FAILURES - 1):
        api._failure_reporter()
    await _settle()
    assert loader.is_running("acme_widget")
    api._failure_reporter()
    api._failure_reporter()
    assert loader._callback_failures["acme_widget"] == loader_module.MAX_CALLBACK_FAILURES
    for _ in range(10):
        await asyncio.sleep(0)
    assert not loader.is_running("acme_widget")
    assert loader._errors["acme_widget"] == (
        f"Auto-disabled after {loader_module.MAX_CALLBACK_FAILURES} consecutive callback failures"
    )


def test_the_default_stop_timeout_is_what_the_messages_quote():
    assert PLUGIN_STOP_TIMEOUT == 10.0


async def test_empty_panel_paths_reach_the_mount_as_none(loader, rec):
    rec.hooks()
    register_plugin_class(_lifecycle_plugin(rec, router=True, panel_paths=()))
    assert await loader.start_plugin("acme_widget") is True
    assert ("mount", ("acme_widget", "router", None, None)) in rec.calls


async def test_a_failed_start_leaves_the_ports_alone(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec, start_raises=ValueError("no")))
    assert await loader.start_plugin("acme_widget") is False
    assert ("sync_ports", None) not in rec.calls


async def test_a_stop_clears_what_the_run_left(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec))
    await loader.start_plugin("acme_widget")
    loader._apis["acme_widget"]._failure_reporter()
    loader._set_error("acme_widget", "Broke.", unmarked=True)
    assert loader._callback_failures == {"acme_widget": 1}
    assert "acme_widget" in loader._instance_epoch
    await loader.stop_plugin("acme_widget")
    assert "acme_widget" not in loader._callback_failures
    assert "acme_widget" not in loader._instance_epoch
    assert "acme_widget" not in loader._errors
    assert "acme_widget" not in loader._unmarked_errors


async def test_stopping_a_plugin_in_error_clears_the_error(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec, start_raises=ValueError("acme said no")))
    await loader.start_plugin("acme_widget")
    assert loader.get_plugin_status("acme_widget") == "error"
    await loader.stop_plugin("acme_widget")
    assert loader.get_plugin_status("acme_widget") == "stopped"
    assert "acme_widget" not in loader._errors
    assert loader.error_for_cloud("acme_widget") is None


async def test_a_start_ends_a_pending_auto_disable_mark(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec))
    loader._auto_disabling.add("acme_widget")
    await loader.start_plugin("acme_widget")
    assert "acme_widget" not in loader._auto_disabling
    loader._apis["acme_widget"]._failure_reporter()
    assert loader._callback_failures == {"acme_widget": 1}


def test_an_install_registers_the_class_with_its_own_id(tmp_path, monkeypatch):
    """The search an install makes takes the class whose id is the plugin's,
    not the first plugin class in the file."""
    from openavc.core import plugin_installer as installer

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(installer, "PLUGIN_REPO_DIR", tmp_path)
    plugin_dir = tmp_path / "acme_pair"
    _write(
        plugin_dir / "acme_pair_plugin.py",
        _plugin_src("acme_other", cls="AaaPlugin") + _plugin_src("acme_pair", cls="BbbPlugin"),
    )
    assert installer._register_installed_plugin("acme_pair", plugin_dir) is None
    assert _PLUGIN_CLASS_REGISTRY["acme_pair"].__name__ == "BbbPlugin"
    assert "acme_other" not in _PLUGIN_CLASS_REGISTRY


async def test_a_stop_without_router_hooks_logs_nothing(loader, rec, caplog):
    register_plugin_class(_lifecycle_plugin(rec, router=True))
    await loader.start_plugin("acme_widget")
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        await loader.stop_plugin("acme_widget")
    assert _msgs(caplog, logging.WARNING) == []
    assert loader.get_plugin_status("acme_widget") == "stopped"


async def test_a_stop_ends_a_pending_auto_disable_mark(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec))
    await loader.start_plugin("acme_widget")
    loader._auto_disabling.add("acme_widget")
    await loader.stop_plugin("acme_widget")
    assert "acme_widget" not in loader._auto_disabling


def test_an_install_puts_deps_on_the_end_of_the_import_path(tmp_path, monkeypatch):
    from openavc.core import plugin_installer as installer

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(installer, "PLUGIN_REPO_DIR", tmp_path)
    (tmp_path / ".deps").mkdir()
    plugin_dir = tmp_path / "acme_deps"
    _write(plugin_dir / "acme_deps_plugin.py", _plugin_src("acme_deps"))
    assert installer._register_installed_plugin("acme_deps", plugin_dir) is None
    assert installer._register_installed_plugin("acme_deps", plugin_dir) is None
    assert sys.path.count(str(tmp_path / ".deps")) == 1
    assert sys.path[-1] == str(tmp_path / ".deps")


async def test_starting_a_running_plugin_with_no_config_changes_nothing(loader, rec):
    register_plugin_class(_lifecycle_plugin(rec))
    await loader.start_plugin("acme_widget", {"level": 3})
    assert await loader.start_plugin("acme_widget") is True
    assert [c for c in rec.calls if c[0] in ("start", "stop")] == [("start", None)]
    assert loader.get_running_config("acme_widget") == {"level": 3}
