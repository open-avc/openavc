"""The project's ui/ tree: what may be written into it, and that it travels.

Two halves, and the second is the one that would fail silently. The rules
(``core/custom_ui.py``) decide what a caller may write; the travel paths decide
whether a control the integrator wrote is still there after they export the
project, import it on the bench machine, or restore last week's backup. A
project that loses its custom controls on export doesn't error -- it opens with
empty boxes on somebody else's machine, which is why every one of those paths
gets a test rather than a promise.
"""

import io
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api.static_files import STATIC_CONTENT_TYPES
from openavc.core.custom_ui import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_SIZE,
    CustomUIPathError,
    extract_from_zip,
    normalize_relpath,
    resolve_within,
    tree_totals,
    zip_entries,
)

# Windows needs Developer Mode or admin rights to create one, so the symlink
# guards are checked where a symlink can actually exist.
_CAN_SYMLINK = os.name != "nt"


# --- The rules ---------------------------------------------------------------


def test_every_writable_type_is_a_type_we_can_serve():
    """A file the browser would only ever get as octet-stream is not a control.

    The two tables are deliberately separate (writing is core, serving is api),
    so this is what keeps them honest in the direction that matters: anything
    writable must have a real content type on the way back out.
    """
    assert ALLOWED_EXTENSIONS <= set(STATIC_CONTENT_TYPES)


def test_server_side_code_is_not_a_custom_ui_file():
    """``ui/`` is browser code. The platform's own code has its own editors."""
    for ext in (".py", ".sh", ".exe", ".avcdriver"):
        assert ext not in ALLOWED_EXTENSIONS


@pytest.mark.parametrize("raw", [
    "index.html",
    "room_map/index.html",
    "Room Map/styles/main.css",
    "a/b/c/d/e/f/g/deep.js",
])
def test_normalize_accepts_ordinary_paths(raw):
    assert normalize_relpath(raw) == raw


def test_normalize_strips_leading_slash_and_dot_segments():
    assert normalize_relpath("/room_map/./index.html") == "room_map/index.html"


@pytest.mark.parametrize("raw,why", [
    ("../../etc/passwd", "traversal"),
    ("room_map/../../secret.html", "traversal mid-path"),
    ("..\\..\\windows.html", "backslash traversal"),
    (".hidden/index.html", "dotfile folder"),
    (".DS_Store", "dotfile"),
    ("", "empty"),
    ("   ", "blank"),
    ("a/b/c/d/e/f/g/h/i/too_deep.html", "too deep"),
    ("room_map/index.py", "not a browser file type"),
    ("room_map/index", "no extension"),
    ("side;drop/index.html", "punctuation we don't take"),
])
def test_normalize_refuses(raw, why):
    with pytest.raises(CustomUIPathError):
        normalize_relpath(raw)


def test_normalize_can_skip_the_extension_rule_for_a_folder():
    """Deleting a whole control names a folder, which has no extension."""
    assert normalize_relpath("room_map", require_extension=False) == "room_map"


