"""The panel lock PIN stays on the server.

A panel client is unauthenticated by design — `api/ws.py` falls back to
`client_type = "panel"` when auth fails, because a wall tablet holds no
credential. Every panel on the LAN is then sent `ui.definition`, which carried
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

from openavc.core.project_loader import ProjectConfig


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

    def test_a_non_ascii_pin_answers_instead_of_raising(self):
        """`compare_digest` raises on two non-ASCII `str` arguments, which
        would 500 every attempt. The Builder's field takes digits, but a
        project file can be hand-written or AI-authored."""
        assert _engine("pässwörd").panel_lock_accepts("pässwörd") is True
        assert _engine("pässwörd").panel_lock_accepts("passwörd") is False
        assert _engine("1234").panel_lock_accepts("123ü") is False

    def test_no_project_accepts_nothing(self):
        from openavc.core.engine import Engine

        engine = Engine.__new__(Engine)
        engine.project = None
        assert engine.panel_lock_accepts("1234") is False


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

    def test_a_missing_code_is_just_a_wrong_one(self, client):
        c, _engine = client
        resp = c.post("/api/panel/unlock", json={})
        assert resp.status_code == 200
        assert resp.json() == {"success": False}


class TestThePanelRuntimeAsksTheServer:
    """`panel.js` is the other half and has no Python to pin it, so these read
    the file. The two paths are deliberate — see `_checkLockCode` — and the
    thing that must never come back is a bare comparison against a code the
    server sent every panel."""

    @pytest.fixture
    def panel_js(self):
        from pathlib import Path

        return (
            Path(__file__).resolve().parents[1]
            / "openavc" / "web" / "panel" / "panel.js"
        ).read_text(encoding="utf-8")

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
