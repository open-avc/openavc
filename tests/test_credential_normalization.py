"""What a typed credential becomes on its way into system.json.

Two rules at the config-save door, and they pull in opposite directions, which
is why they are pinned together.

**Trim.** A credential arrives through a text field and a clipboard. A key
pasted with a trailing newline stored as-typed is a different key from the one
the caller holds, and it authenticates nobody — with no error anywhere, because
the digest is perfectly valid. First-run setup has always trimmed
(`claim_instance`); the Settings door did not, so the same typed string became
two different credentials depending on which door it came through.

**But refuse whitespace-only.** Trimming `"   "` produces the empty string, and
empty means *clear the credential* here — so trimming alone would turn a
fat-fingered spacebar into a silent unclaim of the instance. Stored untrimmed it
is worse: `"   "` hashes into a real scrypt credential that is indistinguishable
from "no password set" to every human who looks at the field, and guessable by
anyone who tries the obvious. Neither outcome is what the person typing meant,
so the door says so instead.
"""

from unittest.mock import MagicMock

import pytest

from openavc.api import auth


@pytest.fixture(autouse=True)
def _isolate_auth(isolated_auth_config):
    """Snapshot/restore the auth config section — see conftest."""


@pytest.fixture
def client(tmp_path):
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


class TestTheRule:
    def test_a_typed_credential_is_trimmed(self):
        assert auth.normalize_credential("  commission123  ") == "commission123"
        assert auth.normalize_credential("key\n") == "key"

    def test_whitespace_only_is_the_refusal(self):
        for raw in ("   ", "\t", "\n", " \t\n "):
            assert auth.normalize_credential(raw) is None, repr(raw)

    def test_empty_is_not_a_refusal(self):
        """Empty is how a credential is deliberately removed, and stays so.

        The distinction the whole rule rests on: `""` is a caller saying
        "no credential", `"   "` is a caller who thinks they typed one.
        """
        assert auth.normalize_credential("") == ""
        assert auth.normalize_credential(None) == ""

    def test_a_credential_that_is_only_padded_survives(self):
        """Trimming must not reach inside. A password with spaces in the
        middle is a perfectly ordinary passphrase."""
        assert auth.normalize_credential(" correct horse battery ") == (
            "correct horse battery"
        )


class TestTheSaveDoor:
    def test_a_whitespace_only_password_is_refused(self, client):
        c, _cfg = client
        resp = c.patch(
            "/api/system/config", json={"auth": {"programmer_password": "   "}}
        )
        assert resp.status_code == 400

    def test_the_refusal_says_what_happened_and_what_to_do(self, client):
        c, _cfg = client
        resp = c.patch(
            "/api/system/config", json={"auth": {"programmer_password": "   "}}
        )
        detail = resp.json()["detail"]
        assert "only spaces" in detail
        assert "clear the field" in detail

    def test_the_refused_password_is_not_stored(self, client):
        """The reported defect: `"   "` became the room's real credential.

        Both halves are asserted — nothing was written, AND the spaces do not
        sign anyone in. A refusal that still hashed the value would leave a
        working credential nobody knows about.
        """
        c, _cfg = client
        c.patch("/api/system/config", json={"auth": {"programmer_password": "   "}})
        assert auth._get_password() == ""
        assert auth.is_claimed() is False
        assert auth._check_password("   ") is False

    def test_the_refusal_writes_nothing_else_either(self, client, tmp_path):
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={
                "auth": {"programmer_password": "   "},
                "logging": {"level": "debug"},
            },
        )
        from openavc.system_config import get_system_config

        assert get_system_config().get("logging", "level") != "debug"
        assert not (tmp_path / "system.json").exists()

    def test_a_whitespace_only_api_key_is_refused(self, client):
        c, _cfg = client
        resp = c.patch("/api/system/config", json={"auth": {"api_key": "  "}})
        assert resp.status_code == 400
        assert "only spaces" in resp.json()["detail"]
        assert auth._get_api_key() == ""

    def test_a_padded_password_signs_in_trimmed(self, client):
        """What the caller holds is what works. Before the trim, a password
        saved with a stray trailing space could only be typed back with it."""
        c, _cfg = client
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "  commission123  "}},
        )
        assert resp.status_code == 200
        assert auth._check_password("commission123") is True

    def test_a_padded_api_key_is_stored_trimmed(self, client):
        c, _cfg = client
        resp = c.patch(
            "/api/system/config",
            json={
                "auth": {
                    "api_key": "  integration-key\n",
                    "programmer_password": "commission123",
                }
            },
        )
        assert resp.status_code == 200
        assert auth._check_api_key("integration-key") is True

    def test_clearing_a_password_still_works(self, client):
        """The empty case the whitespace rule must not have swallowed."""
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "commission123"}},
        )
        assert auth.is_claimed() is True
        # Claimed now, so the door wants the credential it was just given.
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": ""}},
            auth=("", "commission123"),
        )
        assert resp.status_code == 200
        assert auth.is_claimed() is False

    def test_an_ordinary_save_that_touches_no_credential_is_untouched(self, client):
        c, _cfg = client
        resp = c.patch("/api/system/config", json={"logging": {"level": "debug"}})
        assert resp.status_code == 200


class TestTheTwoDoorsAgree:
    """First-run setup and Settings must not shape the same string differently."""

    def test_setup_trims_the_same_way(self, client):
        c, _cfg = client
        resp = c.post(
            "/api/auth/setup", json={"password": "  commission123  "}
        )
        assert resp.status_code == 200
        assert auth._check_password("commission123") is True

    def test_setup_also_refuses_whitespace_only(self, client):
        """Through its length floor rather than this sentence — setup is the
        stricter door, which is the right direction for the two to differ in."""
        c, _cfg = client
        resp = c.post("/api/auth/setup", json={"password": "        "})
        assert resp.status_code >= 400
        assert auth.is_claimed() is False
