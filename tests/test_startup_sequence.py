"""What the server does in the background between its HTTP listener coming up
and the engine being ready (``openavc.main._initialize_engine``).

In order: the update work before the engine starts (a failed update rolled
back, stale update records swept), who can connect (and the warning when that
is the network with no password), the engine start and what a failure leaves
for the splash page, the update confirmation, discovery's driver hints, the
restart wiring, the listen watcher and the banner. Each test drives the real
function with the engine stubbed; nothing here starts a device or a listener.
The boot has no service manager unless a test gives it one, so a failed start
waits on the splash page at once (what follows a failed start under one is
tests/test_start_retry.py's).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openavc import config, main, restart as restart_door, runtime_flags
from openavc.core.start_stages import GoOn
from openavc.core.stop_stages import STOP_SECONDS
from openavc.middleware.startup_splash import FAILED, READY, RunState
from openavc.updater.platform import DeploymentType
from openavc.version import __version__
from openavc.restart import WATCHDOG_MARGIN_SECONDS


class _Exited(BaseException):
    """Stand-in for os._exit. BaseException so the function's own
    ``except Exception`` cannot read a process exit as a startup failure."""

    def __init__(self, code: int) -> None:
        self.code = code


def _exit(code: int) -> None:
    raise _Exited(code)


def _plan(**overrides):
    plan = dict(
        reaches_network=False, mode="loopback", adapter_name="",
        problem=None, local_only=True,
    )
    plan.update(overrides)
    return SimpleNamespace(**plan)


def _no_service_manager(monkeypatch, tmp_path) -> None:
    """Nothing starts this process again (tests/test_start_retry.py gives the
    boot one where it matters)."""
    from openavc.updater import platform

    monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(platform, "_is_docker", lambda: False)
    monkeypatch.setattr(platform, "_DEPLOYMENT_MARKER", tmp_path / "no-marker")


def _deployed_as(monkeypatch, deployment) -> None:
    from openavc.updater import platform

    monkeypatch.setattr(platform, "detect_deployment_type", lambda app_dir=None: deployment)


@pytest.fixture
def boot(monkeypatch, tmp_path):
    """_initialize_engine against a stub engine, with every updater call it
    makes recorded in one ordered list (``boot.order``)."""
    order: list[str] = []

    def recorder(name, result=None):
        def record(*args, **kwargs):
            order.append(name)
            calls.setdefault(name, []).append((args, kwargs))
            return result
        return record

    calls: dict[str, list] = {}
    engine = SimpleNamespace(
        start=AsyncMock(side_effect=lambda run: order.append("engine.start")),
        shutdown=AsyncMock(),
        stopping=False,
        events=SimpleNamespace(on=Mock(), emit=AsyncMock()),
        get_status=Mock(return_value={"local_ip": ""}),
        start_failed=Mock(),
    )
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(
        main.discovery_engine, "load_driver_hints_from_registry",
        Mock(side_effect=lambda drivers: order.append("hints")),
    )
    monkeypatch.setattr(
        main.discovery_engine, "load_discovery_companions_from_dirs",
        Mock(side_effect=lambda dirs: order.append("companions")),
    )
    monkeypatch.setattr(main, "list_registered_drivers", lambda: ["acme_widget"])
    system = SimpleNamespace(data_dir=tmp_path)
    monkeypatch.setattr("openavc.system_config.get_system_config", lambda: system)
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    _no_service_manager(monkeypatch, tmp_path)
    monkeypatch.setattr("openavc.updater.host.get_firmware_host", lambda: None)
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed",
        recorder("check_rollback_needed", False),
    )
    for name in (
        "confirm_startup", "clear_stale_rollback_marker",
        "restore_pre_update_data", "remove_leftover_update_tasks",
    ):
        monkeypatch.setattr(f"openavc.updater.rollback.{name}", recorder(name))
    monkeypatch.setattr(
        "openavc.updater.rollback.perform_rollback", recorder("perform_rollback", True),
    )
    monkeypatch.setattr(
        "openavc.core.device_logins.put_back_for_rollback", recorder("put_back_for_rollback"),
    )
    watcher = Mock()
    watcher.return_value.run = AsyncMock()
    monkeypatch.setattr("openavc.listen.ListenWatcher", watcher)
    monkeypatch.setattr(main.os, "_exit", _exit)
    monkeypatch.setattr(config, "listen_plan", lambda: _plan())
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "")
    monkeypatch.setattr(config, "API_KEY", "")
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", False)
    monkeypatch.setattr(config, "HTTP_PORT", 8080)
    monkeypatch.setattr(config, "TLS_PORT", 8443)
    monkeypatch.setattr(runtime_flags, "port80_active", False)

    app = SimpleNamespace(state=SimpleNamespace(run_state=RunState()))

    async def run():
        await main._initialize_engine(app)

    return SimpleNamespace(
        run=run, app=app, engine=engine, order=order, calls=calls,
        data_dir=tmp_path, system=system, watcher=watcher,
    )


def _requests(monkeypatch):
    """Every restart request the boot's doors make, recorded instead of run."""
    asked: list[tuple] = []
    monkeypatch.setattr(restart_door, "request_exit", lambda *args: asked.append(args))
    return asked


