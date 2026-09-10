"""What still shows an asset, and the two doors that refuse to delete one.

The walk is deliberately field-agnostic -- it matches the stored
``assets://<name>`` string anywhere under a holder rather than reading a list
of field names -- so most of these cases exist to prove that a reference the
schema never named is still found: one inside a free-form ``style`` dict, one
inside a single feedback state's override, one in a plugin's own configuration
blob, and one in a section nothing here walks by name.

Invented devices and made-up file names throughout, per the core-test rule.
"""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openavc.api import assets as assets_api, rest, themes as themes_api
from openavc.core.asset_references import asset_reference, asset_users
from openavc.core.engine import Engine
from openavc.core.project_loader import ProjectConfig
from openavc.main import app


def _project(**sections) -> ProjectConfig:
    base = {
        "project": {"id": "asset_refs", "name": "Asset Reference Room"},
        "devices": [],
        "variables": [],
        "macros": [],
        "ui": {"pages": []},
    }
    base.update(sections)
    return ProjectConfig(**base)


def _page(page_id: str, elements: list[dict], **extra) -> dict:
    return {"id": page_id, "name": page_id.title(), "elements": elements, **extra}


# ──── The stored form ────


def test_the_reference_is_the_form_the_upload_door_hands_back():
    """``assets://<name>`` is the one shape a reference is ever written in, so
    a caller holding either half gets the same answer."""
    assert asset_reference("logo.png") == "assets://logo.png"
    assert asset_reference("assets://logo.png") == "assets://logo.png"


# ──── Where a reference can sit ────


@pytest.mark.parametrize(
    "element,expected",
    [
        ({"id": "hero", "type": "image", "src": "assets://logo.png"},
         "element 'hero' on page 'main'"),
        ({"id": "power", "type": "button", "icon": "assets://logo.png"},
         "element 'power' on page 'main'"),
        ({"id": "power", "type": "button", "button_image": "assets://logo.png"},
         "element 'power' on page 'main'"),
        # Not a declared field -- style is a free-form dict.
        ({"id": "panel", "type": "group",
          "style": {"background_image": "assets://logo.png"}},
         "element 'panel' on page 'main'"),
        # Not a declared field either, and one level deeper: the override that
        # only applies while the control is in one feedback state.
        ({"id": "power", "type": "button",
          "states": {"active": {"button_image": "assets://logo.png"}}},
         "element 'power' on page 'main'"),
    ],
)
def test_an_element_that_shows_it_is_named_with_its_page(element, expected):
    project = _project(ui={"pages": [_page("main", [element])]})
    assert asset_users(project, "logo.png") == [expected]


def test_a_page_background_is_named_as_the_page():
    project = _project(ui={"pages": [
        _page("main", [], background={"image": "assets://logo.png"}),
    ]})
    assert asset_users(project, "logo.png") == ["page 'main'"]


def test_an_element_does_not_make_its_page_look_guilty():
    """A page is asked about itself without its elements, so a refusal sends
    somebody to the control that shows the file, not to the page around it."""
    project = _project(ui={"pages": [
        _page("main", [{"id": "hero", "type": "image", "src": "assets://logo.png"}]),
    ]})
    assert asset_users(project, "logo.png") == ["element 'hero' on page 'main'"]


def test_a_master_element_is_named_without_a_page():
    """It belongs to no page's layout, so naming one would be a lie."""
    project = _project(ui={
        "pages": [],
        "master_elements": [
            {"id": "logo_bar", "type": "image", "src": "assets://logo.png"},
        ],
    })
    assert asset_users(project, "logo.png") == ["master element 'logo_bar'"]


def test_a_plugin_configuration_is_named_by_its_plugin():
    """A sound is the case that made this worth walking: the panel resolves an
    ``assets://`` sound id exactly as it resolves an image, and the id lives in
    a plugin's own configuration where no project field describes it."""
    project = _project(plugins={
        "audio_player": {"enabled": True,
                         "config": {"startup_sound": "assets://chime.mp3"}},
    })
    assert asset_users(project, "chime.mp3") == [
        "plugin 'audio_player' configuration"
    ]


