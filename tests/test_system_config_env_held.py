"""A setting the service's environment holds is reported as held, and cannot be
changed through the config API.

Every packaged install sets ``OPENAVC_BIND=0.0.0.0`` in its service
environment, and ``SystemConfig.load`` applies the environment after
system.json. So Settings > Network > Bind address saved ``127.0.0.1`` to the
file, read back as saved, and after a restart the server was on every adapter
again with the field showing ``0.0.0.0`` and nothing saying why.

Pinned here: which fields the config reports as held (only ones the environment
actually overrode), what ``GET /api/system/config`` tells Settings, and what
``PATCH`` does with a held field -- a change is refused before anything is
written, naming the variable, and a value equal to the running one is a client
echoing what it read, so it is dropped without being copied into system.json.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc.system_config import SystemConfig


def _config_at(tmp_path) -> SystemConfig:
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    return cfg


class TestWhatTheConfigReportsAsHeld:
    def test_a_set_variable_holds_its_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_BIND", "0.0.0.0")
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.env_override("network", "bind_address") == "OPENAVC_BIND"
        assert cfg.env_overrides()["network.bind_address"] == "OPENAVC_BIND"

    def test_an_unset_variable_holds_nothing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAVC_BIND", raising=False)
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.env_override("network", "bind_address") is None
        assert "network.bind_address" not in cfg.env_overrides()

    def test_a_value_load_ignores_holds_nothing(self, tmp_path, monkeypatch):
        """An integer variable that is not an integer is skipped by load(), so
        the file's value is what runs and the field is editable."""
        monkeypatch.setenv("OPENAVC_PORT", "eighty")
        (tmp_path / "system.json").write_text(json.dumps({"network": {"http_port": 9090}}))
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.get("network", "http_port") == 9090
        assert cfg.env_override("network", "http_port") is None

    def test_a_reload_forgets_a_variable_that_went_away(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAVC_BIND", "0.0.0.0")
        cfg = _config_at(tmp_path)
        cfg.load()
        monkeypatch.delenv("OPENAVC_BIND")
        cfg.load()
        assert cfg.env_override("network", "bind_address") is None


@pytest.fixture
def client(tmp_path, monkeypatch, isolated_auth_config):
    """The real app on a config in tmp_path, loaded after the test's env is set."""
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.main import app
    from openavc.system_config import get_system_config, reset_system_config

    engine = MagicMock()
    state = StateStore()
    state.set_event_bus(EventBus())
    engine.state = state
    engine.panel_access_changed = AsyncMock()
    engine.reconcile_runtime_services = AsyncMock()
    rest.set_engine(engine)
    ws.set_engine(engine)

    def start(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        reset_system_config()
        cfg = get_system_config()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()
        return TestClient(app), cfg

    yield start
    rest.set_engine(None)
    ws.set_engine(None)
    reset_system_config()


def _on_disk(tmp_path) -> dict:
    """system.json as written. A save writes every default, so a test asks
    whether a value is there, never whether a key is."""
    path = tmp_path / "system.json"
    return json.loads(path.read_text()) if path.exists() else {}


class TestWhatSettingsIsTold:
    def test_get_names_each_held_field_and_its_variable(self, client):
        c, _cfg = client(OPENAVC_BIND="0.0.0.0", OPENAVC_MDNS_ADVERTISE="false")
        body = c.get("/api/system/config").json()
        env = body["_environment"]
        assert env["overrides"]["network.bind_address"] == "OPENAVC_BIND"
        assert env["overrides"]["discovery.advertise"] == "OPENAVC_MDNS_ADVERTISE"
        assert isinstance(env["deployment_type"], str) and env["deployment_type"]

    def test_the_report_never_reaches_system_json(self, client, tmp_path):
        c, _cfg = client(OPENAVC_BIND="0.0.0.0")
        assert c.patch("/api/system/config", json={"logging": {"level": "debug"}}).status_code == 200
        assert "_environment" not in _on_disk(tmp_path)

    def test_sending_the_report_back_changes_nothing(self, client, tmp_path):
        c, _cfg = client(OPENAVC_BIND="0.0.0.0")
        resp = c.patch(
            "/api/system/config",
            json={"_environment": {"deployment_type": "x", "overrides": {}}},
        )
        assert resp.status_code == 200
        assert "_environment" not in _on_disk(tmp_path)


class TestChangingAHeldField:
    def test_a_change_is_refused_naming_the_variable(self, client, tmp_path):
        c, cfg = client(OPENAVC_BIND="0.0.0.0")
        resp = c.patch("/api/system/config", json={"network": {"bind_address": "10.0.0.5"}})
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "OPENAVC_BIND" in detail and "network.bind_address" in detail
        assert cfg.get("network", "bind_address") == "0.0.0.0"
        assert _on_disk(tmp_path).get("network", {}).get("bind_address") != "10.0.0.5"

    def test_nothing_else_in_the_same_save_is_written(self, client, tmp_path):
        c, cfg = client(OPENAVC_BIND="0.0.0.0")
        before = cfg.get("logging", "level")
        resp = c.patch(
            "/api/system/config",
            json={"network": {"bind_address": "10.0.0.5"}, "logging": {"level": "debug"}},
        )
        assert resp.status_code == 409
        assert cfg.get("logging", "level") == before
        assert _on_disk(tmp_path).get("logging", {}).get("level") != "debug"

    def test_the_running_value_echoed_back_is_accepted_and_not_written(self, client, tmp_path):
        """A client that GETs the config and PATCHes it back sends every held
        value too. That is not a change, and writing it would copy the
        environment's value into system.json, where it would outlive the
        variable."""
        c, cfg = client(OPENAVC_BIND="0.0.0.0", OPENAVC_MDNS_ADVERTISE="false")
        resp = c.patch(
            "/api/system/config",
            json={"network": {"bind_address": "0.0.0.0"}, "discovery": {"advertise": False}},
        )
        assert resp.status_code == 200
        on_disk = _on_disk(tmp_path)
        assert on_disk["network"]["bind_address"] == "127.0.0.1"  # the default, not the env's
        assert on_disk["discovery"]["advertise"] is True

    def test_a_whole_get_body_round_trips(self, client, tmp_path):
        c, _cfg = client(OPENAVC_BIND="0.0.0.0", OPENAVC_LOG_LEVEL="warning")
        body = c.get("/api/system/config").json()
        resp = c.patch("/api/system/config", json=body)
        assert resp.status_code == 200, resp.text
        on_disk = _on_disk(tmp_path)
        assert on_disk["network"]["bind_address"] == "127.0.0.1"
        assert on_disk["logging"]["level"] == "info"

    def test_an_unheld_field_still_saves(self, client, tmp_path):
        c, cfg = client(OPENAVC_BIND="0.0.0.0")
        resp = c.patch("/api/system/config", json={"network": {"http_port": 9191}})
        assert resp.status_code == 200
        assert cfg.get("network", "http_port") == 9191
        assert _on_disk(tmp_path)["network"]["http_port"] == 9191


class TestHeldCredentials:
    """The two credentials are popped out of the body before the generic save
    loop and stored as digests, so they need the refusal on their own path,
    and a sent value cannot be compared with the running one."""

    def test_a_password_held_by_the_environment_is_refused(self, client, tmp_path):
        c, _cfg = client(OPENAVC_PROGRAMMER_PASSWORD="env-pass-123")
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "another-pass-456"}},
            auth=("admin", "env-pass-123"),
        )
        assert resp.status_code == 409
        assert "OPENAVC_PROGRAMMER_PASSWORD" in resp.json()["detail"]
        assert "programmer_password" not in _on_disk(tmp_path).get("auth", {})

    def test_the_same_password_typed_again_is_refused_too(self, client):
        c, _cfg = client(OPENAVC_PROGRAMMER_PASSWORD="env-pass-123")
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "env-pass-123"}},
            auth=("admin", "env-pass-123"),
        )
        assert resp.status_code == 409

    def test_an_api_key_held_by_the_environment_is_refused(self, client, tmp_path):
        c, _cfg = client(
            OPENAVC_PROGRAMMER_PASSWORD="env-pass-123", OPENAVC_API_KEY="env-key-abc",
        )
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"api_key": "new-key-def"}},
            auth=("admin", "env-pass-123"),
        )
        assert resp.status_code == 409
        assert "OPENAVC_API_KEY" in resp.json()["detail"]
        assert "api_key" not in _on_disk(tmp_path).get("auth", {})

    def test_the_redaction_marker_is_still_not_a_change(self, client):
        c, _cfg = client(OPENAVC_PROGRAMMER_PASSWORD="env-pass-123")
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "***"}},
            auth=("admin", "env-pass-123"),
        )
        assert resp.status_code == 200


