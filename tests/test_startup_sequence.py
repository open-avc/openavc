"""What the server does in the background between its HTTP listener coming up
and the engine being ready (``openavc.main._initialize_engine``).

In order: who can connect (and the warning when that is the network with no
password), the update work before the engine starts (a failed update rolled
back, stale update records swept), the engine start and what a failure leaves
for the splash page, the update confirmation, discovery's driver hints, the
restart wiring, the listen watcher and the banner. Each test drives the real
function with the engine stubbed; nothing here starts a device or a listener.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openavc import config, main, runtime_flags


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
        start=AsyncMock(side_effect=lambda: order.append("engine.start")),
        stop=AsyncMock(),
        events=SimpleNamespace(on=Mock(), emit=AsyncMock()),
        get_status=Mock(return_value={"local_ip": ""}),
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

    app = SimpleNamespace(state=SimpleNamespace(engine_ready=False, engine_error=None))

    async def run():
        await main._initialize_engine(app)

    return SimpleNamespace(
        run=run, app=app, engine=engine, order=order, calls=calls,
        data_dir=tmp_path, system=system, watcher=watcher,
    )


def _restart_handler(boot):
    (handler,) = [
        c.args[1] for c in boot.engine.events.on.call_args_list
        if c.args[0] == "system.restart_requested"
    ]
    return handler


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
    assert boot.app.state.engine_ready is True
    assert boot.app.state.engine_error is None


async def test_a_failed_update_is_rolled_back_before_the_engine_starts(boot, monkeypatch):
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )

    with pytest.raises(_Exited) as exited:
        await boot.run()

    project = Path(config.PROJECT_PATH)
    assert boot.order == ["restore_pre_update_data", "put_back_for_rollback", "perform_rollback"]
    assert boot.calls["restore_pre_update_data"] == [((boot.data_dir,), {"project_path": project})]
    assert boot.calls["put_back_for_rollback"] == [((boot.data_dir, project), {})]
    assert boot.calls["perform_rollback"] == [((boot.data_dir,), {"automatic": True})]
    assert exited.value.code == (42 if sys.platform == "win32" else 0)
    boot.engine.start.assert_not_awaited()


async def test_the_rollback_exit_code_tells_windows_service_manager_not_to_restart(
    boot, monkeypatch
):
    """Windows exits 42 (NSSM leaves it to the installer); the check reads
    sys.platform at that moment, so it is switched only for that read."""
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )
    real_platform = sys.platform

    def rolled_back(data_dir, automatic):
        sys.platform = "win32"
        return True

    def exit_restoring_platform(code):
        sys.platform = real_platform
        raise _Exited(code)

    monkeypatch.setattr("openavc.updater.rollback.perform_rollback", rolled_back)
    monkeypatch.setattr(main.os, "_exit", exit_restoring_platform)
    try:
        with pytest.raises(_Exited) as exited:
            await boot.run()
    finally:
        sys.platform = real_platform
    assert exited.value.code == 42


async def test_a_rollback_that_could_not_start_carries_on_booting(boot, monkeypatch, caplog):
    monkeypatch.setattr(
        "openavc.updater.rollback.check_rollback_needed", lambda data_dir: True,
    )
    monkeypatch.setattr("openavc.updater.rollback.perform_rollback", lambda d, automatic: False)

    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()

    assert boot.order[:2] == ["restore_pre_update_data", "put_back_for_rollback"]
    assert "engine.start" in boot.order
    assert boot.app.state.engine_ready is True
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
    assert boot.app.state.engine_ready is True


async def test_with_no_stale_record_nothing_is_logged_about_one(boot, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await boot.run()
    assert not any("apply-update.json" in r.getMessage() for r in caplog.records)


# ===========================================================================
# the engine start, and what a failure leaves
# ===========================================================================


async def test_an_engine_that_fails_to_start_leaves_its_error_for_the_splash(boot, caplog):
    boot.engine.start.side_effect = RuntimeError("project unreadable")

    with caplog.at_level(logging.ERROR, logger="openavc.main"):
        await boot.run()

    assert boot.app.state.engine_error == "project unreadable"
    assert boot.app.state.engine_ready is False
    assert "confirm_startup" not in boot.order
    assert "Engine startup failed: project unreadable" in [r.getMessage() for r in caplog.records]
    assert not hasattr(boot.app.state, "listen_watcher")


async def test_discovery_gets_the_registered_drivers_and_both_driver_folders(boot):
    from openavc.system_config import DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR

    await boot.run()

    main.discovery_engine.load_driver_hints_from_registry.assert_called_once_with(["acme_widget"])
    main.discovery_engine.load_discovery_companions_from_dirs.assert_called_once_with(
        [DRIVER_DEFINITIONS_DIR, DRIVER_REPO_DIR],
    )


async def test_the_restart_request_is_wired_to_the_engine_events(boot):
    await boot.run()
    assert callable(_restart_handler(boot))


# ===========================================================================
# the listen watcher
# ===========================================================================


async def test_the_listen_watcher_restarts_through_the_restart_request(boot):
    await boot.run()
    await boot.app.state.listen_watcher

    restart = boot.watcher.call_args.args[0]
    await restart("adapter gone")

    boot.engine.events.emit.assert_awaited_once_with(
        "system.restart_requested", {"mode": "graceful", "source": "listen"},
    )
    boot.watcher.return_value.run.assert_awaited_once()


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
    assert ticked == [(boot.system, boot.watcher.call_args.args[0])]


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
    assert "Could not resolve who can connect" in lines
    assert not any("LAN access" in m or "NO authentication" in m for m in lines)
    assert boot.app.state.engine_ready is True


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
        main, "_certified_host_for",
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
        main, "_certified_host_for", Mock(side_effect=AssertionError("asked on plain HTTP")),
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
    monkeypatch.setattr(main, "_certified_host_for", lambda host: None)
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
# a restart request: the handler the boot wires to system.restart_requested
# ===========================================================================


@pytest.fixture
def restart(boot, monkeypatch, tmp_path):
    """Fire the wired restart handler with the process exit, the watchdog
    timer, the sleep and the child spawn all recorded instead of run."""
    import asyncio
    import os
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
    boot.engine.stop.side_effect = lambda: seen.order.append("stop")

    def unmanaged():
        monkeypatch.delenv("OPENAVC_SERVICE_MANAGED", raising=False)
        monkeypatch.delenv("INVOCATION_ID", raising=False)
        real_exists = os.path.exists
        monkeypatch.setattr(
            os.path, "exists", lambda p: False if p == "/.dockerenv" else real_exists(p),
        )

    async def fire(data, *, ready=True):
        await boot.run()
        boot.app.state.engine_ready = ready
        with pytest.raises(_Exited) as exited:
            await _restart_handler(boot)("system.restart_requested", data)
        return exited.value.code

    seen.fire = fire
    seen.unmanaged = unmanaged
    return seen


async def test_a_graceful_restart_waits_arms_the_watchdog_stops_and_exits(restart, boot, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        code = await restart.fire({"source": "settings"})

    assert code == 0
    assert restart.sleeps == [2]
    (timer,) = restart.timers
    assert timer.interval == 7.0 and timer.started
    with pytest.raises(_Exited) as watchdog:
        timer.function()
    assert watchdog.value.code == 0
    assert restart.order == ["stop"]
    assert "Restart requested (source=settings, mode=graceful); exiting in 2s" in _messages(caplog)


@pytest.mark.parametrize("data", [None, {}])
async def test_a_bare_request_is_graceful_from_an_unknown_source(restart, caplog, data):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await restart.fire(data)

    assert restart.sleeps == [2]
    assert "Restart requested (source=unknown, mode=graceful); exiting in 2s" in _messages(caplog)


async def test_a_hard_restart_does_not_wait(restart, caplog):
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        await restart.fire({"mode": "hard", "source": "cloud"})

    assert restart.sleeps == [0]
    assert "Restart requested (source=cloud, mode=hard); exiting in 2s" in _messages(caplog)


async def test_with_no_service_manager_the_replacement_is_spawned_before_the_stop(restart):
    restart.unmanaged()
    await restart.fire({})
    assert restart.order == ["spawn", "stop"]


@pytest.mark.parametrize("variable", ["OPENAVC_SERVICE_MANAGED", "INVOCATION_ID"])
async def test_a_service_manager_relaunches_so_nothing_is_spawned(restart, monkeypatch, variable):
    restart.unmanaged()
    monkeypatch.setenv(variable, "1")
    await restart.fire({})
    assert restart.order == ["stop"]


async def test_an_engine_that_never_got_ready_is_not_stopped(restart, boot):
    assert await restart.fire({}, ready=False) == 0
    boot.engine.stop.assert_not_awaited()


async def test_a_stop_that_overruns_five_seconds_is_abandoned(restart, monkeypatch, caplog):
    import asyncio

    timeouts = []

    async def overrun(awaitable, timeout):
        awaitable.close()
        timeouts.append(timeout)
        raise asyncio.TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", overrun)
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        assert await restart.fire({}) == 0

    assert timeouts == [5.0]
    assert "Graceful shutdown exceeded 5s — forcing exit" in _messages(caplog)


async def test_a_stop_that_fails_is_logged_and_the_process_still_exits(restart, boot, caplog):
    boot.engine.stop.side_effect = RuntimeError("driver hung")
    with caplog.at_level(logging.WARNING, logger="openavc.main"):
        assert await restart.fire({}) == 0

    failed = [r for r in caplog.records if r.getMessage() == "Error during graceful shutdown before restart"]
    assert len(failed) == 1 and failed[0].exc_info[1].args == ("driver hung",)
