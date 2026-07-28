"""Every registered route's rate-limit tier is intentional.

The tier table in ``server/middleware/rate_limit.py`` is a second description
of the route surface, and the first version of it drifted: routes that fire
hardware sat on the general budget because their path happened not to contain
the substring the classifier looked for, and a device action's tier depended on
what the *driver author* named the action.

These tests are what keeps the two descriptions in step. The snapshots below
are meant to be edited — when a new route changes one, the failure is asking
you to confirm the tier is the one you wanted, not to paste the new value in
without looking.
"""

import re

import pytest

from server.main import app
from server.middleware.rate_limit import _classify

# --- The intended non-standard tiers -----------------------------------------
# Anything not listed here is expected to land on the standard budget, which is
# the safe default for a new route.

EXPECTED_OPEN = {
    ("GET", "/api/auth/required"),
    ("GET", "/api/certificate"),
    ("GET", "/api/cloud/status"),
    ("GET", "/api/health"),
    ("GET", "/api/setup/status"),
    ("GET", "/api/startup-status"),
    ("GET", "/api/status"),
}

# Security-sensitive: login, cloud pairing, backup restore. Deliberately tiny.
# A route arriving here is a real decision — it gets 10/min.
EXPECTED_STRICT = {
    ("POST", "/api/auth/session"),
    ("POST", "/api/backups/{filename:path}/restore"),
    ("POST", "/api/cloud/pair"),
    ("POST", "/api/cloud/unpair"),
}

# Commissioning operations. Every non-GET route under the control roots lands
# here structurally, so this set grows whenever a device/driver/discovery/ISC
# route is added — which is correct, and the point of the roots.
EXPECTED_CONTROL = {
    ("DELETE", "/api/devices/{device_id}"),
    ("DELETE", "/api/driver-definitions/{driver_id}"),
    ("DELETE", "/api/drivers/installed/{driver_id}"),
    ("DELETE", "/api/python-drivers/{driver_id}"),
    ("PATCH", "/api/devices/{device_id}"),
    ("PATCH", "/api/devices/{device_id}/children/{child_type}/{local_id}"),
    ("PATCH", "/api/driver-definitions/{driver_id}"),
    ("POST", "/api/devices/install-missing"),
    ("POST", "/api/devices/{bridge_id}/ir-emit"),
    ("POST", "/api/devices/{bridge_id}/ir-import"),
    ("POST", "/api/devices/{device_id}/actions/{action_id}"),
    ("POST", "/api/devices/{device_id}/children/refresh"),
    ("POST", "/api/devices/{device_id}/command"),
    ("POST", "/api/devices/{device_id}/pause"),
    ("POST", "/api/devices/{device_id}/reconnect"),
    ("POST", "/api/devices/{device_id}/resume"),
    ("POST", "/api/devices/{device_id}/retry"),
    ("POST", "/api/devices/{device_id}/send-raw"),
    ("POST", "/api/devices/{device_id}/settings/pending"),
    ("POST", "/api/devices/{device_id}/test"),
    ("POST", "/api/discovery/add-device"),
    ("POST", "/api/discovery/clear"),
    ("POST", "/api/discovery/install-and-match"),
    ("POST", "/api/discovery/scan"),
    ("POST", "/api/discovery/stop"),
    ("POST", "/api/driver-definitions"),
    # Called from the Driver Builder on a debounce while an author types, so
    # it is the one control-tier route whose budget a human can spend without
    # touching hardware. 120/min is plenty for a debounced editor that skips
    # unchanged drafts, and the editor keeps its last issue list rather than
    # blanking if a request ever does fail.
    ("POST", "/api/driver-definitions/validate"),
    ("POST", "/api/driver-definitions/{driver_id}/reload"),
    ("POST", "/api/driver-definitions/{driver_id}/test-command"),
    ("POST", "/api/drivers/install"),
    ("POST", "/api/drivers/installed/{driver_id}/update"),
    ("POST", "/api/drivers/upload"),
    ("POST", "/api/drivers/upload-bundle"),
    ("POST", "/api/isc/broadcast"),
    ("POST", "/api/isc/command"),
    ("POST", "/api/isc/send"),
    ("POST", "/api/python-drivers"),
    ("POST", "/api/python-drivers/{driver_id}/reload"),
    ("PUT", "/api/devices/{device_id}/settings/{setting_key}"),
    ("PUT", "/api/discovery/config"),
    ("PUT", "/api/driver-definitions/{driver_id}"),
    ("PUT", "/api/project"),
    ("PUT", "/api/python-drivers/{driver_id}/source"),
}

