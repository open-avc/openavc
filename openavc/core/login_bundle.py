"""Device logins sealed with a passphrase, for a copy of a project that leaves
this system.

Device logins stay on the system they were entered on (``core.device_logins``),
and every copy of a project holds the marker in their place. When somebody
wants the logins in an export, the export also holds this bundle: the logins
of the project's devices, sealed with a key derived from a passphrase they
type. Nothing opens it without that passphrase, this system and anyone the
file passes through included, and a forgotten passphrase cannot be recovered.

The file
--------
``device_logins.sealed.json`` at the root of the export zip. ``project.avc``
is the same with or without it, so the project format does not change, and a
version that does not know the file leaves it alone::

    {
      "format": "openavc-sealed-logins",
      "version": 1,
      "sealed_at": "2026-11-05T14:03:00+00:00",
      "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": "<base64, 16 bytes>"},
      "sealed": "<Fernet token>"
    }

The key is scrypt over the passphrase (Unicode NFC, UTF-8) with that salt and
those parameters, 32 bytes, used as a Fernet key (AES-128-CBC with an
HMAC-SHA256 tag, as the login file). The sealed payload is
``{"sealed_at": ..., "devices": [{"device": id, "driver": driver, "fields":
{setting: login}}]}``. A wrong passphrase and a damaged file fail the same
tag check, so both read as a passphrase that does not open it. The parameters
travel in the file so a later version can raise them and still open this one;
a reader refuses parameters above ``_MAX_SCRYPT_MEMORY`` so a crafted file
cannot make it allocate without bound.

Bringing them in
----------------
An import reads the bundle's header and holds the bundle for the library copy
it made (``hold``) until somebody enters the passphrase or skips it. The
logins are filed as that copy's own (``core.device_logins``), so opening the
copy gives each device the login this system already had, where it had one,
and the bundle's for the rest.

Sealing again later
-------------------
A copy made on a schedule (a cloud backup) derives the key once, when the
passphrase is set, and keeps the key and its salt, never the passphrase:
``derive_key``, then ``seal_with_key``. A Fernet token is different every
time it is made, so a copy that must stay the same while its logins do keeps
the sealed bytes and seals again only when a login changes.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from openavc.core.device_logins import FoundLogin

#: The bundle's name inside an export zip.
BUNDLE_MEMBER = "device_logins.sealed.json"

#: The shortest passphrase accepted, in characters.
MIN_PASSPHRASE = 8

_FORMAT = "openavc-sealed-logins"
_VERSION = 1

# scrypt cost for a new bundle: 32 MiB of memory, about a tenth of a second
# on a current desktop and under a second on a Raspberry Pi 4.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16

# What a reader accepts: a bundle asking for more is refused before deriving.
_MAX_SCRYPT_MEMORY = 256 * 1024 * 1024
_MAX_SCRYPT_P = 4
_MAX_BUNDLE_BYTES = 2 * 1024 * 1024

#: How long an import holds a bundle waiting for its passphrase.
HOLD_FOR_SECONDS = 30 * 60


class BundleUnreadable(ValueError):
    """The file is not a sealed login bundle this version can open."""


class WrongPassphrase(ValueError):
    """The passphrase does not open the bundle."""


class PassphraseTooShort(ValueError):
    """A passphrase shorter than ``MIN_PASSPHRASE``."""


@dataclass(frozen=True)
class KeyParams:
    """The scrypt parameters and salt a key was derived with."""

    n: int
    r: int
    p: int
    salt: bytes

    def as_json(self) -> dict[str, Any]:
        return {
            "name": "scrypt", "n": self.n, "r": self.r, "p": self.p,
            "salt": base64.b64encode(self.salt).decode("ascii"),
        }


@dataclass(frozen=True)
class BundleInfo:
    """What a bundle says about itself before it is opened."""

    sealed_at: str


def _normalized(passphrase: str) -> bytes:
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def check_passphrase(passphrase: Any) -> str:
    """``passphrase`` when it is long enough to seal with; raises
    PassphraseTooShort otherwise."""
    if not isinstance(passphrase, str) or len(unicodedata.normalize("NFC", passphrase)) < MIN_PASSPHRASE:
        raise PassphraseTooShort(f"The passphrase must be at least {MIN_PASSPHRASE} characters.")
    return passphrase


def new_params() -> KeyParams:
    """Parameters and a fresh salt for a new key."""
    return KeyParams(_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, os.urandom(_SALT_BYTES))


def derive_key(passphrase: str, params: KeyParams) -> bytes:
    """The Fernet key ``passphrase`` gives with ``params``."""
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    raw = Scrypt(salt=params.salt, length=32, n=params.n, r=params.r, p=params.p).derive(
        _normalized(passphrase)
    )
    return base64.urlsafe_b64encode(raw)


def seal_with_key(
    found: Iterable[FoundLogin], key: bytes, params: KeyParams, *, now: datetime | None = None,
) -> bytes:
    """The bundle for ``found``, sealed with ``key`` (from ``derive_key`` with
    ``params``)."""
    from cryptography.fernet import Fernet

    sealed_at = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    devices = [
        {"device": f.device_id, "driver": f.driver, "fields": dict(sorted(f.fields.items()))}
        for f in sorted(found, key=lambda f: (f.device_id, f.driver))
    ]
    payload = json.dumps({"sealed_at": sealed_at, "devices": devices}, sort_keys=True).encode("utf-8")
    bundle = {
        "format": _FORMAT,
        "version": _VERSION,
        "sealed_at": sealed_at,
        "kdf": params.as_json(),
        "sealed": Fernet(key).encrypt(payload).decode("ascii"),
    }
    return (json.dumps(bundle, indent=2) + "\n").encode("utf-8")


def seal(found: Iterable[FoundLogin], passphrase: str, *, now: datetime | None = None) -> bytes:
    """The bundle for ``found``, sealed with ``passphrase``."""
    check_passphrase(passphrase)
    params = new_params()
    return seal_with_key(found, derive_key(passphrase, params), params, now=now)


def _parse(raw: bytes) -> tuple[dict[str, Any], KeyParams]:
    if len(raw) > _MAX_BUNDLE_BYTES:
        raise BundleUnreadable("The sealed logins are larger than any export makes.")
    try:
        bundle = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BundleUnreadable("The sealed logins are not readable.") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != _FORMAT:
        raise BundleUnreadable("The file is not a set of sealed device logins.")
    if bundle.get("version") != _VERSION:
        raise BundleUnreadable(
            "The device logins were sealed by a newer version of OpenAVC. Update this system, "
            "then import the file again."
        )
    kdf = bundle.get("kdf")
    if not isinstance(kdf, dict) or kdf.get("name") != "scrypt":
        raise BundleUnreadable("The sealed logins name a key method this version does not know.")
    n, r, p = kdf.get("n"), kdf.get("r"), kdf.get("p")
    try:
        salt = base64.b64decode(str(kdf.get("salt", "")), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BundleUnreadable("The sealed logins are not readable.") from exc
    if not (
        all(isinstance(v, int) and not isinstance(v, bool) for v in (n, r, p))
        and n >= 2 and n & (n - 1) == 0 and r >= 1 and 1 <= p <= _MAX_SCRYPT_P
        and 128 * n * r <= _MAX_SCRYPT_MEMORY and len(salt) >= 8
    ):
        raise BundleUnreadable("The sealed logins ask for a key this version will not derive.")
    if not isinstance(bundle.get("sealed"), str) or not isinstance(bundle.get("sealed_at"), str):
        raise BundleUnreadable("The sealed logins are not readable.")
    return bundle, KeyParams(n, r, p, salt)


def read_info(raw: bytes) -> BundleInfo:
    """What the bundle says about itself, without the passphrase. Raises
    BundleUnreadable."""
    bundle, _ = _parse(raw)
    return BundleInfo(sealed_at=bundle["sealed_at"])


def open_bundle(raw: bytes, passphrase: str) -> list[FoundLogin]:
    """The logins in the bundle. Raises WrongPassphrase when ``passphrase``
    does not open it, BundleUnreadable when it is not a bundle at all."""
    from cryptography.fernet import Fernet, InvalidToken

    bundle, params = _parse(raw)
    if not isinstance(passphrase, str) or passphrase == "":
        raise WrongPassphrase("That passphrase does not open these logins.")
    try:
        payload = json.loads(Fernet(derive_key(passphrase, params)).decrypt(bundle["sealed"].encode("ascii")))
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise WrongPassphrase("That passphrase does not open these logins.") from exc
    found: list[FoundLogin] = []
    for item in payload.get("devices", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        device_id, driver, fields = item.get("device"), item.get("driver"), item.get("fields")
        if not (isinstance(device_id, str) and isinstance(driver, str) and isinstance(fields, dict)):
            continue
        kept = {str(k): v for k, v in fields.items() if isinstance(v, str) and v}
        if kept:
            found.append(FoundLogin(device_id, driver, kept))
    return found


# --- a bundle waiting for its passphrase ------------------------------------------

_held: dict[str, tuple[bytes, float]] = {}
_held_lock = threading.Lock()


def _forget_expired(now: float) -> None:
    for library_id in [k for k, (_, until) in _held.items() if until <= now]:
        del _held[library_id]


def hold(library_id: str, raw: bytes) -> None:
    """Keep ``raw`` for the library copy ``library_id`` until its passphrase is
    entered, it is skipped, or ``HOLD_FOR_SECONDS`` pass. Held in memory only:
    a restart forgets it, and the file is imported again."""
    now = time.monotonic()
    with _held_lock:
        _forget_expired(now)
        _held[library_id] = (raw, now + HOLD_FOR_SECONDS)


def held(library_id: str) -> bytes | None:
    """The bundle waiting for ``library_id``, or None."""
    with _held_lock:
        _forget_expired(time.monotonic())
        entry = _held.get(library_id)
        return entry[0] if entry else None


def release(library_id: str) -> None:
    """Forget the bundle waiting for ``library_id``, if any."""
    with _held_lock:
        _held.pop(library_id, None)
