"""Every surface that prints an address must first ask whether it works.

Three of them printed URLs the server does not serve. The startup banner and
``/api/setup/status`` both built ``http://<lan ip>:<port>/…`` from the
detected local IP without ever consulting the bind address, so a
loopback-bound instance — the default, and what a manual or checkout run gets
— advertised addresses the kernel refuses (F-001, F-001a). The setup page
then appended ``.local`` to a hostname that already carried a domain (F-003),
and the auto-generated certificate put a dot-stripped hostname in its SAN
list (F-052), so the machine's own advertised name never matched even with
the CA installed.

One rule each: the bind decides whether a LAN URL may be printed at all
(``config.loopback_only``), and the hostname decides its own suffix
(``utils.hostnames``). These cases pin both at the surfaces that got it
wrong.
"""

import json
import logging
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import openavc.api.auth as auth_mod
from openavc import config, tls
from openavc.api import rest
from openavc.core.engine import Engine
from openavc.core.project_loader import load_project
from openavc.main import app

EMPTY_PROJECT = {
    "project": {"id": "advertised_urls", "name": "Advertised URLs"},
    "devices": [],
    "variables": [],
    "macros": [],
    "ui": {"pages": [{"id": "main", "name": "Main", "elements": []}]},
}

LAN_IP = "192.168.1.20"


# ===========================================================================
# The predicate itself
# ===========================================================================


class TestWhatCountsAsLocalOnly:
    @pytest.mark.parametrize("bind", ["127.0.0.1", "::1", "[::1]", "localhost", " 127.0.0.1 "])
    def test_every_spelling_of_loopback(self, bind, monkeypatch):
        monkeypatch.setattr(config, "BIND_ADDRESS", bind)
        assert config.loopback_only() is True

    @pytest.mark.parametrize("bind", ["0.0.0.0", "::", LAN_IP])
    def test_anything_reachable_is_not(self, bind, monkeypatch):
        monkeypatch.setattr(config, "BIND_ADDRESS", bind)
        assert config.loopback_only() is False


# ===========================================================================
# F-001: the startup banner
# ===========================================================================


class _Exited(BaseException):
    """Stand-in for os._exit. BaseException so `except Exception` in
    `_initialize_engine` cannot swallow a process exit and read it as a
    clean startup."""


