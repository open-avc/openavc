"""The project's ``ui/`` tree over HTTP — custom controls the integrator writes.

**Three front doors, one folder.** The IDE's editor writes a file (PUT), a
drag-and-drop lands one (POST), and a ``.zip`` unpacks into it (POST with a
zip). They are the same storage, so a control authored here and a control
dropped in are indistinguishable afterwards. What may live there — path rules,
file types, size caps — is ``core/custom_ui.py``, shared with the project
import and backup-restore doors that write into the same folder.

**The tree is world-readable.** The serving route at the bottom is on the open
router because it must be: it is what an iframe's ``src`` fetches, and a wall
panel holds no credential — exactly like a plugin's ``panel/`` directory.
Nothing secret goes in ``ui/``, and the user docs say so in as many words.

**Writes require a claimed instance.** A file here is code that runs in every
panel's browser, so the same door the script and Python-driver editors use
applies: set an admin password first. It runs sandboxed and out of process
rather than in-process like a script, but it is still code an anonymous
visitor on the LAN should not be able to leave on somebody's wall panel.
"""

import io
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from openavc.api._engine import _get_engine
from openavc.api.auth import require_claimed_auth
from openavc.api.static_files import serve_static_file
from openavc.core.custom_ui import (
    MAX_FILE_SIZE,
    MAX_FILES,
    MAX_TOTAL_SIZE,
    UI_DIR_NAME,
    CustomUIPathError,
    iter_files,
    normalize_relpath,
    resolve_within,
    tree_totals,
)
from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

log = get_logger(__name__)

router = APIRouter()
open_router = APIRouter()


def _ui_dir() -> Path:
    """The active project's ``ui/`` directory (created on demand)."""
    engine = _get_engine()
    ui_dir = Path(engine.project_path).parent / UI_DIR_NAME
    ui_dir.mkdir(parents=True, exist_ok=True)
    return ui_dir


def _ui_dir_for(project_id: str) -> Path:
    """Resolve the ``ui/`` directory a ``project_id`` URL segment addresses.

    ``default`` (or empty) is the active project — how the panel and the
    Builder preview address it. Any other id names a saved library project and
    resolves to that project's tree, so a library project's control serves its
    own bytes rather than the active project's. Mirrors ``_assets_dir_for``.
    """
    if project_id in ("", "default"):
        return _ui_dir()
    from openavc.core.project_library import _lib_dir, sanitize_id
    return _lib_dir() / sanitize_id(project_id) / UI_DIR_NAME


def _require_active_project(project_id: str) -> None:
    """Writes only ever touch the active project, like assets.

    A mis-addressed write must fail loudly rather than land on the project the
    caller did not name.
    """
    if project_id not in ("", "default"):
        raise HTTPException(
            status_code=404,
            detail="Custom UI files can only be modified on the active project.",
        )


def _validated(raw: str, *, require_extension: bool = True) -> str:
    """``normalize_relpath`` with its refusal turned into a 400."""
    try:
        return normalize_relpath(raw, require_extension=require_extension)
    except CustomUIPathError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _target(ui_dir: Path, relpath: str) -> Path:
    """``resolve_within`` with its refusal turned into a 400."""
    try:
        return resolve_within(ui_dir, relpath)
    except CustomUIPathError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _check_file_size(size: int, name: str) -> None:
    if size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"'{name}' is larger than the {MAX_FILE_SIZE // (1024 * 1024)} MB "
                "limit for a custom UI file."
            ),
        )


def _check_room_for(
    ui_dir: Path, incoming: int, new_files: int, *, replacing: Path | None = None
) -> None:
    """Refuse a write that would push the tree past its caps."""
    total, count = tree_totals(ui_dir, excluding=replacing)
    if total + incoming > MAX_TOTAL_SIZE:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Custom UI files would go over the {MAX_TOTAL_SIZE // (1024 * 1024)} MB "
                "limit for this project. Large media belongs in Assets."
            ),
        )
    if count + new_files > MAX_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"A project can hold at most {MAX_FILES} custom UI files.",
        )


