"""Tunnels the cloud has authorized to act as a signed-in Programmer client.

Two different people reach this instance through a cloud tunnel without holding
its admin password, and neither of them should be asked for one:

- **OpenAVC support.** The customer granted us access to one system, for a
  while, from their portal. Nobody here holds their instance password and
  nobody should ever be sent it.
- **The system's own owner.** An organization that has turned on password-free
  remote programming for its own rooms. They already signed in to the portal;
  making them type a per-room password set at commissioning, once per room,
  is the friction that setting exists to remove.

Both arrive the same way and are trusted for the same reason, so they are one
mechanism with one secret store. What differs is only the sentence written to
the log, which is why ``open_session`` takes a reason: an instance log that
says "OpenAVC support session" when the person is the customer's own
facilities manager is both wrong and alarming.

**Why the instance is willing to believe the cloud about this.** It is not a
new trust root. A paired instance already lets the cloud push a whole project,
run a software update and restart the service, all over the same authenticated
session channel that carries ``tunnel_open``. An instance that accepts a config
push but refuses a tunnel its owner explicitly authorized is not more secure;
it is just unable to do the thing the customer asked for.

**What it deliberately does not do.** It is not a session token: it is never
minted for a person, never stored, never persisted, and never renewed. It is
not the local console (``is_local_console_request`` still returns False for a
tunnelled request, so host network configuration stays behind the password).
And it does not survive a restart, because the tunnel does not either.
"""

from __future__ import annotations

import secrets
import threading

from openavc.utils.logger import get_logger

log = get_logger(__name__)

# Who authorized the tunnel. Carried on ``tunnel_open`` and used only to say
# the right thing in the log -- the trust decision is identical either way.
REASON_SUPPORT = "support"
REASON_OWNER = "owner"

VALID_REASONS = frozenset({REASON_SUPPORT, REASON_OWNER})

_REASON_SENTENCE = {
    REASON_SUPPORT: (
        "OpenAVC support session opened on tunnel %s under a grant from this "
        "system's account. Until it closes, requests arriving on it are "
        "treated as an authenticated Programmer client."
    ),
    REASON_OWNER: (
        "Remote programming session opened on tunnel %s, authorized by this "
        "system's account. Until it closes, requests arriving on it are "
        "treated as an authenticated Programmer client."
    ),
}

# tunnel_id -> secret. Small by construction: one entry per live authorized
# session, and there is rarely more than one.
_sessions: dict[str, str] = {}
_lock = threading.Lock()


def open_session(tunnel_id: str, reason: str = REASON_SUPPORT) -> str:
    """Mint the secret for an authorized tunnel and return it.

    Re-opening the same tunnel id replaces the secret rather than adding a
    second one, so a reconnecting tunnel cannot leave a live orphan behind.

    An unrecognised ``reason`` still opens the session and logs the neutral
    sentence. The reason is a label, not a permission, and refusing a tunnel
    over an unknown label would break a working session to punish a typo in a
    field that grants nothing.
    """
    secret = secrets.token_urlsafe(32)
    with _lock:
        _sessions[tunnel_id] = secret
    log.info(_REASON_SENTENCE.get(reason, _REASON_SENTENCE[REASON_OWNER]), tunnel_id)
    return secret


def close_session(tunnel_id: str) -> None:
    """Discard an authorized tunnel's secret. Safe to call for any tunnel."""
    with _lock:
        existed = _sessions.pop(tunnel_id, None) is not None
    if existed:
        log.info("Cloud-authorized session on tunnel %s closed.", tunnel_id)


def is_active(secret: str) -> bool:
    """Whether this secret belongs to a session that is still open."""
    if not secret:
        return False
    with _lock:
        live = list(_sessions.values())
    # compare_digest against every live secret rather than a dict lookup: the
    # set is one or two entries, and this keeps the check off the hash table's
    # timing.
    return any(secrets.compare_digest(secret, s) for s in live)


def active_count() -> int:
    """How many sessions are open. For status surfaces and tests."""
    with _lock:
        return len(_sessions)
