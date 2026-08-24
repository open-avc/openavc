"""What `system.json` is allowed to hold, and who can read it.

Three things are pinned here. The admin password and the API key are converted
to digests the first time a build carrying the conversion loads the file — that
is what gets the plaintext off an already-installed box, and it has to happen
without anybody signing in, because an appliance may go months without a login.
Neither credential changes while it happens: the same password signs in and the
same key authenticates afterwards, which is the whole requirement, because a
credential that quietly stopped working would be a lockout with no error
anywhere. And the file is mode 0600, which used to be true only by accident
(`mkstemp` creates at 0600 and `os.replace` carries that mode across) and still
matters: `panel_lock_code`, `isc.auth_key` and `cloud.system_key` all have to
be usable as stored, so the mode is all they have.
"""

import copy
import json
import os
import stat
import sys
from unittest.mock import MagicMock, patch

import pytest

from openavc.system_config import SystemConfig
from openavc.utils.password_hash import (
    hash_api_key,
    hash_password,
    looks_hashed,
    verify_api_key,
    verify_password,
)


def _config_at(tmp_path) -> SystemConfig:
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    return cfg


def _write(tmp_path, data: dict) -> None:
    (tmp_path / "system.json").write_text(json.dumps(data))


def _converted(tmp_path) -> SystemConfig:
    """A config loaded from tmp_path with the startup conversions applied, in
    the order `Engine.start` runs them."""
    cfg = _config_at(tmp_path)
    cfg.load()
    cfg.migrate_admin_password()
    cfg.migrate_api_key()
    return cfg


class TestConvertingAStoredPlaintext:
    def test_a_cleartext_password_becomes_a_digest(self, tmp_path):
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.migrate_admin_password() is True

        stored = cfg.get("auth", "programmer_password")
        assert looks_hashed(stored)
        assert verify_password("commission123", stored)

    def test_the_plaintext_is_gone_from_the_file(self, tmp_path):
        """The conversion is only worth anything if it is written back — the
        exposure is the file, not the process."""
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        _converted(tmp_path)

        on_disk = (tmp_path / "system.json").read_text()
        assert "commission123" not in on_disk
        assert looks_hashed(json.loads(on_disk)["auth"]["programmer_password"])

    def test_the_converted_password_still_signs_in(self, tmp_path):
        """A conversion nobody can log in after is a lockout, not a migration."""
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        _converted(tmp_path)

        reloaded = _config_at(tmp_path)
        reloaded.load()
        assert verify_password(
            "commission123", reloaded.get("auth", "programmer_password")
        )

    def test_an_already_converted_password_is_left_exactly_alone(self, tmp_path):
        """Otherwise every start re-salts it, and every start ends every session
        (the session-token fingerprint is taken over the stored value)."""
        stored = hash_password("commission123")
        _write(tmp_path, {"auth": {"programmer_password": stored}})
        before = (tmp_path / "system.json").read_bytes()

        cfg = _converted(tmp_path)

        assert (tmp_path / "system.json").read_bytes() == before
        assert cfg.get("auth", "programmer_password") == stored

    def test_an_unclaimed_instance_is_not_touched(self, tmp_path):
        _write(tmp_path, {"auth": {"programmer_password": ""}})
        before = (tmp_path / "system.json").read_bytes()

        cfg = _converted(tmp_path)

        assert (tmp_path / "system.json").read_bytes() == before
        assert cfg.get("auth", "programmer_password") == ""

    def test_a_fresh_install_writes_no_file_at_all(self, tmp_path):
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.migrate_admin_password() is False
        assert not (tmp_path / "system.json").exists()

    def test_merely_loading_the_config_rewrites_nothing(self, tmp_path):
        """The conversion is the server's job, not a side effect of reading.
        The simulator subprocess and `python -m openavc.drivers.check` both
        import this module, and neither should rewrite an operator's config on
        a validation run."""
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        before = (tmp_path / "system.json").read_bytes()

        _config_at(tmp_path).load()

        assert (tmp_path / "system.json").read_bytes() == before

    def test_the_rest_of_the_file_survives_the_rewrite(self, tmp_path):
        _write(tmp_path, {
            "auth": {"programmer_password": "commission123", "panel_lock_code": "4321"},
            "network": {"http_port": 9090},
            "some_future_section": {"kept": True},
        })
        _converted(tmp_path)

        saved = json.loads((tmp_path / "system.json").read_text())
        assert saved["network"]["http_port"] == 9090
        assert saved["some_future_section"] == {"kept": True}

    def test_the_panel_lock_code_is_deliberately_left_as_typed(self, tmp_path):
        """Nothing reads it yet, and hashing is one-way: the shape belongs to
        the feature that will check it. See the note beside it in DEFAULTS."""
        _write(tmp_path, {
            "auth": {"programmer_password": "commission123", "panel_lock_code": "4321"},
        })
        _converted(tmp_path)

        saved = json.loads((tmp_path / "system.json").read_text())
        assert saved["auth"]["panel_lock_code"] == "4321"


