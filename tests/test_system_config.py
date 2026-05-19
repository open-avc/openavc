"""Tests for server.system_config — system.json layered configuration."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


from server.system_config import (
    APP_DIR,
    DRIVER_DEFINITIONS_DIR,
    DRIVER_REPO_DIR,
    INSTALL_DIR,
    PLUGIN_REPO_DIR,
    PYPROJECT_PATH,
    THEMES_DIR,
    SystemConfig,
    _deep_merge,
    _is_dev_environment,
    _parse_env_value,
    _resolve_app_dir,
    _resolve_install_dir,
    get_system_config,
    reset_system_config,
)


class TestDeepMerge:
    def test_simple_override(self):
        base = {"network": {"http_port": 8080, "bind_address": "127.0.0.1"}}
        override = {"network": {"http_port": 9090}}
        result = _deep_merge(base, override)
        assert result["network"]["http_port"] == 9090
        assert result["network"]["bind_address"] == "127.0.0.1"

    def test_ignores_unknown_keys(self):
        base = {"network": {"http_port": 8080}}
        override = {"network": {"http_port": 9090}, "unknown_section": {"foo": "bar"}}
        result = _deep_merge(base, override)
        assert "unknown_section" not in result

    def test_empty_override(self):
        base = {"network": {"http_port": 8080}}
        result = _deep_merge(base, {})
        assert result == base

    def test_nested_merge(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        result = _deep_merge(base, override)
        assert result["a"]["b"]["c"] == 99
        assert result["a"]["b"]["d"] == 2


class TestParseEnvValue:
    def test_bool_true(self):
        assert _parse_env_value("true", bool) is True
        assert _parse_env_value("True", bool) is True
        assert _parse_env_value("1", bool) is True
        assert _parse_env_value("yes", bool) is True

    def test_bool_false(self):
        assert _parse_env_value("false", bool) is False
        assert _parse_env_value("0", bool) is False
        assert _parse_env_value("no", bool) is False

    def test_int(self):
        assert _parse_env_value("9090", int) == 9090

    def test_int_invalid(self):
        assert _parse_env_value("not_a_number", int) is None

    def test_str(self):
        assert _parse_env_value("hello", str) == "hello"


class TestSystemConfig:
    def test_load_defaults(self, tmp_path):
        """Config loads with defaults when no system.json exists."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        assert cfg.get("network", "http_port") == 8080
        assert cfg.get("network", "bind_address") == "127.0.0.1"
        assert cfg.get("cloud", "enabled") is False
        assert cfg.get("updates", "channel") == "stable"

    def test_tls_defaults(self, tmp_path):
        """TLS section defaults: disabled, port 8443, auto_generate on, redirect on."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        assert cfg.get("tls", "enabled") is False
        assert cfg.get("tls", "port") == 8443
        assert cfg.get("tls", "auto_generate") is True
        assert cfg.get("tls", "cert_file") == ""
        assert cfg.get("tls", "key_file") == ""
        assert cfg.get("tls", "redirect_http") is True

    def test_tls_section_appears_for_legacy_system_json(self, tmp_path):
        """A system.json written before TLS existed still gets the tls section via DEFAULTS merge."""
        system_json = tmp_path / "system.json"
        system_json.write_text(json.dumps({
            "network": {"http_port": 9090},
            "cloud": {"enabled": True},
        }))

        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = system_json
        cfg.load()

        assert cfg.get("tls", "enabled") is False
        assert cfg.get("tls", "port") == 8443

    def test_tls_env_overrides(self, tmp_path):
        """OPENAVC_TLS_* env vars override defaults."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"

        with patch.dict(os.environ, {
            "OPENAVC_TLS_ENABLED": "true",
            "OPENAVC_TLS_PORT": "9443",
            "OPENAVC_TLS_AUTO_GENERATE": "false",
            "OPENAVC_TLS_CERT_FILE": "/etc/ssl/server.crt",
            "OPENAVC_TLS_KEY_FILE": "/etc/ssl/server.key",
            "OPENAVC_TLS_REDIRECT_HTTP": "false",
        }):
            cfg.load()

        assert cfg.get("tls", "enabled") is True
        assert cfg.get("tls", "port") == 9443
        assert cfg.get("tls", "auto_generate") is False
        assert cfg.get("tls", "cert_file") == "/etc/ssl/server.crt"
        assert cfg.get("tls", "key_file") == "/etc/ssl/server.key"
        assert cfg.get("tls", "redirect_http") is False

    def test_load_from_file(self, tmp_path):
        """Config merges values from system.json."""
        system_json = tmp_path / "system.json"
        system_json.write_text(json.dumps({
            "network": {"http_port": 9090},
            "cloud": {"enabled": True, "system_id": "test-123"},
        }))

        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = system_json
        cfg.load()

        assert cfg.get("network", "http_port") == 9090
        assert cfg.get("network", "bind_address") == "127.0.0.1"  # default kept
        assert cfg.get("cloud", "enabled") is True
        assert cfg.get("cloud", "system_id") == "test-123"

    def test_env_overrides_file(self, tmp_path):
        """Environment variables override system.json values."""
        system_json = tmp_path / "system.json"
        system_json.write_text(json.dumps({
            "network": {"http_port": 9090},
        }))

        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = system_json

        with patch.dict(os.environ, {"OPENAVC_PORT": "7070"}):
            cfg.load()

        assert cfg.get("network", "http_port") == 7070  # env wins

    def test_save_creates_file(self, tmp_path):
        """save() creates system.json in data directory."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()
        cfg.save()

        assert cfg._file_path.exists()
        saved = json.loads(cfg._file_path.read_text())
        assert saved["network"]["http_port"] == 8080

    def test_ensure_file_creates_only_once(self, tmp_path):
        """ensure_file() creates if missing, skips if exists."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        cfg.ensure_file()
        assert cfg._file_path.exists()

        # Modify the file and ensure_file should NOT overwrite
        cfg.set("network", "http_port", 1234)
        cfg.save()
        cfg.ensure_file()  # should be no-op
        saved = json.loads(cfg._file_path.read_text())
        assert saved["network"]["http_port"] == 1234

    def test_section(self, tmp_path):
        """section() returns a copy of a config section."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        net = cfg.section("network")
        assert net["http_port"] == 8080
        # Modifying returned dict doesn't affect config
        net["http_port"] = 9999
        assert cfg.get("network", "http_port") == 8080

    def test_set_and_get(self, tmp_path):
        """set() updates in-memory values."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        cfg.set("network", "http_port", 5555)
        assert cfg.get("network", "http_port") == 5555

    def test_invalid_json_falls_back_to_defaults(self, tmp_path):
        """Corrupt system.json falls back to defaults."""
        system_json = tmp_path / "system.json"
        system_json.write_text("not valid json {{{")

        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = system_json
        cfg.load()

        assert cfg.get("network", "http_port") == 8080

    def test_to_dict_is_deep_copy(self, tmp_path):
        """to_dict() returns a deep copy."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()

        d = cfg.to_dict()
        d["network"]["http_port"] = 9999
        assert cfg.get("network", "http_port") == 8080


class TestSingleton:
    def test_get_system_config_returns_same_instance(self):
        reset_system_config()
        a = get_system_config()
        b = get_system_config()
        assert a is b
        reset_system_config()

    def test_reset_clears_singleton(self):
        reset_system_config()
        a = get_system_config()
        reset_system_config()
        b = get_system_config()
        assert a is not b
        reset_system_config()


class TestPathResolution:
    """Tests for centralized path resolution (APP_DIR, INSTALL_DIR, derived paths)."""

    def test_app_dir_points_to_repo_root_in_dev(self):
        """In dev mode, APP_DIR is the openavc/ repo root."""
        assert (APP_DIR / "pyproject.toml").exists()
        assert (APP_DIR / "server").is_dir()

    def test_install_dir_equals_app_dir_in_dev(self):
        """In dev mode, INSTALL_DIR is the same as APP_DIR."""
        assert INSTALL_DIR == APP_DIR

    def test_derived_paths_relative_to_app_dir(self):
        """Bundle-relative paths are under APP_DIR; user-content repos live
        under the data directory so they survive Docker image pulls and
        Windows installer upgrades (which both rewrite APP_DIR)."""
        # User-installed repos must NOT live under APP_DIR — that's the bug
        # the data_dir move fixed. They live under the data directory, which
        # is captured at import time.
        assert DRIVER_REPO_DIR.name == "driver_repo"
        assert PLUGIN_REPO_DIR.name == "plugin_repo"
        assert DRIVER_REPO_DIR.parent == PLUGIN_REPO_DIR.parent
        assert DRIVER_REPO_DIR.parent.name == "data" or DRIVER_REPO_DIR.parent.parent != APP_DIR
        # Bundle resources still anchor to APP_DIR
        assert THEMES_DIR == APP_DIR / "themes"
        assert DRIVER_DEFINITIONS_DIR == APP_DIR / "server" / "drivers" / "definitions"
        assert PYPROJECT_PATH == APP_DIR / "pyproject.toml"

    def test_resolve_app_dir_frozen(self):
        """When frozen, _resolve_app_dir returns sys._MEIPASS."""
        fake_meipass = "/fake/bundle/_internal"
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", fake_meipass, create=True):
            result = _resolve_app_dir()
        assert result == Path(fake_meipass)

    def test_resolve_install_dir_frozen(self):
        """When frozen, _resolve_install_dir returns the exe's parent directory."""
        fake_exe = str(Path("/fake/install/openavc-server.exe").resolve())
        expected = Path(fake_exe).parent
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "executable", fake_exe):
            result = _resolve_install_dir()
        assert result == expected

    def test_resolve_install_dir_different_from_app_dir_frozen(self):
        """In frozen builds, INSTALL_DIR (exe parent) != APP_DIR (_MEIPASS)."""
        fake_meipass = "/fake/install/_internal"
        fake_exe = str(Path("/fake/install/openavc-server.exe").resolve())
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", fake_meipass, create=True), \
             patch.object(sys, "executable", fake_exe):
            app = _resolve_app_dir()
            install = _resolve_install_dir()
        assert app == Path(fake_meipass)
        assert install == Path(fake_exe).parent
        assert app != install

    def test_systemconfig_properties_use_constants(self, tmp_path):
        """SystemConfig path properties delegate to module-level constants."""
        cfg = SystemConfig()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        assert cfg.driver_repo_path == DRIVER_REPO_DIR
        assert cfg.plugin_repo_path == PLUGIN_REPO_DIR
        assert cfg.themes_dir == THEMES_DIR