# ===========================================================================
# the update work before and after the engine starts
# ===========================================================================


async def test_a_good_boot_checks_sweeps_starts_then_confirms(boot):
    await boot.run()

    assert boot.order == [
        "check_rollback_needed", "clear_stale_rollback_marker",
        "engine.start", "confirm_startup", "hints", "companions",
    ]
    assert boot.calls["check_rollback_needed"] == [((boot.data_dir,), {})]
    assert boot.calls["clear_stale_rollback_marker"] == [((boot.data_dir,), {})]
    assert boot.calls["confirm_startup"] == [((boot.data_dir,), {})]
    assert boot.app.state.run_state.answer() == {
        "phase": READY, "ready": True, "version": __version__, "error": None, "problems": [],
        "held": False, "retry_in": None,
    }


async def test_a_failed_update_is_rolled_back_before_the_engine_starts(boot, monkeypatch):
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )
    _deployed_as(monkeypatch, DeploymentType.LINUX_PACKAGE)

    with pytest.raises(_Exited) as exited:
        await boot.run()

    project = Path(config.PROJECT_PATH)
    assert boot.order == ["restore_pre_update_data", "put_back_for_rollback", "perform_rollback"]
    assert boot.calls["restore_pre_update_data"] == [((boot.data_dir,), {"project_path": project})]
    assert boot.calls["put_back_for_rollback"] == [((boot.data_dir, project), {})]
    assert boot.calls["perform_rollback"] == [((boot.data_dir,), {"automatic": True})]
    assert exited.value.code == 0
    boot.engine.start.assert_not_awaited()


async def test_an_installed_windows_server_s_rollback_leaves_nssm_to_the_installer(
    boot, monkeypatch
):
    """The install type decides the code, not the operating system: NSSM
    leaves the service stopped on it, for the installer the rollback
    scheduled (tests/test_update_exit_code.py has every install type)."""
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )
    _deployed_as(monkeypatch, DeploymentType.WINDOWS_INSTALLER)

    with pytest.raises(_Exited) as exited:
        await boot.run()

    assert exited.value.code == restart_door.LEAVE_STOPPED_EXIT_CODE == 42


async def test_a_rollback_that_could_not_start_carries_on_booting(boot, monkeypatch, caplog):
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )
    monkeypatch.setattr("openavc.updater.rollback.perform_rollback", lambda d, automatic: False)

    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()

    assert boot.order[:2] == ["restore_pre_update_data", "put_back_for_rollback"]
    assert "engine.start" in boot.order
    assert boot.app.state.run_state.ready is True
    assert not any("Rollback initiated" in r.getMessage() for r in caplog.records)


async def test_windows_installer_tasks_are_removed_before_the_rollback_check(boot, monkeypatch):
    """sys.platform reads win32 from the firmware-host check until the
    rollback check, the stretch that branches on it."""
    from openavc.updater.platform import DeploymentType

    real_platform = sys.platform

    def no_host():
        sys.platform = "win32"
        return None

    def check(data_dir):
        sys.platform = real_platform
        boot.order.append("check_rollback_needed")
        return False

    monkeypatch.setattr("openavc.updater.host.get_firmware_host", no_host)
    monkeypatch.setattr("openavc.updater.rollback.check_rollback_needed", check)
    monkeypatch.setattr(
        "openavc.updater.platform.detect_deployment_type",
        lambda: DeploymentType.WINDOWS_INSTALLER,
    )
    try:
        await boot.run()
    finally:
        sys.platform = real_platform

    assert boot.order[:2] == ["remove_leftover_update_tasks", "check_rollback_needed"]