class TestFirstRunUsername:
    """First-run setup writes the username it is given. With
    ``OPENAVC_PROGRAMMER_USERNAME`` set, the environment's name is the one that
    signs in after the next restart, so a different typed name is refused."""

    def test_a_different_username_is_refused(self, client, tmp_path):
        c, cfg = client(OPENAVC_PROGRAMMER_USERNAME="integrator")
        resp = c.post("/api/auth/setup", json={"username": "someone", "password": "commission123"})
        assert resp.status_code == 422
        assert "OPENAVC_PROGRAMMER_USERNAME" in resp.json()["detail"]
        assert not cfg.get("auth", "programmer_password")

    def test_a_blank_username_claims_with_the_environments(self, client, tmp_path):
        c, cfg = client(OPENAVC_PROGRAMMER_USERNAME="integrator")
        resp = c.post("/api/auth/setup", json={"username": "", "password": "commission123"})
        assert resp.status_code == 200
        assert cfg.get("auth", "programmer_username") == "integrator"
        assert _on_disk(tmp_path)["auth"]["programmer_username"] != "integrator"

    def test_the_same_username_is_accepted_and_not_copied(self, client, tmp_path):
        c, _cfg = client(OPENAVC_PROGRAMMER_USERNAME="integrator")
        resp = c.post("/api/auth/setup", json={"username": "integrator", "password": "commission123"})
        assert resp.status_code == 200
        assert _on_disk(tmp_path)["auth"]["programmer_username"] != "integrator"
