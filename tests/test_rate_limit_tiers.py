"""Every registered route's rate-limit tier is intentional.

The tier table in ``openavc/middleware/rate_limit.py`` is a second description
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

from openavc.main import app
from openavc.middleware.rate_limit import _classify

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
    # The bus's door for an outside system. It fires automation the way a
    # command fires a device, and a building system posting occupancy would
    # exhaust the standard 60/min.
    ("POST", "/api/events"),
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
    from openavc.api import assets as assets_api
    from openavc.api import plugins as plugins_api
    from openavc.api import rest
    from openavc.api import themes as themes_api

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


# The panel's static render path — exempt entirely (see rate_limit.py). Listed
# here so that widening the exemption has to be a deliberate edit: an exemption
# is a bigger claim than a tier, because nothing downstream catches it.
EXPECTED_RENDER_PATH_EXEMPT = {
    ("GET", "/api/projects/{project_id}/assets/{filename:path}"),
    ("GET", "/api/projects/{project_id}/ui/{file_path:path}"),
    ("GET", "/api/plugins/{plugin_id}/panel/{file_path:path}"),
    ("GET", "/api/plugins/{plugin_id}/files/{file_path:path}"),
}


def test_only_the_static_render_path_is_exempt_from_rate_limiting():
    """No /api/ route escapes the limiter except the four file-serving families.

    /api/push/ is excluded from the comparison because it is exempt for its own
    reason (device event bursts) and is pinned by its own prefix.
    """
    actual = {
        (m, p) for m, p in _registered_routes()
        if p.startswith("/api/") and not p.startswith("/api/push/")
        and _tier(m, p) == "skip"
    }
    assert actual == EXPECTED_RENDER_PATH_EXEMPT, _diff_message(
        "rate-limit-exempt", EXPECTED_RENDER_PATH_EXEMPT, actual
    )


def test_writing_to_a_render_path_is_still_rate_limited():
    """The open GET and an authenticated DELETE share a path shape.

    Exempting by path alone would have handed the delete an unlimited budget,
    which is why the exemption is GET/HEAD-only.
    """
    assert _classify("DELETE", "/api/projects/default/assets/logo.png") == "standard"
    assert _classify("POST", "/api/projects/default/assets") == "standard"
    assert _classify("PUT", "/api/projects/default/ui/control/index.html") == "standard"


def test_render_path_collection_endpoints_keep_their_budget():
    """Only per-file reads are exempt; listing is a normal authenticated read."""
    assert _classify("GET", "/api/projects/default/assets") == "standard"
    assert _classify("GET", "/api/projects/default/ui") == "standard"


def test_credential_and_theme_routes_are_not_swept_in():
    """The two §106 edges that made this a decision rather than a sweep."""
    assert _classify("GET", "/api/plugins/audio_player/ext-token") == "standard"
    assert _classify("GET", "/api/themes/dark-default") == "standard"
    assert _classify("GET", "/api/themes/dark-default/export") == "standard"


def test_a_heavy_cold_panel_load_no_longer_spends_the_standard_budget():
    """The measured Q-101 load: 46 assets + 7 custom-control fetches.

    Measured at 59 requests against a 60/min limit on a real remote tablet.
    Only the two per-start fetches should still count.
    """
    cold_load = [f"/api/projects/default/assets/tile-{i}.png" for i in range(46)]
    cold_load += [f"/api/projects/default/ui/control-{i}/index.html" for i in range(7)]
    still_counted = [p for p in cold_load if _classify("GET", p) == "standard"]
    assert still_counted == []


def test_the_exemption_is_segment_bounded():
    """A future sibling route must not inherit the exemption by prefix."""
    assert _classify("GET", "/api/projects/default/assets-export/all.zip") == "standard"
    assert _classify("GET", "/api/plugins/x/files-index") == "standard"
    assert _classify("GET", "/api/projects/default/uix/thing") == "standard"


# --- The media tier ----------------------------------------------------------
#
# A video tile is not a person clicking. One low-latency HLS stream re-asks its
# playlist per part and fetches every part, which measured at about 390 requests
# a minute on the bench -- so the standard 60 stops the first tile within
# seconds, and the panel shows a connection error where the picture should be.
#
# The budget is opted into per route by the plugin that serves it, never
# inferred from the path, so these pin BOTH halves: that a declared route gets
# it, and that nothing else does.


class _MediaPatterns:
    """Register media patterns for one prefix and take them away again."""

    def __init__(self, prefix, patterns):
        self.prefix, self.patterns = prefix, patterns

    def __enter__(self):
        from openavc.api.plugin_ext import parse_panel_paths
        from openavc.middleware import rate_limit

        rate_limit.register_media_patterns(self.prefix, parse_panel_paths(self.patterns))
        return self

    def __exit__(self, *exc):
        from openavc.middleware import rate_limit

        rate_limit.unregister_media_patterns(self.prefix)


_VIDEO_EXT = "/api/plugins/video_panel/ext"


def test_a_declared_media_route_gets_the_media_budget():
    with _MediaPatterns(_VIDEO_EXT, ["GET /hls/*", "/whep/*"]):
        assert _classify("GET", f"{_VIDEO_EXT}/hls/cam1/index.m3u8") == "media"
        assert _classify("GET", f"{_VIDEO_EXT}/hls/cam1/seg0.mp4") == "media"
        # No method on the pattern means any method.
        assert _classify("POST", f"{_VIDEO_EXT}/whep/cam1") == "media"
        assert _classify("DELETE", f"{_VIDEO_EXT}/whep/cam1/abc") == "media"


def test_an_undeclared_sibling_stays_on_the_standard_budget():
    """The point of declaring routes one at a time. Stream CRUD sits under the
    same mount and must not inherit a video stream's allowance."""
    with _MediaPatterns(_VIDEO_EXT, ["GET /hls/*"]):
        assert _classify("GET", f"{_VIDEO_EXT}/streams") == "standard"
        assert _classify("POST", f"{_VIDEO_EXT}/streams") == "standard"
        # And the method scope is honoured.
        assert _classify("DELETE", f"{_VIDEO_EXT}/hls/cam1/index.m3u8") == "standard"


