"""Device logins saved on this system: the login file, its key, and the marker.

A device's password, or any other setting its driver declares secret, is kept
here instead of in the project. The project then holds ``SAVED_LOGIN`` in that
field, so every copy of the project (a backup, an export, a template, what the
cloud keeps) says that a login is saved without carrying it, and the login
itself stays in one file on this computer.

The file and its key
--------------------
``device_logins.json`` in the data folder holds the logins, encrypted with
Fernet from ``cryptography`` (AES-128-CBC with an HMAC-SHA256 tag). Its key is
``device_logins.key`` beside it. Both are written owner read/write only on
POSIX; on Windows they inherit the data folder's ACL, which the installer
restricts to SYSTEM and Administrators, as the TLS keys and ``cloud.json`` do.
The key keeps a copy of the login file taken without it unreadable. It does
not protect against anyone who can read this computer's data folder the way
the server does.

Neither file exists until the first login is saved, so a system with no saved
login reads nothing here and writes nothing, and the cipher is not even loaded
until a login file is read or written. An existing key is never replaced: a
copy of the login file taken earlier opens only with it.

A login file that cannot be opened (no key, the wrong key, a format from a
newer version, damage) is reported and left exactly as it is. Lookups find
nothing and every write is refused, so the file still opens once the right key
or version is back.

The format is 2. Format 1 (logins of the running project only) is still read.
Format 2 added the logins of each saved copy in the Project Library, and
changed the number because a reader of format 1 that rewrote the file would
have dropped them; it leaves a format it does not know untouched instead.

Which saved login a device gets
-------------------------------
A login is filed under the device id AND the driver it was saved for, and a
device gets it only when both match. The address is not part of the rule:
DHCP moves addresses on equipment that is still the same unit. A login filed
for another driver under the same id is never handed to this one.

The running project's logins are one set. Each Project Library copy has its
own set, filed under its library id, so a copy of another room finds its own
logins when it is opened: the running set wins for a device in both, and the
copy's own fill the rest (``adopt_library``).

A device that leaves the running project keeps its logins for
``REMOVED_KEEP``, so a backup from before the removal still finds them when it
is restored; after that they are deleted. A device back in the project with
the same driver is current again. ``DeviceLogins.sweep`` applies both, from the
project's device list. A library copy's logins last as long as the copy.

Moving logins out of a project
------------------------------
``move_logins`` is the one step that takes logins out of a project: the first
start of this version, a backup restored, a project opened, imported or pushed,
a setting someone saves, and a setup action that saves a code. The settings it
moves are the connection password and each config field the device's driver
declares ``secret: true`` or ``type: password`` (``login_fields``). For each one
holding a value it:

1. writes the value into the login file,
2. reads the file back from disk and checks it holds exactly what was meant,
3. only then returns a copy of the project with the marker in that field.

A failure at any step returns the project exactly as it was given, logins and
all, so the device keeps working the way it did and the next load tries again.
Nothing is ever written over a value that is not yet in the file.

Which value is kept depends on where the project came from. A setting someone
typed (an edit) replaces the saved login. A copy loaded from anywhere else (a
backup, the library, an import, the cloud) never does: this system's current
login wins, so restoring an old backup never puts back a password changed
since, and the copy's value only fills a gap.

``put_back_for_rollback`` is the reverse, for a rollback to an earlier version,
which expects each login in the project: every marker in the running project
and the library copies is replaced by its saved login before the earlier
version starts. A version that knows this file moves them out again on its
first start.

Copies that hold logins
-----------------------
A project backup holds the logins of its devices as a login file sealed with
this system's key (``backup_copy``), never the key, so a backup copied off
this system holds nothing readable. Restored here, it fills in each login this
system no longer has, such as a device removed longer than ``REMOVED_KEEP``
ago (``restore_copy``); this system's own logins win. An export holds them
only when somebody asks, sealed with a passphrase (``core.login_bundle``).

Filling them in
---------------
``fill_saved_logins`` runs where a device's resolved config is handed to its
driver (``Engine.resolved_device_config``, and the device audit's "use this
device's saved settings"). It is deliberately not part of
``core.device_config.resolve_device_config``, whose other readers (problem
reports, the AI's project summary, the connection test) keep seeing the
marker. A marked field with no saved login keeps the marker, and
``BaseDriver.connect`` refuses to dial while one is left (``login_missing``),
so a device is never sent the marker, or an empty password, as its login.
"""

