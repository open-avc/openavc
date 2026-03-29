"""Tests for server.system_config — system.json layered configuration."""

import json
import os
from unittest.mock import patch


from server.system_config import (
    SystemConfig,
    _deep_merge,
    _is_dev_environment,
    _parse_env_value,
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


class TestDevEnvironmentDetection:
    def test_detects_dev_from_pyproject(self):
        # We're running tests from the repo, so this should be True
        assert _is_dev_environment() is True
