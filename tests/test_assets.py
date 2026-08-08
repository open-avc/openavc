"""Tests for Asset API endpoints."""

import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openavc.core.engine import Engine
from openavc.main import app
from openavc.api import rest, assets as assets_api


TEST_PROJECT = {
    "project": {"id": "asset_test", "name": "Asset Test Room"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": []},
}


@pytest.fixture
async def client():
    """Start engine with a test project, yield TestClient."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False
    ) as f:
        json.dump(TEST_PROJECT, f)
        tmp_path = f.name

    engine = Engine(tmp_path)

    from openavc.core.project_loader import load_project
    engine.project = load_project(tmp_path)
    engine._running = True

    # Create assets directory
    project_dir = Path(tmp_path).parent
    assets_dir = project_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    rest.set_engine(engine)
    assets_api.set_engine(engine)

    yield TestClient(app)

    rest.set_engine(None)
    assets_api.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


def _make_png(size: int = 100) -> bytes:
    """Create a minimal valid PNG file of the specified byte count."""
    # Minimal 1x1 PNG
    import struct
    header = b'\x89PNG\r\n\x1a\n'
    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = 0  # Not a valid CRC but enough for upload testing
    ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
    # IEND chunk
    iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', 0)
    base = header + ihdr + iend
    if len(base) < size:
        base += b'\x00' * (size - len(base))
    return base[:max(size, len(base))]


async def test_list_assets_empty(client):
    resp = client.get("/api/projects/default/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert "assets" in data
    assert isinstance(data["assets"], list)


async def test_upload_asset(client):
    png_data = _make_png()
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("test-image.png", io.BytesIO(png_data), "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "name" in data or "filename" in data


async def test_upload_invalid_extension(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("malware.exe", io.BytesIO(b"evil"), "application/octet-stream")},
    )
    assert resp.status_code == 400


async def test_upload_svg_with_script(client):
    evil_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert("xss")</script></svg>'
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("evil.svg", io.BytesIO(evil_svg), "image/svg+xml")},
    )
    # Should either reject (400) or sanitize
    if resp.status_code == 200:
        # If accepted, verify the script tag was stripped
        serve_resp = client.get("/api/projects/default/assets/evil.svg")
        assert b"<script>" not in serve_resp.content


async def test_upload_safe_svg(client):
    safe_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><circle cx="50" cy="50" r="40"/></svg>'
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("safe.svg", io.BytesIO(safe_svg), "image/svg+xml")},
    )
    assert resp.status_code == 200


async def test_serve_asset(client):
    # Upload first
    png_data = _make_png()
    client.post(
        "/api/projects/default/assets",
        files={"file": ("serve-test.png", io.BytesIO(png_data), "image/png")},
    )

    # Serve
    resp = client.get("/api/projects/default/assets/serve-test.png")
    assert resp.status_code == 200


async def test_serve_nonexistent_asset(client):
    resp = client.get("/api/projects/default/assets/does-not-exist.png")
    assert resp.status_code == 404


async def test_delete_asset(client):
    # Upload
    png_data = _make_png()
    client.post(
        "/api/projects/default/assets",
        files={"file": ("delete-me.png", io.BytesIO(png_data), "image/png")},
    )

    # Delete
    resp = client.delete("/api/projects/default/assets/delete-me.png")
    assert resp.status_code == 200

    # Verify gone
    resp = client.get("/api/projects/default/assets/delete-me.png")
    assert resp.status_code == 404


async def test_filename_traversal(client):
    resp = client.get("/api/projects/default/assets/../../etc/passwd")
    assert resp.status_code in (400, 404, 422)


async def test_filename_validation(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("../../../etc/passwd", io.BytesIO(b"test"), "image/png")},
    )
    assert resp.status_code == 400


# --- Audio asset tests ---


def _make_audio(size: int = 1024) -> bytes:
    """Create a minimal payload that passes extension-based validation.
    Content isn't validated; size is what matters for the upload path."""
    return b"\x00" * size


async def test_upload_mp3(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("chime.mp3", io.BytesIO(_make_audio()), "audio/mpeg")},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "chime.mp3"
    assert data["reference"] == "assets://chime.mp3"
    assert data["type"] == "audio"


async def test_upload_wav(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("bell.wav", io.BytesIO(_make_audio()), "audio/wav")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["type"] == "audio"


async def test_upload_ogg(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("alert.ogg", io.BytesIO(_make_audio()), "audio/ogg")},
    )
    assert resp.status_code == 200, resp.text


async def test_upload_m4a(client):
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("notif.m4a", io.BytesIO(_make_audio()), "audio/mp4")},
    )
    assert resp.status_code == 200, resp.text


async def test_serve_audio(client):
    client.post(
        "/api/projects/default/assets",
        files={"file": ("playme.mp3", io.BytesIO(_make_audio(2048)), "audio/mpeg")},
    )
    resp = client.get("/api/projects/default/assets/playme.mp3")
    assert resp.status_code == 200
    assert len(resp.content) == 2048


