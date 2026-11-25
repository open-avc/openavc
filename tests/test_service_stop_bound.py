"""Every install's service manager gives the server's stop its whole time.

A service stop is a signal from whatever manages the server, then a wait,
then a kill. The server's side runs in ``core/stop_stages.py``'s numbers: the
requests still open get ``DRAIN_SECONDS``, the engine's stop gets
``STOP_SECONDS``, and the whole of it fits ``SIGNAL_STOP_SECONDS``. Each
manager here is told to wait ``SERVICE_STOP_SECONDS`` and to send a signal the
server handles; one left at its own default cut the stop short (launchd's
5 s, NSSM's 1.5 s after its Ctrl+C) or waited long past it on a process that
could not exit (systemd's 90 s).

- Linux and the Pi: the systemd unit, which install.sh installs, and the
  unit the deployment guide prints (an update puts the shipped one on every
  install at its next start: update-helper.sh).
- Windows: NSSM's stop methods, set by install-service.bat at every install
  and update.
- Docker: the compose files' stop_grace_period, and the image's STOPSIGNAL.
  A compose file that names no grace gets Docker's 10 s, which the stop
  still fits.
- macOS: the LaunchDaemon's ExitTimeOut. An in-app update never replaces the
  installed plist, so the run wrapper adds the bound to one that has none
  (from the server's first start after the update, since the wrapper that
  applies an update is the one it replaces); launchd reads it the next time
  it loads the job.
"""

from __future__ import annotations

import asyncio
import plistlib
import re
import signal
import time
from pathlib import Path

import pytest
import yaml
from uvicorn.server import HANDLED_SIGNALS

from openavc.core.stop_stages import (
    DRAIN_SECONDS,
    SERVICE_STOP_SECONDS,
    SIGNAL_STOP_SECONDS,
    STOP_SECONDS,
)
from tests.macos_launch_settings import MAC_WRAPPER, sandboxed_mac

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "installer"
UNIT = INSTALLER / "openavc.service"
DEPLOYMENT_GUIDE = REPO_ROOT / "docs" / "deployment.md"
INSTALL_SERVICE_BAT = INSTALLER / "install-service.bat"
COMPOSE_FILES = [INSTALLER / "docker-compose.yml", INSTALLER / "docker-compose.multi-room.yml"]
DOCKERFILE = REPO_ROOT / "Dockerfile"
MAC_PLIST = INSTALLER / "com.openavc.server.plist"

# What Docker gives a container that names no grace (docker stop's default).
DOCKER_DEFAULT_SECONDS = 10


def test_the_bound_holds_the_whole_stop():
    assert SIGNAL_STOP_SECONDS >= DRAIN_SECONDS + STOP_SECONDS
    assert SERVICE_STOP_SECONDS >= SIGNAL_STOP_SECONDS


def test_a_container_with_no_grace_named_still_gets_the_whole_stop():
    assert SIGNAL_STOP_SECONDS <= DOCKER_DEFAULT_SECONDS


# -- the server: an open request cannot hold the stop -------------------------


async def test_an_open_request_cannot_hold_the_engine_stop(monkeypatch):
    """A download or a stream still open at the stop signal gets
    DRAIN_SECONDS, then the lifespan's stop begins: the main listener as
    main.py makes it, a request that would outlast the stop."""
    from openavc import listen_sockets, main

    monkeypatch.setattr(listen_sockets, "_stop_heard", False)

    answering = asyncio.Event()
    stop_began: list[float] = []

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    stop_began.append(time.monotonic())
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x", "more_body": True})
        answering.set()
        await asyncio.sleep(60)

    config = main._main_http_config()
    config.app = app
    config.log_level = "warning"
    server = listen_sockets.quiet_server(main._main_server(config))
    sock = listen_sockets.bind_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /download HTTP/1.1\r\nHost: test\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(answering.wait(), 5)

        signalled = time.monotonic()
        server.handle_exit(signal.SIGTERM, None)
        await asyncio.wait({serving}, timeout=DRAIN_SECONDS + 3)

        assert stop_began, (
            f"the lifespan's stop had not begun {DRAIN_SECONDS + 3} s after the signal: "
            f"the open request held it"
        )
        assert stop_began[0] - signalled < DRAIN_SECONDS + 3
        writer.close()
    finally:
        server.force_exit = True
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        sock.close()


# -- Linux and the Pi ----------------------------------------------------------


def _guide_unit() -> str:
    text = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")
    body = re.search(r"```ini\n# /etc/systemd/system/openavc\.service\n(.*?)```", text, re.S)
    assert body, "docs/deployment.md no longer shows openavc.service -- this parser is stale"
    return body.group(1)


_UNITS = {
    "installer/openavc.service": lambda: UNIT.read_text(encoding="utf-8"),
    "the deployment guide's unit": _guide_unit,
}


