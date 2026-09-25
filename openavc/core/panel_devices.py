"""Panel devices: the tablets and browsers approved to run this system's panel.

A panel holds no credential. Before this module, anything that could reach the
server's port could open the panel and run the space. Now a panel device is
something the programmer approves once. The first time a browser or the panel
app checks in it becomes a *pending* record with a six-digit code, the
Programmer shows the code, and Approve turns it into an *approved* record that
is remembered until it is revoked. The device is recognised by a cookie the
check-in route sets; ``openavc/api/panel_access.py`` owns the cookie, the
admission rule and the routes, and this module owns the records.

**What is on disk and what is not.** Only approved panels are written, to
``panel_devices.json`` in the data directory, atomically and mode 0600 the way
``system.json`` is. Pending and denied records live in memory: a restart
forgets them, the tablet's next check-in makes a new one with a new code, and
nothing else is lost. A pending record expires 15 minutes after its last
check-in and a denied one 24 hours after the denial, after which the device
may ask again. There are at most 50 pending records per instance and 3 per
source address; past either cap a check-in is answered ``unavailable`` and
nothing is stored.

**The secret.** Each record carries a 256-bit secret the cookie presents,
stored as ``hash_api_key`` (a fast salted SHA-256, because it is checked on
every socket handshake and the entropy is the defence; see
``utils/password_hash.py``). A pending record has its own throwaway secret and
approval issues a new one, so the value a device held while waiting never
becomes a credential. The approved secret reaches the device on its next
check-in, which still presents the pending one: that handoff is remembered in
memory only, for 15 minutes, then dropped. A restart inside that window sends
the device back to pending and the programmer approves it again.

**Access mode.** ``panels.access`` in ``system.json`` is ``approved`` or
``open``; ``access_mode()`` reads it and fails closed, so anything other than
the exact word ``open`` means approved.

Pure stdlib plus ``utils/password_hash`` and the config reader; no FastAPI and
no engine. The socket gate and the Programmer push are in the API layer.
"""

from __future__ import annotations

import json
import os
import secrets
import string
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openavc.utils.logger import get_logger
from openavc.utils.password_hash import hash_api_key, verify_api_key

log = get_logger(__name__)

FILE_NAME = "panel_devices.json"
FILE_VERSION = 1

ACCESS_APPROVED = "approved"
ACCESS_OPEN = "open"
ACCESS_VALUES = (ACCESS_APPROVED, ACCESS_OPEN)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"

# The close code a refused panel socket receives, after the handshake was
# accepted so a browser can read it (a refusal before accept arrives as 1006,
# which a page cannot tell from a dropped network).
CLOSE_CODE_NOT_APPROVED = 4010
CLOSE_REASON_NOT_APPROVED = "Panel not approved"

PENDING_TTL_SECONDS = 15 * 60
DENIED_TTL_SECONDS = 24 * 60 * 60
HANDOFF_TTL_SECONDS = 15 * 60
MAX_PENDING = 50
MAX_PENDING_PER_ADDRESS = 3
LAST_SEEN_WRITE_INTERVAL_SECONDS = 5 * 60

# 400 days, Chromium's cap on a cookie lifetime. Re-issued on use, so an
# active panel never expires.
COOKIE_MAX_AGE_SECONDS = 34_560_000
# A pending cookie keeps the same code across reloads; a day is plenty.
PENDING_COOKIE_MAX_AGE_SECONDS = 24 * 60 * 60
COOKIE_REISSUE_AFTER_SECONDS = 24 * 60 * 60

_ID_ALPHABET = string.ascii_lowercase + string.digits
_USER_AGENT_MAX = 256
_NAME_MAX = 80


def access_mode() -> str:
    """The live ``panels.access`` setting, failing closed.

    Anything other than the exact word ``open`` is treated as approved: a
    hand-edited file with a typo must not open the panel to the network.
    """
    from openavc.system_config import get_system_config

    raw = get_system_config().get("panels", "access", ACCESS_APPROVED)
    return ACCESS_OPEN if raw == ACCESS_OPEN else ACCESS_APPROVED