def test_resolve_within_refuses_an_escape(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    with pytest.raises(CustomUIPathError):
        resolve_within(ui, "../outside.html")


@pytest.mark.skipif(not _CAN_SYMLINK, reason="symlinks need privilege on Windows")
def test_resolve_within_refuses_a_symlink(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    secret = tmp_path / "secret.html"
    secret.write_text("<p>not yours</p>", encoding="utf-8")
    (ui / "link.html").symlink_to(secret)
    with pytest.raises(CustomUIPathError):
        resolve_within(ui, "link.html")


def _zip_of(members: dict[str, bytes]) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_extract_keeps_folders_and_skips_what_it_should(tmp_path):
    zf = _zip_of({
        "ui/room_map/index.html": b"<h1>map</h1>",
        "ui/room_map/style.css": b"body{}",
        "ui/__MACOSX/._index.html": b"junk",
        "ui/room_map/.DS_Store": b"junk",
        "ui/../escape.html": b"nope",
        "ui/room_map/backdoor.py": b"import os",
        "scripts/other.py": b"# not ours",
    })
    with zf:
        written = extract_from_zip(zf, tmp_path / "ui")

    assert sorted(written) == ["room_map/index.html", "room_map/style.css"]
    assert (tmp_path / "ui" / "room_map" / "index.html").read_bytes() == b"<h1>map</h1>"
    assert not (tmp_path / "escape.html").exists()
    assert not (tmp_path / "ui" / "room_map" / "backdoor.py").exists()


def test_extract_skips_a_member_that_overruns_the_file_cap(tmp_path):
    zf = _zip_of({
        "ui/big/index.html": b"x" * (MAX_FILE_SIZE + 10),
        "ui/big/style.css": b"body{}",
    })
    with zf:
        written = extract_from_zip(zf, tmp_path / "ui")
    assert written == ["big/style.css"]


def test_zip_entries_round_trip(tmp_path):
    ui = tmp_path / "ui"
    (ui / "room_map").mkdir(parents=True)
    (ui / "room_map" / "index.html").write_text("<h1>map</h1>", encoding="utf-8")
    entries = zip_entries(ui)
    assert entries == [("ui/room_map/index.html", ui / "room_map" / "index.html")]
    total, count = tree_totals(ui)
    assert count == 1 and total == len("<h1>map</h1>")


# --- The API -----------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    (d / "project.avc").write_text(
        json.dumps({"project": {"id": "test", "name": "Test"}}), encoding="utf-8"
    )
    return d


@pytest.fixture
def client(project_dir):
    """The ui-file routes on their own app, with the claimed gate neutralized.

    The gate itself is generic and covered in test_auth_posture.py; what this
    file checks is that these routes declare it (below) and what they do once
    past it.
    """
    from openavc.api.auth import require_claimed_auth
    from openavc.api.routes import ui_files

    engine = MagicMock()
    engine.project_path = project_dir / "project.avc"

    app = FastAPI()
    app.include_router(ui_files.open_router, prefix="/api")
    app.include_router(ui_files.router, prefix="/api")
    app.dependency_overrides[require_claimed_auth] = lambda: None

    import openavc.api._engine as engine_mod
    engine_mod.set_engine(engine)
    yield TestClient(app)
    engine_mod.set_engine(None)


def test_write_read_and_list(client, project_dir):
    resp = client.put(
        "/api/projects/default/ui/room_map/index.html",
        json={"content": "<h1>Room</h1>"},
    )
    assert resp.status_code == 200, resp.text
    assert (project_dir / "ui" / "room_map" / "index.html").read_text() == "<h1>Room</h1>"

    served = client.get("/api/projects/default/ui/room_map/index.html")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/html")
    assert served.text == "<h1>Room</h1>"

    listed = client.get("/api/projects/default/ui").json()
    assert [f["path"] for f in listed["files"]] == ["room_map/index.html"]
    assert listed["total_size"] == len("<h1>Room</h1>")


def test_serving_an_unknown_type_never_invites_the_browser_to_run_it(client, project_dir):
    weird = project_dir / "ui" / "room_map"
    weird.mkdir(parents=True)
    (weird / "notes.zzz").write_bytes(b"data")
    resp = client.get("/api/projects/default/ui/room_map/notes.zzz")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/octet-stream")


def test_a_missing_page_does_not_draw_the_api_at_the_room(client):
    """Rename a control's file and the frame must not render the API's JSON.

    The panel draws its own message over the frame, but the frame still has a
    body, and a wall panel showing ``{"detail":"File not found"}`` is API
    plumbing pointed at whoever is in the room.
    """
    resp = client.get("/api/projects/default/ui/room_map/index.html")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("text/html")
    assert "detail" not in resp.text
    assert "not in the project" in resp.text
    # A restored file has to show up on the next render, so the miss is never
    # the thing that got cached.
    assert "no-store" in resp.headers.get("cache-control", "")


def test_a_missing_file_that_is_not_a_page_still_answers_json(client):
    """Only the document case changes. A missing stylesheet is an API answer:
    nothing renders it, and a fetch()ing control wants the error shape."""
    resp = client.get("/api/projects/default/ui/room_map/style.css")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "File not found"


@pytest.mark.skipif(not _CAN_SYMLINK, reason="symlinks need privilege on Windows")
def test_a_refused_page_is_a_document_too(client, project_dir, tmp_path):
    """The symlink refusal lands in the same iframe as the 404 does."""
    ui = project_dir / "ui"
    ui.mkdir(parents=True, exist_ok=True)
    secret = tmp_path / "secret.html"
    secret.write_text("<p>not yours</p>", encoding="utf-8")
    (ui / "link.html").symlink_to(secret)

    resp = client.get("/api/projects/default/ui/link.html")
    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith("text/html")
    assert "not yours" not in resp.text
    assert "detail" not in resp.text


def test_saves_show_up_rather_than_serving_a_cached_copy(client, project_dir):
    client.put("/api/projects/default/ui/a.html", json={"content": "one"})
    resp = client.get("/api/projects/default/ui/a.html")
    assert "no-cache" in resp.headers.get("cache-control", "")


@pytest.mark.parametrize("path", [
    "room_map/index.py",
    "..%2F..%2Fescape.html",
    ".hidden.html",
])
def test_write_refuses_a_path_it_should(client, path):
    resp = client.put(f"/api/projects/default/ui/{path}", json={"content": "x"})
    assert resp.status_code == 400


def test_write_refuses_an_oversize_file(client):
    resp = client.put(
        "/api/projects/default/ui/big.html",
        json={"content": "x" * (MAX_FILE_SIZE + 1)},
    )
    assert resp.status_code == 413


def test_writes_only_touch_the_active_project(client):
    resp = client.put(
        "/api/projects/some_saved_project/ui/index.html", json={"content": "x"}
    )
    assert resp.status_code == 404


def test_upload_a_dropped_file_into_a_folder(client, project_dir):
    resp = client.post(
        "/api/projects/default/ui",
        files={"file": ("index.html", b"<h1>hi</h1>", "text/html")},
        data={"path": "room_map"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["written"] == ["room_map/index.html"]
    assert (project_dir / "ui" / "room_map" / "index.html").exists()


def test_upload_a_zip_unpacks_it(client, project_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<h1>map</h1>")
        zf.writestr("css/style.css", "body{}")
        zf.writestr("__MACOSX/._index.html", "junk")
        zf.writestr("../escape.html", "nope")

    resp = client.post(
        "/api/projects/default/ui",
        files={"file": ("control.zip", buf.getvalue(), "application/zip")},
        data={"path": "room_map"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(body["written"]) == ["room_map/css/style.css", "room_map/index.html"]
    assert body["skipped"]
    assert not (project_dir / "escape.html").exists()


def test_delete_removes_a_whole_control(client, project_dir):
    client.put("/api/projects/default/ui/room_map/index.html", json={"content": "a"})
    client.put("/api/projects/default/ui/room_map/style.css", json={"content": "b"})
    resp = client.delete("/api/projects/default/ui/room_map")
    assert resp.status_code == 200
    assert not (project_dir / "ui" / "room_map").exists()


def test_write_routes_require_a_claimed_instance():
    """Custom UI is code somebody's panel runs. The gate must be declared.

    Checked on the route table rather than by driving the gate, because the
    failure this guards against is a route added later without it -- which
    behaves perfectly until the day an anonymous visitor writes a control.
    """
    from openavc.api.auth import require_claimed_auth
    from openavc.api.routes import ui_files

    for route in ui_files.router.routes:
        if route.methods & {"PUT", "POST", "DELETE"}:
            deps = [d.call for d in route.dependant.dependencies]
            assert require_claimed_auth in deps, f"{route.methods} {route.path}"


# --- Travel: the tree has to survive every path a project takes ---------------


@pytest.fixture
def tmp_lib(tmp_path):
    import openavc.core.project_library as plib

    lib_dir = tmp_path / "saved_projects"
    lib_dir.mkdir()
    with patch.object(plib, "config") as mock_config:
        mock_config.SAVED_PROJECTS_DIR = lib_dir
        yield lib_dir


def _write_control(ui_dir: Path, name: str = "room_map") -> None:
    (ui_dir / name).mkdir(parents=True, exist_ok=True)
    (ui_dir / name / "index.html").write_text("<h1>map</h1>", encoding="utf-8")
    (ui_dir / name / "style.css").write_text("body{}", encoding="utf-8")


def _blank_project(pid: str = "test_room"):
    from openavc.core.project_library import create_blank_project
    return create_blank_project(pid, "Test Room")


def test_save_duplicate_and_open_carry_the_controls(tmp_lib, tmp_path):
    from openavc.core.project_library import (
        duplicate_project,
        open_from_library,
        save_to_library,
    )

    active = tmp_path / "active"
    (active / "scripts").mkdir(parents=True)
    _write_control(active / "ui")

    save_to_library(
        "saved", _blank_project("saved"), active / "scripts", "Saved", "",
        ui_dir=active / "ui",
    )
    assert (tmp_lib / "saved" / "ui" / "room_map" / "index.html").exists()

    duplicate_project("saved", "copy", "Copy")
    assert (tmp_lib / "copy" / "ui" / "room_map" / "style.css").exists()

    # Opening replaces the active tree rather than merging into it: a control
    # from the project being closed must not linger in the one being opened.
    other = tmp_path / "other"
    (other / "scripts").mkdir(parents=True)
    _write_control(other / "ui", name="old_control")
    open_from_library("saved", other / "project.avc", other / "scripts", "x", "X")
    assert (other / "ui" / "room_map" / "index.html").exists()
    assert not (other / "ui" / "old_control").exists()


def test_export_then_import_keeps_the_control(tmp_lib, tmp_path):
    from openavc.core.project_library import (
        export_project,
        import_project,
        save_to_library,
    )

    active = tmp_path / "active"
    (active / "scripts").mkdir(parents=True)
    _write_control(active / "ui")
    save_to_library(
        "exported", _blank_project("exported"), active / "scripts", "Exported", "",
        ui_dir=active / "ui",
    )

    content, filename, _ = export_project("exported")
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        assert "ui/room_map/index.html" in zf.namelist()

    import_project(content, filename, override_id="reimported")
    assert (tmp_lib / "reimported" / "ui" / "room_map" / "index.html").read_text() == "<h1>map</h1>"


def test_the_current_project_exports_as_a_whole_room(tmp_lib, tmp_path):
    """Program > Export has to carry what Project Library > Export carries.

    It used to write the store to a Blob client-side, so the button an
    integrator reaches for to move a room to another machine was the one that
    shipped the settings and left every custom control behind.
    """
    from openavc.core.project_library import export_active_project, import_project

    active = tmp_path / "active"
    (active / "scripts").mkdir(parents=True)
    (active / "scripts" / "startup.py").write_text("x = 1", encoding="utf-8")
    (active / "assets").mkdir()
    (active / "assets" / "logo.png").write_bytes(b"PNG")
    _write_control(active / "ui")
    (active / "project.avc").write_text(
        json.dumps(_blank_project("live").model_dump(mode="json")), encoding="utf-8"
    )

    content, filename, content_type = export_active_project(active / "project.avc")
    assert (filename, content_type) == ("live.zip", "application/zip")
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
    assert "project.avc" in names
    assert "ui/room_map/index.html" in names
    assert "scripts/startup.py" in names
    assert "assets/logo.png" in names

    # And the bundle it produces goes back in through the import door.
    import_project(content, filename, override_id="moved")
    assert (tmp_lib / "moved" / "ui" / "room_map" / "index.html").read_text() == "<h1>map</h1>"


def test_backup_carries_the_controls_and_restore_replaces_them(tmp_path):
    from openavc.core.backup_manager import create_backup, restore_from_backup

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    project_file = project_dir / "project.avc"
    project_file.write_text(
        json.dumps({"project": {"id": "p", "name": "P"}}), encoding="utf-8"
    )
    _write_control(project_dir / "ui")

    backup = create_backup(project_dir, reason="test")
    with zipfile.ZipFile(backup) as zf:
        assert "ui/room_map/index.html" in zf.namelist()

    # A control added after the backup must be gone once it is restored --
    # every other tree is swapped whole, and one that merely merged would leave
    # last month's controls beside a freshly restored project.
    _write_control(project_dir / "ui", name="added_later")
    (project_dir / "ui" / "room_map" / "index.html").write_text("edited", encoding="utf-8")

    restore_from_backup(backup, project_dir)
    assert (project_dir / "ui" / "room_map" / "index.html").read_text() == "<h1>map</h1>"
    assert not (project_dir / "ui" / "added_later").exists()
