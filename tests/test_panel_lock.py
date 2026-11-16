"""The panel lock PIN stays on the server.

A panel client holds no programmer credential — `api/ws.py` falls back to
`client_type = "panel"` when auth fails, because a wall tablet holds none (the
panel gate admits it instead). Every admitted panel is then sent
`ui.definition`, which carried
the whole of `engine.panel_ui()`, settings included. So the PIN the lock screen
was about to check was published to the client doing the checking, and
`panel.js` compared the typed attempt against it locally. The flow worked and
looked right; the Builder labels the field "Lock Code (PIN)" with no hint that
it was a courtesy.

The lock itself is a legitimate feature — it stops stray taps on a public
display. What was wrong was the shape: a numeric PIN labelled like security,
whose value the enforcing server handed out. Now the definition carries
`lock_enabled` and the attempt goes to `POST /api/panel/unlock`, which answers
`{"success": bool}` and nothing else.
"""

from unittest.mock import MagicMock

import pytest

from pydantic import ValidationError

from openavc.core.project_loader import ProjectConfig, UISettings


def _project(lock_code: str = "1234") -> ProjectConfig:
    return ProjectConfig.model_validate(
        {
            "openavc_version": "0.13.0",
            "project": {"id": "test", "name": "Test Room"},
            "ui": {"settings": {"lock_code": lock_code}, "pages": []},
        }
    )


def _engine(lock_code: str = "1234"):
    """A real Engine object with only the project wired — panel_ui and the
    lock check read nothing else."""
    from openavc.core.engine import Engine

    engine = Engine.__new__(Engine)
    engine.project = _project(lock_code)
    return engine


class TestWhatAPanelReceives:
    def test_the_pin_is_not_in_the_definition(self):
        ui = _engine("1234").panel_ui()
        assert "lock_code" not in ui["settings"]
        assert "1234" not in str(ui)

    def test_the_definition_says_a_lock_is_configured(self):
        assert _engine("1234").panel_ui()["settings"]["lock_enabled"] is True

    def test_no_pin_reads_as_no_lock(self):
        settings = _engine("").panel_ui()["settings"]
        assert settings["lock_enabled"] is False
        assert "lock_code" not in settings

    def test_the_rest_of_the_settings_still_travel(self):
        """Only the PIN is withheld. A panel cannot draw itself without the
        theme, the idle page and the rest of the bag."""
        from openavc.core.engine import Engine

        engine = Engine.__new__(Engine)
        engine.project = ProjectConfig.model_validate({
            "openavc_version": "0.13.0",
            "project": {"id": "test", "name": "Test Room"},
            "ui": {
                "settings": {
                    "lock_code": "4321", "theme": "dark",
                    "idle_timeout_seconds": 90, "idle_page": "main",
                },
                "pages": [],
            },
        })
        settings = engine.panel_ui()["settings"]
        assert settings["theme"] == "dark"
        assert settings["idle_timeout_seconds"] == 90
        assert settings["idle_page"] == "main"


    def test_the_project_keeps_its_pin(self):
        """The PIN is withheld from the payload, not taken out of the project.

        `panel_ui` builds its dict from `model_dump`, so the pop lands on a
        copy — but the Builder edits the project's own value and the check
        reads it, so a day when that stops being a copy would empty the field
        on the first panel that connected."""
        engine = _engine("1234")
        engine.panel_ui()
        assert engine.project.ui.settings.lock_code == "1234"
        assert engine.panel_lock_accepts("1234") is True


