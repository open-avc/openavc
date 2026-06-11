"""Host network configuration: nmcli parsing, validation, rollback, API gating.

Platform tests with a fake nmcli runner — no NetworkManager is required (or
available) in CI. The contract pinned here:

- terse-output parsing handles escaped colons (MACs, SSIDs) and indexed keys
- static-IP validation rejects unusable input and warns on soft concerns
- a failed activation rolls the connection back to its previous config
- the API 404s when no backend exists (the UI-hiding signal) and allows
  loopback callers without credentials (the on-device bootstrap path)
"""

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import server.api.auth as auth_mod
from server.main import app
from server.system import network as netmod
from server.system.network import (
    NmcliBackend,
    parse_keyed_terse,
    split_terse_line,
    validate_hostname,
    validate_static_ipv4,
)


# --- Terse parsing ---


def test_split_terse_line_unescapes_colons():
    line = r"GENERAL.HWADDR:AA\:BB\:CC\:DD\:EE\:FF"
    assert split_terse_line(line) == ["GENERAL.HWADDR", "AA:BB:CC:DD:EE:FF"]


def test_split_terse_line_plain_fields():
    assert split_terse_line("eth0:ethernet:connected:Wired connection 1") == [
        "eth0", "ethernet", "connected", "Wired connection 1",
    ]


def test_parse_keyed_terse_collects_indexed_keys():
    out = (
        "GENERAL.HWADDR:AA\\:BB\\:CC\\:DD\\:EE\\:FF\n"
        "IP4.ADDRESS[1]:192.168.4.20/24\n"
        "IP4.GATEWAY:192.168.4.1\n"
        "IP4.DNS[1]:8.8.8.8\n"
        "IP4.DNS[2]:1.1.1.1\n"
    )
    fields = parse_keyed_terse(out)
    assert fields["GENERAL.HWADDR"] == ["AA:BB:CC:DD:EE:FF"]
    assert fields["IP4.ADDRESS"] == ["192.168.4.20/24"]
    assert fields["IP4.DNS"] == ["8.8.8.8", "1.1.1.1"]


# --- Validation ---


def test_validate_static_accepts_normal_config():
    addr, gw, dns, warnings = validate_static_ipv4(
        "192.168.1.50/24", "192.168.1.1", ["8.8.8.8", "1.1.1.1"]
    )
    assert addr == "192.168.1.50/24"
    assert gw == "192.168.1.1"
    assert dns == ["8.8.8.8", "1.1.1.1"]
    assert warnings == []


def test_validate_static_requires_prefix():
    with pytest.raises(ValueError, match="prefix"):
        validate_static_ipv4("192.168.1.50", "192.168.1.1", [])


def test_validate_static_rejects_garbage_address():
    with pytest.raises(ValueError):
        validate_static_ipv4("not-an-ip/24", None, [])


def test_validate_static_rejects_bad_gateway():
    with pytest.raises(ValueError, match="gateway"):
        validate_static_ipv4("192.168.1.50/24", "299.0.0.1", [])


def test_validate_static_rejects_bad_dns():
    with pytest.raises(ValueError, match="DNS"):
        validate_static_ipv4("192.168.1.50/24", None, ["8.8.8.999"])


def test_validate_static_warns_on_gateway_outside_subnet():
    _, _, _, warnings = validate_static_ipv4("192.168.1.50/24", "10.0.0.1", [])
    assert len(warnings) == 1
    assert "outside" in warnings[0]


def test_validate_static_rejects_loopback():
    with pytest.raises(ValueError):
        validate_static_ipv4("127.0.0.5/8", None, [])


def test_validate_hostname():
    assert validate_hostname("room-101") == "room-101"
    for bad in ("", "-leading", "trailing-", "spa ce", "a" * 254):
        with pytest.raises(ValueError):
            validate_hostname(bad)


# --- NmcliBackend orchestration (fake runner) ---


class FakeRunner:
    """Stands in for NmcliBackend._run. Routes on the nmcli subcommand and
    records every invocation for assertions."""

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.responses: dict[str, tuple[int, str, str]] = {}
        self.up_results: list[tuple[int, str, str]] = []

    async def __call__(self, *args: str, timeout: float = 20):
        self.calls.append(args)
        joined = " ".join(args)
        if "connection up" in joined:
            return self.up_results.pop(0) if self.up_results else (0, "", "")
        for key, resp in self.responses.items():
            if key in joined:
                return resp
        return (0, "", "")


DEVICE_STATUS = (
    "eth0:ethernet:connected:Wired connection 1\n"
    "wlan0:wifi:disconnected:\n"
    "lo:loopback:unmanaged:\n"
)