def _now_iso() -> str:
    return datetime.fromtimestamp(time.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(text: str) -> float | None:
    """Seconds since the epoch for one of our own timestamps, or None."""
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        ).timestamp()
    except (TypeError, ValueError):
        return None


def new_device_id() -> str:
    return "pd_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(10))


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def format_code(number: int) -> str:
    """Six digits shown as ``482-915``."""
    return f"{number // 1000:03d}-{number % 1000:03d}"


def platform_label(user_agent: str) -> str:
    """What kind of device this is, from its user agent, for a person to read.

    A label, not an identification: it is what the Programmer shows beside the
    code and the address so the installer can tell the tablet on the wall from
    the phone in their pocket. An iPad that asks for desktop pages sends a Mac
    user agent, which is why that label names both.
    """
    ua = user_agent or ""
    if "iPad" in ua:
        return "iPad"
    if "iPhone" in ua:
        return "iPhone"
    if "Silk" in ua:
        return "Fire tablet"
    if "Android" in ua:
        return "Android phone" if "Mobile" in ua else "Android tablet"
    if "Windows" in ua:
        return "Windows PC"
    if "CrOS" in ua:
        return "Chromebook"
    if "Macintosh" in ua:
        return "Mac or iPad"
    if "Linux" in ua:
        return "Linux PC"
    return "Browser" if ua else "Unknown device"


def clean_name(name: Any) -> str:
    """A device name as stored: trimmed, one line, bounded."""
    if not isinstance(name, str):
        return ""
    return " ".join(name.split())[:_NAME_MAX]


@dataclass
class PanelDevice:
    """One remembered device. ``public()`` is what leaves the server."""

    id: str
    status: str
    name: str = ""
    platform: str = ""
    user_agent: str = ""
    address: str = ""
    secret_hash: str = ""
    code: str = ""
    first_seen: str = ""
    approved_at: str | None = None
    approved_by: str = ""
    last_seen: str = ""
    cookie_issued_at: str = ""
    denied_at: str | None = None

    # In memory only, never written: expiry clocks and the approval handoff.
    last_checkin: float = field(default=0.0, repr=False)
    status_since: float = field(default=0.0, repr=False)
    handoff_hash: str = field(default="", repr=False)
    handoff_secret: str = field(default="", repr=False)
    handoff_until: float = field(default=0.0, repr=False)
    last_seen_written: float = field(default=0.0, repr=False)

    def default_name(self) -> str:
        return f"{self.platform} at {self.address}" if self.address else self.platform

    def public(self) -> dict[str, Any]:
        """The record as the Programmer sees it. Never a hash, never a secret."""
        out: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "name": self.name,
            "platform": self.platform,
            "address": self.address,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }
        if self.status == STATUS_PENDING or self.status == STATUS_DENIED:
            out["code"] = self.code
        if self.status == STATUS_APPROVED:
            out["approved_at"] = self.approved_at
            out["approved_by"] = self.approved_by
        if self.status == STATUS_DENIED:
            out["denied_at"] = self.denied_at
        return out

    def to_disk(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "platform": self.platform,
            "user_agent": self.user_agent,
            "address": self.address,
            "secret_hash": self.secret_hash,
            "first_seen": self.first_seen,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "last_seen": self.last_seen,
            "cookie_issued_at": self.cookie_issued_at,
        }

    @classmethod
    def from_disk(cls, raw: Any) -> PanelDevice | None:
        """An approved record read back, or None when the entry is unusable."""
        if not isinstance(raw, dict):
            return None
        device_id = raw.get("id")
        secret_hash = raw.get("secret_hash")
        if not isinstance(device_id, str) or not device_id.startswith("pd_"):
            return None
        if not isinstance(secret_hash, str) or not secret_hash:
            return None

        def text(key: str) -> str:
            value = raw.get(key)
            return value if isinstance(value, str) else ""

        approved_at = raw.get("approved_at")
        return cls(
            id=device_id,
            status=STATUS_APPROVED,
            name=clean_name(text("name")),
            platform=text("platform"),
            user_agent=text("user_agent")[:_USER_AGENT_MAX],
            address=text("address"),
            secret_hash=secret_hash,
            first_seen=text("first_seen"),
            approved_at=approved_at if isinstance(approved_at, str) else None,
            approved_by=text("approved_by"),
            last_seen=text("last_seen"),
            cookie_issued_at=text("cookie_issued_at"),
        )


