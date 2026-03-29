"""Tests for Asset API endpoints."""

import io
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.core.engine import Engine
from server.main import app
from server.api import rest, assets as assets_api


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

    from server.core.project_loader import load_project
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