DEVICE_SHOW = (
    "GENERAL.HWADDR:AA\\:BB\\:CC\\:DD\\:EE\\:FF\n"
    "IP4.ADDRESS[1]:192.168.4.20/24\n"
    "IP4.GATEWAY:192.168.4.1\n"
    "IP4.DNS[1]:192.168.4.1\n"
)

CONNECTION_SHOW = (
    "ipv4.method:auto\n"
    "ipv4.addresses:\n"
    "ipv4.gateway:\n"
    "ipv4.dns:\n"
)


@pytest.fixture
def backend():
    b = NmcliBackend()
    runner = FakeRunner()
    b._run = runner
    b._fake = runner  # test-side handle
    return b


async def test_get_status_shape(backend):
    backend._fake.responses["device status"] = (0, DEVICE_STATUS, "")
    backend._fake.responses["device show"] = (0, DEVICE_SHOW, "")
    backend._fake.responses["connection show"] = (0, CONNECTION_SHOW, "")
    backend._fake.responses["general hostname"] = (0, "openavc\n", "")

    status = await backend.get_status()
    assert status["backend"] == "nmcli"
    assert status["hostname"] == "openavc"
    assert status["capabilities"]["wifi"] is True

    devices = {i["device"]: i for i in status["interfaces"]}
    assert set(devices) == {"eth0", "wlan0"}  # loopback filtered out
    eth = devices["eth0"]
    assert eth["mac"] == "AA:BB:CC:DD:EE:FF"
    assert eth["ip4"]["addresses"] == ["192.168.4.20/24"]
    assert eth["config"]["method"] == "auto"
    assert devices["wlan0"]["connection"] is None
    assert devices["wlan0"]["config"] is None


async def test_set_ipv4_static_happy_path(backend):
    backend._fake.responses["connection show"] = (0, CONNECTION_SHOW, "")
    backend._fake.up_results = [(0, "", "")]

    result = await backend.set_ipv4(
        "Wired connection 1", "manual",
        address="192.168.1.50/24", gateway="192.168.1.1", dns=["8.8.8.8"],
    )
    assert result == {"ok": True, "rolled_back": False}

    mods = [c for c in backend._fake.calls if c[:2] == ("connection", "modify")]
    assert len(mods) == 1
    assert "ipv4.method" in mods[0] and "manual" in mods[0]
    assert "192.168.1.50/24" in mods[0]


async def test_set_ipv4_rolls_back_on_activation_failure(backend):
    backend._fake.responses["connection show"] = (
        0,
        "ipv4.method:manual\n"
        "ipv4.addresses:192.168.4.20/24\n"
        "ipv4.gateway:192.168.4.1\n"
        "ipv4.dns:192.168.4.1\n",
        "",
    )
    # First activation fails, the rollback activation succeeds.
    backend._fake.up_results = [
        (4, "", "Error: Connection activation failed: no DHCP lease"),
        (0, "", ""),
    ]

    result = await backend.set_ipv4("Wired connection 1", "auto")
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert "activation failed" in result["error"].lower()

    mods = [c for c in backend._fake.calls if c[:2] == ("connection", "modify")]
    assert len(mods) == 2  # the change, then the restore
    restore = mods[1]
    assert "192.168.4.20/24" in restore  # previous address restored
    assert "manual" in restore  # previous method restored


async def test_wifi_scan_dedupes_and_sorts(backend):
    backend._fake.responses["device wifi list"] = (
        0,
        "*:ShopNet:72:WPA2\n"
        ":ShopNet:55:WPA2\n"
        ":Cafe\\:Guest:88:\n"
        ":  :40:WPA2\n",  # entries with blank SSID get skipped below
        "",
    )
    # blank-SSID row: nmcli emits truly empty SSID for hidden networks
    backend._fake.responses["device wifi list"] = (
        0,
        "*:ShopNet:72:WPA2\n"
        ":ShopNet:55:WPA2\n"
        ":Cafe\\:Guest:88:\n"
        "::40:WPA2\n",
        "",
    )
    networks = await backend.wifi_scan()
    ssids = [n["ssid"] for n in networks]
    assert ssids == ["Cafe:Guest", "ShopNet"]  # strongest first, deduped, no hidden
    shop = next(n for n in networks if n["ssid"] == "ShopNet")
    assert shop["signal"] == 72
    assert shop["in_use"] is True
    cafe = next(n for n in networks if n["ssid"] == "Cafe:Guest")
    assert cafe["secured"] is False


async def test_wifi_connect_surfaces_error(backend):
    backend._fake.responses["device wifi connect"] = (
        4, "", "Error: Secrets were required, but not provided.\nmore noise",
    )
    result = await backend.wifi_connect("ShopNet", "wrong-psk")
    assert result["ok"] is False
    assert result["error"] == "Error: Secrets were required, but not provided."


