"""Live OpenAVC support sessions on this instance.

A customer grants OpenAVC access to one system, for a while, from their cloud
portal. The cloud opens a Programmer-scoped tunnel under that grant and the
agent proxies it to loopback like any other. What arrives at the far end is the
Programmer's own sign-in screen — and nobody at OpenAVC has the customer's
instance password, nor should they ever be sent it. So the grant used to reach
the door and stop there.

This is the registry that lets it through. When the cloud says a tunnel was
opened under a live grant, the agent mints a secret here, stamps it on
everything it proxies for that tunnel, and ``openavc/api/auth.py`` accepts it
in place of the admin credential. When the tunnel closes — expiry, revocation,
the ticket being closed, the agent restarting — the secret is discarded and
every request carrying it stops working.

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

# tunnel_id -> secret. Small by construction: one entry per live support
# session, and there is rarely more than one.
_sessions: dict[str, str] = {}
_lock = threading.Lock()


def open_session(tunnel_id: str) -> str:
    """Mint the secret for a support tunnel and return it.

    Re-opening the same tunnel id replaces the secret rather than adding a
    second one, so a reconnecting tunnel cannot leave a live orphan behind.
    """
    secret = secrets.token_urlsafe(32)
    with _lock:
        _sessions[tunnel_id] = secret
    log.info(
        "OpenAVC support session opened on tunnel %s. Until it closes, requests "
        "arriving on it are treated as an authenticated Programmer client.",
        tunnel_id,
    )
    return secret


def close_session(tunnel_id: str) -> None:
    """Discard a support tunnel's secret. Safe to call for any tunnel."""
    with _lock:
        existed = _sessions.pop(tunnel_id, None) is not None
    if existed:
        log.info("OpenAVC support session on tunnel %s closed.", tunnel_id)


def is_active(secret: str) -> bool:
    """Whether this secret belongs to a support session that is still open."""
    if not secret:
        return False
    with _lock:
        live = list(_sessions.values())
    # compare_digest against every live secret rather than a dict lookup: the
    # set is one or two entries, and this keeps the check off the hash table's
    # timing.
    return any(secrets.compare_digest(secret, s) for s in live)


def active_count() -> int:
    """How many support sessions are open. For status surfaces and tests."""
    with _lock:
        return len(_sessions)
