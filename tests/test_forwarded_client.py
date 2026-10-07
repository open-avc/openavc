"""Who a reverse proxy says the client is, and when that is believed.

``X-Forwarded-For`` is a header anyone can send. It is believed only from a
proxy the operator declared (``network.trust_forwarded_for`` on, and the peer
loopback or inside ``network.trusted_proxies``), and read from the right, so a
client can neither reach the port directly and name itself nor prepend a name
for an appending proxy to carry. uvicorn reads the same header first for a
loopback peer; the two have to agree, and the peers uvicorn trusts are pinned
on every listener so its own environment variable cannot widen them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import _TrustedHosts

from openavc.utils import request_origin
from openavc.utils.request_origin import (
    UVICORN_FORWARDED_ALLOW_IPS,
    forwarded_client,
    parse_trusted_proxies,
    peer_address,
)

ROOT = Path(__file__).resolve().parent.parent


def _request(peer: str, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/status",
        "headers": headers,
        "client": (peer, 50000),
        "query_string": b"",
    })


@pytest.fixture
def behind_proxy(monkeypatch):
    """trust_forwarded_for on; returns a setter for trusted_proxies."""
    monkeypatch.setattr("openavc.config.TRUST_FORWARDED_FOR", True)
    monkeypatch.setattr("openavc.config.TRUSTED_PROXIES", [])

    def _set(value):
        monkeypatch.setattr("openavc.config.TRUSTED_PROXIES", value)

    return _set


# ---------------------------------------------------------------------------
# Who may speak for the client
# ---------------------------------------------------------------------------


def test_the_header_is_ignored_while_the_setting_is_off(monkeypatch):
    monkeypatch.setattr("openavc.config.TRUST_FORWARDED_FOR", False)
    req = _request("127.0.0.1", "198.51.100.9")
    assert forwarded_client(req) is None
    assert peer_address(req) == "127.0.0.1"


def test_a_client_reaching_the_port_directly_cannot_name_itself(behind_proxy):
    """The defect: with the setting on, any peer's header was believed, so a
    LAN client wrote 127.0.0.1 and was the machine itself to the limiter."""
    req = _request("192.168.4.20", "127.0.0.1")
    assert forwarded_client(req) is None
    assert peer_address(req) == "192.168.4.20"


def test_an_appending_proxy_is_read_from_the_right(behind_proxy):
    """nginx's $proxy_add_x_forwarded_for appends the real address to what the
    client sent; the client's own value at the front is not the answer."""
    req = _request("127.0.0.1", "127.0.0.1, 198.51.100.9")
    assert forwarded_client(req) == "198.51.100.9"
    assert peer_address(req) == "198.51.100.9"


def test_a_replacing_proxy_names_its_client(behind_proxy):
    assert forwarded_client(_request("127.0.0.1", "198.51.100.10")) == "198.51.100.10"


def test_a_proxy_over_ipv6_loopback_is_a_proxy(behind_proxy):
    assert forwarded_client(_request("::1", "198.51.100.11")) == "198.51.100.11"


def test_a_proxy_on_another_machine_is_believed_only_once_listed(behind_proxy):
    req = _request("10.0.0.5", "127.0.0.1, 198.51.100.12")
    assert forwarded_client(req) is None
    assert peer_address(req) == "10.0.0.5"

    behind_proxy(["10.0.0.5"])
    assert forwarded_client(req) == "198.51.100.12"


def test_a_listed_range_covers_its_addresses_and_a_chain_of_them(behind_proxy):
    behind_proxy(["10.0.1.0/24"])
    # Two proxies of ours in a row: both skipped, the client is what is left.
    req = _request("10.0.1.7", "198.51.100.13, 10.0.1.8")
    assert forwarded_client(req) == "198.51.100.13"
    # Outside the range: not ours, header ignored.
    assert forwarded_client(_request("10.0.2.7", "198.51.100.13")) is None


