"""The power-user schema reservations survive a full round-trip.

`element.css_class`, `page.render_mode` and the `ui.custom_css` stylesheet are
reserved fields: declared and persisted now, with no editor in front of them
yet. That makes them exactly the shape of field that goes missing quietly --
`grid_gap` and `thumb_size` both rode `extra="allow"` for months and were only
found by an audit, and a reserved field that round-trips as nothing is worse
than no field at all, because the project that hand-wrote it looks saved.

So the round-trip is pinned in both directions: through the save endpoint that
the Programmer and every import flow use, and through the load/dump cycle that
puts a project back on disk.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.api.rest import router, set_engine
from server.core.project_loader import ProjectConfig, UIConfig, UIPage


def _project_with_power_fields() -> dict:
    return {
        "openavc_version": "0.8.0",
        "project": {"id": "room", "name": "Room"},
        "devices": [],
        "connections": {},
        "ui": {
            "settings": {},
            "custom_css": ".brand-tile { border-radius: 0; }\n",
            "pages": [
                {
                    "id": "main",
                    "name": "Main",
                    "render_mode": "custom",
                    "elements": [
                        {"id": "tile", "type": "button", "css_class": "brand-tile accent"},
                    ],
                    "layouts": [
                        {
                            "id": "landscape",
                            "orientation": "landscape",
                            "primary": True,
                            "placements": {"tile": {"x": 0, "y": 0, "w": 50, "h": 50}},
                            "hidden": [],
                        }
                    ],
                }
            ],
            "master_elements": [
                {
                    "id": "logo",
                    "type": "label",
                    "css_class": "brand-logo",
                    "pages": "*",
                    "placements": {"landscape": {"x": 0, "y": 0, "w": 20, "h": 10}},
                }
            ],
        },
    }


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
    set_engine(mock_engine)
    yield TestClient(app, raise_server_exceptions=False)
    set_engine(None)


class TestSaveEndpointKeepsThem:
    """PUT /project is the door the Programmer saves through."""

    def test_all_three_survive_the_save(self, client, mock_engine):
        resp = client.put("/api/project", json=_project_with_power_fields())
        assert resp.status_code == 200

        project = mock_engine.apply_project.call_args.args[0]
        assert project.ui.custom_css == ".brand-tile { border-radius: 0; }\n"

        page = project.ui.pages[0]
        assert page.render_mode == "custom"
        assert page.elements[0].css_class == "brand-tile accent"
        assert project.ui.master_elements[0].css_class == "brand-logo"


class TestDiskRoundTrip:
    """Load -> dump -> load, which is what a save then a reload actually do."""

    def test_fields_survive_a_dump_and_reload(self):
        first = ProjectConfig(**_project_with_power_fields())
        second = ProjectConfig(**first.model_dump())

        assert second.ui.custom_css == first.ui.custom_css
        assert second.ui.pages[0].render_mode == "custom"
        assert second.ui.pages[0].elements[0].css_class == "brand-tile accent"
        assert second.ui.master_elements[0].css_class == "brand-logo"

    def test_dump_actually_emits_them(self):
        """A field can survive a reload by being re-defaulted rather than
        carried, which looks identical from the model and loses the author's
        value on any consumer reading the raw JSON.
        """
        dumped = ProjectConfig(**_project_with_power_fields()).model_dump()
        assert dumped["ui"]["custom_css"] == ".brand-tile { border-radius: 0; }\n"
        assert dumped["ui"]["pages"][0]["render_mode"] == "custom"
        assert dumped["ui"]["pages"][0]["elements"][0]["css_class"] == "brand-tile accent"


class TestDefaults:
    """No migration ships with these fields, so an existing 0.8.0 project that
    predates them has to load correctly on the declared defaults alone. This is
    the assertion that decision rests on (same reasoning as `locked`).
    """

    def test_absent_fields_take_their_defaults(self):
        ui = UIConfig(**{"settings": {}, "pages": [{"id": "main", "name": "Main"}]})
        assert ui.custom_css == ""
        assert ui.pages[0].render_mode == "elements"

    def test_element_css_class_defaults_to_none(self):
        page = UIPage(id="main", name="Main", elements=[{"id": "b", "type": "button"}])
        assert page.elements[0].css_class is None


class TestDeclaredNotMerelyTolerated:
    """The round-trip assertions above pass whether or not the fields are
    declared, because `extra="allow"` keeps unknown keys AND exposes them as
    attributes -- which is precisely how `grid_gap` and `thumb_size` looked
    healthy for months. So pin the declaration itself: in `model_fields` (the
    schema knows them, they get a type and a default, editors can discover
    them) and absent from `model_extra` (nothing is riding the escape hatch).
    """

    def test_fields_are_declared_on_their_models(self):
        assert "render_mode" in UIPage.model_fields
        assert "custom_css" in UIConfig.model_fields
        from server.core.project_loader import UIElement
        assert "css_class" in UIElement.model_fields
        # The image element's fit mode: written by the Builder, read by the
        # renderer, and undeclared until an audit caught it riding the escape
        # hatch -- the third field found that way, after grid_gap and
        # thumb_size.
        assert "object_fit" in UIElement.model_fields

    def test_fresh_project_is_stamped_with_the_current_format(self):
        """A bare ProjectConfig must not claim an older format than its body.

        The recovery path (engine._create_recovery_project) builds one with no
        explicit version; stamped 0.7.0, everything built on it afterwards gets
        the 0.7->0.8 migration re-run on its next save -- placements collapse
        to the 1x1 cell and every rem value is divided by 14 again.
        """
        from server.core.project_migration import CURRENT_VERSION
        from server.core.project_loader import ProjectMeta
        fresh = ProjectConfig(project=ProjectMeta(id="x", name="x"))
        assert fresh.openavc_version == CURRENT_VERSION

    def test_nothing_lands_in_model_extra(self):
        project = ProjectConfig(**_project_with_power_fields())
        assert project.ui.model_extra == {}
        assert project.ui.pages[0].model_extra == {}
        assert project.ui.pages[0].elements[0].model_extra == {}


class TestRenderModeIsConstrained:
    def test_unknown_render_mode_is_rejected(self):
        """It reserves two named modes. An unconstrained string would let a typo
        persist as a mode nothing will ever implement.
        """
        with pytest.raises(ValidationError):
            UIPage(id="main", name="Main", render_mode="freeform")