async def _announce_ui_files_changed(path: str) -> None:
    """Tell every panel a file moved under it.

    A custom control is a file, not project data, so writing one changes
    nothing the project push carries -- and a panel only re-fetches a control
    when it re-renders. The serving route is ``no-cache``, so a fetch does
    revalidate, but nothing was asking for one: a wall panel kept drawing the
    old control until somebody navigated away and back, or reloaded it. That
    was true of every door into this folder, which is why it is announced here
    rather than at the one that happened to surface it.

    The version rides onto each frame's URL the same way the Builder's own
    counter does in the design canvas. Milliseconds rather than a counter so it
    still moves forward across a restart, when a browser may be holding a
    cached copy stamped with the count from last time.
    """
    engine = _get_engine()
    await engine.broadcast_ws({
        "type": "ui.files",
        "version": str(time.time_ns() // 1_000_000),
        "path": path,
    })


# ──── Listing and editing ────


@router.get("/projects/{project_id}/ui")
async def list_ui_files(project_id: str) -> dict[str, Any]:
    """List the project's custom UI files."""
    ui_dir = _ui_dir_for(project_id)
    files = [
        {
            "path": f.relative_to(ui_dir).as_posix(),
            "size": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in iter_files(ui_dir)
    ]
    total, _ = tree_totals(ui_dir)
    return {
        "files": files,
        "total_size": total,
        "max_total_size": MAX_TOTAL_SIZE,
        "max_file_size": MAX_FILE_SIZE,
    }


@router.put(
    "/projects/{project_id}/ui/{file_path:path}",
    dependencies=[Depends(require_claimed_auth)],
)
async def write_ui_file(project_id: str, file_path: str, request: Request) -> dict[str, Any]:
    """Create or overwrite a text file in ``ui/`` (the IDE editor's door)."""
    _require_active_project(project_id)
    rel = _validated(file_path)
    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="Content must be text.")
    encoded = content.encode("utf-8")
    _check_file_size(len(encoded), PurePosixPath(rel).name)

    ui_dir = _ui_dir()
    target = _target(ui_dir, rel)
    existing = target if target.is_file() else None
    _check_room_for(ui_dir, len(encoded), 0 if existing else 1, replacing=existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, content)
    log.info("Custom UI file saved: %s", rel)
    await _announce_ui_files_changed(rel)
    return {"status": "saved", "path": rel, "size": len(encoded)}


@router.delete(
    "/projects/{project_id}/ui/{file_path:path}",
    dependencies=[Depends(require_claimed_auth)],
)
async def delete_ui_file(project_id: str, file_path: str) -> dict[str, str]:
    """Delete a custom UI file, or a whole control folder."""
    _require_active_project(project_id)
    rel = _validated(file_path, require_extension=False)
    target = _target(_ui_dir(), rel)
    if target.is_dir():
        shutil.rmtree(target)
    elif target.is_file():
        target.unlink()
    else:
        raise HTTPException(status_code=404, detail="File not found")
    log.info("Custom UI file deleted: %s", rel)
    await _announce_ui_files_changed(rel)
    return {"status": "deleted", "path": rel}


# ──── Upload: a dropped file, a dropped folder, or a .zip ────


@router.post(
    "/projects/{project_id}/ui",
    dependencies=[Depends(require_claimed_auth)],
)
async def upload_ui_file(
    project_id: str,
    file: UploadFile = File(...),
    path: str = Form(""),
) -> dict[str, Any]:
    """Drop a file into ``ui/``, or a ``.zip`` to unpack into it.

    ``path`` is always the destination **folder**, empty for the tree root;
    the file keeps its own name. A browser dropping a folder reports each
    entry's ``webkitRelativePath``, so the caller sends the folder half here
    and the structure survives. A ``.zip`` unpacks under ``path``, keeping its
    own folders — the same shape a plugin install has, minus the catalog.
    """
    _require_active_project(project_id)
    content = await file.read()
    ui_dir = _ui_dir()
    folder = path.strip().strip("/")

    if (file.filename or "").lower().endswith(".zip"):
        unpacked = _unpack_zip(ui_dir, content, folder)
        await _announce_ui_files_changed(folder or ".")
        return unpacked

    # Only the basename: a browser may report "room_map/index.html" as the
    # filename, and the folder half is `path`'s job.
    name = PurePosixPath((file.filename or "").replace("\\", "/")).name
    rel = _validated(f"{folder}/{name}" if folder else name)
    _check_file_size(len(content), PurePosixPath(rel).name)

    target = _target(ui_dir, rel)
    existing = target if target.is_file() else None
    _check_room_for(ui_dir, len(content), 0 if existing else 1, replacing=existing)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    log.info("Custom UI file uploaded: %s", rel)
    await _announce_ui_files_changed(rel)
    return {"status": "saved", "written": [rel], "skipped": []}


def _unpack_zip(ui_dir: Path, content: bytes, folder: str) -> dict[str, Any]:
    """Extract an archive into ``ui/``, keeping its folders.

    A control archive is its own files at the root (``index.html`` beside its
    CSS), not a ``ui/`` tree, so this is the one door that does not go through
    ``extract_from_zip`` — it re-roots every member under the destination
    folder first. Members that are not custom-UI file types are skipped and
    reported rather than failing the whole import, for the same reason
    ``extract_from_zip`` skips them: design-tool archives carry ``__MACOSX``
    noise nobody asked about.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="That file is not a readable .zip archive.")

    written: list[str] = []
    skipped: list[str] = []
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise HTTPException(
                status_code=413,
                detail=f"That archive holds more than {MAX_FILES} files.",
            )
        declared = sum(i.file_size for i in infos)
        if declared > MAX_TOTAL_SIZE:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"That archive unpacks to more than "
                    f"{MAX_TOTAL_SIZE // (1024 * 1024)} MB."
                ),
            )
        _check_room_for(ui_dir, declared, len(infos))

        # The declared sizes above come from the archive's own directory, which
        # a hostile archive can understate — so the real bytes are counted as
        # they come out and a member that overruns is skipped rather than
        # written half-way.
        unpacked = 0
        for info in infos:
            member = f"{folder}/{info.filename}" if folder else info.filename
            try:
                rel = normalize_relpath(member)
                target = resolve_within(ui_dir, rel)
            except CustomUIPathError:
                skipped.append(info.filename)
                continue
            with zf.open(info) as src:
                data = src.read(MAX_FILE_SIZE + 1)
            if len(data) > MAX_FILE_SIZE:
                skipped.append(info.filename)
                continue
            unpacked += len(data)
            if unpacked > MAX_TOTAL_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"That archive unpacks to more than "
                        f"{MAX_TOTAL_SIZE // (1024 * 1024)} MB."
                    ),
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(rel)

    log.info("Custom UI archive unpacked: %d file(s), %d skipped", len(written), len(skipped))
    return {"status": "saved", "written": written, "skipped": skipped}


# ──── Serving (open router — a wall panel has no credential) ────


@open_router.get("/projects/{project_id}/ui/{file_path:path}")
async def serve_ui_file(project_id: str, file_path: str):
    """Serve a custom UI file to the panel.

    Same guards as a plugin's ``panel/`` directory, and the same reason for
    being open: this is what an iframe's ``src`` fetches, and a browser
    resource load carries no credential. ``no_cache`` so an author's save
    shows up on the next render instead of a stale copy.
    """
    return serve_static_file(_ui_dir_for(project_id), file_path, no_cache=True)
