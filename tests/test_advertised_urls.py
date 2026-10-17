"""Every surface that prints an address must first ask whether it works.

Three of them printed URLs the server does not serve. The startup banner and
``/api/setup/status`` both built ``http://<lan ip>:<port>/…`` from the
detected local IP without ever consulting the bind address, so a
loopback-bound instance — the default, and what a manual or checkout run gets
— advertised addresses the kernel refuses. The setup page
then appended ``.local`` to a hostname that already carried a domain,
and the auto-generated certificate put a dot-stripped hostname in its SAN
list, so the machine's own advertised name never matched even with
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
from tests.listen_helpers import use_bind

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
        use_bind(bind)
        assert config.loopback_only() is True

    @pytest.mark.parametrize("bind", ["0.0.0.0", "::", LAN_IP])
    def test_anything_reachable_is_not(self, bind, monkeypatch):
        use_bind(bind)
        assert config.loopback_only() is False


# ===========================================================================
# the startup banner
# ===========================================================================


class _Exited(BaseException):
    """Stand-in for os._exit. BaseException so `except Exception` in
    `_initialize_engine` cannot swallow a process exit and read it as a
    clean startup."""


def _banner(monkeypatch, caplog, bind: str, local_ip: str = LAN_IP, before=None) -> list[str]:
    """Run the real startup banner and return the lines it logged."""
    import openavc.main as main

    use_bind(bind)
    if before is not None:
        before()
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
        assert "this computer only" in lan[0] and "Accept connections from" in lan[0], (
            "say who can connect and where to change it"
        )

    def test_one_adapter_advertises_its_address_and_says_only(self, monkeypatch, caplog):
        lines = _banner(monkeypatch, caplog, LAN_IP)
        assert any(
            f"LAN access:  http://{LAN_IP}:8080/panel  (Ethernet only)" in ln for ln in lines
        )

    def test_a_missing_adapter_says_why_in_the_same_words_as_everywhere(
        self, monkeypatch, caplog
    ):
        from openavc import listen
        from tests.listen_helpers import ETHERNET, plan_for

        def missing(*_a, **_k):
            listen.set_current(plan_for("adapter", ETHERNET, adapters=[]))

        lines = _banner(monkeypatch, caplog, "0.0.0.0", before=missing)
        lan = [ln for ln in lines if "LAN access" in ln]
        assert lan == [
            "  LAN access:  none. Not accepting connections from the network: "
            "the Ethernet adapter isn't connected."
        ]

    def test_the_localhost_urls_are_untouched_by_either(self, monkeypatch, caplog):
        """Those are the ones that always work."""
        for bind in ("0.0.0.0", "127.0.0.1"):
            caplog.clear()
            lines = _banner(monkeypatch, caplog, bind)
            assert any("Panel UI:    http://localhost:8080/panel" in ln for ln in lines)
            assert any("Programmer:  http://localhost:8080/programmer" in ln for ln in lines)


# ===========================================================================
# /api/setup/status
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
    use_bind(bind)
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

    async def test_an_appliance_offers_no_url_its_own_screen_cannot_open(
        self, claimed, monkeypatch, tmp_path
    ):
        """The appliance's screen shows only the panel: no address bar to type
        localhost into. The page names the ways in that are left instead."""
        from openavc.updater import platform

        monkeypatch.setattr(
            platform, "detect_deployment_type", lambda: platform.DeploymentType.ANDROID_APPLIANCE,
        )
        monkeypatch.setattr(config, "PROJECT_PATH", str(tmp_path / "project.avc"))
        net = await _network(claimed, monkeypatch, bind="127.0.0.1", hostname="avc-1")

        assert net["local_only"] is True
        assert net["programmer_url"] is None
        assert net["panel_url"] is None
        assert net["own_screen_opens_programmer"] is False
        assert net["cloud_remote_access"] is False

        claimed.cloud_agent = object()
        net = await _network(claimed, monkeypatch, bind="127.0.0.1", hostname="avc-1")
        assert net["cloud_remote_access"] is True

    async def test_an_appliance_on_the_network_keeps_its_address(self, claimed, monkeypatch):
        from openavc.updater import platform

        monkeypatch.setattr(
            platform, "detect_deployment_type", lambda: platform.DeploymentType.ANDROID_APPLIANCE,
        )
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="avc-1")
        assert net["programmer_url"] == f"http://{LAN_IP}:8080/programmer"


class TestTheSetupScreenAppendsTheSuffixOnce:
    async def test_a_bare_hostname_gets_the_mdns_suffix(self, claimed, monkeypatch):
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="avc-1")
        assert net["hostname"] == "avc-1.local"

    async def test_a_dotted_hostname_keeps_the_one_it_has(self, claimed, monkeypatch):
        """The screen used to print `Sams-MacBook-Air.local.local`."""
        net = await _network(
            claimed, monkeypatch, bind="0.0.0.0", hostname="Sams-MacBook-Air.local"
        )
        assert net["hostname"] == "Sams-MacBook-Air.local"

    async def test_the_offline_fallback_url_is_not_doubled_either(
        self, claimed, monkeypatch
    ):
        """The half the doubled-.local fix missed: line 154 built the same doubled name into
        the URL the screen tells you to type."""
        net = await _network(
            claimed,
            monkeypatch,
            bind="0.0.0.0",
            hostname="Sams-MacBook-Air.local",
            ips=[],
        )
        assert net["online"] is False
        assert net["programmer_url"] == (
            "http://Sams-MacBook-Air.local:8080/programmer"
        )

    async def test_a_host_with_no_name_of_its_own_offers_none(self, claimed, monkeypatch):
        net = await _network(claimed, monkeypatch, bind="0.0.0.0", hostname="localhost")
        assert net["hostname"] is None

    def test_the_page_prints_the_name_it_is_given_and_decides_nothing(self):
        """Where the doubled .local was actually visible.

        The payload always carried a correct name; the page appended the
        suffix itself, in the Hostname row and in the SSH line, so a macOS or
        FQDN host read `Sams-MacBook-Air.local.local`. There is no jsdom
        harness for this page, so the rule is pinned at its source: the suffix
        is not the page's to add.
        """
        from openavc.api.routes import setup as setup_mod

        assert "'.local'" not in setup_mod._PAGE, (
            "the setup page must render network.hostname verbatim — the "
            "server already decided whether a suffix belongs on it"
        )

    def test_no_frontend_surface_appends_the_suffix_itself(self):
        """The rule had four copies and three of them were wrong.

        `/api/status` reports the RAW OS hostname, so every frontend surface
        that turns it into something typable has to apply the rule — and two
        did it inline and unconditionally (the Dashboard's Panel Access card
        and the hostname field in Settings > Network). They ask
        `api/hostnames.ts` now, and this is what catches the next inline copy:
        a `.local` string literal anywhere in the IDE source but that module.
        """
        src = Path(__file__).resolve().parent.parent / "openavc" / "web" / "programmer" / "src"
        home = src / "api" / "hostnames.ts"

        offenders = []
        for path in src.rglob("*.ts*"):
            if path == home or path.name.endswith(".test.ts") or path.name.endswith(".test.tsx"):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith(("*", "//")):
                    continue  # a comment naming the bug is not the bug
                if '".local"' in line or "'.local'" in line or ".local`" in line:
                    offenders.append(f"{path.relative_to(src)}:{n}: {line.strip()}")

        assert not offenders, (
            "these build a `.local` name inline instead of asking "
            "api/hostnames.ts, which is how `Sams-MacBook-Air.local.local` "
            "reached two screens:\n  " + "\n  ".join(offenders)
        )


# ===========================================================================
# the auto-generated certificate's SAN list
# ===========================================================================


class TestTheCertificateCoversTheNameTheHostAdvertises:
    def test_a_dotted_hostname_survives_into_the_san_list(self, monkeypatch):
        """It used to arrive as `Sams-MacBook-Airlocal` — dots stripped by a
        single-label sanitizer — so the CA install bought nothing."""
        monkeypatch.setattr(socket, "gethostname", lambda: "Sams-MacBook-Air.local")

        hostnames, _ = tls.collect_local_identifiers([])

        assert "Sams-MacBook-Air.local" in hostnames
        assert "Sams-MacBook-Airlocal" not in hostnames
        assert "localhost" in hostnames

    def test_a_bare_hostname_gets_its_mdns_name_too(self, monkeypatch):
        """`openavc.local` is what the advertiser announces and what /setup
        prints, so it is the name that gets typed."""
        monkeypatch.setattr(socket, "gethostname", lambda: "openavc")

        hostnames, _ = tls.collect_local_identifiers([])

        assert "openavc" in hostnames
        assert "openavc.local" in hostnames

    def test_the_setup_screen_and_the_certificate_agree(self, monkeypatch):
        """The screen prints a name; the cert has to carry that exact one, or
        the trusted-URL promise is empty. Both ask the same rule."""
        from openavc.utils.hostnames import resolvable_hostname

        for raw in ("openavc", "Sams-MacBook-Air.local", "box.corp.example.com"):
            monkeypatch.setattr(socket, "gethostname", lambda raw=raw: raw)
            hostnames, _ = tls.collect_local_identifiers([])
            assert resolvable_hostname(raw) in hostnames, raw
