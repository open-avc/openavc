"""What `system.json` is allowed to hold, and who can read it.

Two things are pinned here. The admin password is converted to a digest the
first time a build carrying the conversion loads the file — that is what gets
the plaintext off an already-installed box, and it has to happen without
anybody signing in, because an appliance may go months without a login. And the
file is mode 0600, which used to be true only by accident (`mkstemp` creates at
0600 and `os.replace` carries that mode across) and matters more now rather than
less: the password is a digest, but `api_key`, `panel_lock_code`, `isc.auth_key`
and `cloud.system_key` all still have to be usable as stored.
"""

import copy
import json
import os
import stat
import sys
from unittest.mock import patch

import pytest

from openavc.system_config import SystemConfig
from openavc.utils.password_hash import hash_password, looks_hashed, verify_password


def _config_at(tmp_path) -> SystemConfig:
    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    return cfg


def _write(tmp_path, data: dict) -> None:
    (tmp_path / "system.json").write_text(json.dumps(data))


def _converted(tmp_path) -> SystemConfig:
    """A config loaded from tmp_path with the startup conversion applied."""
    cfg = _config_at(tmp_path)
    cfg.load()
    cfg.migrate_admin_password()
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
            "auth": {"programmer_password": "commission123", "api_key": "abc123"},
            "network": {"http_port": 9090},
            "some_future_section": {"kept": True},
        })
        _converted(tmp_path)

        saved = json.loads((tmp_path / "system.json").read_text())
        assert saved["network"]["http_port"] == 9090
        assert saved["auth"]["api_key"] == "abc123"
        assert saved["some_future_section"] == {"kept": True}


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
    """The unit tests above prove the conversion works; this one proves it
    happens. A conversion nothing calls is indistinguishable from no fix at
    all, and the failure is invisible — the box keeps working perfectly with
    the password still sitting there in the clear."""

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

        _write(tmp_path, {"auth": {"programmer_password": "commission123"}})
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

        on_disk = json.loads((tmp_path / "system.json").read_text())
        assert "commission123" not in (tmp_path / "system.json").read_text()
        assert verify_password("commission123", on_disk["auth"]["programmer_password"])