from __future__ import annotations

import copy
import json
import os
import stat
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from openavc.drivers.registry import get_driver_class
from openavc.drivers.spec import PASSWORD_FIELD, secret_settings
from openavc.utils.fileio import atomic_write_text
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: What the project holds in a field whose login is saved on this system.
#: Fixed, so two copies of one project stay byte-identical, and never shaped
#: like a ``{{...}}`` placeholder, which template resolution passes through.
SAVED_LOGIN = "<saved on this system>"

LOGINS_FILENAME = "device_logins.json"
KEY_FILENAME = "device_logins.key"

_FORMAT = "openavc-device-logins"
_FORMAT_VERSION = 2
_READABLE_VERSIONS = (1, 2)

#: How long a removed device's logins are kept.
REMOVED_KEEP = timedelta(days=90)

#: The connection setting that holds a login whatever the driver says.
CONNECTION_LOGIN = PASSWORD_FIELD

_PRIVATE = stat.S_IRUSR | stat.S_IWUSR

#: An entry's key: (library id or None for the running project, device id, driver).
_Key = tuple[str | None, str, str]


class LoginFileUnreadable(RuntimeError):
    """The login file is on disk but cannot be opened, so nothing is written
    over it."""


@dataclass(frozen=True)
class _Entry:
    fields: dict[str, str]
    removed_at: datetime | None = None


@dataclass(frozen=True)
class FoundLogin:
    """The logins one device in a project holds: setting name to value."""

    device_id: str
    driver: str
    fields: dict[str, str]