@dataclass(frozen=True)
class CheckIn:
    """What a check-in decided, for the route to turn into a response."""

    status: str  # approved | pending | denied | unavailable
    device: PanelDevice | None = None
    set_cookie: str | None = None
    cookie_max_age: int = 0
    created: bool = False


def cookie_value(device_id: str, secret: str) -> str:
    return f"{device_id}.{secret}"


def split_cookie_value(value: str | None) -> tuple[str, str] | None:
    if not isinstance(value, str) or "." not in value:
        return None
    device_id, secret = value.split(".", 1)
    if not device_id.startswith("pd_") or not secret:
        return None
    return device_id, secret


class PanelDeviceStore:
    """The records, their expiry, and the one file the approved ones live in."""

    def __init__(self, path: Path):
        self._path = Path(path)
        self._devices: dict[str, PanelDevice] = {}
        self._unreadable = False

    @property
    def path(self) -> Path:
        return self._path

    # --- persistence ---

    def load(self) -> None:
        """Read the approved records. A file that cannot be read is left in
        place and set aside on the next save, so a bad byte never silently
        empties the approvals."""
        self._devices = {
            k: v for k, v in self._devices.items() if v.status != STATUS_APPROVED
        }
        self._unreadable = False
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("Cannot read %s: %s", self._path, e)
            self._unreadable = True
            return
        entries = data.get("devices") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            log.error("Cannot read %s: not a panel device file", self._path)
            self._unreadable = True
            return
        for raw in entries:
            device = PanelDevice.from_disk(raw)
            if device is None:
                log.warning("Skipping an unreadable entry in %s", self._path)
                continue
            self._devices[device.id] = device

    def save(self) -> None:
        """Write the approved records atomically (temp file + replace, 0600)."""
        if self._unreadable and self._path.exists():
            aside = self._path.with_name(self._path.name + ".unreadable")
            try:
                self._path.replace(aside)
                log.warning("Set the unreadable %s aside as %s", self._path.name, aside.name)
            except OSError as e:
                log.error("Could not set %s aside: %s", self._path, e)
            self._unreadable = False
        approved = sorted(
            (d for d in self._devices.values() if d.status == STATUS_APPROVED),
            key=lambda d: (d.approved_at or "", d.id),
        )
        content = json.dumps(
            {"version": FILE_VERSION, "devices": [d.to_disk() for d in approved]},
            indent=2,
        ) + "\n"
        fd = None
        tmp_path = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._path.parent), suffix=".tmp", prefix=".panel_devices_"
            )
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            fd = None
            os.replace(tmp_path, str(self._path))
            tmp_path = None
        except OSError as e:
            log.error("Failed to save %s: %s", self._path, e)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # --- reading ---

    def get(self, device_id: str) -> PanelDevice | None:
        return self._devices.get(device_id)

    def list_devices(self) -> dict[str, list[dict[str, Any]]]:
        """The three lists the Programmer shows, newest request first."""
        pending = sorted(
            (d for d in self._devices.values() if d.status == STATUS_PENDING),
            key=lambda d: d.first_seen,
        )
        approved = sorted(
            (d for d in self._devices.values() if d.status == STATUS_APPROVED),
            key=lambda d: (d.name.lower(), d.id),
        )
        denied = sorted(
            (d for d in self._devices.values() if d.status == STATUS_DENIED),
            key=lambda d: d.denied_at or "",
        )
        return {
            "pending": [d.public() for d in pending],
            "approved": [d.public() for d in approved],
            "denied": [d.public() for d in denied],
        }

    def verify(self, value: str | None) -> PanelDevice | None:
        """The approved device this cookie value belongs to, or None.

        One dictionary lookup and one salted SHA-256; a wrong value is a
        256-bit miss. Only an approved record's own secret counts here: the
        handoff a freshly approved device still holds is completed by the
        check-in, never by the socket.
        """
        parts = split_cookie_value(value)
        if parts is None:
            return None
        device = self._devices.get(parts[0])
        if device is None or device.status != STATUS_APPROVED:
            return None
        if not verify_api_key(parts[1], device.secret_hash):
            return None
        return device

    def _match(self, value: str | None, now: float) -> tuple[PanelDevice | None, str]:
        """(device, how) for a presented cookie: how is ``secret`` when the
        record's own secret matched, ``handoff`` when the pending secret a
        just-approved device still holds did, else ``""``."""
        parts = split_cookie_value(value)
        if parts is None:
            return None, ""
        device = self._devices.get(parts[0])
        if device is None:
            return None, ""
        if verify_api_key(parts[1], device.secret_hash):
            return device, "secret"
        if (
            device.handoff_hash
            and now < device.handoff_until
            and verify_api_key(parts[1], device.handoff_hash)
        ):
            return device, "handoff"
        return None, ""

    # --- the check-in ---

    def check_in(
        self, value: str | None, *, address: str, user_agent: str
    ) -> CheckIn:
        """Answer one ``GET /api/panel/access`` from a device.

        A device presenting an approved cookie is approved (and its cookie
        re-issued when a day old); one presenting its pending cookie keeps
        its code; a just-approved one collects its real cookie; a denied one
        is told so. Anything else is a new device, which gets a pending
        record and a code, within the caps.
        """
        now = time.monotonic()
        stamp = _now_iso()
        device, how = self._match(value, now)
        if device is not None:
            device.last_checkin = now
            if device.status == STATUS_APPROVED:
                if how == "handoff":
                    secret = device.handoff_secret
                    self._clear_handoff(device)
                    device.cookie_issued_at = stamp
                    device.last_seen = stamp
                    device.last_seen_written = now
                    self.save()
                    return CheckIn(
                        STATUS_APPROVED, device,
                        set_cookie=cookie_value(device.id, secret),
                        cookie_max_age=COOKIE_MAX_AGE_SECONDS,
                    )
                set_cookie = None
                issued = _parse_iso(device.cookie_issued_at)
                if issued is None or time.time() - issued >= COOKIE_REISSUE_AFTER_SECONDS:
                    set_cookie = value
                    device.cookie_issued_at = stamp
                    device.last_seen = stamp
                    device.last_seen_written = now
                    self.save()
                else:
                    self._touch(device, now, stamp)
                return CheckIn(
                    STATUS_APPROVED, device,
                    set_cookie=set_cookie,
                    cookie_max_age=COOKIE_MAX_AGE_SECONDS if set_cookie else 0,
                )
            device.last_seen = stamp
            if device.status == STATUS_DENIED:
                return CheckIn(STATUS_DENIED, device)
            return CheckIn(STATUS_PENDING, device)

        pending = [d for d in self._devices.values() if d.status == STATUS_PENDING]
        if len(pending) >= MAX_PENDING:
            return CheckIn("unavailable")
        if sum(1 for d in pending if d.address == address) >= MAX_PENDING_PER_ADDRESS:
            return CheckIn("unavailable")

        secret = new_secret()
        device = PanelDevice(
            id=self._new_id(),
            status=STATUS_PENDING,
            platform=platform_label(user_agent),
            user_agent=(user_agent or "")[:_USER_AGENT_MAX],
            address=address or "",
            secret_hash=hash_api_key(secret),
            code=self._new_code(),
            first_seen=stamp,
            last_seen=stamp,
            last_checkin=now,
            status_since=now,
        )
        self._devices[device.id] = device
        return CheckIn(
            STATUS_PENDING, device,
            set_cookie=cookie_value(device.id, secret),
            cookie_max_age=PENDING_COOKIE_MAX_AGE_SECONDS,
            created=True,
        )

    def touch(self, device_id: str) -> None:
        """A socket connected as this device: update ``last_seen``, written to
        disk at most once per five minutes."""
        device = self._devices.get(device_id)
        if device is None or device.status != STATUS_APPROVED:
            return
        self._touch(device, time.monotonic(), _now_iso())

    def _touch(self, device: PanelDevice, now: float, stamp: str) -> None:
        device.last_seen = stamp
        if now - device.last_seen_written >= LAST_SEEN_WRITE_INTERVAL_SECONDS:
            device.last_seen_written = now
            self.save()

    # --- the programmer's actions ---

    def approve(
        self, device_id: str, name: str | None, approved_by: str
    ) -> tuple[PanelDevice, str]:
        """Approve a device. Returns the record and its new secret.

        Approval rotates the secret: the record's hash is replaced and the
        pending value the device still holds is kept aside, in memory, for
        the check-in that hands the new one over. The plaintext is returned
        for the one caller that can deliver it in the same response, the
        claim from the panel itself.
        """
        device = self._require(device_id)
        stamp = _now_iso()
        now = time.monotonic()
        cleaned = clean_name(name)
        if device.status == STATUS_APPROVED:
            if cleaned:
                device.name = cleaned
                self.save()
            return device, device.handoff_secret
        secret = new_secret()
        device.handoff_hash = device.secret_hash
        device.handoff_secret = secret
        device.handoff_until = now + HANDOFF_TTL_SECONDS
        device.secret_hash = hash_api_key(secret)
        device.status = STATUS_APPROVED
        device.status_since = now
        device.approved_at = stamp
        device.approved_by = approved_by
        device.last_seen = stamp
        device.last_seen_written = now
        device.denied_at = None
        device.name = cleaned or device.default_name()
        self.save()
        return device, secret

    def mark_delivered(self, device_id: str) -> None:
        """The approved secret reached the device in the same response
        (the claim route); nothing is left to hand over."""
        device = self._devices.get(device_id)
        if device is not None and device.status == STATUS_APPROVED:
            self._clear_handoff(device)
            device.cookie_issued_at = _now_iso()
            self.save()

    def deny(self, device_id: str) -> PanelDevice:
        device = self._require(device_id)
        if device.status == STATUS_APPROVED:
            raise ValueError("An approved panel is revoked, not denied")
        device.status = STATUS_DENIED
        device.status_since = time.monotonic()
        device.denied_at = _now_iso()
        return device

    def revoke(self, device_id: str) -> PanelDevice:
        """Forget a device. An approved one leaves the file; a pending or
        denied one leaves memory. Its next check-in starts over."""
        device = self._require(device_id)
        del self._devices[device_id]
        if device.status == STATUS_APPROVED:
            self.save()
        return device

    def rename(self, device_id: str, name: str) -> PanelDevice:
        device = self._require(device_id)
        cleaned = clean_name(name)
        if not cleaned:
            raise ValueError("A panel needs a name")
        device.name = cleaned
        if device.status == STATUS_APPROVED:
            self.save()
        return device

    def expire(self) -> list[PanelDevice]:
        """Drop pending records idle for 15 minutes and denied ones a day
        old, and forget a handoff nobody collected. Returns what was dropped
        so the caller can tell the Programmer."""
        now = time.monotonic()
        dropped: list[PanelDevice] = []
        for device in list(self._devices.values()):
            if device.status == STATUS_PENDING:
                if now - device.last_checkin >= PENDING_TTL_SECONDS:
                    dropped.append(self._devices.pop(device.id))
            elif device.status == STATUS_DENIED:
                if now - device.status_since >= DENIED_TTL_SECONDS:
                    dropped.append(self._devices.pop(device.id))
            elif device.handoff_hash and now >= device.handoff_until:
                self._clear_handoff(device)
        return dropped

    def _require(self, device_id: str) -> PanelDevice:
        device = self._devices.get(device_id)
        if device is None:
            raise KeyError(device_id)
        return device

    @staticmethod
    def _clear_handoff(device: PanelDevice) -> None:
        device.handoff_hash = ""
        device.handoff_secret = ""
        device.handoff_until = 0.0

    def _new_id(self) -> str:
        while True:
            device_id = new_device_id()
            if device_id not in self._devices:
                return device_id

    def _new_code(self) -> str:
        """Six digits, unique among the live pending records. It identifies
        the request in the Programmer; it is not a secret and is never typed."""
        live = {
            d.code for d in self._devices.values()
            if d.status in (STATUS_PENDING, STATUS_DENIED)
        }
        while True:
            code = format_code(secrets.randbelow(1_000_000))
            if code not in live:
                return code
