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

Plugins and the project's own secret
------------------------------------
The file also holds the logins plugins declare (``core.plugin_secrets``: a
schema field marked ``secret: true`` and the list items of
``CONFIG_SECRETS``, such as each Video Panel stream's password), filed under
the plugin and the item, and the project's mesh key (``isc.auth_key``). They
move, are kept for ``REMOVED_KEEP`` once gone from the project, travel in a
backup or a sealed export, and are put back before a rollback exactly as a
device's are. Filled in, they differ in one way: a plugin is handed ``""``
for a marked login this system does not have, never the marker, since a
plugin has no refusal to dial and would take the marker as the value
(``filled_plugin_config``); the mesh key the same (``saved_isc_key``).

Every entry has an owner, (kind, name, qualifier): a device is
(``DEVICE``, its id, its driver); a plugin's own settings
(``PLUGIN``, its id, ``""``) and a list item (``PLUGIN``, its id,
``streams[stream_id=cam1]``); the mesh key (``PROJECT``, ``isc``, ``""``).
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

from openavc.core import plugin_secrets
from openavc.drivers.registry import get_driver_class
from openavc.drivers.spec import PASSWORD_FIELD, secret_settings
from openavc.utils.fileio import atomic_write_text
from openavc.utils.log_redaction import IDENTITY_KEYS, is_secret_key
from openavc.utils.logger import get_logger

log = get_logger(__name__)

#: What the project holds in a field whose login is saved on this system.
#: Fixed, so two copies of one project stay byte-identical, and never shaped
#: like a ``{{...}}`` placeholder, which template resolution passes through.
SAVED_LOGIN = "<saved on this system>"

LOGINS_FILENAME = "device_logins.json"
KEY_FILENAME = "device_logins.key"

_FORMAT = "openavc-device-logins"
# Format 2's ``plugins`` and ``project`` lists joined it before any release
# carried the file, so they need no number of their own; a reader from before
# them, which only a development build is, drops them on its next write.
_FORMAT_VERSION = 2
_READABLE_VERSIONS = (1, 2)

#: How long a removed device's logins are kept.
REMOVED_KEEP = timedelta(days=90)

#: The connection setting that holds a login whatever the driver says.
CONNECTION_LOGIN = PASSWORD_FIELD

_PRIVATE = stat.S_IRUSR | stat.S_IWUSR

#: Whose login an entry is (module docstring).
DEVICE, PLUGIN, PROJECT = "device", "plugin", "project"
Owner = tuple[str, str, str]

#: The project's mesh key: its owner and the setting it is in.
ISC_OWNER: Owner = (PROJECT, "isc", "")
ISC_FIELD = "auth_key"

#: An entry's key: (library id or None for the running project, *owner).
_Key = tuple[str | None, str, str, str]


def device_owner(device_id: str, driver: str) -> Owner:
    return (DEVICE, device_id, driver)


def plugin_owner(plugin_id: str, item: str = "") -> Owner:
    return (PLUGIN, plugin_id, item)


class LoginFileUnreadable(RuntimeError):
    """The login file is on disk but cannot be opened, so nothing is written
    over it."""


@dataclass(frozen=True)
class _Entry:
    fields: dict[str, str]
    removed_at: datetime | None = None


@dataclass(frozen=True)
class FoundLogin:
    """The logins one owner in a project holds: setting name to value. For a
    device, ``name`` is its id and ``qualifier`` its driver; for a plugin, its
    id and the item (``""`` for its own settings); for the mesh key, ``isc``
    and ``""``."""

    name: str
    qualifier: str
    fields: dict[str, str]
    kind: str = DEVICE

    @property
    def owner(self) -> Owner:
        return (self.kind, self.name, self.qualifier)


def _usable(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value != SAVED_LOGIN


KIND_ORDER = {DEVICE: 0, PLUGIN: 1, PROJECT: 2}


def _sort_key(key: _Key) -> tuple[int, str, int, str, str]:
    library, kind, name, qualifier = key
    return (0 if library is None else 1, library or "", KIND_ORDER.get(kind, 9), name, qualifier)


Presence = Callable[[Owner], bool] | Iterable[Any]


def _presence(owners: Presence) -> Callable[[Owner], bool]:
    """``owners`` as a test: a callable already is one; otherwise a set of
    owners, where a bare (device id, driver) pair is a device."""
    if callable(owners):
        return owners
    wanted: set[Owner] = set()
    for owner in owners:
        owner = tuple(owner)
        wanted.add((DEVICE, owner[0], owner[1]) if len(owner) == 2 else owner)  # type: ignore[arg-type]
    return wanted.__contains__


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
        return self.secret(device_owner(device_id, driver), field, library=library)

    def secret(self, owner: Owner, field: str, *, library: str | None = None) -> str | None:
        """The saved value of ``field`` for ``owner`` (any kind), or None."""
        self._load()
        entry = self._entries.get((library, *owner))
        if entry is None:
            return None
        value = entry.fields.get(field)
        return value if _usable(value) else None

    def plugin_items(self, plugin_id: str, *, library: str | None = None) -> dict[str, dict[str, str]]:
        """Every login saved for ``plugin_id``, by item: field to value."""
        self._load()
        return {
            qualifier: dict(entry.fields)
            for (lib, kind, name, qualifier), entry in self._entries.items()
            if lib == library and kind == PLUGIN and name == plugin_id and entry.fields
        }

    def libraries(self) -> set[str]:
        """The library ids that have logins filed."""
        self._load()
        return {key[0] for key in self._entries if key[0] is not None}

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
        for kind, listed in (PAYLOAD_LISTS.items() if isinstance(payload, dict) else ()):
            items = payload.get(listed)
            for item in items if isinstance(items, list) else []:
                entry = _entry_from(item, kind)
                if entry is None:
                    log.warning("Skipped an unreadable entry in %s", where)
                    continue
                key, value = entry
                entries[key] = value
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
                key = (library, *login.owner)
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

    def adopt_library(self, library: str, owners: Presence) -> None:
        """A library copy is being opened: file its own logins for ``owners``
        (the copy's devices, plugin items and mesh key; ``in_project``) as the
        running project's, where the running project has nothing saved for
        that setting. The running project's always win."""
        present = _presence(owners)
        with self._lock:
            self._load()
            found = [
                FoundLogin(key[2], key[3], dict(entry.fields), key[1])
                for key, entry in sorted(self._entries.items(), key=lambda i: _sort_key(i[0]))
                if key[0] == library and entry.fields and present(key[1:])
            ]
            if found:
                self.store(found, library=None, typed=False)

    def file_library(self, library: str, owners: Presence) -> None:
        """The running project is being saved to the library as ``library``:
        its logins for ``owners`` become that copy's, replacing whatever the
        copy had."""
        present = _presence(owners)
        with self._lock:
            self._load()
            entries = {k: v for k, v in self._entries.items() if k[0] != library}
            for key, entry in self._entries.items():
                if key[0] is None and entry.fields and present(key[1:]):
                    entries[(library, *key[1:])] = _Entry(dict(entry.fields), None)
            if entries != self._entries:
                self._commit(entries)

    def copy_library(self, source: str, target: str) -> None:
        """A library copy is duplicated: ``target`` gets ``source``'s logins."""
        with self._lock:
            self._load()
            entries = {k: v for k, v in self._entries.items() if k[0] != target}
            for key, entry in self._entries.items():
                if key[0] == source:
                    entries[(target, *key[1:])] = entry
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

    def sweep(self, present: Presence, now: datetime | None = None) -> None:
        """Bring each running-project entry's removal date in line with the
        project. Library copies' logins are not touched.

        ``present`` says which owners the project has (``in_project``; a set
        of (device id, driver) pairs is a project's devices). An entry not
        among them is dated removed now, unless it already was; one dated
        ``REMOVED_KEEP`` ago or more is deleted; one among them is current.
        Writes only when something changed, and never over a file it cannot
        open.
        """
        is_present = _presence(present)
        with self._lock:
            self._load()
            if self._unreadable is not None or not self._entries:
                return
            now = now or datetime.now(timezone.utc)
            entries: dict[_Key, _Entry] = {}
            changed = False
            for key, entry in self._entries.items():
                if key[0] is not None:
                    entries[key] = entry
                    continue
                if is_present(key[1:]):
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

    def backup_copy(self, owners: Presence) -> str | None:
        """The running project's logins for ``owners`` (``in_project``), as a
        login file sealed with this system's key, for a project backup to
        hold. The key itself never goes with it, so the copy opens on this
        system only. None when none of them has a login saved or the login
        file cannot be opened. Never makes a key."""
        present = _presence(owners)
        with self._lock:
            self._load()
            if self._unreadable is not None or not self.key_path.exists():
                return None
            entries: dict[_Key, _Entry] = {
                key: _Entry(dict(entry.fields), None)
                for key, entry in self._entries.items()
                if key[0] is None and entry.fields and present(key[1:])
            }
            if not entries:
                return None
            return self._envelope(entries, self._key())

    def restore_copy(self, text: str, owners: Presence, *, where: str) -> int:
        """A project backup's copy (``backup_copy``) restored on this system:
        each of its logins for ``owners`` (``in_project`` of the restored
        project) that this system has nothing saved for is filed. This
        system's own logins always win. A copy that does not open with this
        system's key, such as one made on another system, files nothing.
        Returns how many settings were filed; never raises."""
        present = _presence(owners)
        try:
            with self._lock:
                self._load()
                if self._unreadable is not None:
                    log.error("The device logins in %s were not restored: %s", where, self._unreadable)
                    return 0
                copy_entries = self._open(text, where)
                found: list[FoundLogin] = []
                filled = 0
                for key, entry in sorted(copy_entries.items(), key=lambda i: _sort_key(i[0])):
                    if key[0] is not None or not present(key[1:]) or not entry.fields:
                        continue
                    current = self._entries.get(key)
                    have = set(current.fields) if current is not None else set()
                    filled += len(set(entry.fields) - have)
                    found.append(FoundLogin(key[2], key[3], dict(entry.fields), key[1]))
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

        payload: dict[str, list[dict[str, Any]]] = {"devices": []}
        for entry_key in sorted(entries, key=_sort_key):
            library, kind, name, qualifier = entry_key
            entry = entries[entry_key]
            item: dict[str, Any] = {
                **owner_fields(kind, name, qualifier),
                "fields": dict(sorted(entry.fields.items())),
                "removed_at": entry.removed_at.isoformat() if entry.removed_at else None,
            }
            if library is not None:
                item["library"] = library
            payload.setdefault(PAYLOAD_LISTS[kind], []).append(item)
        sealed = Fernet(key).encrypt(json.dumps(payload, sort_keys=True).encode("utf-8"))
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


#: Each kind's list in the login file's payload, and the names its owner is
#: written under there and in a sealed export.
PAYLOAD_LISTS = {DEVICE: "devices", PLUGIN: "plugins", PROJECT: "project"}
_OWNER_NAMES = {DEVICE: ("device", "driver"), PLUGIN: ("plugin", "item"), PROJECT: ("section", None)}


def owner_fields(kind: str, name: str, qualifier: str) -> dict[str, str]:
    name_key, qualifier_key = _OWNER_NAMES[kind]
    return {name_key: name, **({qualifier_key: qualifier} if qualifier_key else {})}


def owner_from(item: dict[str, Any], kind: str) -> Owner | None:
    """The owner a payload item names (``owner_fields``), or None."""
    name_key, qualifier_key = _OWNER_NAMES[kind]
    name = item.get(name_key)
    qualifier = item.get(qualifier_key, "") if qualifier_key else ""
    if not (isinstance(name, str) and name and isinstance(qualifier, str)):
        return None
    if kind == DEVICE and not qualifier:
        return None
    return (kind, name, qualifier)


def _entry_from(item: Any, kind: str = DEVICE) -> tuple[_Key, _Entry] | None:
    if not isinstance(item, dict):
        return None
    owner = owner_from(item, kind)
    fields = item.get("fields")
    library = item.get("library")
    if owner is None or not isinstance(fields, dict):
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
    return (library, *owner), _Entry(kept, removed_at)


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


def login_settings_in(driver_id: str, keys: Iterable[Any]) -> list[str]:
    """Which of ``keys`` (setting names of a device on ``driver_id``) hold a
    login, in the order given: each one in ``login_fields``, and, for a driver
    that is not installed, any setting named like a credential
    (``log_redaction.is_secret_key``), a username aside, since nothing else can
    say which of that device's settings are logins."""
    fields = login_fields(driver_id)
    installed = get_driver_class(driver_id) is not None
    return [
        str(key) for key in keys
        if str(key) in fields or (
            not installed and is_secret_key(key) and str(key).lower() not in IDENTITY_KEYS
        )
    ]


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


def plugin_configs(project: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every plugin in ``project`` (a ProjectConfig, or a project file read as
    JSON) with its config, in project order."""
    plugins = project.get("plugins") if isinstance(project, dict) else getattr(project, "plugins", None)
    if not isinstance(plugins, dict):
        return []
    found: list[tuple[str, dict[str, Any]]] = []
    for plugin_id, entry in plugins.items():
        config = entry.get("config") if isinstance(entry, dict) else getattr(entry, "config", None)
        if isinstance(plugin_id, str) and isinstance(config, dict):
            found.append((plugin_id, config))
    return found


def _set_plugin_config(project: Any, plugin_id: str, config: dict[str, Any]) -> None:
    entry = project["plugins"][plugin_id] if isinstance(project, dict) else project.plugins[plugin_id]
    if isinstance(entry, dict):
        entry["config"] = config
    else:
        entry.config = config


def isc_key(project: Any) -> Any:
    """``project``'s mesh key as it holds it (the key, the marker, or empty)."""
    isc = project.get("isc") if isinstance(project, dict) else getattr(project, "isc", None)
    return isc.get(ISC_FIELD) if isinstance(isc, dict) else getattr(isc, ISC_FIELD, None)


def _set_isc_key(project: Any, value: str) -> None:
    isc = project.get("isc") if isinstance(project, dict) else project.isc
    if isinstance(isc, dict):
        isc[ISC_FIELD] = value
    else:
        isc.auth_key = value


def find_plugin_logins(plugin_id: str, config: Any) -> list[FoundLogin]:
    """The logins the installed plugin ``plugin_id`` declares that ``config``
    holds in the clear (``core.plugin_secrets``). None for a plugin that is
    not installed: nothing can say which of its settings are logins."""
    found = plugin_secrets.find(config, plugin_secrets.declared_for(plugin_id))
    return [FoundLogin(plugin_id, item, fields, PLUGIN) for item, fields in sorted(found.items())]


def find_logins(project: Any, *, skip: Callable[[str], bool] | None = None) -> list[FoundLogin]:
    """The logins ``project`` holds in the clear: device by device, then each
    plugin's, then the mesh key. A device's value for a setting is the one it
    dials: its connection row's over its config's. ``skip`` leaves out a
    device by id."""
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
    for plugin_id, config in plugin_configs(project):
        found.extend(find_plugin_logins(plugin_id, config))
    key = isc_key(project)
    if _usable(key):
        found.append(FoundLogin(ISC_OWNER[1], ISC_OWNER[2], {ISC_FIELD: key}, PROJECT))
    return found


def _marked_copy(project: Any, found: list[FoundLogin]) -> Any:
    """A copy of ``project`` with the marker in every place a login in
    ``found`` was held in the clear."""
    marked = _marked_devices(project, [f for f in found if f.kind == DEVICE])
    by_plugin: dict[str, dict[str, dict[str, str]]] = {}
    for f in found:
        if f.kind == PLUGIN:
            by_plugin.setdefault(f.name, {})[f.qualifier] = f.fields
    for plugin_id, config in plugin_configs(marked):
        if plugin_id in by_plugin:
            _set_plugin_config(marked, plugin_id, plugin_secrets.marked(config, by_plugin[plugin_id]))
    if any(f.kind == PROJECT for f in found) and _usable(isc_key(marked)):
        _set_isc_key(marked, SAVED_LOGIN)
    return marked


def _marked_devices(project: Any, found: list[FoundLogin]) -> Any:
    """A copy of ``project`` with the marker in each device setting ``found``
    names."""
    wanted = {(f.name, f.qualifier): set(f.fields) for f in found}

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
    if not _stored(found, logins, typed=typed, library=library, where=where):
        return project
    return _marked_copy(project, found)


def _stored(
    found: list[FoundLogin], logins: DeviceLogins, *, typed: bool, library: str | None, where: str,
) -> bool:
    """File ``found``, read the file back and check each one is there. False
    (and the reason logged) on any failure, when nothing may be marked."""
    try:
        logins.store(found, library=library, typed=typed)
        logins.verify()
        for login in found:
            for name in login.fields:
                if logins.secret(login.owner, name, library=library) is None:
                    raise LoginFileUnreadable(f"The login file has no {name} for {login.name}.")
    except Exception as exc:  # leave every login where it is, whatever failed
        log.error(
            "Logins in %s stay in it for now: they could not be saved on this system (%s)",
            where, exc,
        )
        return False
    log.info(
        "Moved %d login(s) out of %s into this system's login file",
        sum(len(f.fields) for f in found), where,
    )
    return True


def move_plugin_logins(
    plugin_id: str,
    config: dict[str, Any],
    logins: DeviceLogins,
    *,
    current: dict[str, Any] | None = None,
    typed: bool = True,
) -> dict[str, Any]:
    """One plugin's ``config`` with each login it declares moved into the
    running project's logins and the marker left in its place: the config a
    plugin saves itself (``api.save_config``), which reaches the project
    without passing ``move_logins``. ``config`` itself when nothing moved or
    the move could not finish.

    ``current`` is the config the project holds now. A login it marks that
    this system has nothing behind was handed to the plugin as ``""``
    (``filled_plugin_config``); handed back still empty, it keeps the marker,
    so saving something else does not turn "needs entering here" into "not
    set"."""
    found = find_plugin_logins(plugin_id, config)
    out = config
    if found:
        if not _stored(found, logins, typed=typed, library=None, where=f"plugin '{plugin_id}'"):
            return config
        out = plugin_secrets.marked(config, {f.qualifier: f.fields for f in found})
    if current is None:
        return out
    secrets = plugin_secrets.declared_for(plugin_id)
    still_waiting = [
        (item, field) for item, field, _ in plugin_secrets.places(out, secrets)
        if plugin_secrets.get_field(plugin_secrets.holder_of(out, item), field) == ""
        and plugin_secrets.get_field(plugin_secrets.holder_of(current, item), field) == SAVED_LOGIN
        and logins.secret(plugin_owner(plugin_id, item), field) is None
    ]
    if not still_waiting:
        return out
    out = copy.deepcopy(out)
    for item, field in still_waiting:
        plugin_secrets.set_field(plugin_secrets.holder_of(out, item), field, SAVED_LOGIN)
    return out


# --- putting them back -------------------------------------------------------------


class StoreView:
    """The saved logins a project gets from this system: ``libraries`` in
    order (None, the running project's, by default), the first that has a
    setting winning, as opening a library copy gives them."""

    def __init__(self, logins: DeviceLogins, *libraries: str | None) -> None:
        self._logins = logins
        self._libraries = libraries or (None,)

    def secret(self, owner: Owner, field: str) -> str | None:
        for library in self._libraries:
            value = self._logins.secret(owner, field, library=library)
            if value is not None:
                return value
        return None

    def plugin_items(self, plugin_id: str) -> dict[str, dict[str, str]]:
        merged: dict[str, dict[str, str]] = {}
        for library in reversed(self._libraries):
            for item, fields in self._logins.plugin_items(plugin_id, library=library).items():
                merged.setdefault(item, {}).update(fields)
        return merged


class FoundView:
    """The logins in ``found`` (a sealed export's, say), looked up as a
    ``StoreView`` is."""

    def __init__(self, found: Iterable[FoundLogin]) -> None:
        self._fields: dict[Owner, dict[str, str]] = {}
        for f in found:
            self._fields.setdefault(f.owner, {}).update(f.fields)

    def secret(self, owner: Owner, field: str) -> str | None:
        value = self._fields.get(owner, {}).get(field)
        return value if _usable(value) else None

    def plugin_items(self, plugin_id: str) -> dict[str, dict[str, str]]:
        return {
            owner[2]: dict(fields) for owner, fields in self._fields.items()
            if owner[0] == PLUGIN and owner[1] == plugin_id
        }


def put_back(project: dict[str, Any], view: StoreView | FoundView) -> dict[str, Any] | None:
    """A copy of ``project`` (read as JSON) with each marker replaced by its
    saved login in ``view``: every device's, every plugin's and the mesh
    key's; None when nothing changed. A marker with no saved login stays."""
    marked = copy.deepcopy(project)
    changed = False
    for found in saved_logins_of(marked, view):
        if found.kind == DEVICE:
            continue
        changed = True
        if found.kind == PROJECT:
            _set_isc_key(marked, found.fields[ISC_FIELD])
            continue
        config = dict(plugin_configs(marked))[found.name]
        holder = plugin_secrets.holder_of(config, found.qualifier)
        for field, value in found.fields.items():
            plugin_secrets.set_field(holder, field, value)
    connections = marked.get("connections") if isinstance(marked.get("connections"), dict) else {}

    def restore(holder: Any, device_id: str, driver: str) -> None:
        nonlocal changed
        if not isinstance(holder, dict):
            return
        for name in marked_fields(holder):
            value = view.secret(device_owner(device_id, driver), name)
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


def _put_back_file(path: Path, view: StoreView) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(raw, dict):
        return
    restored = put_back(raw, view)
    if restored is not None:
        atomic_write_text(path, json.dumps(restored, indent=4, ensure_ascii=False))
        log.warning("Put the saved logins back into %s for the earlier version", path)


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
                try:
                    _put_back_file(avc, StoreView(logins, None, avc.parent.name))
                except OSError:
                    log.exception("Could not put the device logins back into %s", avc)
    except Exception:
        log.exception("Device logins could not be put back into the Project Library before the rollback")

    with _project_save_lock:
        try:
            if logins is not None and project_path is not None and Path(project_path).exists():
                _put_back_file(Path(project_path), StoreView(logins))
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


def in_project(project: Any) -> Callable[[Owner], bool]:
    """Whether an owner is in ``project`` (a ProjectConfig, or a project file
    read as JSON): a device by id and driver, a plugin's own settings while
    the plugin is in it, a plugin's list item while its config holds that
    item, and the mesh key always. Read from the project alone, so it is
    right for a plugin that is not installed."""
    devices = {device_owner(d, drv) for d, drv in project_devices(project)}
    configs = dict(plugin_configs(project))

    def present(owner: Owner) -> bool:
        kind, name, qualifier = owner
        if kind == DEVICE:
            return owner in devices
        if kind == PLUGIN:
            return name in configs and plugin_secrets.holder_of(configs[name], qualifier) is not None
        return owner == ISC_OWNER

    return present


def saved_logins_of(project: Any, view: StoreView | FoundView) -> list[FoundLogin]:
    """The saved logins behind the markers in ``project`` (a ProjectConfig, or
    a project file read as JSON) that ``view`` has: each device setting that
    holds the marker in its config or its connection row, each plugin login
    that holds it where ``view`` filed one, and the mesh key."""
    found: list[FoundLogin] = []
    for row in _rows_of(project):
        values: dict[str, str] = {}
        for name in sorted(set(marked_fields(row.config)) | set(marked_fields(row.connection))):
            value = view.secret(device_owner(row.device_id, row.driver), name)
            if _usable(value):
                values[name] = value
        if values:
            found.append(FoundLogin(row.device_id, row.driver, values))
    for plugin_id, config in plugin_configs(project):
        items = view.plugin_items(plugin_id)
        by_item: dict[str, dict[str, str]] = {}
        for item, field in plugin_secrets.marked_places(config, sorted(items.items())):
            value = view.secret(plugin_owner(plugin_id, item), field)
            if _usable(value):
                by_item.setdefault(item, {})[field] = value
        found.extend(FoundLogin(plugin_id, item, fields, PLUGIN) for item, fields in by_item.items())
    if isc_key(project) == SAVED_LOGIN:
        value = view.secret(ISC_OWNER, ISC_FIELD)
        if _usable(value):
            found.append(FoundLogin(ISC_OWNER[1], ISC_OWNER[2], {ISC_FIELD: value}, PROJECT))
    return found


def filled_plugin_config(plugin_id: str, config: Any, logins: DeviceLogins) -> Any:
    """``config`` as the plugin ``plugin_id`` is handed it: each marker
    replaced by the running project's saved login, and ``""`` for any marker
    this system has nothing behind. Never the marker: a plugin has no refusal
    to dial, as a device does, and would use the marker itself as the value
    (a Present display key anyone could type), where ``""`` reads as not set
    (Present then makes a new key). Returned as it is when nothing is marked."""
    return plugin_secrets.fill(config, logins.plugin_items(plugin_id).items())


def plugin_logins_waiting(plugin_id: str, config: Any, logins: DeviceLogins) -> list[str]:
    """The logins the installed plugin ``plugin_id`` declares that ``config``
    marks as saved and this system does not have, the ones the plugin makes
    itself aside: somebody has to enter them here. As the Programmer names
    them (``plugin_secrets.display_path``)."""
    secrets = plugin_secrets.declared_for(plugin_id)
    return [
        plugin_secrets.display_path(item, field)
        for item, field in plugin_secrets.waiting(
            config, secrets, lambda item, field: logins.secret(plugin_owner(plugin_id, item), field),
        )
    ]


def saved_isc_key(project: Any, logins: DeviceLogins) -> str:
    """The mesh key ``project`` runs with: its saved key when it holds the
    marker (``""`` when this system has none: the mesh then refuses every
    connection, as with no key at all), else the key as it holds it."""
    key = isc_key(project)
    if key == SAVED_LOGIN:
        return logins.secret(ISC_OWNER, ISC_FIELD) or ""
    return key if isinstance(key, str) else ""


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