class TestConvertingAStoredApiKey:
    """The key is the same exposure one field over, and the more direct one:
    presented in `X-API-Key` it satisfies the admin check outright, with no
    password involved."""

    def test_a_cleartext_key_becomes_a_digest(self, tmp_path):
        _write(tmp_path, {"auth": {"api_key": "integration-key-1"}})
        cfg = _config_at(tmp_path)
        cfg.load()
        assert cfg.migrate_api_key() is True

        stored = cfg.get("auth", "api_key")
        assert looks_hashed(stored)
        assert verify_api_key("integration-key-1", stored)

    def test_the_plaintext_is_gone_from_the_file(self, tmp_path):
        _write(tmp_path, {"auth": {"api_key": "integration-key-1"}})
        _converted(tmp_path)

        on_disk = (tmp_path / "system.json").read_text()
        assert "integration-key-1" not in on_disk
        assert looks_hashed(json.loads(on_disk)["auth"]["api_key"])

    def test_the_converted_key_still_authenticates(self, tmp_path):
        """Whatever automation holds this key is using it right now. A
        conversion that invalidated it would be a lockout that produces no
        error anyone sees — the integration just starts getting 401s."""
        _write(tmp_path, {"auth": {"api_key": "integration-key-1"}})
        _converted(tmp_path)

        reloaded = _config_at(tmp_path)
        reloaded.load()
        assert verify_api_key("integration-key-1", reloaded.get("auth", "api_key"))

    def test_an_already_converted_key_is_left_exactly_alone(self, tmp_path):
        """Otherwise every start re-salts it, and every start re-writes the
        file for nothing."""
        stored = hash_api_key("integration-key-1")
        _write(tmp_path, {"auth": {"api_key": stored}})
        before = (tmp_path / "system.json").read_bytes()

        cfg = _converted(tmp_path)

        assert (tmp_path / "system.json").read_bytes() == before
        assert cfg.get("auth", "api_key") == stored

    def test_an_instance_with_no_key_is_not_touched(self, tmp_path):
        _write(tmp_path, {"auth": {"api_key": ""}})
        before = (tmp_path / "system.json").read_bytes()

        cfg = _converted(tmp_path)

        assert (tmp_path / "system.json").read_bytes() == before
        assert cfg.get("auth", "api_key") == ""

    def test_both_credentials_convert_on_the_same_start(self, tmp_path):
        """The two conversions are separate calls so a failure in one cannot
        leave the other as typed, which only pays off if the start runs both."""
        _write(tmp_path, {
            "auth": {"programmer_password": "commission123", "api_key": "integration-key-1"},
        })
        _converted(tmp_path)

        saved = json.loads((tmp_path / "system.json").read_text())
        assert verify_password("commission123", saved["auth"]["programmer_password"])
        assert verify_api_key("integration-key-1", saved["auth"]["api_key"])

    def test_merely_loading_the_config_converts_no_key_either(self, tmp_path):
        """Same reason as the password: the simulator subprocess and
        `python -m openavc.drivers.check` both import this module."""
        _write(tmp_path, {"auth": {"api_key": "integration-key-1"}})
        before = (tmp_path / "system.json").read_bytes()

        _config_at(tmp_path).load()

        assert (tmp_path / "system.json").read_bytes() == before


