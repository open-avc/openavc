"""Panel access is decided once for a system that never had it written down.

A system updated from a release before Panel access existed has wall tablets
connected that nobody approved. Left at the shipped default they would all go
to the waiting screen the moment the update finished, so such a system is
settled to ``open`` on its first start with the setting, and told so once; a
fresh install is settled to ``approved``. The signal is whether the data
directory has run before: the instance id file a first start creates, or a
claimed password in the file. The decision is written, so it is made once.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from openavc.core import panel_devices as pd
from openavc.system_config import get_system_config, reset_system_config
from tests.panel_access_helpers import PASSWORD, lan_client

AUTH = ("admin", PASSWORD)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENAVC_PANEL_ACCESS", raising=False)
    reset_system_config()
    try:
        yield tmp_path
    finally:
        reset_system_config()


def _file(data_dir: Path) -> dict:
    return json.loads((data_dir / "system.json").read_text(encoding="utf-8"))


def _settle(data_dir: Path, *, id_file_present: bool) -> str | None:
    id_file = data_dir / "projects" / "default" / ".instance_id"
    if id_file_present:
        id_file.parent.mkdir(parents=True, exist_ok=True)
        id_file.write_text(str(uuid.uuid4()), encoding="utf-8")
    return pd.settle_access_default(get_system_config(), id_file)


def test_a_fresh_install_is_settled_to_approved_and_written(data_dir):
    assert _settle(data_dir, id_file_present=False) == "approved"
    assert _file(data_dir)["panels"] == {"access": "approved", "upgrade_notice": False}
    assert pd.access_mode() == "approved"
    assert get_system_config().persisted_has("panels", "access")


def test_a_system_that_ran_before_is_settled_to_open_with_the_notice_due(data_dir):
    assert _settle(data_dir, id_file_present=True) == "open"
    assert _file(data_dir)["panels"] == {"access": "open", "upgrade_notice": True}
    assert pd.access_mode() == "open"


def test_a_claimed_password_in_the_file_also_means_an_existing_system(data_dir):
    # No instance id (a system whose advertising and mesh were both off), but
    # somebody set the admin password: it was running.
    (data_dir / "system.json").write_text(
        json.dumps({"auth": {"programmer_password": "scrypt$16384$8$1$abc$def"}}),
        encoding="utf-8",
    )
    reset_system_config()
    assert _settle(data_dir, id_file_present=False) == "open"
    assert _file(data_dir)["panels"]["upgrade_notice"] is True
    # The password itself is untouched by the write.
    assert _file(data_dir)["auth"]["programmer_password"] == "scrypt$16384$8$1$abc$def"


def test_a_file_that_already_says_is_left_alone(data_dir):
    (data_dir / "system.json").write_text(
        json.dumps({"panels": {"access": "approved"}}), encoding="utf-8",
    )
    reset_system_config()
    before = (data_dir / "system.json").read_bytes()
    assert _settle(data_dir, id_file_present=True) is None
    assert (data_dir / "system.json").read_bytes() == before
    assert pd.access_mode() == "approved"


def test_the_decision_is_made_once(data_dir):
    # Day one: fresh, approved. Day two: claimed, the instance id exists,
    # restarted. Still approved: the file now says so and the question is
    # never asked again.
    assert _settle(data_dir, id_file_present=False) == "approved"
    reset_system_config()
    assert _settle(data_dir, id_file_present=True) is None
    assert pd.access_mode() == "approved"
    assert _file(data_dir)["panels"]["upgrade_notice"] is False


def test_an_environment_override_keeps_winning_at_runtime(data_dir, monkeypatch):
    monkeypatch.setenv("OPENAVC_PANEL_ACCESS", "approved")
    reset_system_config()
    assert _settle(data_dir, id_file_present=True) == "open"
    # Written down as open, for the day the variable is unset...
    assert _file(data_dir)["panels"]["access"] == "open"
    # ...but the operator's variable is the truth while it is set.
    assert get_system_config().get("panels", "access") == "approved"
    assert pd.access_mode() == "approved"


# --- the Programmer's side: the flag on the list, the switch and Dismiss ---


def test_the_device_list_carries_the_notice_and_dismiss_clears_it(claimed_engine, access_mode):
    access_mode("open")
    cfg = get_system_config()
    cfg.set("panels", "upgrade_notice", True)
    client = lan_client()
    assert client.get("/api/panel/devices", auth=AUTH).json()["upgrade_notice"] is True

    resp = client.patch("/api/system/config", json={"panels": {"upgrade_notice": False}}, auth=AUTH)
    assert resp.status_code == 200
    assert client.get("/api/panel/devices", auth=AUTH).json()["upgrade_notice"] is False
    assert cfg.get("panels", "upgrade_notice") is False


def test_switching_to_approved_retires_the_notice(claimed_engine, access_mode):
    access_mode("open")
    cfg = get_system_config()
    cfg.set("panels", "upgrade_notice", True)
    client = lan_client()
    resp = client.patch("/api/system/config", json={"panels": {"access": "approved"}}, auth=AUTH)
    assert resp.status_code == 200
    listed = client.get("/api/panel/devices", auth=AUTH).json()
    assert listed["access"] == "approved"
    assert listed["upgrade_notice"] is False


def test_the_notice_flag_takes_only_a_bool(claimed_engine):
    resp = lan_client().patch("/api/system/config", json={"panels": {"upgrade_notice": "no"}}, auth=AUTH)
    assert resp.status_code == 422
    assert "true or false" in resp.json()["detail"]