def _service_setting(unit_text: str, name: str) -> list[str]:
    section = re.search(r"^\[Service\]\n(.*?)(?=^\[|\Z)", unit_text, re.S | re.M)
    assert section, "no [Service] section"
    return re.findall(rf"^{name}=(\S+)\s*$", section.group(1), re.M)


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_the_unit_gives_the_stop_its_time(name: str) -> None:
    unit = _UNITS[name]()
    bound = _service_setting(unit, "TimeoutStopSec")
    assert bound, f"{name} names no TimeoutStopSec: systemd's default (90 s) is not the stop's"
    assert [int(value.removesuffix("s")) for value in bound] == [SERVICE_STOP_SECONDS]


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_the_unit_sends_a_signal_the_server_handles(name: str) -> None:
    sent = _service_setting(_UNITS[name](), "KillSignal")
    assert sent == ["SIGTERM"], f"{name} names KillSignal={sent or 'nothing'}"
    assert signal.SIGTERM in HANDLED_SIGNALS


# -- Windows -------------------------------------------------------------------


def _nssm_settings() -> dict[str, str]:
    settings = {}
    for line in INSTALL_SERVICE_BAT.read_text(encoding="utf-8").splitlines():
        found = re.match(r'^"%INSTALL_DIR%\\nssm\.exe" set OpenAVC (AppStop\w+) (\S+)\s*$', line)
        if found:
            settings[found.group(1)] = found.group(2)
    return settings


def test_nssm_gives_the_stop_its_time_after_its_ctrl_c():
    """NSSM sends Ctrl+C (the server hears SIGINT) and waits
    AppStopMethodConsole, 1.5 s unless set."""
    settings = _nssm_settings()
    assert "AppStopMethodConsole" in settings, (
        "install-service.bat sets no AppStopMethodConsole: NSSM waits 1.5 s after its Ctrl+C"
    )
    assert int(settings["AppStopMethodConsole"]) == SERVICE_STOP_SECONDS * 1000
    assert signal.SIGINT in HANDLED_SIGNALS


def test_nssm_ends_the_server_after_that_wait_and_nothing_else():
    """The window and thread messages reach nothing in the server; skipped, so
    the wait above is the whole bound before NSSM ends the process."""
    skip = int(_nssm_settings().get("AppStopMethodSkip", "0"))
    assert skip & 1 == 0, "the Ctrl+C is skipped"
    assert skip & 2 and skip & 4, "NSSM still tries a window message and a thread message"
    assert skip & 8 == 0, "NSSM never ends a server that cannot exit"


# -- Docker --------------------------------------------------------------------


def _seconds(duration: str) -> float:
    found = re.fullmatch(r"(\d+)s", str(duration))
    assert found, f"stop_grace_period {duration!r} is not written in seconds"
    return float(found.group(1))


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda path: path.name)
def test_every_compose_service_gives_the_stop_its_time(compose: Path) -> None:
    services = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]
    assert services
    for name, service in services.items():
        assert "stop_grace_period" in service, f"{compose.name}: {name} names no stop_grace_period"
        assert _seconds(service["stop_grace_period"]) == SERVICE_STOP_SECONDS, name


def test_the_image_names_its_stop_signal():
    named = re.findall(r"^STOPSIGNAL (\S+)\s*$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert named == ["SIGTERM"]
    assert signal.SIGTERM in HANDLED_SIGNALS


# -- macOS ---------------------------------------------------------------------


def test_the_launch_settings_give_the_stop_its_time():
    """launchd sends SIGTERM and kills the job ExitTimeOut seconds later, 5 when
    the plist names none."""
    with MAC_PLIST.open("rb") as handle:
        daemon = plistlib.load(handle)
    assert daemon.get("ExitTimeOut") == SERVICE_STOP_SECONDS
    assert signal.SIGTERM in HANDLED_SIGNALS


@pytest.fixture
def mac(tmp_path):
    return sandboxed_mac(tmp_path)


def test_an_update_gives_earlier_launch_settings_the_bound(mac):
    mac.install(settings={"ExitTimeOut": None})

    mac.run()

    assert mac.installed()["ExitTimeOut"] == SERVICE_STOP_SECONDS


def test_a_bound_someone_set_stays(mac):
    mac.install(settings={"ExitTimeOut": 45})

    mac.run()

    assert mac.installed()["ExitTimeOut"] == 45


def test_the_wrapper_adds_the_bound_the_shipped_plist_names():
    """One number in the run wrapper, the same the .pkg installs."""
    script = MAC_WRAPPER.read_text(encoding="utf-8")
    named = re.findall(r"^STOP_BOUND_SECONDS=(\d+)\s*$", script, re.M)
    assert named == [str(SERVICE_STOP_SECONDS)]


def test_the_launch_settings_given_the_bound_stay_root_owned():
    """launchd refuses a daemon plist that is not root's or that others can
    write; the wrapper runs as root, so this is what a test run cannot show."""
    script = MAC_WRAPPER.read_text(encoding="utf-8")
    add = script[script.index("add_stop_bound() {"):]
    add = add[:add.index("\n}\n")]
    added = add[add.index("Add :ExitTimeOut"):]
    assert 'chown root:wheel "$DAEMON_PLIST"' in added
    assert 'chmod 644 "$DAEMON_PLIST"' in added
