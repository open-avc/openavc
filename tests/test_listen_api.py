"""Who can connect over the config API: what GET reports, what PATCH
accepts and arms, the preview, the confirm, and the adapter list.

The adapters are invented (tests/listen_helpers.py) and the browser's
connection is set by the test client's base URL, which becomes the address
of this computer the request arrived on.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openavc import listen, listen_safety_net
from tests.listen_helpers import ETHERNET, WIFI, use_plan

WIFI_BROWSER = "http://192.168.1.40:8080"
ETHERNET_BROWSER = "http://10.20.30.5:8080"


@pytest.fixture
def api(tmp_path, monkeypatch, isolated_auth_config):
    """The real app on a config in tmp_path, two invented adapters, and a
    running plan of any network."""
    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.main import app
    from openavc.system_config import get_system_config, reset_system_config

    monkeypatch.delenv("OPENAVC_BIND", raising=False)
    monkeypatch.setattr(listen, "list_adapters", lambda: [ETHERNET, WIFI])
    engine = MagicMock()
    state = StateStore()
    state.set_event_bus(EventBus())
    engine.state = state
    engine.isc = None
    engine.cloud_agent = None  # a MagicMock attribute would read as paired
    engine.panel_devices = SimpleNamespace(list_devices=lambda: {"approved": []})
    engine.panel_access_changed = AsyncMock()
    engine.reconcile_runtime_services = AsyncMock()
    rest.set_engine(engine)
    ws.set_engine(engine)

    def start(base_url: str = WIFI_BROWSER, client=None, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        reset_system_config()
        cfg = get_system_config()
        cfg._data_dir = tmp_path
        cfg._file_path = tmp_path / "system.json"
        cfg.load()
        listen.settle_choice(cfg, [ETHERNET, WIFI], deployment_type="linux_package")
        use_plan("any")
        kwargs = {"client": client} if client else {}
        return TestClient(app, base_url=base_url, **kwargs), cfg

    start.engine = engine
    yield start
    rest.set_engine(None)
    ws.set_engine(None)
    reset_system_config()


def _on_disk(tmp_path) -> dict:
    path = tmp_path / "system.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _choose(c, mode, adapter=None):
    network = {"accept_connections_from": mode}
    if adapter is not None:
        network["accept_connections_adapter"] = adapter.identity()
    return c.patch("/api/system/config", json={"network": network})


class TestWhatGetReports:
    def test_the_running_choice_and_whether_a_restart_waits(self, api):
        c, _ = api()
        listen_block = c.get("/api/system/config").json()["_listen"]
        assert listen_block["mode"] == "any"
        assert listen_block["addresses"] == ["0.0.0.0"]
        assert listen_block["restart_needed"] is False
        assert listen_block["safety_net"] is None

        assert _choose(c, "adapter", ETHERNET).status_code == 200
        assert c.get("/api/system/config").json()["_listen"]["restart_needed"] is True

    def test_the_adapters_route_lists_each_adapter_once(self, api, monkeypatch):
        from openavc.discovery import network_scanner

        monkeypatch.setattr(network_scanner, "get_network_adapters", lambda: [])
        monkeypatch.setattr(network_scanner, "get_adapter_inventory", lambda: [
            {"name": "Ethernet", "mac": ETHERNET.mac, "addresses": [], "link": False,
             "has_address": False},
        ])
        c, _ = api()
        body = c.get("/api/network/adapters").json()
        assert body["adapters"] == []
        assert body["interfaces"][0]["name"] == "Ethernet"


class TestWhatPatchAccepts:
    def test_one_adapter_is_stored_by_identity_with_its_mirror(self, api, tmp_path):
        c, _ = api()
        assert _choose(c, "adapter", ETHERNET).status_code == 200
        network = _on_disk(tmp_path)["network"]
        assert network["accept_connections_from"] == "adapter"
        assert network["accept_connections_adapter"] == ETHERNET.identity()
        assert network["bind_address"] == "0.0.0.0"

    def test_another_choice_forgets_the_adapter(self, api, tmp_path):
        c, _ = api()
        _choose(c, "adapter", ETHERNET)
        assert _choose(c, "this_computer").status_code == 200
        network = _on_disk(tmp_path)["network"]
        assert network["accept_connections_adapter"] == {}
        assert network["bind_address"] == "127.0.0.1"

    def test_a_mode_that_is_not_a_choice_is_refused(self, api, tmp_path):
        c, _ = api()
        resp = _choose(c, "everyone")
        assert resp.status_code == 422
        assert "this_computer" in resp.json()["detail"]
        assert _on_disk(tmp_path).get("network", {}).get("accept_connections_from") != "everyone"

    def test_an_adapter_this_computer_lacks_is_refused(self, api):
        c, _ = api()
        resp = c.patch("/api/system/config", json={"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": {"name": "Ethernet 9", "mac": "00-00-00-00-00-09"},
        }})
        assert resp.status_code == 422
        assert "Ethernet 9" in resp.json()["detail"]

    def test_the_adapter_already_chosen_is_kept_while_it_is_unplugged(self, api, monkeypatch):
        c, _ = api()
        _choose(c, "adapter", ETHERNET)
        monkeypatch.setattr(listen, "list_adapters", lambda: [WIFI])
        assert _choose(c, "adapter", ETHERNET).status_code == 200

    def test_an_address_is_never_a_choice_from_here(self, api):
        c, _ = api()
        resp = c.patch("/api/system/config", json={"network": {
            "accept_connections_from": "adapter",
            "accept_connections_adapter": {"address": "10.20.30.5"},
        }})
        assert resp.status_code == 422

    def test_the_old_bind_address_is_refused_but_an_echo_passes(self, api):
        c, cfg = api()
        resp = c.patch("/api/system/config", json={"network": {"bind_address": "10.0.0.5"}})
        assert resp.status_code == 422
        assert "accept_connections_from" in resp.json()["detail"]
        echo = cfg.get("network", "bind_address")
        resp = c.patch("/api/system/config", json={"network": {"bind_address": echo}})
        assert resp.status_code == 200

    def test_the_environment_locks_it(self, api, tmp_path):
        c, _ = api(OPENAVC_BIND="0.0.0.0")
        resp = _choose(c, "this_computer")
        assert resp.status_code == 409
        assert "OPENAVC_BIND" in resp.json()["detail"]


class TestArmingTheSafetyNet:
    def test_dropping_the_network_this_browser_uses_arms_it(self, api, tmp_path):
        c, _ = api(base_url=WIFI_BROWSER)
        resp = _choose(c, "adapter", ETHERNET)
        assert resp.json()["safety_net"] == {
            "previous": "Any network",
            "choice": "Ethernet only",
            "running": False,
            "deadline": None,
        }
        assert (tmp_path / listen_safety_net.MARKER_NAME).exists()

    def test_staying_on_the_network_this_browser_uses_does_not(self, api, tmp_path):
        c, _ = api(base_url=ETHERNET_BROWSER)
        assert _choose(c, "adapter", ETHERNET).json()["safety_net"] is None
        assert not (tmp_path / listen_safety_net.MARKER_NAME).exists()

    def test_this_computers_own_screen_never_arms_it(self, api, tmp_path):
        c, _ = api(base_url="http://127.0.0.1:8080", client=("127.0.0.1", 50000))
        assert _choose(c, "this_computer").json()["safety_net"] is None
        assert not (tmp_path / listen_safety_net.MARKER_NAME).exists()

    def test_changing_back_to_the_running_choice_disarms_it(self, api, tmp_path):
        c, _ = api(base_url=WIFI_BROWSER)
        _choose(c, "adapter", ETHERNET)
        assert _choose(c, "any").json()["safety_net"] is None
        assert not (tmp_path / listen_safety_net.MARKER_NAME).exists()


class TestPreview:
    def test_a_cut_off_change_is_said_before_saving(self, api):
        c, _ = api(base_url=WIFI_BROWSER)
        result = c.post("/api/system/listen/preview", json={
            "accept_connections_from": "adapter",
            "accept_connections_adapter": ETHERNET.identity(),
        }).json()
        assert result["keeps_this_connection"] is False
        assert result["safety_net"] is True
        assert result["connected_through"] == "192.168.1.40"
        assert result["panel_addresses"] == ["10.20.30.5"]
        assert result["held_by"] is None

    def test_this_computer_only_lists_what_stops(self, api):
        c, _ = api()
        api.engine.isc = object()
        api.engine.panel_devices = SimpleNamespace(list_devices=lambda: {
            "approved": [{"address": "10.20.30.20"}, {"address": "192.168.1.9"}],
        })
        result = c.post(
            "/api/system/listen/preview", json={"accept_connections_from": "this_computer"}
        ).json()
        assert [x["code"] for x in result["consequences"]] == ["approved_panels", "inter_system"]
        assert result["consequences"][0]["message"] == "2 approved touch panels will stop connecting."

    def test_a_bad_choice_is_refused_the_same_way_patch_refuses_it(self, api):
        c, _ = api()
        resp = c.post("/api/system/listen/preview", json={"accept_connections_from": "lan"})
        assert resp.status_code == 422


class TestPreviewNamesCloudRemoteAccess:
    """The cut-off warning says "Remote access through OpenAVC Cloud keeps
    working." only when the preview says it is set up. Pairing writes
    cloud.json beside the project, and system.json's cloud section can read
    enabled: false with no system_id on a paired, connected system (found on
    a real one), so that section must not decide."""

    @pytest.fixture
    def project_dir(self, tmp_path, monkeypatch):
        import openavc.config as config

        folder = tmp_path / "project"
        folder.mkdir()
        monkeypatch.setattr(config, "PROJECT_PATH", str(folder / "project.avc"))
        return folder

    @staticmethod
    def _cut_off(c) -> dict:
        return c.post("/api/system/listen/preview", json={
            "accept_connections_from": "adapter",
            "accept_connections_adapter": ETHERNET.identity(),
        }).json()

    def test_a_running_agent_means_it_is_set_up(self, api, project_dir):
        c, cfg = api(base_url=WIFI_BROWSER)
        api.engine.cloud_agent = object()
        assert cfg.get("cloud", "enabled") is False
        assert self._cut_off(c)["cloud_remote_access"] is True

    def test_a_saved_pairing_means_it_is_set_up(self, api, project_dir):
        (project_dir / "cloud.json").write_text(json.dumps({
            "enabled": True, "system_id": "acme-room-1", "system_key": "k",
        }))
        c, _ = api(base_url=WIFI_BROWSER)
        assert self._cut_off(c)["cloud_remote_access"] is True

    def test_system_json_alone_says_nothing(self, api, tmp_path, project_dir):
        (tmp_path / "system.json").write_text(json.dumps({
            "cloud": {"enabled": True, "system_id": "acme-room-1"},
        }))
        c, _ = api(base_url=WIFI_BROWSER)
        assert self._cut_off(c)["cloud_remote_access"] is False


class TestConfirm:
    def test_it_keeps_the_choice_once_the_server_runs_it(self, api, tmp_path):
        c, _ = api(base_url=WIFI_BROWSER)
        _choose(c, "adapter", ETHERNET)
        # Not yet restarted: the old choice is still running, so loading the
        # Programmer through it proves nothing.
        assert c.post("/api/system/listen/confirm").json() == {"kept": None, "went_back": None}
        # The restart: the new choice runs and its clock starts.
        running = use_plan("adapter", ETHERNET)
        listen_safety_net.note_listening(tmp_path, running.choice)
        c2, _ = api(base_url=ETHERNET_BROWSER)
        listen.set_current(running)
        assert c2.post("/api/system/listen/confirm").json() == {
            "kept": "Kept: Accept connections from Ethernet only.",
            "went_back": None,
        }
        assert not (tmp_path / listen_safety_net.MARKER_NAME).exists()

    def test_the_went_back_sentence_is_handed_over_once(self, api, tmp_path):
        c, _ = api()
        (tmp_path / listen_safety_net.NOTICE_NAME).write_text(
            json.dumps({"message": "Went back to Any network because nobody opened the "
                                   "Programmer through Ethernet within 10 minutes."}),
            encoding="utf-8",
        )
        first = c.post("/api/system/listen/confirm").json()
        assert first["went_back"].startswith("Went back to Any network")
        assert c.post("/api/system/listen/confirm").json()["went_back"] is None