class TestTheApiKeyEnvOverride:
    """`OPENAVC_API_KEY` is supplied fresh each boot and is deliberately never
    persisted, so it is never converted either."""

    def test_an_env_key_stays_a_plaintext_at_runtime(self, tmp_path):
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_API_KEY": "from-env-key"}):
            cfg.load()
            assert cfg.get("auth", "api_key") == "from-env-key"

    def test_an_env_key_is_not_written_to_the_file(self, tmp_path):
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_API_KEY": "from-env-key"}):
            cfg.load()
            cfg.save()
        assert "from-env-key" not in (tmp_path / "system.json").read_text()

    def test_an_env_key_does_not_stop_the_file_being_converted(self, tmp_path):
        """The two layers are independent: the env value wins at runtime, and
        the plaintext sitting in the file still has to go."""
        _write(tmp_path, {"auth": {"api_key": "integration-key-1"}})
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_API_KEY": "from-env-key"}):
            cfg.load()
            cfg.migrate_api_key()
            assert cfg.get("auth", "api_key") == "from-env-key"

        saved = json.loads((tmp_path / "system.json").read_text())
        assert "integration-key-1" not in (tmp_path / "system.json").read_text()
        assert verify_api_key("integration-key-1", saved["auth"]["api_key"])


class TestTheEnvOverride:
    """`OPENAVC_PROGRAMMER_PASSWORD` is supplied fresh each boot and is
    deliberately never persisted, so it is never converted either."""

    def test_an_env_password_stays_a_plaintext_at_runtime(self, tmp_path):
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_PROGRAMMER_PASSWORD": "from-env-123"}):
            cfg.load()
            assert cfg.get("auth", "programmer_password") == "from-env-123"

    def test_an_env_password_is_not_written_to_the_file(self, tmp_path):
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_PROGRAMMER_PASSWORD": "from-env-123"}):
            cfg.load()
            cfg.save()
        assert "from-env-123" not in (tmp_path / "system.json").read_text()

    def test_an_env_password_does_not_stop_the_file_being_converted(self, tmp_path):
        """The two layers are independent: the env value wins at runtime, and
        the plaintext sitting in the file still has to go."""
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        cfg = _config_at(tmp_path)
        with patch.dict(os.environ, {"OPENAVC_PROGRAMMER_PASSWORD": "from-env-123"}):
            cfg.load()
            cfg.migrate_admin_password()
            assert cfg.get("auth", "programmer_password") == "from-env-123"

        saved = json.loads((tmp_path / "system.json").read_text())
        assert "commission123" not in (tmp_path / "system.json").read_text()
        assert verify_password("commission123", saved["auth"]["programmer_password"])


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
class TestFileMode:
    """0600 was inherited behaviour before it was intended behaviour. Pinned,
    because the recoverable secrets left in this file (api_key,
    panel_lock_code, isc.auth_key, cloud.system_key) have nothing but the mode
    protecting them from another account on the same host."""

    def test_a_saved_config_is_readable_only_by_its_owner(self, tmp_path):
        cfg = _config_at(tmp_path)
        cfg.load()
        cfg.set("auth", "api_key", "abc123")
        cfg.save()

        mode = stat.S_IMODE((tmp_path / "system.json").stat().st_mode)
        assert mode == 0o600, f"system.json is {oct(mode)}"

    def test_a_world_readable_config_is_tightened_on_the_next_save(self, tmp_path):
        """os.replace carries the temp file's mode onto the destination, so a
        file left loose by an older release or a hand edit is fixed rather than
        preserved."""
        path = tmp_path / "system.json"
        _write(tmp_path, {"network": {"http_port": 9090}})
        path.chmod(0o644)

        cfg = _config_at(tmp_path)
        cfg.load()
        cfg.set("network", "http_port", 7070)
        cfg.save()

        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_the_conversion_rewrite_lands_at_0600_too(self, tmp_path):
        """The one save that happens without anybody asking for it."""
        path = tmp_path / "system.json"
        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
        path.chmod(0o644)

        _converted(tmp_path)

        assert stat.S_IMODE(path.stat().st_mode) == 0o600