def test_a_macro_step_is_named_by_its_macro():
    project = _project(macros=[{
        "id": "welcome", "name": "Welcome",
        "steps": [{"action": "state.set", "key": "ui.hero.src",
                   "value": "assets://logo.png"}],
    }])
    assert asset_users(project, "logo.png") == ["macro 'welcome'"]


def test_a_trigger_reports_as_the_macro_that_holds_it():
    """Triggers are nested in their macro rather than a top-level section, and
    the macro is where somebody sent to fix it has to go."""
    project = _project(macros=[{
        "id": "welcome", "name": "Welcome", "steps": [],
        "triggers": [{"id": "on_welcome", "type": "event",
                      "event": "assets://logo.png"}],
    }])
    assert asset_users(project, "logo.png") == ["macro 'welcome'"]


def test_a_section_nobody_walks_by_name_still_reports():
    """The sweep is the reason a missed surface cannot make a delete look safe.

    Nothing puts an asset reference in ``settings`` today. That is the point:
    the walk needs no list of what can hold one, so a section added later
    reports by its own name instead of going quiet.
    """
    project = _project(settings={"panel": {"splash_image": "assets://logo.png"}})
    assert asset_users(project, "logo.png") == ["the project's 'settings' section"]


def test_a_theme_reports_when_the_caller_can_read_themes():
    """A theme's page background resolves through the same path, and losing it
    takes the background off every page running that theme."""
    themes = [{"id": "midnight",
               "page_defaults": {"background_image": "assets://logo.png"}}]
    assert asset_users(_project(), "logo.png", themes=themes) == ["theme 'midnight'"]


def test_themes_are_only_claimed_about_when_they_are_passed():
    """No themes in means no claim about themes, rather than a claim they are
    clean -- the caller that cannot read them must not imply it did."""
    assert asset_users(_project(), "logo.png") == []


# ──── What is NOT a reference ────


def test_an_asset_nothing_names_has_no_users():
    project = _project(ui={"pages": [
        _page("main", [{"id": "hero", "type": "image", "src": "assets://other.png"}]),
    ]})
    assert asset_users(project, "logo.png") == []


def test_a_string_that_merely_contains_the_reference_is_not_a_use():
    """The panel tests ``startsWith('assets://')`` against the whole value and
    takes the rest as the filename, so a reference is the entire value or it
    draws nothing. A stylesheet mentioning one was never going to load it, and
    refusing a delete over that would be refusing over nothing.
    """
    project = _project(ui={
        "pages": [],
        "custom_css": ".hero { background: url(assets://logo.png); }",
    })
    assert asset_users(project, "logo.png") == []


def test_two_holders_are_both_named_and_the_order_is_stable():
    project = _project(ui={"pages": [
        _page("main", [{"id": "hero", "type": "image", "src": "assets://logo.png"}]),
        _page("aux", [], background={"image": "assets://logo.png"}),
    ]})
    assert asset_users(project, "logo.png") == [
        "element 'hero' on page 'main'", "page 'aux'",
    ]


# ──── The doors ────


TEST_PROJECT = {
    "project": {"id": "asset_refs", "name": "Asset Reference Room"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [{
        "id": "main", "name": "Main",
        "elements": [{"id": "hero", "type": "image", "src": "assets://logo.png"}],
    }]},
}


def _png() -> bytes:
    import struct
    ihdr = struct.pack(">I", 13) + b"IHDR" + struct.pack(
        ">IIBBBBB", 1, 1, 8, 2, 0, 0, 0
    ) + struct.pack(">I", 0)
    return b"\x89PNG\r\n\x1a\n" + ihdr + struct.pack(">I", 0) + b"IEND" + struct.pack(">I", 0)