def _usable(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value != SAVED_LOGIN


def _sort_key(key: _Key) -> tuple[int, str, str, str]:
    library, device_id, driver = key
    return (0 if library is None else 1, library or "", device_id, driver)


class DeviceLogins:
    """The logins saved on this system, read from disk on first use."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / LOGINS_FILENAME
        self.key_path = Path(data_dir) / KEY_FILENAME
        self._entries: dict[_Key, _Entry] = {}
        self._loaded = False
        self._unreadable: str | None = None
        # The engine writes from the event loop and the Project Library from
        # request handlers and worker threads; one write at a time.
        self._lock = threading.RLock()

    # --- reading -------------------------------------------------------------

    @property
    def unreadable(self) -> str | None:
        """Why the login file on disk cannot be opened, or None."""
        self._load()
        return self._unreadable

    def login(self, device_id: str, driver: str, field: str, *, library: str | None = None) -> str | None:
        """The saved value of ``field`` for this device and driver, or None.

        ``library`` names a Project Library copy's logins; None is the running
        project's."""
        self._load()
        entry = self._entries.get((library, device_id, driver))
        if entry is None:
            return None
        value = entry.fields.get(field)
        return value if _usable(value) else None

    def libraries(self) -> set[str]:
        """The library ids that have logins filed."""
        self._load()
        return {library for library, _, _ in self._entries if library is not None}

    def _load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            if not self.path.exists():
                return
            try:
                self._entries = self._read()
            except LoginFileUnreadable as exc:
                self._unreadable = str(exc)
                log.error("%s", exc)

    def _read(self) -> dict[_Key, _Entry]:
        where = f"The device login file {self.path}"
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LoginFileUnreadable(f"{where} cannot be read ({exc}). It is left as it is.") from exc
        return self._open(text, where)

    def _open(self, text: str, where: str) -> dict[_Key, _Entry]:
        """The entries of a login file's text, opened with this system's key."""
        try:
            envelope = json.loads(text)
        except ValueError as exc:
            raise LoginFileUnreadable(f"{where} cannot be read ({exc}). It is left as it is.") from exc
        if not isinstance(envelope, dict) or envelope.get("format") != _FORMAT:
            raise LoginFileUnreadable(f"{where} is not a device login file. It is left as it is.")
        version = envelope.get("version")
        if version not in _READABLE_VERSIONS:
            raise LoginFileUnreadable(
                f"{where} was written by a newer version of OpenAVC (format {version!r}). "
                "It is left as it is, and opens again on that version."
            )
        if not self.key_path.exists():
            raise LoginFileUnreadable(
                f"{where} cannot be opened: its key file {self.key_path} is missing. "
                "It is left as it is, and opens again once that key file is back."
            )
        from cryptography.fernet import Fernet, InvalidToken

        try:
            key = self.key_path.read_bytes().strip()
            payload = json.loads(Fernet(key).decrypt(str(envelope.get("sealed", "")).encode("ascii")))
        except (OSError, ValueError, InvalidToken) as exc:
            raise LoginFileUnreadable(
                f"{where} does not open with the key file {self.key_path}. It is left as it is."
            ) from exc
        entries: dict[_Key, _Entry] = {}
        for item in payload.get("devices", []) if isinstance(payload, dict) else []:
            entry = _entry_from(item)
            if entry is None:
                log.warning("Skipped an unreadable entry in %s", where)
                continue
            key_triple, value = entry
            entries[key_triple] = value
        return entries

    def verify(self) -> None:
        """Read the file back from disk and check it holds exactly what this
        store last wrote. Raises LoginFileUnreadable when it does not."""
        with self._lock:
            fresh = DeviceLogins(self.path.parent)
            fresh._load()
            if fresh._unreadable is not None:
                raise LoginFileUnreadable(fresh._unreadable)
            if fresh._entries != self._entries:
                raise LoginFileUnreadable(
                    f"The device login file {self.path} read back differently from what was written."
                )

    # --- writing -------------------------------------------------------------

    def save(
        self, device_id: str, driver: str, fields: dict[str, str], *, library: str | None = None,
    ) -> None:
        """File ``fields`` (setting name to login) for this device and driver.

        Replaces those fields' saved values and leaves the device's other saved
        fields alone. Raises ValueError for an empty value or the marker
        itself, and LoginFileUnreadable when the file on disk cannot be opened.
        """
        self.store([FoundLogin(device_id, driver, dict(fields))], library=library, typed=True)

    def store(self, found: Iterable[FoundLogin], *, library: str | None = None, typed: bool) -> None:
        """File every login in ``found`` in one write.

        ``typed`` is a value somebody entered: it replaces the saved one. A
        value from a copy of a project (``typed=False``) is filed only where
        nothing is saved for that setting yet. Raises ValueError for an empty
        value or the marker, LoginFileUnreadable when the file cannot be
        opened; nothing is written then.
        """
        found = list(found)
        for login in found:
            for name, value in login.fields.items():
                if not _usable(value):
                    raise ValueError(f"A saved login cannot be empty or {SAVED_LOGIN!r} ({name}).")
        with self._lock:
            self._load()
            entries = dict(self._entries)
            changed = False
            for login in found:
                key = (library, login.device_id, login.driver)
                current = entries.get(key)
                saved = dict(current.fields) if current else {}
                for name, value in login.fields.items():
                    if typed or name not in saved:
                        saved[name] = value
                if current is None or saved != current.fields or current.removed_at is not None:
                    entries[key] = _Entry(saved, None)
                    changed = True
            if changed:
                self._commit(entries)

    def adopt_library(self, library: str, devices: Iterable[tuple[str, str]]) -> None:
        """A library copy is being opened: file its own logins for ``devices``
        (id, driver) as the running project's, where the running project has
        nothing saved for that setting. The running project's always win."""
        with self._lock:
            self._load()
            found = []
            for device_id, driver in devices:
                entry = self._entries.get((library, device_id, driver))
                if entry is not None and entry.fields:
                    found.append(FoundLogin(device_id, driver, dict(entry.fields)))
            if found:
                self.store(found, library=None, typed=False)

    def file_library(self, library: str, devices: Iterable[tuple[str, str]]) -> None:
        """The running project is being saved to the library as ``library``:
        its logins for ``devices`` (id, driver) become that copy's, replacing
        whatever the copy had."""
        with self._lock:
            self._load()
            entries = {k: v for k, v in self._entries.items() if k[0] != library}
            for device_id, driver in devices:
                entry = self._entries.get((None, device_id, driver))
                if entry is not None and entry.fields:
                    entries[(library, device_id, driver)] = _Entry(dict(entry.fields), None)
            if entries != self._entries:
                self._commit(entries)

    def copy_library(self, source: str, target: str) -> None:
        """A library copy is duplicated: ``target`` gets ``source``'s logins."""
        with self._lock:
            self._load()
            entries = {k: v for k, v in self._entries.items() if k[0] != target}
            for (library, device_id, driver), entry in self._entries.items():
                if library == source:
                    entries[(target, device_id, driver)] = entry
            if entries != self._entries:
                self._commit(entries)

    def drop_library(self, library: str) -> None:
        """A library copy is gone: delete its logins."""
        with self._lock:
            self._load()
            if self._unreadable is not None:
                return
            entries = {k: v for k, v in self._entries.items() if k[0] != library}
            if entries != self._entries:
                self._commit(entries)

    def sweep(self, present: Iterable[tuple[str, str]], now: datetime | None = None) -> None:
        """Bring each running-project entry's removal date in line with the
        project. Library copies' logins are not touched.

        ``present`` is every (device id, driver) in the project. An entry not
        among them is dated removed now, unless it already was; one dated
        ``REMOVED_KEEP`` ago or more is deleted; one among them is current.
        Writes only when something changed, and never over a file it cannot
        open.
        """
        with self._lock:
            self._load()
            if self._unreadable is not None or not self._entries:
                return
            now = now or datetime.now(timezone.utc)
            present = set(present)
            entries: dict[_Key, _Entry] = {}
            changed = False
            for key, entry in self._entries.items():
                library, device_id, driver = key
                if library is not None:
                    entries[key] = entry
                    continue
                if (device_id, driver) in present:
                    if entry.removed_at is not None:
                        entry = _Entry(entry.fields, None)
                        changed = True
                elif entry.removed_at is None:
                    entry = _Entry(entry.fields, now)
                    changed = True
                elif now - entry.removed_at >= REMOVED_KEEP:
                    changed = True
                    continue
                entries[key] = entry
            if changed:
                self._commit(entries)

    # --- a project backup's copy -------------------------------------------------

    def backup_copy(self, devices: Iterable[tuple[str, str]]) -> str | None:
        """The running project's logins for ``devices`` (id, driver), as a
        login file sealed with this system's key, for a project backup to
        hold. The key itself never goes with it, so the copy opens on this
        system only. None when none of them has a login saved or the login
        file cannot be opened. Never makes a key."""
        with self._lock:
            self._load()
            if self._unreadable is not None or not self.key_path.exists():
                return None
            entries: dict[_Key, _Entry] = {}
            for device_id, driver in devices:
                entry = self._entries.get((None, device_id, driver))
                if entry is not None and entry.fields:
                    entries[(None, device_id, driver)] = _Entry(dict(entry.fields), None)
            if not entries:
                return None
            return self._envelope(entries, self._key())

    def restore_copy(self, text: str, devices: Iterable[tuple[str, str]], *, where: str) -> int:
        """A project backup's copy (``backup_copy``) restored on this system:
        each of its logins for ``devices`` (id, driver) that this system has
        nothing saved for is filed. This system's own logins always win. A
        copy that does not open with this system's key, such as one made on
        another system, files nothing. Returns how many settings were filed;
        never raises."""
        try:
            with self._lock:
                self._load()
                if self._unreadable is not None:
                    log.error("The device logins in %s were not restored: %s", where, self._unreadable)
                    return 0
                copy_entries = self._open(text, where)
                wanted = set(devices)
                found: list[FoundLogin] = []
                filled = 0
                for (library, device_id, driver), entry in sorted(copy_entries.items(), key=lambda i: _sort_key(i[0])):
                    if library is not None or (device_id, driver) not in wanted or not entry.fields:
                        continue
                    current = self._entries.get((None, device_id, driver))
                    have = set(current.fields) if current is not None else set()
                    filled += len(set(entry.fields) - have)
                    found.append(FoundLogin(device_id, driver, dict(entry.fields)))
                if found:
                    self.store(found, typed=False)
                    self.verify()
        except Exception as exc:  # the restore goes ahead; those devices ask for their login
            log.warning("The device logins in %s were not restored (%s)", where, exc)
            return 0
        if filled:
            log.info("Restored %d device login(s) this system did not have from %s", filled, where)
        return filled

    def _envelope(self, entries: dict[_Key, _Entry], key: bytes) -> str:
        """``entries`` as the text of a login file sealed with ``key``."""
        from cryptography.fernet import Fernet

        devices = []
        for triple in sorted(entries, key=_sort_key):
            library, device_id, driver = triple
            entry = entries[triple]
            item: dict[str, Any] = {
                "device": device_id,
                "driver": driver,
                "fields": dict(sorted(entry.fields.items())),
                "removed_at": entry.removed_at.isoformat() if entry.removed_at else None,
            }
            if library is not None:
                item["library"] = library
            devices.append(item)
        sealed = Fernet(key).encrypt(json.dumps({"devices": devices}, sort_keys=True).encode("utf-8"))
        envelope = {"format": _FORMAT, "version": _FORMAT_VERSION, "sealed": sealed.decode("ascii")}
        return json.dumps(envelope, indent=2) + "\n"

    def _commit(self, entries: dict[_Key, _Entry]) -> None:
        """Write ``entries`` to disk, then make them current."""
        if self._unreadable is not None:
            raise LoginFileUnreadable(self._unreadable)
        atomic_write_text(self.path, self._envelope(entries, self._key()), mode=_PRIVATE)
        self._entries = entries

    def _key(self) -> bytes:
        """This system's login key, made on the first save."""
        from cryptography.fernet import Fernet

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            key = self.key_path.read_bytes().strip()
            try:
                Fernet(key)
            except ValueError as exc:
                raise LoginFileUnreadable(
                    f"The device login key file {self.key_path} is damaged. It is left as it is."
                ) from exc
            return key
        key = Fernet.generate_key()
        atomic_write_text(self.key_path, key.decode("ascii") + "\n", mode=_PRIVATE)
        return key


def _entry_from(item: Any) -> tuple[_Key, _Entry] | None:
    if not isinstance(item, dict):
        return None
    device_id, driver, fields = item.get("device"), item.get("driver"), item.get("fields")
    library = item.get("library")
    if not (isinstance(device_id, str) and isinstance(driver, str) and isinstance(fields, dict)):
        return None
    if library is not None and not isinstance(library, str):
        return None
    kept = {str(k): v for k, v in fields.items() if _usable(v)}
    removed_at = None
    if item.get("removed_at"):
        # A date that does not read keeps the logins as current: the next
        # sweep dates them again if the device is gone.
        try:
            removed_at = datetime.fromisoformat(str(item["removed_at"]))
        except ValueError:
            removed_at = None
        if removed_at is not None and removed_at.tzinfo is None:
            removed_at = removed_at.replace(tzinfo=timezone.utc)
    return (library, device_id, driver), _Entry(kept, removed_at)


# --- which settings hold a login -------------------------------------------------------


def login_fields(driver_id: str) -> frozenset[str]:
    """The settings of a device on ``driver_id`` that hold a login: the
    connection password, and each config field that is a secret by the one
    rule (``drivers/spec.is_secret_setting``: ``secret: true`` or
    ``type: password``). For a driver that is not installed, the password
    only: its other settings move once it is."""
    cls = get_driver_class(driver_id)
    schema = cls.DRIVER_INFO.get("config_schema") if cls is not None else None
    return frozenset({CONNECTION_LOGIN}) | secret_settings(schema)


@dataclass
class _Row:
    """One device's settings in a project, wherever the project is held."""

    device_id: str
    driver: str
    config: dict[str, Any]
    connection: dict[str, Any] | None


def _rows_of(project: Any) -> list[_Row]:
    """Every device in ``project`` (a ProjectConfig, or a project file read as
    JSON in any format version) with its config and connection row."""
    rows: list[_Row] = []
    if isinstance(project, dict):
        connections = project.get("connections")
        connections = connections if isinstance(connections, dict) else {}
        for device in project.get("devices") or []:
            if not isinstance(device, dict):
                continue
            device_id, driver = device.get("id"), device.get("driver")
            if not (isinstance(device_id, str) and isinstance(driver, str)):
                continue
            config = device.get("config")
            connection = connections.get(device_id)
            rows.append(_Row(
                device_id, driver,
                config if isinstance(config, dict) else {},
                connection if isinstance(connection, dict) else None,
            ))
        return rows
    for device in getattr(project, "devices", None) or []:
        connection = project.connections.get(device.id)
        rows.append(_Row(
            device.id, device.driver, device.config or {},
            connection if isinstance(connection, dict) else None,
        ))
    return rows


def find_logins(project: Any, *, skip: Callable[[str], bool] | None = None) -> list[FoundLogin]:
    """The logins ``project`` holds in the clear, device by device. The value
    for a setting is the one the device dials: its connection row's over its
    config's. ``skip`` leaves out a device by id."""
    found: list[FoundLogin] = []
    for row in _rows_of(project):
        if skip is not None and skip(row.device_id):
            continue
        values: dict[str, str] = {}
        for name in login_fields(row.driver):
            for holder in (row.connection, row.config):
                value = holder.get(name) if holder is not None else None
                if _usable(value):
                    values[name] = value
                    break
        if values:
            found.append(FoundLogin(row.device_id, row.driver, values))
    return found


def _marked_copy(project: Any, found: list[FoundLogin]) -> Any:
    """A copy of ``project`` with the marker in every place a login in
    ``found`` was held in the clear."""
    wanted = {(f.device_id, f.driver): set(f.fields) for f in found}

    def mark(holder: dict[str, Any] | None, names: set[str]) -> dict[str, Any] | None:
        if holder is None or not any(_usable(holder.get(n)) for n in names):
            return None
        return {k: (SAVED_LOGIN if k in names and _usable(v) else v) for k, v in holder.items()}

    if isinstance(project, dict):
        marked = copy.deepcopy(project)
        connections = marked.get("connections") if isinstance(marked.get("connections"), dict) else {}
        for device in marked.get("devices") or []:
            if not isinstance(device, dict):
                continue
            names = wanted.get((device.get("id"), device.get("driver")))
            if not names:
                continue
            config = mark(device.get("config") if isinstance(device.get("config"), dict) else None, names)
            if config is not None:
                device["config"] = config
            row = connections.get(device.get("id"))
            connection = mark(row if isinstance(row, dict) else None, names)
            if connection is not None:
                connections[device["id"]] = connection
        return marked
    marked = project.model_copy(deep=True)
    for device in marked.devices:
        names = wanted.get((device.id, device.driver))
        if not names:
            continue
        config = mark(device.config, names)
        if config is not None:
            device.config = config
        row = marked.connections.get(device.id)
        connection = mark(row if isinstance(row, dict) else None, names)
        if connection is not None:
            marked.connections[device.id] = connection
    return marked


def move_logins(
    project: Any,
    logins: DeviceLogins,
    *,
    typed: bool,
    library: str | None = None,
    skip: Callable[[str], bool] | None = None,
) -> Any:
    """``project`` with each login it holds in the clear moved into the login
    file and the marker left in its place.

    ``project`` is a ProjectConfig or a project file read as JSON; the result
    is the same kind, and is ``project`` itself when nothing moved. Each login
    is in the file and read back before the marker replaces it. On any failure
    ``project`` is returned untouched, logins and all, and the reason is
    logged. ``typed`` and ``library`` are as for ``DeviceLogins.store``.
    """
    found = find_logins(project, skip=skip)
    if not found:
        return project
    where = f"library copy '{library}'" if library is not None else "the project"
    try:
        logins.store(found, library=library, typed=typed)
        logins.verify()
        for login in found:
            for name in login.fields:
                if logins.login(login.device_id, login.driver, name, library=library) is None:
                    raise LoginFileUnreadable(f"The login file has no {name} for {login.device_id}.")
    except Exception as exc:  # leave every login where it is, whatever failed
        log.error(
            "Device logins in %s stay in it for now: they could not be saved on this system (%s)",
            where, exc,
        )
        return project
    log.info(
        "Moved %d device login(s) out of %s into this system's login file",
        sum(len(f.fields) for f in found), where,
    )
    return _marked_copy(project, found)


# --- putting them back -------------------------------------------------------------


def put_back(project: dict[str, Any], lookup: Callable[[str, str, str], str | None]) -> dict[str, Any] | None:
    """A copy of ``project`` (read as JSON) with each marker replaced by
    ``lookup(device id, driver, setting)``; None when nothing changed. A marker
    with no saved login stays."""
    marked = copy.deepcopy(project)
    connections = marked.get("connections") if isinstance(marked.get("connections"), dict) else {}
    changed = False

    def restore(holder: Any, device_id: str, driver: str) -> None:
        nonlocal changed
        if not isinstance(holder, dict):
            return
        for name in marked_fields(holder):
            value = lookup(device_id, driver, name)
            if value is not None:
                holder[name] = value
                changed = True

    for device in marked.get("devices") or []:
        if not isinstance(device, dict):
            continue
        device_id, driver = device.get("id"), device.get("driver")
        if not (isinstance(device_id, str) and isinstance(driver, str)):
            continue
        restore(device.get("config"), device_id, driver)
        restore(connections.get(device_id), device_id, driver)
    return marked if changed else None


def _put_back_file(path: Path, lookup: Callable[[str, str, str], str | None]) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    restored = put_back(raw, lookup)
    if restored is not None:
        atomic_write_text(path, json.dumps(restored, indent=4, ensure_ascii=False))
        log.warning("Put the saved device logins back into %s for the earlier version", path)


def put_back_for_rollback(
    data_dir: Path, project_path: Path | None, then: Callable[[], None] | None = None,
) -> None:
    """Before a rollback to an earlier version: every marker in the running
    project and in each Project Library copy is replaced by its saved login,
    since an earlier version reads logins from the project. A library copy
    gets the running project's login first and its own after, as opening it
    would.

    ``then`` (the process exit, for a rollback asked for while the server
    runs) is called holding the project save lock, after the running project
    is written, so no save can land in between and write the markers back.
    It is called whatever happens here. Never raises: the rollback goes ahead.
    """
    from openavc.core.project_loader import _project_save_lock

    logins: DeviceLogins | None = None
    try:
        logins = DeviceLogins(Path(data_dir))
        if not logins.path.exists():
            logins = None
        elif logins.unreadable is not None:
            log.error("Device logins could not be put back before the rollback: %s", logins.unreadable)
            logins = None
        if logins is not None:
            library_dir = Path(data_dir) / "saved_projects"
            for avc in sorted(library_dir.glob("*/project.avc")) if library_dir.is_dir() else []:
                if avc.parent.name.startswith("."):
                    continue

                def copy_login(device_id: str, driver: str, name: str, sid: str = avc.parent.name) -> str | None:
                    return (logins.login(device_id, driver, name)
                            or logins.login(device_id, driver, name, library=sid))

                try:
                    _put_back_file(avc, copy_login)
                except OSError:
                    log.exception("Could not put the device logins back into %s", avc)
    except Exception:
        log.exception("Device logins could not be put back into the Project Library before the rollback")

    with _project_save_lock:
        try:
            if logins is not None and project_path is not None and Path(project_path).exists():
                _put_back_file(Path(project_path), logins.login)
        except Exception:
            log.exception("Device logins could not be put back into the project before the rollback")
        if then is not None:
            then()


# --- filling them in -----------------------------------------------------------


def marked_fields(config: dict[str, Any] | None) -> list[str]:
    """The settings in ``config`` that hold the marker."""
    if not isinstance(config, dict):
        return []
    return sorted(str(key) for key, value in config.items() if value == SAVED_LOGIN)


def project_devices(project: Any) -> list[tuple[str, str]]:
    """Every device in ``project`` (a ProjectConfig, or a project file read as
    JSON) as (id, driver)."""
    return [(row.device_id, row.driver) for row in _rows_of(project)]


def saved_logins_of(project: Any, lookup: Callable[[str, str, str], str | None]) -> list[FoundLogin]:
    """The saved logins behind the markers in ``project`` (a ProjectConfig, or
    a project file read as JSON): for each device, each setting that holds
    the marker in its config or its connection row and that
    ``lookup(device id, driver, setting)`` has a login for."""
    found: list[FoundLogin] = []
    for row in _rows_of(project):
        values: dict[str, str] = {}
        for name in sorted(set(marked_fields(row.config)) | set(marked_fields(row.connection))):
            value = lookup(row.device_id, row.driver, name)
            if _usable(value):
                values[name] = value
        if values:
            found.append(FoundLogin(row.device_id, row.driver, values))
    return found


def fill_saved_logins(record: dict[str, Any], logins: DeviceLogins) -> dict[str, Any]:
    """``record`` (a resolved device: id, driver, config) with each marked
    setting replaced by this system's saved login. A setting with no saved
    login keeps the marker, which the driver refuses to dial. The record is
    returned as it is when nothing in it is marked."""
    config = record.get("config")
    marked = marked_fields(config)
    if not marked:
        return record
    device_id, driver = str(record.get("id", "")), str(record.get("driver", ""))
    filled = dict(config)
    for key in marked:
        value = logins.login(device_id, driver, key)
        if value is not None:
            filled[key] = value
    return {**record, "config": filled}


# --- the store for this system ----------------------------------------------------

_stores: dict[str, DeviceLogins] = {}


def device_logins(data_dir: Path | None = None) -> DeviceLogins:
    """The login store for ``data_dir`` (this system's data folder when
    omitted), one per folder for the life of the process."""
    if data_dir is None:
        from openavc.system_config import get_data_dir

        data_dir = get_data_dir()
    where = os.path.abspath(data_dir)
    store = _stores.get(where)
    if store is None:
        store = _stores[where] = DeviceLogins(Path(where))
    return store
