"""The panel device store: what is remembered, what is written, what expires.

Pure store tests; the routes and the socket gate are test_panel_access.py and
test_ws_panel_gate.py.
"""

from __future__ import annotations

import json
import re
import stat
import sys

import pytest

from openavc.core import panel_devices as pd
from openavc.core.panel_devices import (
    DENIED_TTL_SECONDS,
    HANDOFF_TTL_SECONDS,
    PENDING_TTL_SECONDS,
    PanelDeviceStore,
    format_code,
    platform_label,
)

UA_IPAD = "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15"


class FakeClock:
    """A clock the store reads instead of ``time``: both monotonic and wall."""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(pd, "time", fake)
    return fake


@pytest.fixture
def store(tmp_path):
    return PanelDeviceStore(tmp_path / "panel_devices.json")


def _new_device(store, address="10.1.1.50", ua=UA_IPAD):
    result = store.check_in(None, address=address, user_agent=ua)
    assert result.status == "pending" and result.created
    return result


# --- codes and labels ---


def test_a_code_is_six_digits_with_a_dash():
    assert format_code(482915) == "482-915"
    assert format_code(7) == "000-007"


def test_a_new_device_gets_a_pending_record_and_a_code(store):
    result = _new_device(store)
    assert re.fullmatch(r"\d{3}-\d{3}", result.device.code)
    assert result.device.platform == "iPad"
    assert result.device.address == "10.1.1.50"
    assert result.set_cookie.startswith(result.device.id + ".")
    assert result.cookie_max_age == pd.PENDING_COOKIE_MAX_AGE_SECONDS


def test_codes_are_unique_among_live_requests(store, monkeypatch):
    draws = iter([123456, 123456, 654321])
    monkeypatch.setattr(pd.secrets, "randbelow", lambda n: next(draws))
    first = _new_device(store, address="10.0.0.1")
    second = _new_device(store, address="10.0.0.2")
    assert first.device.code == "123-456"
    assert second.device.code == "654-321"


