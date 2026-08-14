"""The assistant's half of the project's ``ui/`` folder, through the real door.

Every call here goes through ``AIToolHandler.handle`` rather than the mixin
method, because the things worth pinning are properties of the door: that a
refusal comes back as a correctable error rather than an exception, that a
write announces itself to the panels, that a delete will not orphan a control
somebody is looking at, and that a reply never claims more than "the bytes
landed and were read".

The rules themselves are ``core/custom_ui.py``'s -- these assert that this door
asks that module rather than restating it, which is the whole reason it exists.

Everything is invented: a fictional control in a fictional project.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler

ENTRY = "room_map/index.html"

GOOD_CONTROL = (
    "<!DOCTYPE html><html><head><style>html, body { margin: 0; height: 100% }</style>"
    "</head><body><div id='out'></div><script>"
    "window.onerror = m => parent.postMessage({type: 'openavc:error', message: String(m)}, '*');"
    "window.addEventListener('message', e => {"
    "  if (e.data.type === 'openavc:init') show(e.data.state['device.dsp1.level']);"
    "});"
    "function show(v) { document.getElementById('out').textContent = v; }"
    "</script></body></html>"
)


def _make_project():
    from openavc.core.project_loader import (
        DeviceConfig, Layout, Placement, ProjectConfig, ProjectMeta,
        UIConfig, UIElement, UIPage,
    )
    return ProjectConfig(
        project=ProjectMeta(id="test_project", name="Test Room"),
        devices=[DeviceConfig(id="dsp1", driver="acme_dsp", name="Processor")],
        ui=UIConfig(pages=[
            UIPage(
                id="main", name="Main",
                elements=[
                    UIElement(
                        id="room_map", type="custom", custom_file=ENTRY,
                        grant={"devices": ["dsp1"]},
                    ),
                ],
                layouts=[Layout(id="landscape", primary=True, placements={
                    "room_map": Placement(x=0, y=0, w=50, h=50),
                })],
            ),
        ]),
    )


def _tool_call(tool_name, tool_input=None, request_id="req-1"):
    from openavc.cloud.protocol import AI_TOOL_CALL, _now_iso

    return {
        "type": AI_TOOL_CALL,
        "ts": _now_iso(),
        "seq": 1,
        "session": "test",
        "payload": {
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        },
    }


async def _drain():
    for _ in range(10):
        await asyncio.sleep(0)  # noqa: ASYNC110 - bounded drain, not a busy-wait
        others = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not others:
            return
        await asyncio.wait(others, timeout=2.0)


@pytest.fixture
def project_dir(tmp_path):
    ui = tmp_path / "ui" / "room_map"
    ui.mkdir(parents=True)
    (ui / "index.html").write_text(GOOD_CONTROL, encoding="utf-8")
    return tmp_path


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_message = AsyncMock()
    return agent


@pytest.fixture
def mock_engine(project_dir):
    engine = MagicMock()
    engine.project = _make_project()
    engine.project_path = str(project_dir / "project.avc")
    engine.broadcast_ws = AsyncMock()

    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        return 1

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    with patch("openavc.core.project_loader.save_project"):
        yield


@pytest.fixture
def handler(mock_agent):
    devices = MagicMock()
    devices.list_devices.return_value = []
    return AIToolHandler(mock_agent, devices, MagicMock(emit=AsyncMock()))


async def _run(handler, engine, agent, tool, payload=None):
    with patch.object(handler, "_get_engine", return_value=engine), \
            patch("openavc.api._engine._get_engine", return_value=engine), \
            patch("openavc.api.routes.ui_files._get_engine", return_value=engine):
        await handler.handle(_tool_call(tool, payload))
        await _drain()
    return agent.send_message.call_args[0][1]


def _result(payload):
    assert payload["success"] is True, payload
    return payload["result"]


def _error(payload):
    assert payload["success"] is False, payload
    return payload["error"]


# --- Reading ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_listing_says_what_uses_each_file(handler, mock_engine, mock_agent):
    """The back-reference is the half that makes this worth a call: it is how
    "is this control still in use" gets answered without reading the project
    again."""
    result = _result(await _run(handler, mock_engine, mock_agent, "list_ui_files"))

    entry = next(f for f in result["files"] if f["path"] == ENTRY)
    assert entry["used_by"] == ["element 'room_map' on page 'main'"]
    assert entry["readable"] is True
    assert result["file_count"] == 1


@pytest.mark.asyncio
async def test_an_empty_folder_says_how_to_put_something_in_it(
    handler, mock_engine, mock_agent, project_dir,
):
    (project_dir / "ui" / "room_map" / "index.html").unlink()
    result = _result(await _run(handler, mock_engine, mock_agent, "list_ui_files"))

    assert result["files"] == []
    assert "write_ui_file" in result["note"]


@pytest.mark.asyncio
async def test_a_file_a_control_points_at_and_nobody_wrote_is_listed_as_missing(
    handler, mock_engine, mock_agent, project_dir,
):
    """The element keeps drawing and its box comes up empty. Nothing else in a
    tool reply would ever mention it."""
    (project_dir / "ui" / "room_map" / "index.html").unlink()
    result = _result(await _run(handler, mock_engine, mock_agent, "list_ui_files"))

    assert result["missing"] == {ENTRY: ["element 'room_map' on page 'main'"]}


@pytest.mark.asyncio
async def test_reading_a_file_returns_its_text(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "read_ui_file", {"path": ENTRY}))
    assert result["content"] == GOOD_CONTROL


@pytest.mark.asyncio
async def test_reading_an_image_answers_with_its_size_and_never_its_bytes(
    handler, mock_engine, mock_agent, project_dir,
):
    """There is no reason to spend a context window on a PNG."""
    (project_dir / "ui" / "room_map" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    result = _result(await _run(
        handler, mock_engine, mock_agent, "read_ui_file", {"path": "room_map/logo.png"},
    ))

    assert "content" not in result
    assert result["size"] == 48
    assert result["type"] == "png"


@pytest.mark.asyncio
async def test_reading_a_file_that_is_not_there_lists_what_is(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "read_ui_file", {"path": "room_map/map.js"})
    assert ENTRY in _error(payload)


@pytest.mark.asyncio
async def test_a_path_that_climbs_out_of_the_folder_is_refused_in_the_modules_words(
    handler, mock_engine, mock_agent,
):
    """The rule is ``core/custom_ui.py``'s and the message is its own -- an
    integrator refused at the drag-and-drop door and a model refused here are
    being told the same thing by the same rule."""
    payload = await _run(
        handler, mock_engine, mock_agent, "read_ui_file", {"path": "../../etc/passwd"},
    )
    assert "not a valid file or folder name" in _error(payload)


# --- Writing ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_write_lands_tells_the_panels_and_never_claims_it_ran(
    handler, mock_engine, mock_agent, project_dir,
):
    """Three things at once, and the third is the one that matters.

    Nothing on this path executes markup, so a reply that read as "the control
    works" would be the tool lying about the only thing it cannot know.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/index.html", "content": GOOD_CONTROL,
    }))

    assert result["status"] == "replaced"
    assert (project_dir / "ui" / "room_map" / "index.html").read_text() == GOOD_CONTROL
    frames = [c.args[0] for c in mock_engine.broadcast_ws.call_args_list]
    assert any(f["type"] == "ui.files" and f["path"] == "room_map/index.html" for f in frames)
    assert "not run" in result["note"]