async def test_list_includes_type_and_extension(client):
    client.post(
        "/api/projects/default/assets",
        files={"file": ("img.png", io.BytesIO(_make_png()), "image/png")},
    )
    client.post(
        "/api/projects/default/assets",
        files={"file": ("snd.mp3", io.BytesIO(_make_audio()), "audio/mpeg")},
    )
    resp = client.get("/api/projects/default/assets")
    assert resp.status_code == 200
    by_name = {a["name"]: a for a in resp.json()["assets"]}
    assert by_name["img.png"]["type"] == "image"
    assert by_name["img.png"]["extension"] == "png"
    assert by_name["snd.mp3"]["type"] == "audio"
    assert by_name["snd.mp3"]["extension"] == "mp3"


async def test_audio_size_limit_higher_than_image(client):
    """A 60 MB file: rejected as image, accepted as audio."""
    big = _make_audio(60 * 1024 * 1024)
    # As image: should fail (50 MB image cap)
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("huge.png", io.BytesIO(big), "image/png")},
    )
    assert resp.status_code == 400
    # As audio: should succeed (200 MB audio cap)
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("long.mp3", io.BytesIO(big), "audio/mpeg")},
    )
    assert resp.status_code == 200, resp.text


async def test_audio_too_large_rejected(client):
    """File over 200 MB audio cap is rejected."""
    too_big = _make_audio(201 * 1024 * 1024)
    resp = client.post(
        "/api/projects/default/assets",
        files={"file": ("toobig.mp3", io.BytesIO(too_big), "audio/mpeg")},
    )
    assert resp.status_code == 400


async def test_delete_audio(client):
    client.post(
        "/api/projects/default/assets",
        files={"file": ("delme.mp3", io.BytesIO(_make_audio()), "audio/mpeg")},
    )
    resp = client.delete("/api/projects/default/assets/delme.mp3")
    assert resp.status_code == 200
    resp = client.get("/api/projects/default/assets/delme.mp3")
    assert resp.status_code == 404


# --- Per-project scoping of the {project_id} path param ---
#
# Reads (serve, list) honor {project_id}: `default` = active project, any other
# id = that saved library project. Writes (upload, delete) act only on the
# active project and reject any other id, so a mis-addressed request can never
# silently hit — or mutate — the wrong project.


def _seed_library_project(monkeypatch, tmp_path, project_id, files):
    """Create a saved library project with the given assets under a temp lib dir."""
    from openavc import config
    from openavc.core.project_library import sanitize_id

    lib_root = tmp_path / "saved_projects"
    monkeypatch.setattr(config, "SAVED_PROJECTS_DIR", lib_root)
    proj_assets = lib_root / sanitize_id(project_id) / "assets"
    proj_assets.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (proj_assets / name).write_bytes(data)


async def test_list_scoped_to_library_project(client, monkeypatch, tmp_path):
    """Listing a library project id returns ITS assets, not the active project's."""
    client.post(
        "/api/projects/default/assets",
        files={"file": ("active-only.png", io.BytesIO(_make_png()), "image/png")},
    )
    _seed_library_project(monkeypatch, tmp_path, "roomB", {"lib-only.png": _make_png()})

    active = {a["name"] for a in client.get("/api/projects/default/assets").json()["assets"]}
    assert "active-only.png" in active
    assert "lib-only.png" not in active

    lib = {a["name"] for a in client.get("/api/projects/roomB/assets").json()["assets"]}
    assert lib == {"lib-only.png"}


async def test_list_unknown_project_is_empty(client, monkeypatch, tmp_path):
    from openavc import config
    monkeypatch.setattr(config, "SAVED_PROJECTS_DIR", tmp_path / "saved_projects")
    resp = client.get("/api/projects/does-not-exist/assets")
    assert resp.status_code == 200
    assert resp.json() == {"assets": [], "total_size": 0}


async def test_serve_scoped_to_library_project(client, monkeypatch, tmp_path):
    _seed_library_project(monkeypatch, tmp_path, "roomB", {"pic.png": _make_png(256)})
    resp = client.get("/api/projects/roomB/assets/pic.png")
    assert resp.status_code == 200
    assert len(resp.content) == 256


async def test_upload_rejects_non_active_project(client):
    """A write addressed to a non-active project 404s instead of hitting active."""
    resp = client.post(
        "/api/projects/roomB/assets",
        files={"file": ("stray.png", io.BytesIO(_make_png()), "image/png")},
    )
    assert resp.status_code == 404
    active = {a["name"] for a in client.get("/api/projects/default/assets").json()["assets"]}
    assert "stray.png" not in active


async def test_delete_rejects_non_active_project(client):
    """A delete addressed to a non-active project 404s and leaves the active asset intact."""
    client.post(
        "/api/projects/default/assets",
        files={"file": ("keep.png", io.BytesIO(_make_png()), "image/png")},
    )
    resp = client.delete("/api/projects/roomB/assets/keep.png")
    assert resp.status_code == 404
    assert client.get("/api/projects/default/assets/keep.png").status_code == 200