def test_a_proxy_s_own_word_never_earns_loopback(behind_proxy):
    """Every entry is a proxy: the leftmost is the answer, as uvicorn does. It
    is still a forwarded answer, which the limiter never exempts (see
    tests/test_rate_limit.py); here it is only the label."""
    behind_proxy(["10.0.0.5"])
    assert forwarded_client(_request("10.0.0.5", "127.0.0.1")) == "127.0.0.1"


@pytest.mark.parametrize("header", ["", " ", ",", " , "])
def test_an_empty_header_falls_back_to_the_peer(behind_proxy, header):
    req = _request("127.0.0.1", header)
    assert forwarded_client(req) is None
    assert peer_address(req) == "127.0.0.1"


# ---------------------------------------------------------------------------
# network.trusted_proxies
# ---------------------------------------------------------------------------


def test_trusted_proxies_takes_a_list_or_a_comma_separated_string():
    as_list, bad = parse_trusted_proxies(["10.0.0.5", " 10.0.1.0/24 "])
    assert bad == []
    as_text, bad = parse_trusted_proxies("10.0.0.5, 10.0.1.0/24")
    assert bad == []
    assert as_list == as_text
    assert [str(n) for n in as_list] == ["10.0.0.5/32", "10.0.1.0/24"]


@pytest.mark.parametrize("entry", ["nope", "10.0.0.300", "10.0.0.0/33", 10, None, {"a": 1}])
def test_an_entry_that_is_not_an_address_is_named(entry):
    _, bad = parse_trusted_proxies(["10.0.0.5", entry])
    assert bad == [str(entry)]


@pytest.mark.parametrize("value", [["10.0.0.5", "nope"], ["10.0.0.5", 10], ["10.0.0.5", {"a": 1}], "10.0.0.5,nope"])
def test_a_hand_edited_bad_entry_is_dropped_and_the_rest_still_work(behind_proxy, value, caplog):
    behind_proxy(value)
    request_origin._proxy_networks_for.cache_clear()
    assert forwarded_client(_request("10.0.0.5", "198.51.100.14")) == "198.51.100.14"
    assert "network.trusted_proxies: ignoring" in caplog.text


def _config_at(tmp_path):
    from openavc.system_config import SystemConfig

    cfg = SystemConfig()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    return cfg


def test_the_environment_variable_is_comma_separated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAVC_TRUSTED_PROXIES", "10.0.0.5, 10.0.1.0/24,")
    cfg = _config_at(tmp_path)
    cfg.load()
    assert cfg.get("network", "trusted_proxies") == ["10.0.0.5", "10.0.1.0/24"]
    assert cfg.env_override("network", "trusted_proxies") == "OPENAVC_TRUSTED_PROXIES"


def test_the_default_trusts_no_other_machine(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAVC_TRUSTED_PROXIES", raising=False)
    cfg = _config_at(tmp_path)
    cfg.load()
    assert cfg.get("network", "trusted_proxies") == []


# ---------------------------------------------------------------------------
# uvicorn, the first reader
# ---------------------------------------------------------------------------


def test_every_peer_uvicorn_trusts_is_a_proxy_here_too():
    """The composition rule (request_origin's docstring): a peer uvicorn has
    already resolved is either a proxy, whose header is walked again by the
    same rule, or a client, whose header is ignored. That holds only while
    this module's proxies include uvicorn's."""
    for host in UVICORN_FORWARDED_ALLOW_IPS:
        assert request_origin._is_proxy(host, ()), host


@pytest.mark.parametrize("header", [
    "198.51.100.9",
    "127.0.0.1, 198.51.100.9",
    "198.51.100.9, 127.0.0.1",
    "203.0.113.1, 198.51.100.9, ::1",
    "127.0.0.1",
    "127.0.0.1, ::1",
])
def test_the_walk_agrees_with_uvicorn_s(behind_proxy, header):
    """Same header, same trusted set, same answer: right to left, skip the
    proxies, leftmost when they all are."""
    uvicorn_answer = _TrustedHosts(list(UVICORN_FORWARDED_ALLOW_IPS)).get_trusted_client_host(header)
    assert forwarded_client(_request("127.0.0.1", header)) == uvicorn_answer


def test_uvicorn_s_variable_cannot_widen_the_pinned_peers(monkeypatch):
    """FORWARDED_ALLOW_IPS=* made uvicorn take the leftmost entry from anyone,
    so a LAN client that sent 127.0.0.1 was handed the console. With the pin
    passed explicitly, the variable is not read."""
    import uvicorn

    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")

    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]

    config = uvicorn.Config(app, forwarded_allow_ips=list(UVICORN_FORWARDED_ALLOW_IPS))
    config.load()

    import asyncio

    scope = {
        "type": "http",
        "client": ("192.168.4.20", 50000),
        "headers": [(b"x-forwarded-for", b"127.0.0.1")],
    }
    asyncio.run(config.loaded_app(scope, None, None))
    assert seen["client"][0] == "192.168.4.20"