@pytest.mark.asyncio
async def test_a_new_control_comes_back_with_what_will_go_wrong_in_a_room(
    handler, mock_engine, mock_agent,
):
    """The write still lands. That is the whole posture: a rejection costs a
    round trip, a warning in this reply costs nothing."""
    result = _result(await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/index.html",
        "content": '<script src="https://cdn.example.com/chart.js"></script>',
    }))

    assert result["status"] == "replaced"
    assert any("no internet" in w for w in result["warnings"])
    assert "not run" in result["warning_note"]


@pytest.mark.asyncio
async def test_a_write_reports_a_grant_the_control_never_uses(
    handler, mock_engine, mock_agent,
):
    """The over-grant finding needs both halves -- the markup and the element
    that granted it -- so the file door is one of the two places it can fire."""
    result = _result(await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/index.html",
        "content": (
            "<style>html, body { margin: 0 }</style><script>"
            "window.onerror = m => parent.postMessage({type:'openavc:error', message:String(m)}, '*');"
            "window.addEventListener('message', e => e.data.type === 'openavc:init');"
            "</script>"
        ),
    }))

    assert any("'dsp1'" in w and "custom control 'room_map'" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_a_clean_control_is_told_where_to_look_at_it(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/index.html",
        "content": GOOD_CONTROL.replace("device.dsp1.level", "device.dsp1.level"),
    }))

    assert "warnings" not in result
    assert "design canvas" in result["note"]


@pytest.mark.asyncio
async def test_a_file_type_the_folder_will_not_take_is_refused_by_the_shared_rule(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/setup.py", "content": "import os",
    })
    assert "not a file type custom UI can use" in _error(payload)


