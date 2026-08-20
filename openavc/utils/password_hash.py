"""Password hashing at rest — the admin credential, and nothing else yet.

`system.json` used to hold the web admin password exactly as typed. Anything
that could read one file got a working credential: a lifted SD card, a
diagnostic copy someone emails, and — the one that matters most here — **any
plugin or script**, because those run in-process as the service user. Installing
a community plugin should not mean handing over the admin password. On a Pi
appliance the same string is the OS account password, so the blast radius was a
shell as well as a login.

What is stored now is a `hashlib.scrypt` digest and the parameters that produced
it, in one self-describing string:

    scrypt$<n>$<r>$<p>$<salt-b64>$<digest-b64>

Verification reads the parameters back out of the record, so re-tuning the cost
later leaves every already-stored password verifiable. `$` is not in the base64
alphabet, which is what makes the split unambiguous.

scrypt is stdlib (OpenSSL under `hashlib`), so the MIT-only dependency rule is
untouched — that is why it was chosen over the usual argon2/bcrypt packages.

**A value that is not in that form is compared literally.** Two live cases need
it and neither is a legacy tolerance to be removed later: `OPENAVC_PROGRAMMER_PASSWORD`
supplies a plaintext password fresh each boot and is deliberately never persisted,
and an operator provisioning a box by hand may write one into `system.json`
directly (`SystemConfig.migrate_admin_password`, which the server calls at
startup, converts that one on the next start).

Pure stdlib and imports nothing from `openavc`. `system_config` still imports it
inside the function rather than at module scope, because it sits in the import
closure the simulator and the driver validator are held to — see
`tests/test_compiled_protocol_purity.py`.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets

# scrypt cost: the parameters the scrypt paper gives for an interactive login.
# 16 MiB and 22 ms measured on the dev Mac; the smallest box OpenAVC ships on is
# a Raspberry Pi, where it is several times that and still fine for a sign-in.
#
# Raising n buys resistance to cracking a stolen `system.json`, which is the
# threat this module exists for. What holds it here is the other side: a
# password check runs on requests nobody has authenticated yet. A wrong password
# over HTTP is throttled (401s feed the brute-force counter at the strict rate),
# but the WebSocket handshake checks a credential too and no rate limiter sees
# it — `/ws` is skipped, and the middleware never runs on a websocket scope
# regardless. So n is also what an unauthenticated caller can make the box spend
# per attempt, in memory as much as in time, and 16 MiB is where that stays
# survivable on a Pi.
_N = 1 << 14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16

_ALGO = "scrypt"
_PREFIX = _ALGO + "$"


# OpenSSL refuses a derivation whose estimated memory exceeds maxmem, and its
# estimate (128 * r * (n + p + 2)) is a shade over the 128*n*r the parameters
# imply. Ask for double so a future cost bump doesn't trip an opaque ValueError.
def _maxmem(n: int, r: int) -> int:
    return 128 * n * r * 2


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(plaintext: str) -> str:
    """Return the storable form of ``plaintext``.

    An empty password stores as an empty string rather than a digest of nothing:
    "no credential is set" is a state the rest of the system reads by truthiness
    (`is_claimed`, the `if pw` gates in the auth checks), and a digest there
    would claim the instance while matching no password anyone could type.
    """
    if not plaintext:
        return ""
    salt = os.urandom(_SALT_BYTES)
    digest = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=_N, r=_R, p=_P,
        maxmem=_maxmem(_N, _R),
        dklen=_DKLEN,
    )
    return f"{_PREFIX}{_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def looks_hashed(stored: str) -> bool:
    """Whether ``stored`` is one of our digest records rather than a plaintext."""
    return bool(stored) and stored.startswith(_PREFIX)


def verify_password(provided: str, stored: str) -> bool:
    """Timing-safe check of ``provided`` against the stored credential.

    Returns False when nothing is stored. Callers all gate on a configured
    password before asking, so this only ever matters as a backstop — but the
    backstop belongs here, where the answer to "does the empty password match
    the empty credential" is unambiguously no.
    """
    if not stored:
        return False
    if not looks_hashed(stored):
        return secrets.compare_digest(provided, stored)

    parsed = _parse(stored)
    if parsed is None:
        # A record we cannot read authenticates nobody. Left deliberately quiet:
        # this runs on every failed sign-in attempt, and the only way to get
        # here is a hand-mangled system.json.
        return False
    n, r, p, salt, expected = parsed
    try:
        actual = hashlib.scrypt(
            provided.encode("utf-8"),
            salt=salt,
            n=n, r=r, p=p,
            maxmem=_maxmem(n, r),
            dklen=len(expected),
        )
    except ValueError:
        # Parameters that OpenSSL rejects (a bad n, or a cost far above what
        # this build's maxmem allows). Same answer as an unreadable record.
        return False
    return secrets.compare_digest(actual, expected)


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
    """Split a record into (n, r, p, salt, digest), or None if it is malformed."""
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _ALGO:
        return None
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        digest = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return None
    if n < 2 or r < 1 or p < 1 or not salt or not digest:
        return None
    return n, r, p, salt, digest