def test_every_listener_pins_the_peers():
    """Each uvicorn.Config / uvicorn.run in main.py passes the pin; a new
    listener without it would read FORWARDED_ALLOW_IPS again."""
    tree = ast.parse((ROOT / "openavc" / "main.py").read_text(encoding="utf-8"))

    pin_sources = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_FORWARDED_ALLOW_IPS" for t in node.targets)
    ]
    assert len(pin_sources) == 1
    assert ast.unparse(pin_sources[0]) == "list(UVICORN_FORWARDED_ALLOW_IPS)"

    listeners = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
            and node.func.attr in {"Config", "run"}
        ):
            listeners.append(node)
    assert len(listeners) >= 4, "the four listeners main.py starts"
    for call in listeners:
        pinned = [
            kw for kw in call.keywords
            if kw.arg == "forwarded_allow_ips"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "_FORWARDED_ALLOW_IPS"
        ]
        assert pinned, f"main.py:{call.lineno} starts a listener without forwarded_allow_ips"


# ---------------------------------------------------------------------------
# The config door
# ---------------------------------------------------------------------------


@pytest.fixture
def config_api(tmp_path, monkeypatch, isolated_auth_config):
    """The real app on a system.json in tmp_path."""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient

    from openavc.api import rest, ws
    from openavc.core.event_bus import EventBus
    from openavc.core.state_store import StateStore
    from openavc.main import app
    from openavc.system_config import get_system_config, reset_system_config

    monkeypatch.delenv("OPENAVC_TRUSTED_PROXIES", raising=False)
    engine = MagicMock()
    state = StateStore()
    state.set_event_bus(EventBus())
    engine.state = state
    engine.panel_access_changed = AsyncMock()
    engine.reconcile_runtime_services = AsyncMock()
    rest.set_engine(engine)
    ws.set_engine(engine)
    reset_system_config()
    cfg = get_system_config()
    cfg._data_dir = tmp_path
    cfg._file_path = tmp_path / "system.json"
    cfg.load()
    yield TestClient(app), cfg
    rest.set_engine(None)
    ws.set_engine(None)
    reset_system_config()


def test_a_list_of_addresses_and_ranges_saves(config_api):
    client, cfg = config_api
    resp = client.patch("/api/system/config", json={"network": {"trusted_proxies": ["10.0.0.5", "10.0.1.0/24"]}})
    assert resp.status_code == 200, resp.text
    assert cfg.get("network", "trusted_proxies") == ["10.0.0.5", "10.0.1.0/24"]


@pytest.mark.parametrize("value, named", [
    (["10.0.0.5", "proxy.example"], "proxy.example"),
    (["10.0.0.5", 10], "10"),
    ("10.0.0.5", None),
    ({"proxy": "10.0.0.5"}, None),
])
def test_anything_else_is_refused_and_nothing_is_written(config_api, value, named):
    client, cfg = config_api
    resp = client.patch("/api/system/config", json={"network": {"trusted_proxies": value}})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail.startswith("Trusted proxies is a list of addresses or ranges")
    if named:
        assert f"Not an address or range: {named}." in detail
    assert cfg.get("network", "trusted_proxies") == []