class TestTheServerDoesItAtStartup:
    """The unit tests above prove the conversions work; this one proves they
    happen, both of them, on one real start. A conversion nothing calls is
    indistinguishable from no fix at all, and the failure is invisible — the
    box keeps working perfectly with the credentials still sitting there in the
    clear."""

    @pytest.fixture
    def redirected_singleton(self, tmp_path):
        """Point the live singleton at tmp_path and put it back afterwards.

        `load()` replaces `_data` and `_file_data` wholesale, so restoring the
        paths alone would leave the whole session's config reading from this
        test's directory — the leak conftest's `isolated_auth_config` exists to
        describe, arriving as unexplained 401s in unrelated files.
        """
        from openavc.system_config import get_system_config

        cfg = get_system_config()
        saved = (
            cfg._data_dir, cfg._file_path,
            copy.deepcopy(cfg._data), copy.deepcopy(cfg._file_data),
        )
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        yield cfg
        cfg._data_dir, cfg._file_path, cfg._data, cfg._file_data = saved

    async def test_starting_the_engine_converts_a_stored_plaintext(
        self, tmp_path, redirected_singleton
    ):
        from openavc.core.engine import Engine

        _write(tmp_path, {
            "auth": {
                "programmer_password": "commission123",
                "api_key": "integration-key-1",
            },
        })
        redirected_singleton.load()

        project = tmp_path / "project.avc"
        project.write_text(json.dumps({
            "version": "0.3.0",
            "project": {"id": "conversion_test", "name": "Conversion Test"},
            "devices": [], "connections": {}, "variables": [], "macros": [],
            "ui": {"pages": []}, "plugins": {},
        }))

        engine = Engine(str(project))
        try:
            await engine.start()
        finally:
            await engine.stop()

        raw = (tmp_path / "system.json").read_text()
        on_disk = json.loads(raw)
        assert "commission123" not in raw
        assert "integration-key-1" not in raw
        assert verify_password("commission123", on_disk["auth"]["programmer_password"])
        assert verify_api_key("integration-key-1", on_disk["auth"]["api_key"])


_KEY_HEADER = {"X-API-Key": "integration-key-1"}
# A key never travels alone through this door: one with no password beside it
# is refused, because it would claim the instance and leave no way into the
# Programmer from a browser.
_KEY_AND_PASSWORD = {
    "api_key": "integration-key-1",
    "programmer_password": "commission123",
}