async def test_another_windows_install_type_keeps_its_tasks(boot, monkeypatch):
    from openavc.updater.platform import DeploymentType

    real_platform = sys.platform

    def no_host():
        sys.platform = "win32"
        return None

    def check(data_dir):
        sys.platform = real_platform
        return False

    monkeypatch.setattr("openavc.updater.host.get_firmware_host", no_host)
    monkeypatch.setattr("openavc.updater.rollback.check_rollback_needed", check)
    monkeypatch.setattr(
        "openavc.updater.platform.detect_deployment_type", lambda: DeploymentType.DOCKER,
    )
    try:
        await boot.run()
    finally:
        sys.platform = real_platform

    assert "remove_leftover_update_tasks" not in boot.order


async def test_a_stale_apply_record_is_swept_with_a_warning(boot, caplog):
    stale = boot.data_dir / "apply-update.json"
    stale.write_text("{}", encoding="utf-8")
    keep = boot.data_dir / "apply-update.retry"
    keep.write_text("{}", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()

    assert not stale.exists()
    assert keep.exists()
    assert "Removed stale apply-update.json at startup" in [r.getMessage() for r in caplog.records]


async def test_a_stale_apply_record_that_cannot_be_removed_does_not_stop_the_boot(
    boot, monkeypatch, caplog
):
    stale = boot.data_dir / "apply-update.json"
    stale.write_text("{}", encoding="utf-8")
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == stale:
            raise PermissionError("held")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()

    assert stale.exists()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == [
        "Could not remove stale apply-update.json at startup"
    ]
    assert boot.app.state.run_state.ready is True


async def test_with_no_stale_record_nothing_is_logged_about_one(boot, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()
    assert not any("apply-update.json" in r.getMessage() for r in caplog.records)


# ===========================================================================
# the engine start, and what a failure leaves
# ===========================================================================


_PLUGINS = GoOn("plugins", "starting the plugins", "The plugins did not start.")


async def test_an_engine_that_fails_to_start_leaves_its_stage_and_error_for_the_splash(
        boot, caplog):
    async def start(run):
        await run.go_on(_PLUGINS, Mock(side_effect=RuntimeError("plugin folder unreadable")))
        await run.run("loading the project", Mock(side_effect=RuntimeError("project unreadable")))

    boot.engine.start.side_effect = start

    with caplog.at_level(logging.ERROR, logger="openavc.main"):
        await boot.run()

    # The stage that stopped the start is named, and what it went on past
    # before that is kept.
    assert boot.app.state.run_state.answer() == {
        "phase": FAILED,
        "ready": False,
        "version": __version__,
        "error": "Loading the project failed: project unreadable",
        "problems": [{"stage": "plugins", "detail": "The plugins did not start."}],
        "held": True,
        "retry_in": None,
    }
    assert "confirm_startup" not in boot.order
    assert (
        "Engine startup failed: Loading the project failed: project unreadable"
        in [r.getMessage() for r in caplog.records]
    )
    assert not hasattr(boot.engine, "listen_watcher")
    # system.started reads False, whichever stage stopped the start.
    boot.engine.start_failed.assert_called_once_with()
    # The door is armed: Start again and a restart from the cloud are taken.
    assert restart_door._engine is boot.engine


async def test_the_last_update_is_checked_before_anything_else_can_stop_the_start(
        boot, monkeypatch):
    """An update whose next start fails at any later stage is rolled back by
    the start after it."""
    async def start(run):
        await run.run("loading the project", Mock(side_effect=RuntimeError("project unreadable")))

    boot.engine.start.side_effect = start

    await boot.run()

    assert boot.order == ["check_rollback_needed", "clear_stale_rollback_marker"]
    assert boot.app.state.run_state.error == "Loading the project failed: project unreadable"


async def test_a_failed_start_s_reason_stays_until_a_start_finishes(boot, monkeypatch):
    from openavc import status_folder

    reason = boot.data_dir / "status" / status_folder.STARTUP_ERROR_FILE_NAME
    status_folder.write_run_state(FAILED, __version__, "an earlier failure")
    watch = main._watch_listen_choice
    monkeypatch.setattr(main, "_watch_listen_choice", Mock(side_effect=RuntimeError("no watcher")))
    await boot.run()
    # A start that fails clears nothing (this boot's record reports nowhere).
    assert "an earlier failure" in reason.read_text(encoding="utf-8")

    restart_door.reset()
    monkeypatch.setattr(main, "_watch_listen_choice", watch)
    await boot.run()
    assert not reason.exists()


async def test_a_stage_of_the_servers_own_that_stops_the_start_is_named(boot, monkeypatch):
    monkeypatch.setattr(main, "_watch_listen_choice", Mock(side_effect=RuntimeError("no watcher")))

    await boot.run()

    assert boot.app.state.run_state.answer() == {
        "phase": FAILED,
        "ready": False,
        "version": __version__,
        "error": "Setting up restarts failed: no watcher",
        "problems": [],
        "held": True,
        "retry_in": None,
    }


async def test_a_stage_of_the_servers_own_that_goes_on_is_kept(boot, monkeypatch):
    monkeypatch.setattr(main, "_load_discovery_hints", Mock(side_effect=RuntimeError("no index")))

    await boot.run()

    answer = boot.app.state.run_state.answer()
    assert (answer["phase"], answer["error"]) == (READY, None)
    assert answer["problems"] == [{"stage": "discovery_data", "detail": main._DISCOVERY_DATA.detail}]
    assert "no index" not in answer["problems"][0]["detail"]
    # What follows it still ran: the restart door is armed.
    assert restart_door._engine is boot.engine


async def test_a_start_that_went_on_past_a_stage_is_ready_and_keeps_it(boot):
    async def start(run):
        await run.go_on(_PLUGINS, Mock(side_effect=RuntimeError("plugin folder unreadable")))

    boot.engine.start.side_effect = start

    await boot.run()

    assert boot.app.state.run_state.answer() == {
        "phase": READY,
        "ready": True,
        "version": __version__,
        "error": None,
        "problems": [{"stage": "plugins", "detail": "The plugins did not start."}],
        "held": False,
        "retry_in": None,
    }


async def test_discovery_gets_the_registered_drivers_and_both_driver_folders(boot):
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    await boot.run()

    main.discovery_engine.load_driver_hints_from_registry.assert_called_once_with(["acme_widget"])
    main.discovery_engine.load_discovery_companions_from_dirs.assert_called_once_with(
        [DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR],
    )


async def test_restarts_are_refused_until_the_engine_is_up_then_accepted(boot, monkeypatch):
    accepted = []
    monkeypatch.setattr(restart_door, "_exit_after", AsyncMock(side_effect=lambda *a: accepted.append(a)))
    started = boot.engine.start.side_effect

    def start(run):
        # A request inside the start (the cloud agent is up by now) is refused.
        refused = restart_door.request_exit("graceful", "cloud")
        assert (refused.accepted, refused.refusal) == (False, restart_door.NOT_READY)
        return started(run)

    boot.engine.start.side_effect = start
    await boot.run()

    assert restart_door.request_exit("graceful", "cloud").accepted
    await asyncio.gather(*restart_door._exiting)
    assert accepted == [(2, boot.engine)]


# ===========================================================================
# the listen watcher
# ===========================================================================


async def test_the_listen_watcher_restarts_through_the_restart_door(boot, monkeypatch):
    asked = _requests(monkeypatch)
    await boot.run()
    await boot.engine.listen_watcher

    restart = boot.watcher.call_args.args[0]
    await restart("adapter gone")

    assert asked == [("graceful", "listen")]
    boot.engine.events.emit.assert_not_awaited()
    boot.watcher.return_value.run.assert_awaited_once()


async def test_the_safety_net_restarts_through_the_door_under_its_own_name(boot, monkeypatch):
    asked = _requests(monkeypatch)
    restarts = []

    async def tick(system, restart):
        restarts.append(restart)
        await restart("the safety net went back to the previous choice")

    monkeypatch.setattr("openavc.reach_devices.choice", lambda: "")
    monkeypatch.setattr("openavc.listen_safety_net.tick", tick)
    await boot.run()

    await boot.watcher.call_args.kwargs["on_tick"]()

    assert asked == [("graceful", "listen safety net")]
    assert restarts[0] is not boot.watcher.call_args.args[0]


async def test_each_tick_refreshes_reach_devices_then_ticks_the_safety_net(boot, monkeypatch):
    refreshed, ticked = [], []
    monkeypatch.setattr("openavc.reach_devices.choice", lambda: "eth0")
    monkeypatch.setattr("openavc.reach_devices.refresh", lambda: refreshed.append("refresh"))

    async def tick(system, restart):
        ticked.append((system, restart))

    monkeypatch.setattr("openavc.listen_safety_net.tick", tick)
    await boot.run()

    on_tick = boot.watcher.call_args.kwargs["on_tick"]
    await on_tick()

    assert refreshed == ["refresh"]
    assert [system for system, _restart in ticked] == [boot.system]


async def test_a_tick_with_no_reach_choice_skips_the_refresh(boot, monkeypatch):
    refreshed = []
    monkeypatch.setattr("openavc.reach_devices.choice", lambda: "")
    monkeypatch.setattr("openavc.reach_devices.refresh", lambda: refreshed.append("refresh"))
    monkeypatch.setattr("openavc.listen_safety_net.tick", AsyncMock())
    await boot.run()

    await boot.watcher.call_args.kwargs["on_tick"]()

    assert refreshed == []


async def test_a_failed_refresh_still_ticks_the_safety_net(boot, monkeypatch, caplog):
    def broken():
        raise OSError("no adapter")

    tick = AsyncMock()
    monkeypatch.setattr("openavc.reach_devices.choice", lambda: "eth0")
    monkeypatch.setattr("openavc.reach_devices.refresh", broken)
    monkeypatch.setattr("openavc.listen_safety_net.tick", tick)
    await boot.run()

    with caplog.at_level(logging.DEBUG, logger="openavc.main"):
        await boot.watcher.call_args.kwargs["on_tick"]()

    tick.assert_awaited_once()
    assert "Reach devices through: refresh failed" in [r.getMessage() for r in caplog.records]


# ===========================================================================
# who can connect: the warning, and the banner
# ===========================================================================


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


async def test_an_open_network_with_no_password_warns(boot, monkeypatch, caplog):
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    lines = _messages(caplog)
    start = lines.index("  WARNING: Accepting connections from any network with NO authentication.")
    assert lines[start - 1] == "=" * 60
    assert lines[start + 1:start + 5] == [
        "  Anyone on the network can access and control devices.",
        "  Set an admin password (open the Programmer), or choose",
        "  This computer only in Settings > Network > Accept connections from.",
        "=" * 60,
    ]


@pytest.mark.parametrize("adapter, where", [("Ethernet", "the Ethernet network"), ("", "the chosen network")])
async def test_one_adapter_with_no_password_names_that_network(
    boot, monkeypatch, caplog, adapter, where
):
    monkeypatch.setattr(
        config, "listen_plan",
        lambda: _plan(reaches_network=True, mode="adapter", adapter_name=adapter, local_only=False),
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert f"  WARNING: Accepting connections from {where} with NO authentication." in _messages(caplog)


@pytest.mark.parametrize("credential", ["PROGRAMMER_PASSWORD", "API_KEY"])
async def test_a_password_or_key_silences_the_warning(boot, monkeypatch, caplog, credential):
    monkeypatch.setattr(config, credential, "set")
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()
    assert not any("NO authentication" in m for m in _messages(caplog))


async def test_this_computer_only_never_warns(boot, caplog):
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()
    assert not any("NO authentication" in m for m in _messages(caplog))


async def test_a_plan_that_cannot_be_resolved_is_logged_and_the_boot_goes_on(
    boot, monkeypatch, caplog
):
    def broken():
        raise RuntimeError("no adapters")

    monkeypatch.setattr(config, "listen_plan", broken)
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    lines = _messages(caplog)
    assert "Working out who can connect failed" in lines
    assert not any("LAN access" in m or "NO authentication" in m for m in lines)
    assert boot.app.state.run_state.ready is True
    assert [p.stage for p in boot.app.state.run_state.problems] == ["who_can_connect"]


def _banner(caplog) -> list[str]:
    lines = _messages(caplog)
    first = lines.index("=" * 60, lines.index(next(m for m in lines if m.startswith("  Panel UI:"))) - 1)
    return lines[first:lines.index("=" * 60, first + 1) + 1]


async def test_the_plain_banner(boot, caplog):
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert _banner(caplog) == [
        "=" * 60,
        "  Panel UI:    http://localhost:8080/panel",
        "  Programmer:  http://localhost:8080/programmer",
        "  REST API:    http://localhost:8080/api",
        "  LAN access:  none. Accepting connections from this computer only; change "
        "Settings > Network > Accept connections from and restart to reach it from the network.",
        "=" * 60,
    ]


async def test_the_https_banner_with_its_redirect_short_and_trusted_urls(boot, monkeypatch, caplog):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    monkeypatch.setattr(runtime_flags, "port80_active", True)
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "set")
    boot.engine.get_status.return_value = {"local_ip": "192.168.4.20"}
    certified = []
    monkeypatch.setattr(
        main, "certified_host_for",
        lambda host: certified.append(host) or "192-168-4-20.abc.example.net",
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert certified == ["192.168.4.20"]
    assert _banner(caplog) == [
        "=" * 60,
        "  Panel UI:    https://localhost:8443/panel",
        "  Programmer:  https://localhost:8443/programmer",
        "  REST API:    https://localhost:8443/api",
        "  LAN access:  https://192.168.4.20:8443/panel",
        "  Short URL:   http://192.168.4.20/panel",
        "  Trusted URL: https://192-168-4-20.abc.example.net:8443/panel  (no browser warnings)",
        "  HTTP redirect: http://localhost:8080 -> https://localhost:8443",
        "=" * 60,
    ]


async def test_plain_http_never_asks_for_a_trusted_name(boot, monkeypatch, caplog):
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "set")
    monkeypatch.setattr(config, "TLS_REDIRECT_HTTP", True)
    boot.engine.get_status.return_value = {"local_ip": "192.168.4.20"}
    monkeypatch.setattr(
        main, "certified_host_for", Mock(side_effect=AssertionError("asked on plain HTTP")),
    )
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    banner = _banner(caplog)
    assert "  LAN access:  http://192.168.4.20:8080/panel" in banner
    assert not any(m.startswith(("  Short URL", "  Trusted URL", "  HTTP redirect")) for m in banner)


async def test_https_with_no_trusted_name_names_no_trusted_url(boot, monkeypatch, caplog):
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "set")
    boot.engine.get_status.return_value = {"local_ip": "192.168.4.20"}
    monkeypatch.setattr(main, "certified_host_for", lambda host: None)
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert not any(m.startswith("  Trusted URL") for m in _banner(caplog))


@pytest.mark.parametrize("local_ip", ["", "127.0.0.1", None])
async def test_a_network_plan_with_no_lan_address_names_none(boot, monkeypatch, caplog, local_ip):
    monkeypatch.setattr(
        config, "listen_plan", lambda: _plan(reaches_network=True, mode="any", local_only=False),
    )
    monkeypatch.setattr(config, "PROGRAMMER_PASSWORD", "set")
    boot.engine.get_status.return_value = {"local_ip": local_ip}
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert not any("LAN access" in m for m in _banner(caplog))


async def test_an_unresolved_plan_has_no_lan_line(boot, monkeypatch, caplog):
    def broken():
        raise RuntimeError("no adapters")

    monkeypatch.setattr(config, "listen_plan", broken)
    boot.engine.get_status.return_value = {"local_ip": "192.168.4.20"}
    with caplog.at_level(logging.INFO, logger="openavc.main"):
        await boot.run()

    assert len(_banner(caplog)) == 5


# ===========================================================================
# a restart request: the door the boot arms (openavc/restart.py)
# ===========================================================================


@pytest.fixture
def restart(boot, monkeypatch, tmp_path):
    """Send a request through the armed restart door, with the process exit,
    the watchdog timer, the sleep and the child spawn all recorded instead of
    run."""
    import asyncio
    import subprocess

    seen = SimpleNamespace(timers=[], sleeps=[], order=[])

    class Timer:
        def __init__(self, interval, function):
            self.interval, self.function, self.started = interval, function, False
            seen.timers.append(self)

        def start(self):
            self.started = True

    async def sleep(seconds):
        seen.sleeps.append(seconds)

    def popen(cmd, **kwargs):
        seen.order.append("spawn")
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr("threading.Timer", Timer)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr("openavc.system_config.get_data_dir", lambda: tmp_path)
    monkeypatch.setenv("OPENAVC_SERVICE_MANAGED", "1")
    boot.engine.shutdown.side_effect = lambda deadline: seen.order.append("stop")

    def unmanaged():
        from openavc.updater import platform

        monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        # The one deployment detection, seeing neither a container nor a marker.
        monkeypatch.setattr(platform, "_is_docker", lambda: False)
        monkeypatch.setattr(platform, "_DEPLOYMENT_MARKER", tmp_path / "no-marker")

    async def fire(mode=None, source="settings"):
        await boot.run()
        assert restart_door.request_exit(mode, source).accepted
        with pytest.raises(_Exited) as exited:
            await asyncio.gather(*restart_door._exiting)
        return exited.value.code

    seen.fire = fire
    seen.unmanaged = unmanaged
    return seen


async def test_a_graceful_restart_waits_arms_the_watchdog_stops_and_exits(restart, boot, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        code = await restart.fire(source="settings")

    assert code == 0
    assert restart.sleeps == [2]
    (timer,) = restart.timers
    # A little after the stop's deadline: the one every stop has.
    assert timer.interval == STOP_SECONDS + WATCHDOG_MARGIN_SECONDS == 10.0 and timer.started
    with pytest.raises(_Exited) as watchdog:
        timer.function()
    assert watchdog.value.code == 0
    assert restart.order == ["stop"]
    assert "Restart requested (source=settings, mode=graceful); stopping in 2 s" in _messages(caplog)
    # Filed where these lines have always been, whichever module logs them.
    assert {r.name for r in caplog.records} == {"openavc.main"}


async def test_a_request_that_names_no_mode_is_graceful(restart, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await restart.fire(None, "api")

    assert restart.sleeps == [2]
    assert "Restart requested (source=api, mode=graceful); stopping in 2 s" in _messages(caplog)


async def test_a_hard_restart_does_not_wait(restart, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await restart.fire("hard", "cloud")

    assert restart.sleeps == [0]
    assert "Restart requested (source=cloud, mode=hard); stopping now" in _messages(caplog)


async def test_with_no_service_manager_the_replacement_is_spawned_before_the_stop(restart):
    restart.unmanaged()
    await restart.fire()
    assert restart.order == ["spawn", "stop"]


@pytest.mark.parametrize("variable", ["OPENAVC_SERVICE_MANAGED", "INVOCATION_ID"])
async def test_a_service_manager_relaunches_so_nothing_is_spawned(restart, monkeypatch, variable):
    restart.unmanaged()
    monkeypatch.setenv(variable, "1")
    await restart.fire()
    assert restart.order == ["stop"]


async def test_the_stop_is_the_engines_shutdown_against_the_one_deadline(
        restart, boot, monkeypatch):
    monkeypatch.setattr("openavc.restart.stop_deadline", lambda: 1234.5)
    assert await restart.fire() == 0
    boot.engine.shutdown.assert_awaited_once_with(1234.5)


async def test_a_stop_that_fails_is_logged_and_the_process_still_exits(restart, boot, caplog):
    boot.engine.shutdown.side_effect = RuntimeError("driver hung")
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        assert await restart.fire() == 0

    failed = [r for r in caplog.records if r.getMessage() == "Error during graceful shutdown before restart"]
    assert len(failed) == 1 and failed[0].exc_info[1].args == ("driver hung",)