@pytest.mark.asyncio
async def test_an_image_cannot_be_written_as_text(handler, mock_engine, mock_agent):
    """The folder takes a ``.png``; a tool that takes text cannot produce one.
    Saying so beats writing markup into a file every panel will try to decode."""
    payload = await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/logo.png", "content": "not really a png",
    })
    assert "has to be added in the IDE" in _error(payload)


@pytest.mark.asyncio
async def test_content_that_is_not_text_says_a_write_replaces_the_whole_file(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": ENTRY, "content": {"html": "<div/>"},
    })
    assert "whole file" in _error(payload)


@pytest.mark.asyncio
async def test_a_file_over_the_size_cap_is_refused_with_the_number(
    handler, mock_engine, mock_agent,
):
    from openavc.core.custom_ui import MAX_FILE_SIZE

    payload = await _run(handler, mock_engine, mock_agent, "write_ui_file", {
        "path": "room_map/big.js", "content": "x" * (MAX_FILE_SIZE + 1),
    })
    assert "MB limit" in _error(payload)


# --- Deleting --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_file_a_control_still_shows_cannot_be_deleted(
    handler, mock_engine, mock_agent, project_dir,
):
    """The one refusal in this folder that is about the PROJECT rather than the
    path: the element keeps drawing and its box comes up empty, with nothing on
    the glass to say why."""
    payload = await _run(handler, mock_engine, mock_agent, "delete_ui_file", {"path": ENTRY})

    assert "element 'room_map' on page 'main'" in _error(payload)
    assert (project_dir / "ui" / "room_map" / "index.html").is_file()


@pytest.mark.asyncio
async def test_deleting_the_whole_folder_of_a_control_in_use_is_refused_too(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "delete_ui_file", {"path": "room_map"})
    assert "element 'room_map' on page 'main'" in _error(payload)


@pytest.mark.asyncio
async def test_a_file_nothing_points_at_is_deleted_and_announced(
    handler, mock_engine, mock_agent, project_dir,
):
    (project_dir / "ui" / "notes.md").write_text("scratch", encoding="utf-8")
    result = _result(await _run(
        handler, mock_engine, mock_agent, "delete_ui_file", {"path": "notes.md"},
    ))

    assert result["status"] == "deleted"
    assert not (project_dir / "ui" / "notes.md").exists()
    frames = [c.args[0] for c in mock_engine.broadcast_ws.call_args_list]
    assert any(f["type"] == "ui.files" for f in frames)


@pytest.mark.asyncio
async def test_deleting_something_that_is_not_there_lists_what_is(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "delete_ui_file", {"path": "gone.html"})
    assert ENTRY in _error(payload)


# --- The stylesheet --------------------------------------------------------


@pytest.mark.asyncio
async def test_the_stylesheet_comes_back_with_the_classes_it_defines(
    handler, mock_engine, mock_agent,
):
    mock_engine.project.ui.custom_css = ".brand { color: red }"
    result = _result(await _run(handler, mock_engine, mock_agent, "get_project_stylesheet"))

    assert result["css"] == ".brand { color: red }"
    assert result["classes"] == ["brand"]


@pytest.mark.asyncio
async def test_setting_the_stylesheet_replaces_the_whole_document(
    handler, mock_engine, mock_agent,
):
    """A document, not a patch -- the same shape as update_script_source, and
    for the same reason a partial merge of one is how you get a mangled one."""
    mock_engine.project.ui.custom_css = ".old { color: red }"
    result = _result(await _run(handler, mock_engine, mock_agent, "set_project_stylesheet", {
        "css": ".brand-button { background: #8AB493 }",
    }))

    assert result["status"] == "saved"
    assert mock_engine.project.ui.custom_css == ".brand-button { background: #8AB493 }"
    assert result["classes"] == ["brand-button"]


@pytest.mark.asyncio
async def test_a_stylesheet_rule_that_hits_the_whole_panel_is_warned_about(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "set_project_stylesheet", {
        "css": "button { display: none }",
    }))

    assert result["status"] == "saved"
    assert any("!important" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_a_class_an_element_names_and_the_sheet_lacks_is_warned_about(
    handler, mock_engine, mock_agent,
):
    mock_engine.project.ui.pages[0].elements[0].css_class = "brand"
    result = _result(await _run(handler, mock_engine, mock_agent, "set_project_stylesheet", {
        "css": ".other { color: red }",
    }))

    assert any("'brand'" in w and "element 'room_map'" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_a_stylesheet_sent_as_something_other_than_text_is_refused(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "set_project_stylesheet", {
        "css": {"button": {"color": "red"}},
    })
    assert "whole stylesheet" in _error(payload)


# --- Reviewing on demand ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_review_answers_for_the_whole_folder_without_writing(
    handler, mock_engine, mock_agent, project_dir,
):
    """The peer of review_ui, and for the same reason: findings ride back on the
    write that caused them, which is no use for a control written thirty calls
    ago or in a session that has since been compacted."""
    (project_dir / "ui" / "room_map" / "index.html").write_text(
        '<script src="https://cdn.example.com/x.js"></script>', encoding="utf-8",
    )
    result = _result(await _run(handler, mock_engine, mock_agent, "review_custom_ui"))

    assert result["status"] == "findings"
    assert any("no internet" in w for w in result["files"][ENTRY])
    assert result["finding_count"] >= 1
    # Read-only: the file is untouched.
    assert "cdn.example.com" in (project_dir / "ui" / "room_map" / "index.html").read_text()


@pytest.mark.asyncio
async def test_a_clean_project_reviews_clean(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "review_custom_ui"))
    assert result["status"] == "clean"