class TestTheSettingsWriteDoor:
    """`PATCH /api/system/config` is the only door that sets an API key, so it
    is the only place a plaintext could still be written. It is taken out of
    the generic section loop for exactly that reason — the loop copies whatever
    it is handed.

    Every save here carries a password beside the key because the door refuses
    a key that would be the only credential — see
    `tests/test_api_key_needs_a_password.py`. That is posture, not storage;
    what these tests are about is the form the key lands in."""

    @pytest.fixture
    def client(self, tmp_path):
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
        rest.set_engine(engine)
        ws.set_engine(engine)

        reset_system_config()
        cfg = get_system_config()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()
        yield TestClient(app), cfg
        rest.set_engine(None)
        ws.set_engine(None)
        reset_system_config()

    def test_a_saved_key_is_stored_as_a_digest(self, client, tmp_path):
        c, cfg = client
        resp = c.patch("/api/system/config", json={"auth": _KEY_AND_PASSWORD})
        assert resp.status_code == 200

        saved = json.loads((tmp_path / "system.json").read_text())
        assert "integration-key-1" not in (tmp_path / "system.json").read_text()
        assert verify_api_key("integration-key-1", saved["auth"]["api_key"])

    def test_the_saved_key_authenticates_immediately(self, client):
        """Without a restart: the same request that stores it has to leave the
        runtime layer holding something the door accepts."""
        c, cfg = client
        c.patch("/api/system/config", json={"auth": _KEY_AND_PASSWORD})

        from openavc.api.auth import _check_api_key

        assert _check_api_key("integration-key-1") is True
        assert _check_api_key("something-else") is False

    def test_clearing_the_key_clears_it(self, client, tmp_path):
        c, cfg = client
        c.patch("/api/system/config", json={"auth": _KEY_AND_PASSWORD})
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"api_key": ""}},
            headers=_KEY_HEADER,
        )
        assert resp.status_code == 200

        saved = json.loads((tmp_path / "system.json").read_text())
        assert saved["auth"]["api_key"] == ""

    def test_the_endpoint_never_hands_back_a_key_to_send_again(self, client):
        """The read half of the round trip the next test exercises. Every
        request from here on carries the key that was just saved, which is the
        end-to-end proof that a digest in the file authenticates over HTTP."""
        c, cfg = client
        c.patch("/api/system/config", json={"auth": _KEY_AND_PASSWORD})

        resp = c.get("/api/system/config", headers=_KEY_HEADER)
        assert resp.status_code == 200
        assert resp.json()["auth"]["api_key"] == "***"

    def test_patching_the_redaction_marker_back_changes_nothing(self, client, tmp_path):
        """A client that GETs this config and PATCHes it back sends `***` for
        every secret it never saw. Taking that literally would set the key to
        the digest of `***` — an instant lockout of whatever holds the real
        one, with nothing to read back to find out what happened."""
        c, cfg = client
        c.patch("/api/system/config", json={"auth": _KEY_AND_PASSWORD})
        before = json.loads((tmp_path / "system.json").read_text())["auth"]["api_key"]

        body = c.get("/api/system/config", headers=_KEY_HEADER).json()
        resp = c.patch(
            "/api/system/config", json={"auth": body["auth"]}, headers=_KEY_HEADER
        )
        assert resp.status_code == 200

        after = json.loads((tmp_path / "system.json").read_text())["auth"]["api_key"]
        assert after == before
        assert verify_api_key("integration-key-1", after)

    def test_the_password_survives_the_same_round_trip(self, client, tmp_path):
        c, cfg = client
        c.patch(
            "/api/system/config", json={"auth": {"programmer_password": "commission123"}}
        )
        signed_in = ("admin", "commission123")

        body = c.get("/api/system/config", auth=signed_in).json()
        resp = c.patch(
            "/api/system/config", json={"auth": body["auth"]}, auth=signed_in
        )
        assert resp.status_code == 200

        saved = json.loads((tmp_path / "system.json").read_text())
        assert verify_password("commission123", saved["auth"]["programmer_password"])

    def test_the_shared_secrets_survive_it_too(self, client, tmp_path):
        """`isc.auth_key` and `cloud.system_key` are stored as-is on purpose —
        this instance has to present them — so `***` coming back is the one way
        they could be destroyed."""
        c, cfg = client
        cfg.set("isc", "auth_key", "isc-shared-secret")
        cfg.set("cloud", "system_key", "cloud-shared-secret")
        cfg.save()

        body = c.get("/api/system/config").json()
        resp = c.patch(
            "/api/system/config", json={"isc": body["isc"], "cloud": body["cloud"]}
        )
        assert resp.status_code == 200

        saved = json.loads((tmp_path / "system.json").read_text())
        assert saved["isc"]["auth_key"] == "isc-shared-secret"
        assert saved["cloud"]["system_key"] == "cloud-shared-secret"
