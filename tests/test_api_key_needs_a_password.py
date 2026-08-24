"""An API key may not be a system's only credential.

Either credential claims the instance, so setting an API key on an open box
closes the admin surface to anonymous callers. But a key is only ever accepted
in `X-API-Key`, and a browser cannot attach that to a page it is opening; the
sign-in screen has one door, `POST /api/auth/session`, and it mints its session
from a password. So a key with nothing beside it leaves the REST API reachable
and the Programmer unreachable from every browser, with the filesystem as the
only way back — and the person locked out is the integrator, out of the one
surface that could fix it.

`auth.allow_anonymous` does not rescue it: that is consulted only when no
credential is set at all.

Three surfaces, all pinned here:

- the rule, `auth.api_key_would_be_sole_credential`, which answers about the
  state a save would LEAVE rather than about one field — so it catches the
  quiet direction too, clearing the password on a box that has a key;
- the door, `PATCH /api/system/config`, which refuses with 400 before writing
  anything, the same shape as the TLS invariant check beside it;
- `Engine.start`, which can only warn, because the two doors that reach this
  state without a save — `OPENAVC_API_KEY` and a hand-written system.json —
  are deployment decisions rather than clicks and are left standing.
"""

import logging
from unittest.mock import MagicMock

import pytest

from openavc.api import auth


@pytest.fixture(autouse=True)
def _isolate_auth(isolated_auth_config):
    """Snapshot/restore the auth config section — see conftest."""


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parent.parent)


def _store(*, password: str | None = None, api_key: str | None = None) -> None:
    if password is not None:
        auth.store_admin_password(password)
    if api_key is not None:
        auth.store_api_key(api_key)


class TestTheRule:
    def test_a_key_proposed_onto_a_box_with_no_password(self):
        _store(password="", api_key="")
        assert auth.api_key_would_be_sole_credential(api_key="integration-key") is True

    def test_a_key_proposed_with_a_password_in_the_same_save(self):
        _store(password="", api_key="")
        assert (
            auth.api_key_would_be_sole_credential(
                api_key="integration-key", password="commission123"
            )
            is False
        )

    def test_a_key_proposed_onto_a_box_that_already_has_a_password(self):
        """`password=None` means this save isn't touching it, so the stored one
        still counts."""
        _store(password="commission123", api_key="")
        assert auth.api_key_would_be_sole_credential(api_key="integration-key") is False

    def test_clearing_the_password_on_a_box_that_has_a_key(self):
        """The quiet direction. Nothing about this save mentions the key, and
        it arrives at exactly the same place."""
        _store(password="commission123", api_key="integration-key")
        assert auth.api_key_would_be_sole_credential(password="") is True

    def test_clearing_the_key_is_always_fine(self):
        _store(password="", api_key="integration-key")
        assert auth.api_key_would_be_sole_credential(api_key="") is False

    def test_a_password_alone_is_fine(self):
        _store(password="commission123", api_key="")
        assert auth.api_key_would_be_sole_credential() is False

    def test_no_credential_at_all_is_not_this_problem(self):
        """An unclaimed box is a posture the platform already has an answer for
        (`anonymous_access_allowed`), not a lockout."""
        _store(password="", api_key="")
        assert auth.api_key_would_be_sole_credential() is False

    def test_the_no_argument_form_reads_the_stored_state(self):
        _store(password="", api_key="integration-key")
        assert auth.api_key_would_be_sole_credential() is True