_PARAM = re.compile(r"\{[^}]+\}")


def _registered_routes():
    """(method, path_template) for every registered HTTP route."""
    out = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods:
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            out.add((method, path))
    return out


def _tier(method: str, path_template: str, param_value: str = "X") -> str:
    return _classify(method, _PARAM.sub(param_value, path_template))


def _actual(tier: str) -> set:
    return {(m, p) for m, p in _registered_routes() if _tier(m, p) == tier}


def _diff_message(tier, expected, actual):
    added = sorted(actual - expected)
    removed = sorted(expected - actual)
    lines = [f"The set of {tier}-tier routes changed."]
    if added:
        lines.append(f"  Now {tier} but not listed: {added}")
        lines.append(f"  -> Is {tier} the tier you want for these? If yes, add them to EXPECTED_{tier.upper()}.")
    if removed:
        lines.append(f"  Listed but no longer {tier}: {removed}")
        lines.append("  -> Either the route was removed/renamed, or a classifier change moved it. Check which.")
    return "\n".join(lines)


@pytest.mark.parametrize("tier,expected", [
    ("open", EXPECTED_OPEN),
    ("control", EXPECTED_CONTROL),
    ("strict", EXPECTED_STRICT),
])
def test_tier_membership_is_intentional(tier, expected):
    actual = _actual(tier)
    assert actual == expected, _diff_message(tier, expected, actual)


def test_open_tier_routes_are_actually_unauthenticated():
    """An open-tier path must be on the open router.

    The open tier hands out the largest budget on the reasoning that the caller
    has no credential to offer — a wall panel, a health check. Putting an
    authenticated route there just widens its budget for no reason, and would
    mean the tier table and the auth posture disagree about what "open" means.
    """
    from server.api import assets as assets_api
    from server.api import plugins as plugins_api
    from server.api import rest
    from server.api import themes as themes_api

    open_paths = set()
    for module in (rest, plugins_api, assets_api, themes_api):
        router = getattr(module, "open_router", None)
        if router is None:
            continue
        # Sub-routers are included into these with the prefix already applied,
        # so route.path is the full mounted path — don't add the prefix again.
        for route in router.routes:
            open_paths.add(getattr(route, "path", ""))

    not_open = sorted(p for _m, p in EXPECTED_OPEN if p not in open_paths)
    assert not not_open, (
        "These paths are on the open rate-limit tier but are not registered on an "
        f"unauthenticated open router: {not_open}"
    )


def test_tier_never_depends_on_a_path_parameter_value():
    """A route's tier must not change with the data in its path.

    This is the concrete bug the segment matching replaced: the classifier
    looked for "/test" anywhere in the path, so `POST /devices/{id}/actions/
    {action_id}` was control when a driver author happened to name the action
    "self_test" and standard when they named it "discover" — the rate limit
    depended on someone else's naming choice.
    """
    adversarial = ["test", "command", "install", "restore", "cloud", "session", "certificate"]
    offenders = []
    for method, path in _registered_routes():
        if not _PARAM.search(path):
            continue
        baseline = _tier(method, path)
        for value in adversarial:
            if _tier(method, path, value) != baseline:
                offenders.append(f"{method} {path}: '{value}' in a path param changes the tier")
    assert not offenders, "Tier depends on path-parameter data:\n  " + "\n  ".join(offenders)


def test_hardware_firing_device_routes_share_the_command_budget():
    """The routes that made this finding: they fire a device like /command does.

    Named explicitly (rather than left to the membership snapshot) because the
    whole point of the fix is that these four stop being special cases.
    """
    for method, path in [
        ("POST", "/api/devices/{device_id}/send-raw"),
        ("POST", "/api/devices/{bridge_id}/ir-emit"),
        ("POST", "/api/drivers/upload-bundle"),
        ("POST", "/api/isc/command"),
    ]:
        assert _tier(method, path) == "control", f"{method} {path} should be control"