@pytest.mark.asyncio
async def test_the_review_can_be_scoped_to_one_control(
    handler, mock_engine, mock_agent, project_dir,
):
    (project_dir / "ui" / "other.js").write_text("localStorage.getItem('x')", encoding="utf-8")
    result = _result(await _run(
        handler, mock_engine, mock_agent, "review_custom_ui", {"path": "room_map"},
    ))

    assert result["status"] == "clean"


# --- The grant, which the assistant may now set ----------------------------


@pytest.mark.asyncio
async def test_a_grant_change_is_echoed_rather_than_folded_into_updated(
    handler, mock_engine, mock_agent,
):
    """The grant is the whole reach model for markup somebody else wrote, and
    the one field nobody can see afterwards without reading the project back.

    The AI may set it (Aaron, 2026-08-14), which is what makes saying so out
    loud part of the deal rather than a nicety.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "room_map",
        "grant": {"devices": ["dsp1"], "variables": ["var.mode"], "navigate": True},
    }))

    assert result["status"] == "updated"
    assert result["grant"]["before"]["devices"] == ["dsp1"]
    assert result["grant"]["before"]["variables"] == []
    assert result["grant"]["after"]["variables"] == ["var.mode"]
    assert result["grant"]["after"]["navigate"] is True


@pytest.mark.asyncio
async def test_an_update_that_says_nothing_about_the_grant_echoes_nothing(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "room_map", "label": "Room map",
    }))
    assert "grant" not in result


@pytest.mark.asyncio
async def test_a_grant_set_on_create_is_echoed_too(handler, mock_engine, mock_agent):
    """A grant arrives on a create as often as on an update, and it is no more
    visible for having arrived early."""
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "second_map", "type": "custom", "custom_file": ENTRY,
            "grant": {"devices": ["dsp1"]},
            "placement": {"x": 50, "y": 0, "w": 40, "h": 40},
        }],
    }))

    assert result["grants"]["second_map"]["devices"] == ["dsp1"]


@pytest.mark.asyncio
async def test_a_page_handed_to_markup_echoes_its_grant_and_is_reviewed(
    handler, mock_engine, mock_agent, project_dir,
):
    """A page write that touches render_mode, custom_file or grant is reviewed
    even though no box moved -- those three are exactly the write whose mistake
    is invisible.

    The finding it must produce is the over-grant, because that is the mistake
    this call made: the file was already there and already fine, and the grant
    is what just arrived.
    """
    (project_dir / "ui" / "room_map" / "index.html").write_text(
        GOOD_CONTROL.replace("device.dsp1.level", "var.mode"), encoding="utf-8",
    )
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_page", {
        "page_id": "main",
        "render_mode": "custom",
        "custom_file": ENTRY,
        "grant": {"devices": ["dsp1"]},
    }))

    assert result["grant"]["before"] is None
    assert result["grant"]["after"]["devices"] == ["dsp1"]
    assert any("'dsp1'" in w and "page 'main'" in w for w in result["warnings"])


# --- The posture the whole design hangs on ---------------------------------


def test_the_read_tools_are_declared_read_only():
    """They take no lock and fire no backup, which is what keeps a listing cheap
    enough to call whenever it is useful."""
    for name in ("list_ui_files", "read_ui_file", "get_project_stylesheet"):
        assert name in AIToolHandler._READ_ONLY_TOOLS


def test_the_write_tools_are_serialized_like_every_other_write():
    """Anything outside _CONCURRENT_SAFE_TOOLS takes the project lock and rides
    the one-time pre-AI backup -- which already collects ui/ recursively, so
    the undo story for a bad control is the one that already existed."""
    for name in ("write_ui_file", "delete_ui_file", "set_project_stylesheet"):
        assert name not in AIToolHandler._CONCURRENT_SAFE_TOOLS