class TestTheSaveDoor:
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

    def test_a_key_with_no_password_is_refused(self, client):
        c, _cfg = client
        resp = c.patch(
            "/api/system/config", json={"auth": {"api_key": "integration-key"}}
        )
        assert resp.status_code == 400

    def test_the_refusal_says_what_to_do_next(self, client):
        """The one place a reason belongs: the caller did something and needs
        the step that gets them out of it. Both ways forward are named, and both
        controls are on the screen they are already looking at."""
        c, _cfg = client
        resp = c.patch(
            "/api/system/config", json={"auth": {"api_key": "integration-key"}}
        )
        detail = resp.json()["detail"]
        assert "password" in detail.lower()
        assert "clear the API key" in detail

    def test_the_refusal_writes_nothing(self, client, tmp_path):
        """Refusing after a partial write would be worse than not refusing: the
        key would be stored and the caller told it wasn't."""
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={"auth": {"api_key": "integration-key"}, "logging": {"level": "debug"}},
        )
        assert auth._get_api_key() == ""
        from openavc.system_config import get_system_config

        assert get_system_config().get("logging", "level") != "debug"
        assert not (tmp_path / "system.json").exists()

    def test_a_key_and_a_password_in_one_save_is_allowed(self, client):
        c, _cfg = client
        resp = c.patch(
            "/api/system/config",
            json={
                "auth": {
                    "api_key": "integration-key",
                    "programmer_password": "commission123",
                }
            },
        )
        assert resp.status_code == 200
        assert auth._check_api_key("integration-key") is True

    def test_a_key_onto_a_box_that_already_has_a_password_is_allowed(self, client):
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": "commission123"}},
        )
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"api_key": "integration-key"}},
            auth=("admin", "commission123"),
        )
        assert resp.status_code == 200

    def test_clearing_the_password_under_a_key_is_refused(self, client):
        """Same lockout, arriving by the other door — and this is the one a
        field-by-field check would have let through."""
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={
                "auth": {
                    "api_key": "integration-key",
                    "programmer_password": "commission123",
                }
            },
        )
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"programmer_password": ""}},
            auth=("admin", "commission123"),
        )
        assert resp.status_code == 400
        assert auth._check_password("commission123") is True

    def test_clearing_the_key_is_allowed(self, client):
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={
                "auth": {
                    "api_key": "integration-key",
                    "programmer_password": "commission123",
                }
            },
        )
        resp = c.patch(
            "/api/system/config",
            json={"auth": {"api_key": ""}},
            auth=("admin", "commission123"),
        )
        assert resp.status_code == 200

    def test_a_system_already_in_that_state_can_still_change_other_settings(
        self, client
    ):
        """A key from `OPENAVC_API_KEY` or a hand-written file puts a box here
        with no save involved. Refusing its every unrelated save would widen the
        lockout rather than close it — the settings it needs to reach are not
        the ones that got it here."""
        c, _cfg = client
        auth.store_api_key("integration-key")
        resp = c.patch(
            "/api/system/config",
            json={"logging": {"level": "debug"}},
            headers={"X-API-Key": "integration-key"},
        )
        assert resp.status_code == 200

    def test_the_redacted_round_trip_is_not_read_as_clearing_the_password(
        self, client
    ):
        """A client that GETs this config and PATCHes it back sends `***` for
        both credentials. Those are dropped before the check, so the save has to
        read as touching neither — not as clearing the password under a key."""
        c, _cfg = client
        c.patch(
            "/api/system/config",
            json={
                "auth": {
                    "api_key": "integration-key",
                    "programmer_password": "commission123",
                }
            },
        )
        signed_in = ("admin", "commission123")
        body = c.get("/api/system/config", auth=signed_in).json()
        assert body["auth"]["api_key"] == "***"
        assert body["auth"]["programmer_password"] == "***"

        resp = c.patch(
            "/api/system/config", json={"auth": body["auth"]}, auth=signed_in
        )
        assert resp.status_code == 200
        assert auth._check_api_key("integration-key") is True
        assert auth._check_password("commission123") is True


class TestTheDoorCensus:
    """A guard on one door is not a guard. These are the other ways in."""

    def test_the_first_run_claim_sets_a_password_and_never_a_key(self):
        """Which is why the shipped claim path can't reach this state. Asserted
        rather than read off the source, so a later 'also issue an API key at
        claim time' turns this red instead of shipping the lockout."""
        _store(password="", api_key="")
        auth.claim_instance("commission123")
        assert auth._get_password() != ""
        assert auth._get_api_key() == ""

    def test_store_api_key_is_the_only_writer(self):
        """`PATCH /api/system/config` is its only caller, so guarding that one
        route covers every save. If a second caller appears, it asks the rule
        too — this is the tripwire that says one showed up."""
        import subprocess

        out = subprocess.run(
            ["git", "grep", "-n", "store_api_key", "--", "openavc/"],
            capture_output=True,
            text=True,
            cwd=_repo_root(),
        ).stdout
        callers = {
            line.split(":")[0]
            for line in out.splitlines()
            if "def store_api_key" not in line and "store_api_key`" not in line
        }
        assert callers == {"openavc/api/routes/system.py"}, out


