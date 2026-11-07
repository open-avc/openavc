"""Panel software updates, staged by this server for a device supervisor.

On appliance hardware a device supervisor starts this server, keeps it
running and shows the panel. Integrators know it as the panel software. A
newer copy replaces it the way any installed app is replaced, and the
supervisor does that itself: this module only puts the new file where the
supervisor said to and, when asked, writes the request that tells it to
install. The supervisor decides WHETHER a file is fit (its own name, its own
signing key, strictly newer); this server decides WHEN to ask.

THE DECLARATION. A supervisor that takes updates this way writes
``supervisor/supervisor.json`` in the data directory at every start: a flat
JSON object with its version, the one file it installs from, the file that
asks for an install, and the result of its last attempt. Every path comes
from that file and nothing here names one; a declared file anywhere but the
declaration's own folder is refused, so a damaged declaration cannot aim a
download at a project or a login file. No declaration means no supervisor,
and then nothing here says anything anywhere: no state key, no log line.

THE ORDER, which the supervisor relies on. Any old request is removed first,
the download lands beside the update file and is renamed over it only once
its SHA-256 matches, and the request (``sha256=<lowercase hex>``) is written
last. The supervisor refuses a request whose file is missing or different,
so any other order is a recorded refusal.

WHEN TO ASK. A requested install runs once nobody has touched the panel for
the supervisor's idle window. A presentation generates no touches either, so
while the project's "Stay bright while" key reads on, the request is held
back here, and taken away again if the key comes on before the install
starts. With no key set, the idle window is the only guard.

NOTHING HERE CAN BE UNDONE. There is no rollback for panel software, so this
never stages a version at or below the installed one, never asks again for an
install the supervisor refused, and never stages while OpenAVC is applying its
own update. Backups never include any of these files: ``create_backup``
collects named files and trees, never the whole data directory, so a restore
cannot bring back a stale request.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from openavc.utils.paths import is_link, plain_file_in

log = logging.getLogger(__name__)

# The supervisor's folder and declaration, relative to the data directory.
# The only names this module knows; everything else comes from the declaration.
SUPERVISOR_DIR = "supervisor"
DECLARATION_NAME = "supervisor.json"
DECLARATION_SCHEMA = 1

# This server's own note of what it staged. Beside the supervisor's folder,
# not in it: that folder is the supervisor's.
RECORD_NAME = "panel-software-staged.json"

# The suffix a download is written under until its hash matches.
PARTIAL_SUFFIX = ".partial"

STATE_PREFIX = "system.panel_software."
STATE_FIELDS = (
    "version",
    "version_code",
    "last_result",
    "last_result_version",
    "last_result_message",
    "last_result_at",
    "pending_version",
    "pending_version_code",
    "pending_install",
    "install_idle_seconds",
)

# What `install` may ask for. At the panel: staged, and somebody standing at
# it starts the install. When idle: the request is written, and the panel
# installs it once nobody has touched it for its idle window.
INSTALL_AT_PANEL = "at_panel"
INSTALL_WHEN_IDLE = "when_idle"
INSTALL_MODES = (INSTALL_AT_PANEL, INSTALL_WHEN_IDLE)

# `pending_install` when a requested update is waiting for the space to be
# free; the other values are the install modes above, or "" for nothing.
PENDING_HELD = "held"

POLL_SECONDS = 15.0
# Far above any panel software build and far below anything that could fill
# a panel's storage by accident.
MAX_UPDATE_BYTES = 256 * 1024 * 1024
MIN_FREE_BYTES = 64 * 1024 * 1024
DEFAULT_IDLE_SECONDS = 600

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_TEXT_LIMIT = 500


class DeclarationError(ValueError):
    """A declaration exists but cannot be used."""


@dataclass(frozen=True)
class Declaration:
    """What the supervisor says about itself (schema 1)."""

    version: str
    version_code: int
    signers: frozenset[str]
    update_file: str
    request_file: str
    idle_seconds: int
    last_result: str
    last_result_version: str
    last_result_message: str
    last_result_at: str

    def targets(self, folder: Path) -> tuple[Path, Path] | None:
        """The update file and the request file, when both are files directly
        in ``folder`` and neither is a link; None otherwise."""
        update = _declared_file(folder, self.update_file)
        request = _declared_file(folder, self.request_file)
        if update is None or request is None or update.name == request.name:
            return None
        return update, request


@dataclass(frozen=True)
class StageOutcome:
    success: bool
    message: str
    # Empty on success; otherwise a stable word the cloud can match on.
    code: str = ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ")[:_TEXT_LIMIT]


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _signer_set(value: Any) -> frozenset[str]:
    """A comma-joined list of lowercase hex SHA-256 values. Anything else is
    an unknown signer, which is never a match."""
    if not isinstance(value, str) or not value.strip():
        return frozenset()
    parts = {p.strip().lower() for p in value.split(",") if p.strip()}
    if not parts or not all(_HEX64.match(p) for p in parts):
        return frozenset()
    return frozenset(parts)


def _declared_file(folder: Path, raw: str) -> Path | None:
    if not raw:
        return None
    declared = Path(raw)
    if not declared.is_absolute():
        return None
    name = declared.name
    if (
        name == DECLARATION_NAME
        or name.endswith(PARTIAL_SUFFIX)
        or name.endswith(".tmp")
        or plain_file_in(folder, name) is None
    ):
        return None
    try:
        if declared.parent.resolve() != folder.resolve():
            return None
    except OSError:
        return None
    target = folder / name
    if is_link(folder) or is_link(target):
        return None
    return target


def parse_declaration(text: str) -> Declaration:
    """Read a declaration's text. Raises DeclarationError when it cannot be used."""
    try:
        data = json.loads(text)
    except ValueError as e:
        raise DeclarationError("is not valid JSON") from e
    if not isinstance(data, dict):
        raise DeclarationError("is not a JSON object")
    if data.get("schema") != DECLARATION_SCHEMA:
        raise DeclarationError(
            f"has schema {data.get('schema')!r}; this version reads schema {DECLARATION_SCHEMA}"
        )
    code = _positive_int(data.get("version_code"))
    if code is None:
        raise DeclarationError("has no usable version_code")
    idle = _positive_int(data.get("auto_install_idle_seconds")) or DEFAULT_IDLE_SECONDS
    return Declaration(
        version=_text(data.get("version")),
        version_code=code,
        signers=_signer_set(data.get("signer_sha256")),
        update_file=data.get("update_file") if isinstance(data.get("update_file"), str) else "",
        request_file=data.get("request_file") if isinstance(data.get("request_file"), str) else "",
        idle_seconds=idle,
        last_result=_text(data.get("last_result")),
        last_result_version=_text(data.get("last_result_version")),
        last_result_message=_text(data.get("last_result_message")),
        last_result_at=_text(data.get("last_result_at")),
    )