class TestTheCheck:
    def test_the_right_pin_is_accepted(self):
        assert _engine("1234").panel_lock_accepts("1234") is True

    def test_a_wrong_pin_is_refused(self):
        assert _engine("1234").panel_lock_accepts("0000") is False

    @pytest.mark.parametrize("attempt", ["", None, "12345", " 1234", "1234 "])
    def test_nothing_near_the_pin_gets_in(self, attempt):
        assert _engine("1234").panel_lock_accepts(attempt) is False

    def test_a_project_with_no_pin_accepts_nothing(self):
        """Not everything — the panel never raises the screen without a PIN,
        so an attempt arriving anyway is not somebody who left the field
        blank."""
        assert _engine("").panel_lock_accepts("") is False
        assert _engine("").panel_lock_accepts("1234") is False

    def test_a_non_ascii_attempt_answers_instead_of_raising(self):
        """`compare_digest` raises on two `str` arguments that are not both
        ASCII, which would answer the attempt with a 500. The schema holds the
        PIN to digits, but the check is a method anything can call."""
        assert _engine("1234").panel_lock_accepts("123ü") is False
        assert _engine("1234").panel_lock_accepts("١٢٣٤") is False

    def test_no_project_accepts_nothing(self):
        from openavc.core.engine import Engine

        engine = Engine.__new__(Engine)
        engine.project = None
        assert engine.panel_lock_accepts("1234") is False


class TestWhatAPinMayBe:
    """A PIN is what the panel's lock screen can take: the digits 0 to 9, at
    most six of them. Blank is no lock. Anything else could never be typed at
    the panel, so that panel could never be unlocked there."""

    @pytest.mark.parametrize("pin", ["", "0", "1234", "0123", "123456"])
    def test_a_pin_the_lock_screen_can_take_is_taken(self, pin):
        assert UISettings(lock_code=pin).lock_code == pin

    @pytest.mark.parametrize(
        "pin", ["1234567", "12 34", " 1234", "1234 ", "1234\n", "é12", "pässwörd", "١٢٣٤", "12a4"],
    )
    def test_a_pin_the_lock_screen_cannot_take_is_refused(self, pin):
        with pytest.raises(ValidationError) as caught:
            UISettings(lock_code=pin)
        assert [e["loc"] for e in caught.value.errors()] == [("lock_code",)]

    def test_the_unlock_route_takes_the_same_rule(self):
        """One rule, read by both: the request a lock screen sends is a PIN."""
        from openavc.api.models import PanelUnlockRequest

        assert PanelUnlockRequest.model_fields["code"].metadata == (
            UISettings.model_fields["lock_code"].metadata
        )

    def test_the_table_carries_it_for_the_builder_and_the_panel(self):
        from openavc.core.project_values_gen import project_values

        assert project_values()["UISettings"]["lock_code"] == {
            "default": "", "max_length": 6, "digits": True,
        }


class TestAnOlderProjectsPin:
    """A project saved before the rule holding a PIN the lock screen could
    not take loads with the PIN that screen holds when somebody types it."""

    @staticmethod
    def _older(pin) -> dict:
        return {
            "openavc_version": "0.16.0",
            "project": {"id": "acme_room", "name": "Acme Room"},
            "ui": {"settings": {"lock_code": pin}, "pages": []},
        }

    def _loaded_engine(self, tmp_path, pin):
        import json

        from openavc.core.engine import Engine
        from openavc.core.project_loader import load_project

        path = tmp_path / "project.avc"
        path.write_text(json.dumps(self._older(pin)), encoding="utf-8")
        engine = Engine.__new__(Engine)
        engine.project = load_project(path)
        return engine

    def test_a_pin_longer_than_the_box_unlocks_with_what_the_box_keeps(self, tmp_path):
        engine = self._loaded_engine(tmp_path, "12345678")
        assert engine.project.ui.settings.lock_code == "123456"
        assert engine.panel_lock_accepts("123456") is True

    def test_a_pin_with_a_space_unlocks_with_its_digits(self, tmp_path):
        engine = self._loaded_engine(tmp_path, "12 34")
        assert engine.panel_lock_accepts("1234") is True

    def test_a_pin_with_no_digit_loads_with_no_lock(self, tmp_path):
        engine = self._loaded_engine(tmp_path, "pässwörd")
        assert engine.project.ui.settings.lock_code == ""
        assert engine.panel_ui()["settings"]["lock_enabled"] is False