@pytest.fixture
def engine_client():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)
    from openavc.core.project_loader import load_project
    engine.project = load_project(tmp_path)
    engine._running = True

    assets_dir = Path(tmp_path).parent / "assets"
    assets_dir.mkdir(exist_ok=True)
    for name in ("logo.png", "spare.png"):
        (assets_dir / name).write_bytes(_png())

    rest.set_engine(engine)
    assets_api.set_engine(engine)
    themes_api.set_engine(engine)
    yield TestClient(app), engine, assets_dir
    rest.set_engine(None)
    assets_api.set_engine(None)
    themes_api.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)
    for name in ("logo.png", "spare.png"):
        (assets_dir / name).unlink(missing_ok=True)


async def test_the_rest_door_refuses_an_asset_still_shown(engine_client):
    client, _engine, assets_dir = engine_client
    resp = client.delete("/api/projects/default/assets/logo.png")
    assert resp.status_code == 409
    assert "element 'hero' on page 'main'" in resp.json()["detail"]
    assert (assets_dir / "logo.png").exists(), "it refused and deleted it anyway"


async def test_the_rest_door_still_deletes_one_nothing_shows(engine_client):
    """Both halves of the rule are argued: a door that refuses everything
    would pass the test above on its own."""
    client, _engine, assets_dir = engine_client
    resp = client.delete("/api/projects/default/assets/spare.png")
    assert resp.status_code == 200
    assert not (assets_dir / "spare.png").exists()


async def test_a_missing_asset_is_still_a_404_not_a_reference_answer(engine_client):
    """The check is asked after the file is known to exist, so an element
    pointing at a name that is already gone says so plainly."""
    client, _engine, _assets_dir = engine_client
    resp = client.delete("/api/projects/default/assets/never-uploaded.png")
    assert resp.status_code == 404


async def test_the_listing_says_who_shows_each_asset(engine_client):
    client, _engine, _assets_dir = engine_client
    listed = {a["name"]: a for a in
              client.get("/api/projects/default/assets").json()["assets"]}
    assert listed["logo.png"]["used_by"] == ["element 'hero' on page 'main'"]
    assert listed["spare.png"]["used_by"] == []


async def test_the_ai_door_refuses_with_the_same_sentence(engine_client):
    """One rule, asked by both doors. They used to be able to disagree about
    whether an asset was in use, because neither of them asked anything."""
    _client, engine, assets_dir = engine_client
    from unittest.mock import MagicMock

    from openavc.cloud.ai_tool_handler import AIToolHandler

    # The handler reads the engine from api.rest, which the fixture set; its
    # constructor arguments are the agent, devices and events it does not use
    # on this path.
    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    assert handler._get_engine() is engine
    refused = await handler._delete_asset({"filename": "logo.png"})
    assert "element 'hero' on page 'main'" in refused["error"]
    assert refused["still_shown_by"] == ["element 'hero' on page 'main'"]
    assert (assets_dir / "logo.png").exists()

    deleted = await handler._delete_asset({"filename": "spare.png"})
    assert deleted["status"] == "deleted"
    assert not (assets_dir / "spare.png").exists()


async def test_the_ai_listing_carries_used_by(engine_client):
    _client, _engine, _assets_dir = engine_client
    from unittest.mock import MagicMock

    from openavc.cloud.ai_tool_handler import AIToolHandler

    handler = AIToolHandler(MagicMock(), MagicMock(), MagicMock())
    listed = {a["name"]: a for a in (await handler._list_assets({}))["assets"]}
    assert listed["logo.png"]["used_by"] == ["element 'hero' on page 'main'"]
    assert listed["spare.png"]["used_by"] == []


async def test_unreadable_themes_never_turn_a_delete_into_a_503(engine_client):
    """Whether themes can be read is not a reason to refuse a delete.

    The reader raises rather than returns when its own module has no engine,
    and an asset door that let that through answered 503 to every delete and
    every listing -- which is how this was found. The answer narrows to what
    the project itself says; it never becomes an error.
    """
    client, _engine, assets_dir = engine_client
    themes_api.set_engine(None)
    resp = client.delete("/api/projects/default/assets/spare.png")
    assert resp.status_code == 200
    assert not (assets_dir / "spare.png").exists()
    still = client.delete("/api/projects/default/assets/logo.png")
    assert still.status_code == 409, "the project half of the walk stopped working"
