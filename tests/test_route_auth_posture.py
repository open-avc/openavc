"""The app's route table, pinned where getting it wrong is silent.

Two properties, both about the whole mounted app rather than any one module:

1. **Exactly these routes are reachable without programmer auth.** Which
   router a route is registered on decides whether the world can call it, and
   nothing else in the app makes that visible — a route moved to the wrong
   router keeps working, keeps its tests green, and is simply public. That is
   the failure mode a route-module split (or a copy-pasted decorator) creates,
   so the open set is a checked-in list rather than an emergent property.

2. **No (path, method) pair is registered twice.** Duplicate registration
   makes the winner depend on include order, which is invisible at the call
   site — the first-registered handler answers and the second is dead code
   that still reads live.

Neither test says a route is *right*, only that changing its exposure has to
be deliberate. `tests/test_rate_limit_tiers.py` pins the rate-limit tiers over
the same table; this pins the auth posture underneath them.
"""

import pytest

from openavc.api.auth import require_programmer_auth
from openavc.main import app

# Every (method, path) callable without a programmer credential.
#
# Adding a line here means "the world may call this". Most entries earn it:
# liveness/readiness probes, the pre-login auth handshake, the panel's own
# read paths, the CA cert (already public in every TLS handshake), the
# device push callbacks (source-IP gated instead), and the on-device setup
# and network screens (loopback-or-auth, so a fresh appliance can be
# configured from its own display before a credential exists).
EXPECTED_OPEN = {
    ("DELETE", "/api/auth/session"),
    ("GET", "/"),
    ("GET", "/api/auth/required"),
    ("GET", "/api/certificate"),
    ("GET", "/api/cloud/status"),
    ("GET", "/api/health"),
    ("GET", "/api/plugins/extensions"),
    ("GET", "/api/plugins/{plugin_id}/ext-token"),
    ("GET", "/api/plugins/{plugin_id}/files/{file_path:path}"),
    ("GET", "/api/plugins/{plugin_id}/panel/{file_path:path}"),
    ("GET", "/api/projects/{project_id}/assets/{filename:path}"),
    ("GET", "/api/setup/status"),
    ("GET", "/api/startup-status"),
    ("GET", "/api/status"),
    ("GET", "/api/system/network"),
    ("GET", "/api/themes/{theme_id}"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/openapi.json"),
    ("GET", "/pair"),
    ("GET", "/redoc"),
    ("GET", "/setup"),
    ("NOTIFY", "/api/push/{device_id}"),
    ("NOTIFY", "/api/push/{device_id}/{label}"),
    ("POST", "/api/auth/session"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/push/{device_id}"),
    ("POST", "/api/push/{device_id}/{label}"),
    ("POST", "/api/system/network/hostname"),
    ("POST", "/api/system/network/ipv4"),
    ("POST", "/api/system/network/wifi/connect"),
    ("POST", "/api/system/network/wifi/radio"),
    ("POST", "/api/system/network/wifi/scan"),
}


def _http_routes():
    """(method, path, requires_programmer_auth) for every mounted HTTP route."""
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        dependant = getattr(route, "dependant", None)
        calls = {d.call for d in getattr(dependant, "dependencies", [])} if dependant else set()
        calls |= {getattr(d, "dependency", None) for d in getattr(route, "dependencies", []) or []}
        for method in methods - {"HEAD", "OPTIONS"}:
            out.append((method, path, require_programmer_auth in calls))
    return out


def test_only_the_listed_routes_are_unauthenticated():
    actual = {(m, p) for m, p, needs_auth in _http_routes() if not needs_auth}

    newly_open = sorted(actual - EXPECTED_OPEN)
    no_longer_open = sorted(EXPECTED_OPEN - actual)

    message = []
    if newly_open:
        message += [
            f"These routes are now callable with NO credential: {newly_open}",
            "  -> If that is intended, add them to EXPECTED_OPEN with a reason.",
            "  -> If not, they belong on the protected router, not open_router.",
        ]
    if no_longer_open:
        message += [
            f"These routes are no longer open: {no_longer_open}",
            "  -> Fine if the route was removed, renamed, or deliberately "
            "protected; drop the line. If it was accidental, a panel or probe "
            "that used to work now gets a 401.",
        ]
    assert not message, "\n".join(message)


def test_no_route_is_registered_twice():
    """A duplicate (path, method) means include order picks the winner."""
    seen = {}
    dupes = []
    for method, path, _needs_auth in _http_routes():
        if (path, method) in seen:
            dupes.append(f"{method} {path}")
        seen[(path, method)] = True
    assert not dupes, (
        "These (method, path) pairs are registered more than once, so which "
        f"handler answers depends on router include order: {sorted(dupes)}"
    )


@pytest.mark.parametrize("method,path", [
    ("PATCH", "/api/system/config"),
    ("POST", "/api/cloud/pair"),
    ("POST", "/api/system/ssh"),
    ("POST", "/api/system/tls/upload-cert"),
])
def test_representative_admin_routes_stay_protected(method, path):
    """Spot-check the shape the big list is protecting, in a form that names
    the stakes: writing system config, cloud pairing, host SSH and installing
    a certificate are where 'accidentally open' is worst.

    Matched on (method, path), not path alone — a path can carry an open read
    and a protected write, and checking the path would call that protected.
    """
    protected = {(m, p) for m, p, needs_auth in _http_routes() if needs_auth}
    assert (method, path) in protected, (
        f"{method} {path} lost its programmer-auth requirement"
    )
