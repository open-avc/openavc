"""The time zone this server's clock reads, and where it comes from.

A host that runs the server on UTC while the site is somewhere else fires an
"11:00 PM" schedule hours early. The panel on the computer's own screen runs in
the site's zone and reports it at its check-in; the server takes it when its
own clock disagrees, and applies the saved zone at the next start. An operator's
TZ wins, and a report from anywhere but the computer's own screen is ignored.

The process's TZ is global, so every test here puts it back. POSIX only:
Windows has no tzset and always runs in the computer's own zone.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

import pytest

from openavc.core import time_zone
from tests.panel_access_helpers import TUNNEL_HEADERS, lan_client, loopback_client

pytestmark = pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX time zones")

# A June instant: New York is four hours behind UTC then, London one ahead.
JUNE = datetime(2026, 6, 15, 12, 0).timestamp()
JANUARY = datetime(2026, 1, 15, 12, 0).timestamp()


def _offset_hours(at: float) -> float:
    return time.localtime(at).tm_gmtoff / 3600


@pytest.fixture
def clock(tmp_path, monkeypatch):
    """A server whose clock is on UTC, nothing saved, no operator TZ; the
    process's TZ and this module's memory are put back afterwards."""
    before = os.environ.get("TZ")
    saved = tmp_path / "time_zone.json"
    monkeypatch.setattr(time_zone, "_saved_path", lambda: saved)
    monkeypatch.setattr(time_zone, "_operator_tz", None)
    monkeypatch.setattr(time_zone, "_applied", None)
    os.environ["TZ"] = "UTC0"
    time.tzset()
    yield saved
    if before is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = before
    time.tzset()


def test_a_screen_in_another_zone_moves_the_clock(clock):
    assert time_zone.adopt_from_local_screen("America/New_York") is True
    assert _offset_hours(JUNE) == -4
    assert _offset_hours(JANUARY) == -5
    assert time_zone.current_name() == "America/New_York"
    assert json.loads(clock.read_text())["name"] == "America/New_York"


def test_a_screen_in_the_same_zone_changes_nothing(clock):
    os.environ["TZ"] = "EST5EDT,M3.2.0,M11.1.0"
    time.tzset()
    assert time_zone.adopt_from_local_screen("America/New_York") is False
    assert not clock.exists()


def test_a_zone_that_differs_only_in_summer_is_still_taken(clock):
    """London agrees with UTC all winter: offsets now alone would miss it."""
    assert time_zone.adopt_from_local_screen("Europe/London") is True
    assert _offset_hours(JUNE) == 1
    assert _offset_hours(JANUARY) == 0


def test_an_operator_tz_wins(clock, monkeypatch):
    monkeypatch.setattr(time_zone, "_operator_tz", "UTC0")
    assert time_zone.adopt_from_local_screen("America/New_York") is False
    assert os.environ["TZ"] == "UTC0"
    assert not clock.exists()


@pytest.mark.parametrize("name", ["", "Not/AZone", "../../etc/passwd", "/etc/localtime", "x" * 200])
def test_a_name_that_is_not_a_zone_is_ignored(clock, name):
    assert time_zone.adopt_from_local_screen(name) is False
    assert os.environ["TZ"] == "UTC0"


def test_a_zone_the_clock_will_not_take_is_put_back(clock, monkeypatch):
    monkeypatch.setattr(time_zone, "_clock_follows", lambda zone: False)
    assert time_zone.adopt_from_local_screen("America/New_York") is False
    assert os.environ["TZ"] == "UTC0"
    assert not clock.exists()


def test_the_saved_zone_is_applied_at_the_next_start(clock):
    clock.write_text(json.dumps({"name": "America/Chicago"}))
    time_zone.restore()
    assert _offset_hours(JUNE) == -5
    assert time_zone.current_name() == "America/Chicago"


def test_a_damaged_saved_file_leaves_the_clock_alone(clock):
    clock.write_text("{not json")
    time_zone.restore()
    assert os.environ["TZ"] == "UTC0"


# --- the check-in ---


def test_the_computer_s_own_screen_sets_the_clock(clock, claimed_engine, access_mode):
    access_mode("open")
    resp = loopback_client().get("/api/panel/access", params={"time_zone": "Asia/Tokyo"})
    assert resp.status_code == 200
    assert _offset_hours(JUNE) == 9
    assert claimed_engine.state.get("system.time_zone") == "Asia/Tokyo"


def test_a_panel_elsewhere_on_the_network_does_not(clock, claimed_engine, access_mode):
    access_mode("open")
    lan_client().get("/api/panel/access", params={"time_zone": "Asia/Tokyo"})
    assert os.environ["TZ"] == "UTC0"
    assert claimed_engine.state.get("system.time_zone") != "Asia/Tokyo"


def test_a_panel_through_remote_access_does_not(clock, claimed_engine, access_mode):
    access_mode("open")
    loopback_client().get(
        "/api/panel/access", params={"time_zone": "Asia/Tokyo"}, headers=TUNNEL_HEADERS
    )
    assert os.environ["TZ"] == "UTC0"


def test_a_bad_zone_never_refuses_the_panel(clock, claimed_engine, access_mode):
    access_mode("open")
    resp = loopback_client().get("/api/panel/access", params={"time_zone": "Not/AZone"})
    assert resp.json() == {"access": "open"}
