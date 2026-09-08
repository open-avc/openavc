"""A refused project says which field is wrong and why.

Every project ingress door hands a raw dict to ``ProjectConfig`` and catches
the ``ValidationError`` itself, so FastAPI's request-validation handler never
sees it. All three doors turned it into a fixed sentence and discarded the
field list Pydantic had already produced, leaving the caller -- a person, an
integration, or the AI writing a project section -- to retry blind or bisect
the payload by hand.

The natural authoring mistake is realistic: `name`/`value` instead of
`id`/`default`, because the state API speaks `value`.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.errors import (
    StructuredApiError,
    structured_api_error_handler,
    validation_error_lines,
)
from openavc.api.rest import router, set_engine


def _project(**over) -> dict:
    body = {
        "openavc_version": "0.13.0",
        "project": {"id": "room", "name": "Room"},
        "devices": [],
        "connections": {},
        "variables": [],
        "macros": [],
        "ui": {"pages": []},
    }
    body.update(over)
    return body


@pytest.fixture
def mock_engine(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.avc").write_text("{}", encoding="utf-8")

    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"
    engine._project_revision = 0
    engine.apply_project = AsyncMock(return_value=1)
    engine.reload_project = AsyncMock()
    engine.broadcast_ws = AsyncMock()
    return engine


@pytest.fixture
def client(mock_engine):
    app = FastAPI()
    app.include_router(router)
    # main.py registers this; without it a StructuredApiError falls back to
    # FastAPI's plain HTTPException rendering and the sibling `errors` key is
    # dropped -- so a test app that skips it cannot see what ships.
    app.add_exception_handler(StructuredApiError, structured_api_error_handler)
    set_engine(mock_engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


class TestSaveDoor:
    def test_names_the_field_and_the_problem(self, client):
        resp = client.put(
            "/api/project",
            json=_project(variables=[{"name": "source", "value": "hdmi1"}]),
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "variables.0.id" in body["detail"]
        assert "Field required" in body["detail"]

    def test_keeps_the_sentence_in_front_of_the_list(self, client):
        """A client that renders only `detail` must still get both halves."""
        resp = client.put(
            "/api/project",
            json=_project(variables=[{"name": "source", "value": "hdmi1"}]),
        )
        detail = resp.json()["detail"]
        assert detail.startswith("Invalid project configuration:")
        assert detail.splitlines()[1:] == ["variables.0.id: Field required"]

    def test_carries_the_lines_alongside_for_a_client_that_wants_them(self, client):
        resp = client.put(
            "/api/project",
            json=_project(variables=[{"name": "source", "value": "hdmi1"}]),
        )
        assert resp.json()["errors"] == ["variables.0.id: Field required"]

    def test_names_every_problem_not_just_the_first(self, client):
        resp = client.put(
            "/api/project",
            json=_project(
                devices=[{"name": "Widget"}],
                variables=[{"name": "source", "value": "hdmi1"}],
            ),
        )
        errors = resp.json()["errors"]
        assert "devices.0.id: Field required" in errors
        assert "devices.0.driver: Field required" in errors
        assert "variables.0.id: Field required" in errors

    def test_never_echoes_the_caller_s_own_data_back(self, client):
        """Pydantic's error dicts carry `input_value`. That is the caller's
        data, and a project body holds their room's whole configuration."""
        resp = client.put(
            "/api/project",
            json=_project(variables=[{"name": "source", "value": "hdmi1"}]),
        )
        assert "hdmi1" not in resp.text
        assert "input_value" not in resp.text

    def test_a_failure_that_is_not_a_validation_error_keeps_its_sentence(self, client):
        """The generic branch still stands -- a body that breaks the migration
        chain has no field list to give."""
        with patch(
            "openavc.core.project_migration.migrate_project",
            side_effect=RuntimeError("chain blew up"),
        ):
            resp = client.put("/api/project", json=_project())
        assert resp.status_code == 422
        assert resp.json()["detail"] == "Invalid project configuration"
        # And it does not leak the exception text.
        assert "chain blew up" not in resp.text


class TestImportDoor:
    def test_names_the_field(self, client):
        bad = json.dumps(_project(variables=[{"name": "source", "value": "hdmi1"}]))
        resp = client.post(
            "/api/library/import",
            files={"file": ("broken.avc", bad, "application/json")},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"].startswith("Invalid project file 'broken.avc':")
        assert body["errors"] == ["variables.0.id: Field required"]

    def test_a_plain_value_error_still_gets_its_own_sentence(self, client):
        """Pydantic's ValidationError IS a ValueError. If the branches are
        ordered the other way the field list is swallowed; if the new branch
        is too greedy, refusals like this one lose their wording."""
        resp = client.post(
            "/api/library/import",
            files={"file": ("notes.txt", "nope", "text/plain")},
        )
        assert resp.status_code == 422
        assert "must be .avc" in resp.json()["detail"]


class TestOpenFromLibraryDoor:
    def test_names_the_field_in_a_stored_project(self, client, tmp_path):
        import openavc.core.project_library as plib

        lib_dir = tmp_path / "saved_projects"
        (lib_dir / "broken").mkdir(parents=True)
        bad = _project(variables=[{"name": "source", "value": "hdmi1"}])
        (lib_dir / "broken" / "project.avc").write_text(
            json.dumps(bad), encoding="utf-8"
        )

        with patch.object(plib, "config") as mock_config:
            mock_config.SAVED_PROJECTS_DIR = lib_dir
            resp = client.post(
                "/api/project/open-from-library",
                json={"library_id": "broken", "project_name": "Broken"},
            )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"].startswith(
            "Saved project 'broken' is not a valid project file:"
        )
        assert body["errors"] == ["variables.0.id: Field required"]


class TestValidationErrorLines:
    def test_drops_the_location_marker_a_typed_endpoint_adds(self):
        assert validation_error_lines(
            [{"loc": ("body", "variables", 0, "id"), "msg": "Field required"}]
        ) == ["variables.0.id: Field required"]

    def test_leaves_a_bare_pydantic_path_alone(self):
        assert validation_error_lines(
            [{"loc": ("variables", 0, "id"), "msg": "Field required"}]
        ) == ["variables.0.id: Field required"]

    def test_has_something_to_say_with_no_path_and_no_message(self):
        assert validation_error_lines([{}]) == ["request: invalid value"]

    def test_keeps_pydantic_s_order(self):
        lines = validation_error_lines(
            [
                {"loc": ("devices", 0, "id"), "msg": "Field required"},
                {"loc": ("variables", 0, "id"), "msg": "Field required"},
            ]
        )
        assert lines == [
            "devices.0.id: Field required",
            "variables.0.id: Field required",
        ]