def test_another_plugins_mount_is_untouched():
    with _MediaPatterns(_VIDEO_EXT, ["GET /hls/*"]):
        assert _classify("GET", "/api/plugins/other/ext/hls/cam1/index.m3u8") == "standard"


def test_the_prefix_is_segment_bounded():
    """`/api/plugins/video_panel/ext` must not match a sibling mount whose name
    merely starts with it."""
    with _MediaPatterns(_VIDEO_EXT, ["GET /hls/*"]):
        assert _classify("GET", "/api/plugins/video_panel/extra/hls/x.m3u8") == "standard"


def test_unregistering_puts_the_route_back_on_standard():
    """A plugin that stops must not leave its allowance behind."""
    with _MediaPatterns(_VIDEO_EXT, ["GET /hls/*"]):
        pass
    assert _classify("GET", f"{_VIDEO_EXT}/hls/cam1/index.m3u8") == "standard"


def test_no_route_is_media_by_default():
    """Nothing gets this budget from its path shape alone. If this fails,
    something started classifying by convention instead of by declaration."""
    from openavc.middleware import rate_limit

    assert not rate_limit._media_patterns, (
        "a media pattern leaked from another test; the budget must only exist "
        "while a plugin that declared it is running"
    )
    for method, path in (
        ("GET", "/api/plugins/video_panel/ext/hls/cam1/index.m3u8"),
        ("GET", "/api/project"),
        ("POST", "/api/devices/x/command"),
    ):
        assert _classify(method, path) != "media"


def test_the_media_budget_is_large_but_finite():
    """A ceiling, not an exemption: it is what stops a player stuck in a loop.
    The static render path is exempt because a panel load ends; a stream does
    not."""
    from openavc import config

    assert config.RATE_LIMIT_MEDIA_PER_MINUTE > config.RATE_LIMIT_STANDARD_PER_MINUTE
    assert config.RATE_LIMIT_MEDIA_PER_MINUTE < 100000


def test_a_media_route_has_its_own_window():
    """Sharing the standard window would let one tile 429 the panel's own
    ordinary calls, which is the failure this whole tier exists to stop."""
    from openavc.middleware.rate_limit import _IPBuckets

    buckets = _IPBuckets()
    assert buckets.get_window("media") is buckets.media
    assert buckets.get_window("media") is not buckets.get_window("standard")