# --- API endpoints ---


class StubBackend(NmcliBackend):
    async def get_status(self):
        return {"backend": "stub", "hostname": "x", "capabilities": {}, "interfaces": []}


@pytest.fixture
def no_backend(monkeypatch):
    monkeypatch.setattr(netmod, "get_backend", lambda: None)
    return TestClient(app)


@pytest.fixture
def with_backend(monkeypatch):
    stub = StubBackend()
    monkeypatch.setattr(netmod, "get_backend", lambda: stub)
    # Open instance (dev posture) so remote calls don't need credentials here;
    # auth-specific behavior is tested separately below.
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    monkeypatch.setattr(auth_mod, "anonymous_access_allowed", lambda: True)
    return TestClient(app)


async def test_endpoints_404_without_backend(no_backend, monkeypatch):
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")
    monkeypatch.setattr(auth_mod, "anonymous_access_allowed", lambda: True)
    assert no_backend.get("/api/system/network").status_code == 404
    assert no_backend.post(
        "/api/system/network/ipv4",
        json={"connection": "x", "method": "auto", "confirmed": True},
    ).status_code == 404


async def test_status_passthrough(with_backend):
    resp = with_backend.get("/api/system/network")
    assert resp.status_code == 200
    assert resp.json()["backend"] == "stub"


async def test_ipv4_dry_run_returns_warnings(with_backend):
    resp = with_backend.post(
        "/api/system/network/ipv4",
        json={
            "connection": "Wired connection 1",
            "method": "manual",
            "address": "192.168.1.50/24",
            "gateway": "10.0.0.1",
            "confirmed": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] is False
    assert any("outside" in w for w in body["warnings"])


async def test_ipv4_invalid_address_is_400(with_backend):
    resp = with_backend.post(
        "/api/system/network/ipv4",
        json={"connection": "x", "method": "manual", "address": "bogus", "confirmed": True},
    )
    assert resp.status_code == 400


async def test_hostname_invalid_is_400(with_backend):
    resp = with_backend.post(
        "/api/system/network/hostname", json={"hostname": "-bad-"}
    )
    assert resp.status_code == 400


# --- Deployment-provided backend hook ---


class _ProvidedBackend(NmcliBackend):
    name = "provided"


async def test_deployment_backend_module_wins(monkeypatch):
    """network.backend_module loads first, beating built-in detection."""
    import sys
    import types

    module = types.ModuleType("fake_net_backend")
    module.create_backend = lambda: _ProvidedBackend()
    monkeypatch.setitem(sys.modules, "fake_net_backend", module)

    import server.system_config as syscfg
    cfg = syscfg.get_system_config()
    monkeypatch.setattr(
        cfg, "get",
        lambda section, key, default=None: "fake_net_backend"
        if (section, key) == ("network", "backend_module") else default,
    )
    netmod.reset_backend_cache()
    try:
        backend = netmod.get_backend()
        assert backend is not None and backend.name == "provided"
    finally:
        netmod.reset_backend_cache()


async def test_broken_backend_module_falls_through(monkeypatch):
    """A module that fails to import or create never breaks detection."""
    import server.system_config as syscfg
    cfg = syscfg.get_system_config()
    monkeypatch.setattr(
        cfg, "get",
        lambda section, key, default=None: "module_that_does_not_exist"
        if (section, key) == ("network", "backend_module") else default,
    )
    monkeypatch.setattr(netmod, "_nmcli_running", lambda: False)
    netmod.reset_backend_cache()
    try:
        assert netmod.get_backend() is None  # fell through cleanly
    finally:
        netmod.reset_backend_cache()


# --- Auth: loopback bootstrap vs remote callers ---


@pytest.fixture
def claimed_with_backend(monkeypatch):
    stub = StubBackend()
    monkeypatch.setattr(netmod, "get_backend", lambda: stub)
    monkeypatch.setattr(auth_mod, "_get_password", lambda: "secret-pw-123")
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")


async def test_remote_anonymous_is_401_on_claimed_instance(claimed_with_backend):
    client = TestClient(app)  # client host "testclient" = remote
    assert client.get("/api/system/network").status_code == 401


async def test_remote_authenticated_is_allowed(claimed_with_backend):
    client = TestClient(app)
    resp = client.get("/api/system/network", auth=("admin", "secret-pw-123"))
    assert resp.status_code == 200


async def test_loopback_anonymous_is_allowed(claimed_with_backend):
    """The on-device bootstrap path: the device's own screen needs network
    config before the device has any network or credentials."""
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.get("/api/system/network")
    assert resp.status_code == 200