def _banner(monkeypatch, caplog, bind: str, local_ip: str = LAN_IP) -> list[str]:
    """Run the real startup banner and return the lines it logged."""
    import openavc.main as main

    monkeypatch.setattr(config, "BIND_ADDRESS", bind)
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)

    app_stub = SimpleNamespace(
        state=SimpleNamespace(engine_ready=False, engine_error=None)
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        with patch.object(main.engine, "start", new=AsyncMock()), \
                patch.object(
                    main.engine,
                    "get_status",
                    return_value={"local_ip": local_ip},
                ), \
                patch("openavc.updater.rollback.check_rollback_needed", return_value=None), \
                patch("openavc.updater.rollback.confirm_startup"), \
                patch.object(main.os, "_exit", side_effect=_Exited):
            import asyncio

            asyncio.run(main._initialize_engine(app_stub))
    return [r.getMessage() for r in caplog.records]


class TestTheStartupBannerConsultsTheBind:
    """It is the only instruction a manual or checkout run ever gets."""

    def test_a_lan_bind_advertises_the_lan_address(self, monkeypatch, caplog):
        lines = _banner(monkeypatch, caplog, "0.0.0.0")
        assert any(f"LAN access:  http://{LAN_IP}:8080/panel" in ln for ln in lines)

    def test_a_loopback_bind_says_so_instead_of_naming_an_address(self, monkeypatch, caplog):
        lines = _banner(monkeypatch, caplog, "127.0.0.1")

        lan = [ln for ln in lines if "LAN access" in ln]
        assert len(lan) == 1, "the banner still has to answer the question"
        assert LAN_IP not in lan[0], (
            "the kernel refuses this address on a loopback bind — printing it "
            "is an instruction that cannot be followed"
        )
        assert "127.0.0.1" in lan[0] and "0.0.0.0" in lan[0], (
            "say what it is bound to and what to change it to"
        )

    def test_the_localhost_urls_are_untouched_by_either(self, monkeypatch, caplog):
        """Those are the ones that always work."""
        for bind in ("0.0.0.0", "127.0.0.1"):
            caplog.clear()
            lines = _banner(monkeypatch, caplog, bind)
            assert any("Panel UI:    http://localhost:8080/panel" in ln for ln in lines)
            assert any("Programmer:  http://localhost:8080/programmer" in ln for ln in lines)


# ===========================================================================
# F-001a + F-003: /api/setup/status
# ===========================================================================


@pytest.fixture
async def claimed(monkeypatch):
    """Real app + engine with a password configured (the instance is claimed)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(EMPTY_PROJECT, f)
        tmp_path = f.name
    engine = Engine(tmp_path)
    engine.project = load_project(tmp_path)
    engine._running = True
    rest.set_engine(engine)

    monkeypatch.setattr(auth_mod, "_get_password", lambda: "advertised-secret-1")
    monkeypatch.setattr(auth_mod, "_get_username", lambda: "")
    monkeypatch.setattr(auth_mod, "_get_api_key", lambda: "")

    yield engine

    rest.set_engine(None)
    Path(tmp_path).unlink(missing_ok=True)


async def _network(engine, monkeypatch, *, bind: str, hostname: str, ips=None) -> dict:
    ips = [LAN_IP] if ips is None else ips
    monkeypatch.setattr(config, "BIND_ADDRESS", bind)
    monkeypatch.setattr(
        engine,
        "refresh_network_info",
        lambda: (ips[0] if ips else "127.0.0.1", hostname, list(ips)),
    )
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        resp = await c.get("/api/setup/status")
        assert resp.status_code == 200
        return resp.json()["network"]


class TestTheSetupScreenConsultsTheBind:
    """/setup exists to tell somebody which URL to open from a laptop."""

    async def test_a_lan_bind_hands_out_the_lan_address(self, claimed, monkeypatch):
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="avc-1")

        assert net["local_only"] is False
        assert net["programmer_url"] == f"http://{LAN_IP}:8080/programmer"
        assert net["panel_url"] == f"http://{LAN_IP}:8080/panel"

    async def test_a_loopback_bind_hands_out_the_only_address_that_works(
        self, claimed, monkeypatch
    ):
        net = await _network(claimed, monkeypatch, bind="127.0.0.1", hostname="avc-1")

        assert net["local_only"] is True
        assert net["bind_address"] == "127.0.0.1"
        assert net["programmer_url"] == "http://localhost:8080/programmer"
        assert net["panel_url"] == "http://localhost:8080/panel"
        assert LAN_IP not in json.dumps(
            [net["programmer_url"], net["panel_url"], net["other_programmer_urls"]]
        ), "no URL on this screen may be built from an address nothing answers"

    async def test_the_alternate_legs_go_away_rather_than_lie(self, claimed, monkeypatch):
        """A multi-homed box on a loopback bind serves none of its legs."""
        legs = [LAN_IP, "10.50.0.20"]
        net = await _network(
            claimed, monkeypatch, bind="127.0.0.1", hostname="avc-1", ips=legs
        )

        assert net["other_programmer_urls"] == []
        assert net["ips"] == legs, (
            "the addresses themselves are still true of the machine, and they "
            "are what the integrator needs once the bind is opened"
        )

    async def test_a_lan_bind_keeps_offering_them(self, claimed, monkeypatch):
        net = await _network(
            claimed,
            monkeypatch,
            bind="0.0.0.0",
            hostname="avc-1",
            ips=[LAN_IP, "10.50.0.20"],
        )
        assert net["other_programmer_urls"] == ["http://10.50.0.20:8080/programmer"]


class TestTheSetupScreenAppendsTheSuffixOnce:
    async def test_a_bare_hostname_gets_the_mdns_suffix(self, claimed, monkeypatch):
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="avc-1")
        assert net["hostname"] == "avc-1.local"

    async def test_a_dotted_hostname_keeps_the_one_it_has(self, claimed, monkeypatch):
        """F-003: the screen used to print `Aarons-MacBook-Air.local.local`."""
        net = await _network(
            claimed, monkeypatch, bind="0.0.0.0", hostname="Aarons-MacBook-Air.local"
        )
        assert net["hostname"] == "Aarons-MacBook-Air.local"

    async def test_the_offline_fallback_url_is_not_doubled_either(
        self, claimed, monkeypatch
    ):
        """The half F-003 missed: line 154 built the same doubled name into
        the URL the screen tells you to type."""
        net = await _network(
            claimed,
            monkeypatch,
            bind="0.0.0.0",
            hostname="Aarons-MacBook-Air.local",
            ips=[],
        )
        assert net["online"] is False
        assert net["programmer_url"] == (
            "http://Aarons-MacBook-Air.local:8080/programmer"
        )

    async def test_a_host_with_no_name_of_its_own_offers_none(self, claimed, monkeypatch):
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="localhost")
        assert net["hostname"] is None

    def test_the_page_prints_the_name_it_is_given_and_decides_nothing(self):
        """Where F-003 was actually visible.

        The payload always carried a correct name; the page appended the
        suffix itself, in the Hostname row and in the SSH line, so a macOS or
        FQDN host read `Aarons-MacBook-Air.local.local`. There is no jsdom
        harness for this page, so the rule is pinned at its source: the suffix
        is not the page's to add.
        """
        from openavc.api.routes import setup as setup_mod

        assert "'.local'" not in setup_mod._PAGE, (
            "the setup page must render network.hostname verbatim — the "
            "server already decided whether a suffix belongs on it"
        )


# ===========================================================================
# F-052: the auto-generated certificate's SAN list
# ===========================================================================


class TestTheCertificateCoversTheNameTheHostAdvertises:
    def test_a_dotted_hostname_survives_into_the_san_list(self, monkeypatch):
        """It used to arrive as `Aarons-MacBook-Airlocal` — dots stripped by a
        single-label sanitizer — so the CA install bought nothing."""
        monkeypatch.setattr(socket, "gethostname", lambda: "Aarons-MacBook-Air.local")

        hostnames, _ = tls.collect_local_identifiers("127.0.0.1")

        assert "Aarons-MacBook-Air.local" in hostnames
        assert "Aarons-MacBook-Airlocal" not in hostnames
        assert "localhost" in hostnames

    def test_a_bare_hostname_gets_its_mdns_name_too(self, monkeypatch):
        """`openavc.local` is what the advertiser announces and what /setup
        prints, so it is the name that gets typed."""
        monkeypatch.setattr(socket, "gethostname", lambda: "openavc")

        hostnames, _ = tls.collect_local_identifiers("127.0.0.1")

        assert "openavc" in hostnames
        assert "openavc.local" in hostnames

    def test_the_setup_screen_and_the_certificate_agree(self, monkeypatch):
        """The screen prints a name; the cert has to carry that exact one, or
        the trusted-URL promise is empty. Both ask the same rule."""
        from openavc.utils.hostnames import resolvable_hostname

        for raw in ("openavc", "Aarons-MacBook-Air.local", "box.corp.example.com"):
            monkeypatch.setattr(socket, "gethostname", lambda raw=raw: raw)
            hostnames, _ = tls.collect_local_identifiers("127.0.0.1")
            assert resolvable_hostname(raw) in hostnames, raw