@pytest.mark.parametrize("ua,label", [
    (UA_IPAD, "iPad"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)", "iPhone"),
    ("Mozilla/5.0 (Linux; Android 16; TB311FU) AppleWebKit/537.36 Chrome/140 Safari/537.36", "Android tablet"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36", "Android phone"),
    ("Mozilla/5.0 (Linux; Android 11; KFTRWI) AppleWebKit/537.36 Silk/120", "Fire tablet"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/153", "Windows PC"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15", "Mac or iPad"),
    ("Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36", "Chromebook"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Linux PC"),
    ("curl/8.4.0", "Browser"),
    ("", "Unknown device"),
])
def test_platform_label_names_the_device_a_person_would_recognise(ua, label):
    assert platform_label(ua) == label


# --- the record on disk ---


def test_approval_writes_the_record_and_a_reload_reads_it_back(store, tmp_path):
    result = _new_device(store)
    device, _secret = store.approve(result.device.id, "Room 101 wall", "admin")
    assert device.status == "approved"
    assert device.name == "Room 101 wall"
    assert device.approved_by == "admin"

    raw = json.loads((tmp_path / "panel_devices.json").read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert [d["id"] for d in raw["devices"]] == [device.id]
    on_disk = raw["devices"][0]
    assert on_disk["name"] == "Room 101 wall"
    assert on_disk["platform"] == "iPad"
    assert on_disk["secret_hash"].startswith("sha256$")

    reloaded = PanelDeviceStore(tmp_path / "panel_devices.json")
    reloaded.load()
    assert reloaded.get(device.id).name == "Room 101 wall"
    assert reloaded.list_devices()["pending"] == []


def test_only_hashes_reach_the_disk(store, tmp_path):
    result = _new_device(store)
    pending_secret = result.set_cookie.split(".", 1)[1]
    _device, approved_secret = store.approve(result.device.id, None, "admin")
    text = (tmp_path / "panel_devices.json").read_text(encoding="utf-8")
    assert pending_secret not in text
    assert approved_secret not in text
    assert "handoff" not in text


def test_a_pending_record_is_never_written(store, tmp_path):
    _new_device(store)
    assert not (tmp_path / "panel_devices.json").exists()


def test_a_default_name_is_the_platform_and_the_address(store):
    result = _new_device(store)
    device, _ = store.approve(result.device.id, "   ", "admin")
    assert device.name == "iPad at 10.1.1.50"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_file_is_private_to_the_service_user(store, tmp_path):
    result = _new_device(store)
    store.approve(result.device.id, "x", "admin")
    mode = stat.S_IMODE((tmp_path / "panel_devices.json").stat().st_mode)
    assert mode == 0o600


def test_an_unreadable_file_is_set_aside_not_overwritten_silently(tmp_path):
    path = tmp_path / "panel_devices.json"
    path.write_text("{not json", encoding="utf-8")
    store = PanelDeviceStore(path)
    store.load()
    assert store.list_devices()["approved"] == []
    result = _new_device(store)
    store.approve(result.device.id, "x", "admin")
    assert (tmp_path / "panel_devices.json.unreadable").read_text(encoding="utf-8") == "{not json"
    assert json.loads(path.read_text(encoding="utf-8"))["devices"]


def test_an_entry_without_a_hash_is_skipped_and_the_rest_load(tmp_path):
    path = tmp_path / "panel_devices.json"
    path.write_text(json.dumps({
        "version": 1,
        "devices": [
            {"id": "pd_broken0001"},
            {"id": "pd_good000001", "name": "Good", "secret_hash": "sha256$AA$BB"},
        ],
    }), encoding="utf-8")
    store = PanelDeviceStore(path)
    store.load()
    assert [d["id"] for d in store.list_devices()["approved"]] == ["pd_good000001"]


# --- the secret ---


def test_approval_rotates_the_secret_and_the_pending_value_collects_it(store):
    result = _new_device(store)
    pending_cookie = result.set_cookie
    device, _ = store.approve(result.device.id, "Wall", "admin")

    # The value the device held while waiting is not a credential.
    assert store.verify(pending_cookie) is None

    # Its next check-in hands over the real one.
    handoff = store.check_in(pending_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert handoff.status == "approved"
    assert handoff.set_cookie is not None and handoff.set_cookie != pending_cookie
    assert handoff.cookie_max_age == pd.COOKIE_MAX_AGE_SECONDS
    assert store.verify(handoff.set_cookie) is device

    # And the pending value is spent: presenting it again is a new device.
    again = store.check_in(pending_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created
    assert again.device.id != device.id


def test_a_wrong_secret_for_a_known_id_is_a_stranger(store):
    result = _new_device(store)
    device, _ = store.approve(result.device.id, "Wall", "admin")
    wrong = f"{device.id}.not-the-secret"
    assert store.verify(wrong) is None
    again = store.check_in(wrong, address="10.1.1.51", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created


def test_the_claim_path_delivers_the_secret_in_the_same_response(store):
    result = _new_device(store)
    device, secret = store.approve(result.device.id, "Wall", "admin password on the panel")
    store.mark_delivered(device.id)
    assert store.verify(f"{device.id}.{secret}") is device
    # Nothing left to hand over, so the pending value is spent at once.
    again = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created


def test_a_handoff_nobody_collected_is_forgotten(store, clock):
    result = _new_device(store)
    store.approve(result.device.id, "Wall", "admin")
    clock.advance(HANDOFF_TTL_SECONDS + 1)
    store.expire()
    again = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created


def test_an_approved_cookie_is_reissued_once_a_day(store, clock):
    result = _new_device(store)
    store.approve(result.device.id, "Wall", "admin")
    approved = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    cookie = approved.set_cookie

    soon = store.check_in(cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert soon.status == "approved" and soon.set_cookie is None

    clock.advance(pd.COOKIE_REISSUE_AFTER_SECONDS + 1)
    later = store.check_in(cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert later.status == "approved"
    assert later.set_cookie == cookie
    assert later.cookie_max_age == pd.COOKIE_MAX_AGE_SECONDS


def test_last_seen_is_written_at_most_every_five_minutes(store, clock, monkeypatch):
    result = _new_device(store)
    device, _ = store.approve(result.device.id, "Wall", "admin")
    writes = []
    monkeypatch.setattr(store, "save", lambda: writes.append(clock.now))

    store.touch(device.id)
    store.touch(device.id)
    assert writes == []  # approval just wrote; nothing due yet
    clock.advance(pd.LAST_SEEN_WRITE_INTERVAL_SECONDS + 1)
    store.touch(device.id)
    store.touch(device.id)
    assert len(writes) == 1


# --- the programmer's actions ---


def test_deny_keeps_the_device_told_until_it_expires(store, clock):
    result = _new_device(store)
    store.deny(result.device.id)
    told = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert told.status == "denied"
    assert store.list_devices()["denied"][0]["id"] == result.device.id

    clock.advance(DENIED_TTL_SECONDS + 1)
    dropped = store.expire()
    assert [d.id for d in dropped] == [result.device.id]
    again = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created


def test_a_denied_device_can_still_be_approved(store):
    result = _new_device(store)
    store.deny(result.device.id)
    device, _ = store.approve(result.device.id, "Changed my mind", "admin")
    assert device.status == "approved"
    assert device.denied_at is None


def test_an_approved_device_is_revoked_not_denied(store):
    result = _new_device(store)
    store.approve(result.device.id, "Wall", "admin")
    with pytest.raises(ValueError):
        store.deny(result.device.id)


def test_revoke_removes_the_record_from_disk(store, tmp_path):
    result = _new_device(store)
    device, _ = store.approve(result.device.id, "Wall", "admin")
    approved = store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    store.revoke(device.id)
    assert store.verify(approved.set_cookie) is None
    raw = json.loads((tmp_path / "panel_devices.json").read_text(encoding="utf-8"))
    assert raw["devices"] == []
    again = store.check_in(approved.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)
    assert again.status == "pending" and again.created


def test_rename_trims_and_refuses_blank(store):
    result = _new_device(store)
    device, _ = store.approve(result.device.id, "Wall", "admin")
    assert store.rename(device.id, "  Lobby   tablet ").name == "Lobby tablet"
    with pytest.raises(ValueError):
        store.rename(device.id, "   ")


def test_unknown_ids_raise_key_error(store):
    with pytest.raises(KeyError):
        store.approve("pd_nobody0000", None, "admin")
    with pytest.raises(KeyError):
        store.revoke("pd_nobody0000")


# --- expiry and caps ---


def test_a_pending_record_expires_after_fifteen_idle_minutes(store, clock):
    result = _new_device(store)
    clock.advance(PENDING_TTL_SECONDS - 1)
    store.check_in(result.set_cookie, address="10.1.1.50", user_agent=UA_IPAD)  # keeps it alive
    clock.advance(PENDING_TTL_SECONDS - 1)
    assert store.expire() == []
    clock.advance(2)
    dropped = store.expire()
    assert [d.id for d in dropped] == [result.device.id]
    assert store.list_devices()["pending"] == []


def test_three_requests_per_address_then_unavailable(store):
    for _ in range(pd.MAX_PENDING_PER_ADDRESS):
        _new_device(store, address="10.1.1.9")
    fourth = store.check_in(None, address="10.1.1.9", user_agent=UA_IPAD)
    assert fourth.status == "unavailable"
    assert fourth.device is None and fourth.set_cookie is None
    # Another address is still served.
    assert store.check_in(None, address="10.1.1.10", user_agent=UA_IPAD).status == "pending"


def test_fifty_pending_per_instance_then_unavailable(store, monkeypatch):
    monkeypatch.setattr(pd, "MAX_PENDING", 4)
    for i in range(4):
        _new_device(store, address=f"10.2.0.{i}")
    assert store.check_in(None, address="10.2.0.99", user_agent=UA_IPAD).status == "unavailable"
    assert len(store.list_devices()["pending"]) == 4


def test_public_records_carry_no_secret(store):
    result = _new_device(store)
    pending = result.device.public()
    assert set(pending) == {"id", "status", "name", "platform", "address", "first_seen", "last_seen", "code"}
    device, _ = store.approve(result.device.id, "Wall", "admin")
    approved = device.public()
    assert "secret_hash" not in approved and "code" not in approved
    assert approved["approved_by"] == "admin"