class TestTheDoor:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from openavc.api import rest, ws
        from openavc.main import app
        from tests.test_api_endpoints import _make_mock_engine

        engine = _make_mock_engine()
        engine.panel_lock_accepts = MagicMock(return_value=False)
        rest.set_engine(engine)
        ws.set_engine(engine)
        yield TestClient(app), engine
        rest.set_engine(None)
        ws.set_engine(None)

    def test_the_right_pin_answers_ok(self, client):
        c, engine = client
        engine.panel_lock_accepts.return_value = True
        resp = c.post("/api/panel/unlock", json={"code": "1234"})
        assert resp.status_code == 200
        assert resp.json() == {"success": True}

    def test_a_wrong_pin_answers_false_rather_than_an_error(self, client):
        """A 4xx would be a second channel saying the same thing, and a panel
        would have to know which shape means "wrong"."""
        c, _engine = client
        resp = c.post("/api/panel/unlock", json={"code": "0000"})
        assert resp.status_code == 200
        assert resp.json() == {"success": False}

    def test_the_answer_carries_nothing_but_the_verdict(self, client):
        c, engine = client
        engine.panel_lock_accepts.return_value = True
        assert set(c.post("/api/panel/unlock", json={"code": "1234"}).json()) == {"success"}

    def test_an_oversized_attempt_is_refused_at_the_model(self, client):
        """Open door: nothing is gained by letting an anonymous caller hand the
        comparison a megabyte."""
        c, _engine = client
        resp = c.post("/api/panel/unlock", json={"code": "9" * 5000})
        assert resp.status_code == 422

    @pytest.mark.parametrize("attempt", ["1234567", "12a4", "12 34", "١٢٣٤", "1234\n"])
    def test_an_attempt_no_pin_could_be_is_refused_at_the_model(self, client, attempt):
        """The route takes what a PIN may be, as the project does: a panel's
        box types nothing else, so only another caller sends one, and no
        project holds a PIN it could match."""
        c, engine = client
        resp = c.post("/api/panel/unlock", json={"code": attempt})
        assert resp.status_code == 422
        engine.panel_lock_accepts.assert_not_called()

    def test_an_attempt_as_long_as_a_pin_is_checked(self, client):
        c, engine = client
        resp = c.post("/api/panel/unlock", json={"code": "123456"})
        assert resp.status_code == 200
        engine.panel_lock_accepts.assert_called_once_with("123456")

    def test_a_missing_code_is_just_a_wrong_one(self, client):
        c, _engine = client
        resp = c.post("/api/panel/unlock", json={})
        assert resp.status_code == 200
        assert resp.json() == {"success": False}


class TestThePanelRuntimeAsksTheServer:
    """The panel is the other half and has no Python to pin it, so these read
    its source: `panel.js` and the lock it imports (`modules/lock.js`). The two
    paths are deliberate — see `PanelLock.check` — and the thing that must
    never come back is a bare comparison against a code the server sent every
    panel."""

    @pytest.fixture
    def panel_js(self):
        from pathlib import Path

        panel = Path(__file__).resolve().parents[1] / "openavc" / "web" / "panel"
        return "\n".join(
            (panel / name).read_text(encoding="utf-8")
            for name in ("panel.js", "modules/lock.js")
        )

    def test_the_runtime_posts_the_attempt(self, panel_js):
        assert "/api/panel/unlock" in panel_js

    def test_the_lock_screen_does_not_compare_against_the_definition(self, panel_js):
        assert "input.value === lockCode" not in panel_js

    def test_the_draft_path_is_the_only_local_comparison(self, panel_js):
        """The Builder's Preview is handed the project it is editing, unsaved
        PIN and all, so it compares locally — otherwise changing a PIN and
        pressing Preview would test the last saved one. It is an authenticated
        surface; a real panel is never given a `lock_code` to compare with."""
        assert panel_js.count("uiSettings?.lock_code") == 1
        assert "attempt === drafted" in panel_js

    def test_the_runtime_reads_the_enabled_flag(self, panel_js):
        assert "lock_enabled" in panel_js

    def test_an_unreachable_server_is_not_reported_as_a_wrong_pin(self, panel_js):
        """"Incorrect PIN" for a server that did not answer sends somebody to
        retype the PIN they already know. The check answers null for that, and
        the timeout is what turns a hung request into it rather than into an
        Unlock button that never comes back."""
        assert "Can't reach the server" in panel_js
        assert "AbortController" in panel_js