class TestDevEnvironmentDetection:
    def test_detects_dev_from_pyproject(self):
        # We're running tests from the repo, so this should be True
        assert _is_dev_environment() is True

    def test_returns_false_when_frozen(self):
        """Frozen builds are never dev environments."""
        with patch.object(sys, "frozen", True, create=True):
            assert _is_dev_environment() is False


class TestMigrateLegacyRepos:
    """The migration shim moves APP_DIR/{driver,plugin}_repo into data_dir
    on first start of a new release. It must be idempotent and conservative
    (never overwrite populated destinations, never delete unless the move
    fully succeeded)."""

    def _run(self, tmp_path, *, legacy_plugin=None, legacy_driver=None,
             dest_plugin_existing=None, dest_driver_existing=None):
        legacy_root = tmp_path / "app"
        data_root = tmp_path / "data"
        (legacy_root).mkdir()
        (data_root).mkdir()
        if legacy_plugin:
            (legacy_root / "plugin_repo").mkdir()
            for name, content in legacy_plugin.items():
                p = legacy_root / "plugin_repo" / name
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, dict):
                    p.mkdir()
                    for sub, body in content.items():
                        (p / sub).write_text(body)
                else:
                    p.write_text(content)
        if legacy_driver:
            (legacy_root / "driver_repo").mkdir()
            for name, content in legacy_driver.items():
                (legacy_root / "driver_repo" / name).write_text(content)
        if dest_plugin_existing:
            (data_root / "plugin_repo").mkdir()
            for name, content in dest_plugin_existing.items():
                (data_root / "plugin_repo" / name).write_text(content)
        if dest_driver_existing:
            (data_root / "driver_repo").mkdir()
            for name, content in dest_driver_existing.items():
                (data_root / "driver_repo" / name).write_text(content)

        from server import system_config as sc
        with patch.object(sc, "_LEGACY_PLUGIN_REPO_DIR", legacy_root / "plugin_repo"), \
             patch.object(sc, "_LEGACY_DRIVER_REPO_DIR", legacy_root / "driver_repo"), \
             patch.object(sc, "PLUGIN_REPO_DIR", data_root / "plugin_repo"), \
             patch.object(sc, "DRIVER_REPO_DIR", data_root / "driver_repo"):
            sc.migrate_legacy_repos()
        return legacy_root, data_root

    def test_noop_when_legacy_missing(self, tmp_path):
        legacy_root, data_root = self._run(tmp_path)
        assert not (legacy_root / "plugin_repo").exists()
        assert not (data_root / "plugin_repo").exists()

    def test_moves_plugin_directory(self, tmp_path):
        legacy_root, data_root = self._run(
            tmp_path,
            legacy_plugin={"my_plugin": {"plugin.py": "x = 1\n"}},
        )
        assert (data_root / "plugin_repo" / "my_plugin" / "plugin.py").read_text() == "x = 1\n"
        # Legacy directory drained and removed
        assert not (legacy_root / "plugin_repo").exists()

    def test_moves_deps_and_install_error_files(self, tmp_path):
        legacy_root, data_root = self._run(
            tmp_path,
            legacy_plugin={
                ".deps": {"site_packages_marker": ""},
                "broken": ".install-error\n",
            },
        )
        assert (data_root / "plugin_repo" / ".deps" / "site_packages_marker").exists()
        assert (data_root / "plugin_repo" / "broken").read_text() == ".install-error\n"

    def test_preserves_populated_destination(self, tmp_path):
        legacy_root, data_root = self._run(
            tmp_path,
            legacy_plugin={"new": "from legacy"},
            dest_plugin_existing={"existing": "from data_dir"},
        )
        # Destination user content untouched
        assert (data_root / "plugin_repo" / "existing").read_text() == "from data_dir"
        # Legacy NOT drained (conservative skip)
        assert (legacy_root / "plugin_repo" / "new").read_text() == "from legacy"

    def test_idempotent(self, tmp_path):
        # First call moves content. Re-running with empty legacy should no-op.
        from server import system_config as sc
        legacy_dir = tmp_path / "app" / "plugin_repo"
        data_dir = tmp_path / "data" / "plugin_repo"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "p").mkdir()
        (legacy_dir / "p" / "x").write_text("hi")
        with patch.object(sc, "_LEGACY_PLUGIN_REPO_DIR", legacy_dir), \
             patch.object(sc, "_LEGACY_DRIVER_REPO_DIR", tmp_path / "missing"), \
             patch.object(sc, "PLUGIN_REPO_DIR", data_dir), \
             patch.object(sc, "DRIVER_REPO_DIR", tmp_path / "data" / "driver_repo"):
            sc.migrate_legacy_repos()
            # Second call: legacy gone, destination has content; must be no-op.
            sc.migrate_legacy_repos()
        assert (data_dir / "p" / "x").read_text() == "hi"

    def test_same_path_short_circuits(self, tmp_path):
        # When OPENAVC_DATA_DIR points at APP_DIR, legacy == target. Migration
        # must not delete content or otherwise mangle the directory.
        from server import system_config as sc
        shared = tmp_path / "plugin_repo"
        shared.mkdir()
        (shared / "p.py").write_text("ok")
        with patch.object(sc, "_LEGACY_PLUGIN_REPO_DIR", shared), \
             patch.object(sc, "_LEGACY_DRIVER_REPO_DIR", tmp_path / "missing"), \
             patch.object(sc, "PLUGIN_REPO_DIR", shared), \
             patch.object(sc, "DRIVER_REPO_DIR", tmp_path / "data" / "driver_repo"):
            sc.migrate_legacy_repos()
        assert (shared / "p.py").read_text() == "ok"