class TestTheStartupWarning:
    """The two doors no refusal can reach — `OPENAVC_API_KEY`, supplied fresh
    each boot and deliberately never persisted, and a system.json somebody
    provisioned by hand. Both stand; what they get instead is a line in the log,
    because otherwise nothing anywhere says the box can't be opened."""

    def test_a_key_with_no_password_warns(self, caplog):
        _store(password="", api_key="integration-key")
        with caplog.at_level(logging.WARNING, logger="openavc.api.auth"):
            assert auth.warn_if_api_key_is_sole_credential() is True
        assert "cannot be opened in a browser" in caplog.text

    def test_the_warning_names_where_to_fix_it(self, caplog):
        _store(password="", api_key="integration-key")
        with caplog.at_level(logging.WARNING, logger="openavc.api.auth"):
            auth.warn_if_api_key_is_sole_credential()
        assert "Settings > Security" in caplog.text

    def test_a_key_with_a_password_is_quiet(self, caplog):
        _store(password="commission123", api_key="integration-key")
        with caplog.at_level(logging.WARNING, logger="openavc.api.auth"):
            assert auth.warn_if_api_key_is_sole_credential() is False
        assert caplog.text.strip() == ""

    def test_an_unclaimed_box_is_quiet(self, caplog):
        _store(password="", api_key="")
        with caplog.at_level(logging.WARNING, logger="openavc.api.auth"):
            assert auth.warn_if_api_key_is_sole_credential() is False
        assert caplog.text.strip() == ""

    def test_the_engine_calls_it_at_startup(self):
        """The line above is only worth anything if something runs it. Pins the
        call site — re-running `Engine.start` here would cost a server."""
        from pathlib import Path

        source = (Path(_repo_root()) / "openavc/core/engine.py").read_text()
        assert "warn_if_api_key_is_sole_credential()" in source


class TestTheLockoutItself:
    """What the guard exists to prevent, asserted directly: with a key and no
    password, nothing a browser can send gets in. If any of these ever starts
    passing, the refusal has become unnecessary and should be reconsidered
    rather than left in place."""

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
        auth.store_api_key("integration-key")
        yield TestClient(app)
        rest.set_engine(None)
        ws.set_engine(None)
        reset_system_config()

    def test_the_key_authenticates_in_a_header(self, client):
        assert (
            client.get(
                "/api/system/config", headers={"X-API-Key": "integration-key"}
            ).status_code
            == 200
        )

    def test_anonymous_is_refused(self, client):
        assert client.get("/api/system/config").status_code == 401

    def test_the_key_is_not_accepted_as_a_password(self, client):
        assert (
            client.get(
                "/api/system/config", auth=("admin", "integration-key")
            ).status_code
            == 401
        )

    def test_the_sign_in_route_will_not_mint_a_session_from_it(self, client):
        assert (
            client.post(
                "/api/auth/session", auth=("admin", "integration-key")
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/auth/session", headers={"X-API-Key": "integration-key"}
            ).status_code
            == 401
        )

    def test_the_first_run_claim_cannot_be_used_to_recover(self, client):
        """The obvious escape hatch, and it is closed: the key already claimed
        the instance."""
        resp = client.post("/api/auth/setup", json={"password": "commission123"})
        assert resp.status_code == 409

    def test_allow_anonymous_does_not_rescue_it(self, client):
        """The other thing somebody would reach for. `anonymous_access_allowed`
        is consulted only when NO credential is set."""
        from openavc.system_config import get_system_config

        get_system_config().set("auth", "allow_anonymous", True)
        assert client.get("/api/system/config").status_code == 401


class TestTheSettingsCardMirrorsIt:
    """The card blocks the same save the server refuses, so the sentence lands
    before the click rather than as a 400 afterwards. Hand-mirrored prose, so
    what is pinned is that the two agree on WHEN, not word for word."""

    def _card(self) -> str:
        from pathlib import Path

        return (
            Path(_repo_root())
            / "openavc/web/programmer/src/views/SystemSettingsView.tsx"
        ).read_text()

    def test_the_card_computes_the_same_condition(self):
        card = self._card()
        assert "const apiKeyNeedsPassword = !!auth.api_key && !auth.programmer_password;" in card

    def test_the_card_blocks_saving_on_it(self):
        assert "|| apiKeySaveBlocked" in self._card()

    def test_the_card_only_blocks_when_a_credential_was_edited(self):
        """Same carve-out as the server: a box already in that state has to be
        able to change its log level."""
        card = self._card()
        assert "apiKeyNeedsPassword && credentialEdited" in card

    def test_the_card_says_it_on_screen(self):
        assert "An API key can't sign in to the Programmer." in self._card()