def read_declaration(folder: Path) -> Declaration | None:
    """The declaration in ``folder``, or None when there is none."""
    try:
        text = (folder / DECLARATION_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as e:
        raise DeclarationError(f"could not be read: {e}") from e
    return parse_declaration(text)


@dataclass
class _Record:
    """What this server staged, and whether it has asked for the install."""

    sha256: str
    version: str
    version_code: int
    install: str
    size: int
    mtime_ns: int
    staged_at: str
    requested: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> "_Record | None":
        if not isinstance(data, dict):
            return None
        try:
            record = cls(
                sha256=str(data["sha256"]),
                version=_text(data.get("version")),
                version_code=int(data["version_code"]),
                install=str(data["install"]),
                size=int(data["size"]),
                mtime_ns=int(data["mtime_ns"]),
                staged_at=_text(data.get("staged_at")),
                requested=bool(data.get("requested", False)),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if not _HEX64.match(record.sha256) or record.install not in INSTALL_MODES:
            return None
        return record


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _label(version: str, version_code: int) -> str:
    return version or f"build {version_code}"


class PanelSoftwareUpdates:
    """Publishes the panel software's state and stages its updates.

    ``update_manager`` provides the download (``download_to``) and says when
    OpenAVC is applying its own update (``update_in_progress``).
    ``display_settings`` returns the project's display settings, read each time
    because a project can change under it.
    """

    def __init__(
        self,
        state,
        update_manager,
        *,
        data_dir: Path | None = None,
        display_settings: Callable[[], Any] | None = None,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        if data_dir is None:
            from openavc.system_config import get_system_config

            data_dir = get_system_config().data_dir
        self._state = state
        self._updates = update_manager
        self._data_dir = Path(data_dir)
        self._folder = self._data_dir / SUPERVISOR_DIR
        self._record_path = self._data_dir / RECORD_NAME
        self._display = display_settings or (lambda: None)
        self._poll_seconds = poll_seconds
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._hold_key = ""
        self._hold_sub: str | None = None
        self._published = False
        self._warned: str | None = None
        self._refresh_fault: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if _firmware_host() is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._remove_leftover_partial()
        self._safe_refresh()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._set_hold_subscription("")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            self._safe_refresh()

    def _safe_refresh(self) -> None:
        try:
            self.refresh()
        except Exception as e:
            # Once per distinct fault: the poll repeats every few seconds.
            if self._refresh_fault != repr(e):
                self._refresh_fault = repr(e)
                log.exception("Panel software: refresh failed")
        else:
            self._refresh_fault = None

    # ---- what is installed and what is waiting --------------------------

    def refresh(self) -> None:
        """Read the declaration, settle what this server staged, ask for or
        hold back the install, and publish the result as state.

        Does nothing while an update is being staged: staging publishes
        what it did itself, under the same lock."""
        if self._lock.locked():
            return
        decl = self._declaration()
        self._set_hold_subscription(self._current_hold_key() if decl is not None else "")
        record = self._read_record()
        held = False
        if record is not None:
            if decl is None:
                self._drop_record()
                record = None
            else:
                record, held = self._settle(decl, record)
        self._publish(decl, record, held)

    def _declaration(self) -> Declaration | None:
        try:
            decl = read_declaration(self._folder)
        except DeclarationError as e:
            if self._warned != str(e):
                self._warned = str(e)
                log.warning("Panel software: its declaration %s, so it is not shown or updated", e)
            return None
        self._warned = None
        return decl

    def _settle(self, decl: Declaration, record: _Record) -> tuple[_Record | None, bool]:
        targets = decl.targets(self._folder)
        if targets is None:
            self._drop_record()
            return None, False
        update_file, request_file = targets
        try:
            st = update_file.stat()
        except OSError:
            st = None
        if st is None or (st.st_size, st.st_mtime_ns) != (record.size, record.mtime_ns):
            # Installed (the supervisor deletes the file it installed from),
            # replaced by somebody else, or removed by hand.
            self._drop_record()
            return None, False
        if decl.version_code >= record.version_code:
            # Installed: the supervisor deletes the spent file at its next start.
            self._drop_record()
            return None, False
        requested_now = request_file.exists()
        if record.install != INSTALL_WHEN_IDLE:
            # Somebody at the panel starts this one, so nothing here asks.
            if requested_now:
                _remove(request_file)
            return record, False

        if record.requested and not requested_now:
            # The supervisor took the request away: it refused the file or the
            # install failed, and its declaration says which. Not asked again.
            self._drop_record()
            return None, False

        held = self._hold_engaged()
        if held:
            if requested_now:
                _remove(request_file)
            if record.requested:
                record.requested = False
                self._write_record(record)
        elif not (requested_now and record.requested):
            _write_atomic(request_file, f"sha256={record.sha256}\n")
            record.requested = True
            self._write_record(record)
        return record, held

    def _publish(self, decl: Declaration | None, record: _Record | None, held: bool) -> None:
        if self._state is None:
            return
        if decl is None:
            if self._published:
                for field in STATE_FIELDS:
                    self._state.delete(STATE_PREFIX + field, source="system")
                self._published = False
            return
        if record is None:
            pending = ""
        elif record.install == INSTALL_WHEN_IDLE and held:
            pending = PENDING_HELD
        else:
            pending = record.install
        values: dict[str, Any] = {
            "version": decl.version,
            "version_code": decl.version_code,
            "last_result": decl.last_result,
            "last_result_version": decl.last_result_version,
            "last_result_message": decl.last_result_message,
            "last_result_at": decl.last_result_at,
            "pending_version": record.version if record else "",
            "pending_version_code": record.version_code if record else None,
            "pending_install": pending,
            "install_idle_seconds": decl.idle_seconds,
        }
        self._state.set_batch({STATE_PREFIX + k: v for k, v in values.items()}, source="system")
        self._published = True

    # ---- the space in use -----------------------------------------------

    def _current_hold_key(self) -> str:
        try:
            settings = self._display()
        except Exception:
            return ""
        return str(getattr(settings, "idle_dim_hold_state_key", "") or "").strip()

    def _hold_engaged(self) -> bool:
        try:
            settings = self._display()
            return bool(settings is not None and settings.hold_engaged(self._state))
        except Exception:
            return False

    def _set_hold_subscription(self, key: str) -> None:
        if key == self._hold_key:
            return
        if self._hold_sub is not None and self._state is not None:
            self._state.unsubscribe(self._hold_sub)
        self._hold_sub = None
        self._hold_key = key
        if key and self._state is not None:
            self._hold_sub = self._state.subscribe(key, self._on_hold_change)

    def _on_hold_change(self, *_args) -> None:
        # Settle on the event loop once the change that called this has
        # finished notifying. A script can write state from its own thread,
        # and that is the thread this is called on.
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._safe_refresh)

    # ---- the record of what this server staged --------------------------

    def _read_record(self) -> _Record | None:
        try:
            text = self._record_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as e:
            log.warning("Panel software: could not read %s: %s", self._record_path.name, e)
            return None
        try:
            record = _Record.from_dict(json.loads(text))
        except ValueError:
            record = None
        if record is None:
            log.warning("Panel software: %s is damaged; forgetting it", self._record_path.name)
            self._drop_record()
        return record

    def _write_record(self, record: _Record) -> None:
        _write_atomic(self._record_path, json.dumps(asdict(record), indent=2) + "\n")

    def _drop_record(self) -> None:
        _remove(self._record_path)

    def _remove_leftover_partial(self) -> None:
        """A download this server was writing when it stopped."""
        decl = self._declaration()
        targets = decl.targets(self._folder) if decl is not None else None
        if targets is not None:
            _remove(targets[0].with_name(targets[0].name + PARTIAL_SUFFIX))

    # ---- staging --------------------------------------------------------

    async def stage(
        self,
        *,
        update_url: Any,
        checksum_sha256: Any,
        target_version: Any,
        target_version_code: Any,
        signer_sha256: Any = None,
        install: Any = None,
    ) -> StageOutcome:
        """Download a panel software update to where the supervisor reads it.

        Never touches OpenAVC itself: no backup, no restart, nothing in the
        install directory. ``install`` is ``"at_panel"`` (the default) or
        ``"when_idle"``. The install itself happens later, and its result
        arrives in ``system.panel_software.last_result``.
        """
        host = _firmware_host()
        if host is not None:
            return StageOutcome(False, host.guidance, "firmware_managed")

        url = update_url if isinstance(update_url, str) else ""
        scheme = urlparse(url).scheme
        if scheme != "https":
            return StageOutcome(
                False, f"Refusing a download address that is not HTTPS ({scheme or 'none'}).", "invalid_request"
            )
        sha = checksum_sha256.strip().lower() if isinstance(checksum_sha256, str) else ""
        if not _HEX64.match(sha):
            return StageOutcome(False, "The update has no valid SHA-256 checksum, so nothing was staged.", "invalid_request")
        code = _positive_int(target_version_code)
        if code is None:
            return StageOutcome(False, "The update has no valid version code, so nothing was staged.", "invalid_request")
        mode = INSTALL_AT_PANEL if install is None else install
        if mode not in INSTALL_MODES:
            return StageOutcome(False, f"Unknown install choice {install!r}.", "invalid_request")
        version = _text(target_version)[:64]
        label = _label(version, code)
        wanted_signers = _signer_set(signer_sha256) if signer_sha256 else frozenset()
        if signer_sha256 and not wanted_signers:
            return StageOutcome(False, "The update's signer is not a valid SHA-256 value.", "invalid_request")

        if self._lock.locked():
            return StageOutcome(False, "A panel software update is already being staged on this system.", "busy")
        async with self._lock:
            return await self._stage_locked(url, sha, code, version, label, wanted_signers, mode)

    async def _stage_locked(
        self,
        url: str,
        sha: str,
        code: int,
        version: str,
        label: str,
        wanted_signers: frozenset[str],
        mode: str,
    ) -> StageOutcome:
        if getattr(self._updates, "update_in_progress", False):
            return StageOutcome(
                False, "OpenAVC is installing its own update. Try again once it has restarted.", "server_updating"
            )
        try:
            decl = read_declaration(self._folder)
        except DeclarationError as e:
            return StageOutcome(False, f"The panel software's declaration {e}, so nothing was staged.", "declaration_invalid")
        if decl is None:
            return StageOutcome(False, "This system has no panel software that can be updated remotely.", "no_panel_software")
        targets = decl.targets(self._folder)
        if targets is None:
            return StageOutcome(
                False,
                "The panel software names an update location outside its own folder, so nothing was staged.",
                "declaration_invalid",
            )
        update_file, request_file = targets
        if code <= decl.version_code:
            return StageOutcome(
                False,
                f"This panel already has panel software {_label(decl.version, decl.version_code)}, "
                f"which is the same as {label} or newer.",
                "not_newer",
            )
        if wanted_signers and decl.signers and wanted_signers != decl.signers:
            return StageOutcome(False, f"Panel software {label} is not signed with this panel's key, so nothing was staged.", "wrong_signer")

        record = self._read_record()
        if record is not None and record.sha256 == sha and self._intact(update_file, record):
            # The same file, staged already. Sent again, it is asked for
            # again, under whichever install choice it now carries.
            record.install = mode
            record.requested = False
            self._write_record(record)
            self._publish_after_stage()
            return StageOutcome(True, self._ready_message(label, mode, decl))

        try:
            free = shutil.disk_usage(str(self._folder)).free
        except OSError as e:
            return StageOutcome(False, f"The panel software's folder could not be read: {e}", "declaration_invalid")
        if free < MIN_FREE_BYTES:
            return StageOutcome(
                False,
                f"Not enough free space to stage the update: {free // (1024 * 1024)} MB free, "
                f"{MIN_FREE_BYTES // (1024 * 1024)} MB needed.",
                "insufficient_space",
            )

        # The order the supervisor relies on: the old request goes first, so
        # nothing can ask for a file while it is being replaced.
        _remove(request_file)
        self._drop_record()
        self._publish_after_stage()

        partial = update_file.with_name(update_file.name + PARTIAL_SUFFIX)
        log.info("Panel software: downloading %s", label)
        try:
            await self._updates.download_to(url, partial, max_bytes=MAX_UPDATE_BYTES)
        except Exception as e:
            _remove(partial)
            log.warning("Panel software: download of %s failed: %s", label, e)
            return StageOutcome(False, f"Panel software {label} could not be downloaded: {e}", "download_failed")

        try:
            actual = await asyncio.to_thread(_sha256_file, partial)
        except OSError as e:
            _remove(partial)
            return StageOutcome(False, f"The downloaded file could not be read: {e}", "download_failed")
        if actual != sha:
            _remove(partial)
            log.warning("Panel software: %s did not match its checksum; deleted", label)
            return StageOutcome(
                False,
                f"The download of panel software {label} did not match its checksum, so it was deleted and nothing was staged.",
                "checksum_mismatch",
            )

        os.chmod(partial, 0o644)
        os.replace(partial, update_file)
        st = update_file.stat()
        self._write_record(
            _Record(
                sha256=sha,
                version=version,
                version_code=code,
                install=mode,
                size=st.st_size,
                mtime_ns=st.st_mtime_ns,
                staged_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )
        log.info("Panel software: %s staged (%s)", label, mode)
        self._publish_after_stage()
        return StageOutcome(True, self._ready_message(label, mode, decl))

    def _publish_after_stage(self) -> None:
        # Called with the staging lock held, which refresh() reads as "do not
        # settle". Settle here directly instead.
        decl = self._declaration()
        record = self._read_record()
        held = False
        if decl is not None and record is not None:
            record, held = self._settle(decl, record)
        self._publish(decl, record, held)

    @staticmethod
    def _intact(update_file: Path, record: _Record) -> bool:
        try:
            st = update_file.stat()
        except OSError:
            return False
        return (st.st_size, st.st_mtime_ns) == (record.size, record.mtime_ns)

    def _ready_message(self, label: str, mode: str, decl: Declaration) -> str:
        if mode == INSTALL_AT_PANEL:
            return f"Panel software {label} is ready on this panel. It installs when somebody starts it at the panel."
        minutes = max(1, round(decl.idle_seconds / 60))
        wait = f"nobody has touched the panel for {minutes} minute{'s' if minutes != 1 else ''}"
        if self._current_hold_key():
            return (
                f"Panel software {label} is ready on this panel. It installs once the space is "
                f"not in use and {wait}."
            )
        return f"Panel software {label} is ready on this panel. It installs once {wait}."


def _remove(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


def _firmware_host():
    from openavc.updater.host import get_firmware_host

    return get_firmware_host()
