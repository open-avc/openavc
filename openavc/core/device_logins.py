"""Device logins saved on this system: the login file, its key, and the marker.

A device's password, or any other setting its driver declares secret, can be
kept here instead of in the project. The project then holds ``SAVED_LOGIN``
in that field, so every copy of the project (a backup, an export, a template,
what the cloud keeps) says that a login is saved without carrying it, and the
login itself stays in one file on this computer.

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

Which saved login a device gets
-------------------------------
A login is filed under the device id AND the driver it was saved for, and a
device gets it only when both match. The address is not part of the rule:
DHCP moves addresses on equipment that is still the same unit. A login filed
for another driver under the same id is never handed to this one.

A device that leaves the project keeps its logins for ``REMOVED_KEEP``, so a
backup from before the removal still finds them when it is restored; after
that they are deleted. A device back in the project with the same driver is
current again. ``DeviceLogins.sweep`` applies both, from the project's device
list.

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

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

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
_FORMAT_VERSION = 1

#: How long a removed device's logins are kept.
REMOVED_KEEP = timedelta(days=90)

_PRIVATE = stat.S_IRUSR | stat.S_IWUSR


class LoginFileUnreadable(RuntimeError):
    """The login file is on disk but cannot be opened, so nothing is written
    over it."""


@dataclass(frozen=True)
class _Entry:
    fields: dict[str, str]
    removed_at: datetime | None = None


def _usable(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value != SAVED_LOGIN


class DeviceLogins:
    """The logins saved on this system, read from disk on first use."""

    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / LOGINS_FILENAME
        self.key_path = Path(data_dir) / KEY_FILENAME
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._loaded = False
        self._unreadable: str | None = None

    # --- reading -------------------------------------------------------------

    @property
    def unreadable(self) -> str | None:
        """Why the login file on disk cannot be opened, or None."""
        self._load()
        return self._unreadable

    def login(self, device_id: str, driver: str, field: str) -> str | None:
        """The saved value of ``field`` for this device and driver, or None."""
        self._load()
        entry = self._entries.get((device_id, driver))
        if entry is None:
            return None
        value = entry.fields.get(field)
        return value if _usable(value) else None

    def _load(self) -> None:
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

    def _read(self) -> dict[tuple[str, str], _Entry]:
        where = f"The device login file {self.path}"
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LoginFileUnreadable(f"{where} cannot be read ({exc}). It is left as it is.") from exc
        if not isinstance(envelope, dict) or envelope.get("format") != _FORMAT:
            raise LoginFileUnreadable(f"{where} is not a device login file. It is left as it is.")
        version = envelope.get("version")
        if version != _FORMAT_VERSION:
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
        entries: dict[tuple[str, str], _Entry] = {}
        for item in payload.get("devices", []) if isinstance(payload, dict) else []:
            entry = _entry_from(item)
            if entry is None:
                log.warning("Skipped an unreadable entry in %s", self.path)
                continue
            key_pair, value = entry
            entries[key_pair] = value
        return entries

    # --- writing -------------------------------------------------------------

    def save(self, device_id: str, driver: str, fields: dict[str, str]) -> None:
        """File ``fields`` (setting name to login) for this device and driver.

        Replaces those fields' saved values and leaves the device's other saved
        fields alone. Raises ValueError for an empty value or the marker
        itself, and LoginFileUnreadable when the file on disk cannot be opened.
        """
        for name, value in fields.items():
            if not _usable(value):
                raise ValueError(f"A saved login cannot be empty or {SAVED_LOGIN!r} ({name}).")
        self._load()
        entries = dict(self._entries)
        current = entries.get((device_id, driver))
        merged = {**(current.fields if current else {}), **fields}
        entries[(device_id, driver)] = _Entry(merged, None)
        self._commit(entries)

    def sweep(self, present: Iterable[tuple[str, str]], now: datetime | None = None) -> None:
        """Bring each entry's removal date in line with the project.

        ``present`` is every (device id, driver) in the project. An entry not
        among them is dated removed now, unless it already was; one dated
        ``REMOVED_KEEP`` ago or more is deleted; one among them is current.
        Writes only when something changed, and never over a file it cannot
        open.
        """
        self._load()
        if self._unreadable is not None or not self._entries:
            return
        now = now or datetime.now(timezone.utc)
        present = set(present)
        entries: dict[tuple[str, str], _Entry] = {}
        changed = False
        for pair, entry in self._entries.items():
            if pair in present:
                if entry.removed_at is not None:
                    entry = _Entry(entry.fields, None)
                    changed = True
            elif entry.removed_at is None:
                entry = _Entry(entry.fields, now)
                changed = True
            elif now - entry.removed_at >= REMOVED_KEEP:
                changed = True
                continue
            entries[pair] = entry
        if changed:
            self._commit(entries)

    def _commit(self, entries: dict[tuple[str, str], _Entry]) -> None:
        """Write ``entries`` to disk, then make them current."""
        if self._unreadable is not None:
            raise LoginFileUnreadable(self._unreadable)
        from cryptography.fernet import Fernet

        key = self._key()
        payload = {
            "devices": [
                {
                    "device": device_id,
                    "driver": driver,
                    "fields": dict(sorted(entry.fields.items())),
                    "removed_at": entry.removed_at.isoformat() if entry.removed_at else None,
                }
                for (device_id, driver), entry in sorted(entries.items())
            ],
        }
        sealed = Fernet(key).encrypt(json.dumps(payload, sort_keys=True).encode("utf-8"))
        envelope = {"format": _FORMAT, "version": _FORMAT_VERSION, "sealed": sealed.decode("ascii")}
        atomic_write_text(self.path, json.dumps(envelope, indent=2) + "\n", mode=_PRIVATE)
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


def _entry_from(item: Any) -> tuple[tuple[str, str], _Entry] | None:
    if not isinstance(item, dict):
        return None
    device_id, driver, fields = item.get("device"), item.get("driver"), item.get("fields")
    if not (isinstance(device_id, str) and isinstance(driver, str) and isinstance(fields, dict)):
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
    return (device_id, driver), _Entry(kept, removed_at)


# --- filling them in -----------------------------------------------------------


def marked_fields(config: dict[str, Any] | None) -> list[str]:
    """The settings in ``config`` that hold the marker."""
    if not isinstance(config, dict):
        return []
    return sorted(str(key) for key, value in config.items() if value == SAVED_LOGIN)


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
