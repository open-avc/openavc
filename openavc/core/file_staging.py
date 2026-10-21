"""Where a command's files wait: an upload on its way to a driver, and a file
a command handed back on its way to the person who asked for it.

A ``file`` param (``drivers/spec.py``) is uploaded before its command runs,
in chunks, because a file can be far larger than one request should carry:
the cloud tunnel moves a request as a single message and closes its whole
data channel past 16 MiB, so a chunk is small through the tunnel and large
on a direct connection (:func:`chunk_size_for`). The door that will run the
command opens the upload (the device page or a Device Audit), naming the
command and param, so the command's ``accept`` and ``max_bytes``, the
platform ceiling and the disk's free space all refuse before the first byte.

**Nothing is kept.** Everything lives under ``{data_dir}/command_files/``,
which no backup takes (the updater's backup names its trees; a project
backup is the project folder). An upload is deleted when its command ends,
or :data:`UPLOAD_IDLE_SECONDS` after its last chunk if nothing used it. A
returned file is deleted once it has been downloaded in full, or
:data:`HELD_SECONDS` after its command. The folder is emptied when the
server starts and when it stops.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openavc.drivers.command_files import CommandFile, DeviceFile, describe_size
from openavc.drivers.param_labels import param_label
from openavc.drivers.spec import FILE_PARAM_MAX_BYTES
from openavc.utils.logger import get_logger

log = get_logger(__name__)

STAGING_DIRNAME = "command_files"

# Chunk sizes the server hands the uploader. Through the cloud tunnel a 2 MiB
# chunk is 2.7 MB once base64-encoded: far under the tunnel's 16 MiB message
# limit, and answered well inside the cloud's 30 s on a slow site link.
CHUNK_BYTES_DIRECT = 16 * 1024 * 1024
CHUNK_BYTES_TUNNEL = 2 * 1024 * 1024

# An upload nobody used, and a returned file nobody downloaded, are deleted
# after these.
UPLOAD_IDLE_SECONDS = 15 * 60
HELD_SECONDS = 10 * 60

# Free space left on the disk after an upload is accepted.
FREE_SPACE_MARGIN = 64 * 1024 * 1024

SWEEP_SECONDS = 60

_NAME_MAX = 255


def chunk_size_for(tunneled: bool) -> int:
    return CHUNK_BYTES_TUNNEL if tunneled else CHUNK_BYTES_DIRECT


class StagingError(Exception):
    """A refusal with a sentence for the person and the HTTP status it means."""

    def __init__(self, message: str, status: int = 400, **fields: Any) -> None:
        super().__init__(message)
        self.status = status
        self.fields = fields


@dataclass(eq=False)
class Upload:
    id: str
    owner: str
    command: str
    param: str
    name: str
    size: int
    path: Path
    received: int = 0
    touched: float = field(default_factory=time.monotonic)
    in_use: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The file a driver reads, once taken (closed before the delete).
    file: CommandFile | None = None

    @property
    def complete(self) -> bool:
        return self.received == self.size

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "size": self.size,
            "received": self.received, "complete": self.complete,
        }


@dataclass(eq=False)
class HeldFile:
    id: str
    owner: str
    name: str
    size: int
    content_type: str
    path: Path
    expires: float

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "size": self.size}


def safe_file_name(name: object) -> str:
    """The name a person gave the file, reduced to its last path part.

    It is shown back and handed to the driver; it never names anything on
    the server's disk (staged files are named by id).
    """
    text = str(name or "").replace("\\", "/").split("/")[-1].strip()
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    return text[:_NAME_MAX]


def file_rules_refusal(
    param_name: str, pdef: dict[str, Any], name: str, size: int,
) -> tuple[str, int] | None:
    """Why ``name`` / ``size`` will not do for this file param, and the HTTP
    status that means, or None."""
    label = param_label(param_name, pdef)
    if size <= 0:
        return f"{name} is empty. Choose the file again.", 400
    declared = pdef.get("max_bytes")
    limit = FILE_PARAM_MAX_BYTES
    if isinstance(declared, int) and not isinstance(declared, bool) and declared > 0:
        limit = min(declared, FILE_PARAM_MAX_BYTES)
    if size > limit:
        return (
            f"{name} is {describe_size(size)}. {label} takes a file up to "
            f"{describe_size(limit)}."
        ), 413
    accept = pdef.get("accept")
    if isinstance(accept, (list, tuple)) and accept:
        endings = [a for a in accept if isinstance(a, str)]
        if endings and not any(name.lower().endswith(a.lower()) for a in endings):
            listed = ", ".join(endings[:-1]) + (" or " if len(endings) > 1 else "") + endings[-1]
            return f"{label} takes a {listed} file.", 400
    return None


class FileStaging:
    """The uploads and returned files waiting under one folder."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._uploads: dict[str, Upload] = {}
        self._held: dict[str, HeldFile] = {}
        self._sweeper: asyncio.Task | None = None
        # Files a delete could not remove yet (still open somewhere), retried
        # on every sweep.
        self._stale: set[Path] = set()

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Empty the folder (anything there is from an earlier run) and start
        the sweep that expires what nobody used."""
        self._clear_disk()
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        task, self._sweeper = self._sweeper, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for upload in self._uploads.values():
            if upload.file is not None:
                upload.file.close()
        self._uploads.clear()
        self._held.clear()
        self._stale.clear()
        self._clear_disk()

    def _clear_disk(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root, ignore_errors=True)

    async def _sweep_loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            try:
                self.sweep()
            except Exception:  # a sweep fault must not end the sweeping
                log.exception("Command file sweep failed")

    def sweep(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for path in list(self._stale):
            if _unlink(path):
                self._stale.discard(path)
        for upload in list(self._uploads.values()):
            if not upload.in_use and now - upload.touched > UPLOAD_IDLE_SECONDS:
                self.discard(upload.id)
        for held in list(self._held.values()):
            if now > held.expires:
                self.release_held(held.id)

    # -- uploads --------------------------------------------------------------

    def begin(
        self,
        *,
        owner: str,
        command: str,
        param: str,
        pdef: dict[str, Any],
        name: object,
        size: int,
    ) -> Upload:
        """Open an upload for one file param of one command, or refuse."""
        clean = safe_file_name(name)
        if not clean:
            raise StagingError("The file has no name. Choose it again.")
        if isinstance(size, bool) or not isinstance(size, int):
            raise StagingError("The file's size is missing. Choose it again.")
        refusal = file_rules_refusal(param, pdef, clean, size)
        if refusal:
            raise StagingError(*refusal)
        self.sweep()
        self.root.mkdir(parents=True, exist_ok=True)
        waiting = sum(u.size - u.received for u in self._uploads.values())
        try:
            free = shutil.disk_usage(self.root).free
        except OSError:
            free = None
        if free is not None and size + waiting + FREE_SPACE_MARGIN > free:
            raise StagingError(
                f"The OpenAVC server doesn't have room for {clean} "
                f"({describe_size(size)}). Free some disk space and try again.",
                507,
            )
        upload_id = secrets.token_hex(12)
        path = self.root / f"{upload_id}.part"
        path.write_bytes(b"")
        upload = Upload(
            id=upload_id, owner=owner, command=command, param=param,
            name=clean, size=size, path=path,
        )
        self._uploads[upload_id] = upload
        return upload

    def get(self, upload_id: str) -> Upload:
        upload = self._uploads.get(upload_id)
        if upload is None:
            raise StagingError(
                "This upload is no longer waiting. Choose the file again.", 404,
            )
        return upload

    async def write(self, upload_id: str, offset: int, data: bytes) -> Upload:
        """Append one chunk at ``offset``, which must be where the last one
        ended (a retried chunk is answered with where to resume)."""
        upload = self.get(upload_id)
        async with upload.lock:
            if upload.in_use:
                raise StagingError("This file is already in use.", 409)
            if offset != upload.received:
                raise StagingError(
                    f"The upload expected byte {upload.received}, not {offset}.",
                    409, received=upload.received,
                )
            if upload.received + len(data) > upload.size:
                raise StagingError(
                    f"{upload.name} is larger than the {describe_size(upload.size)} "
                    "it was opened for. Choose the file again.",
                    413,
                )
            await asyncio.to_thread(_append, upload.path, data)
            upload.received += len(data)
            upload.touched = time.monotonic()
            return upload

    def take(self, upload_id: str, *, owner: str, command: str, param: str) -> CommandFile:
        """The finished upload as the file a driver reads, bound to the command
        it was opened for. It stays staged until :meth:`discard`."""
        upload = self.get(upload_id)
        if (upload.owner, upload.command, upload.param) != (owner, command, param):
            raise StagingError(
                f"{upload.name} was chosen for a different command. Choose it again.",
                409,
            )
        if not upload.complete:
            raise StagingError(
                f"{upload.name} has not finished uploading "
                f"({describe_size(upload.received)} of {describe_size(upload.size)}).",
                409,
            )
        if upload.in_use:
            raise StagingError(f"{upload.name} is already in use.", 409)
        upload.in_use = True
        upload.file = CommandFile(upload.name, upload.size, upload.path)
        return upload.file

    def untake(self, upload_id: str) -> None:
        """Give a taken upload back unused (its run never started)."""
        upload = self._uploads.get(upload_id)
        if upload is not None:
            upload.in_use = False
            upload.file = None
            upload.touched = time.monotonic()

    def discard(self, upload_id: str) -> None:
        upload = self._uploads.pop(upload_id, None)
        if upload is None:
            return
        if upload.file is not None:
            upload.file.close()
        if not _unlink(upload.path):
            self._stale.add(upload.path)

    def discard_all(self, upload_ids: Iterable[str]) -> None:
        for upload_id in upload_ids:
            self.discard(upload_id)

    # -- files handed back ----------------------------------------------------

    def hold(self, owner: str, device_file: DeviceFile) -> HeldFile:
        """Keep a returned file until it is downloaded or expires."""
        self.sweep()
        self.root.mkdir(parents=True, exist_ok=True)
        held_id = secrets.token_hex(12)
        path = self.root / f"{held_id}.out"
        path.write_bytes(device_file.data)
        held = HeldFile(
            id=held_id, owner=owner,
            name=safe_file_name(device_file.name) or "download",
            size=device_file.size, content_type=device_file.content_type,
            path=path, expires=time.monotonic() + HELD_SECONDS,
        )
        self._held[held_id] = held
        return held

    def held(self, held_id: str | None) -> HeldFile | None:
        if not held_id:
            return None
        held = self._held.get(held_id)
        if held is not None and time.monotonic() > held.expires:
            self.release_held(held_id)
            return None
        return held

    def release_held(self, held_id: str) -> None:
        held = self._held.pop(held_id, None)
        if held is not None and not _unlink(held.path):
            self._stale.add(held.path)

    # -- reading back (tests, diagnostics) ------------------------------------

    def staged_paths(self) -> list[Path]:
        if not self.root.exists():
            return []
        return sorted(p for p in self.root.iterdir() if p.is_file())


def _append(path: Path, data: bytes) -> None:
    with path.open("ab") as handle:
        handle.write(data)


def _unlink(path: Path) -> bool:
    """Delete a staged file; False when it is still held open somewhere."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True
