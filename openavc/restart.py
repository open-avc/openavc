"""How the server restarts itself.

A restart request (``system.restart_requested``: from Settings and the REST
route, from the cloud agent once it has answered, and from the listen watcher
following the chosen adapter) ends this process. A service manager (NSSM,
systemd, Docker's restart policy) starts the next one; a dev session
(``python -m openavc.main``) has none, so the process spawns its own
replacement first. ``exit_for_restart`` is the request's handler, wired by
``openavc.main`` once the engine is up.

The rule worth knowing before changing it: the exit never depends on the event
loop being healthy. ``EventBus.emit`` nests ``asyncio.gather()`` when handlers
emit events, and cancelling that chain at shutdown can recurse past Python's
stack limit, leaving ``engine.stop()`` (and a ``wait_for`` around it) unable to
finish. So a watchdog thread exits the process 7 s after the request whatever
the graceful stop is doing, and the replacement is spawned before the stop is
tried, so a dev session comes back even when the watchdog is what ends it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from datetime import datetime

from openavc import system_config
from openavc.utils.logger import get_logger

# Logged under the entry point's name, where these lines have always been filed.
log = get_logger("openavc.main")


async def exit_for_restart(data: dict | None, engine, app_state) -> None:
    """End this process for a restart request; returns only through ``os._exit``.

    ``data`` is the event's payload: ``mode`` ("graceful" waits 2 s so the
    request's own reply and log line get out first, "hard" does not) and
    ``source`` (who asked, for the log). The engine is stopped only if it
    finished starting (``app_state.engine_ready``).
    """
    mode = (data or {}).get("mode", "graceful")
    source = (data or {}).get("source", "unknown")
    log.warning(
        "Restart requested (source=%s, mode=%s); exiting in 2s", source, mode,
    )
    await asyncio.sleep(0 if mode == "hard" else 2)

    threading.Timer(7.0, lambda: os._exit(0)).start()

    if not is_service_managed():
        spawn_replacement()

    await _stop_engine(engine, app_state)
    os._exit(0)


async def _stop_engine(engine, app_state) -> None:
    """Best-effort graceful stop. The timeout covers a stop that hangs but
    can still be cancelled; the watchdog covers one that cannot."""
    try:
        if app_state.engine_ready:
            await asyncio.wait_for(engine.stop(), timeout=5.0)
    except asyncio.TimeoutError:
        log.warning("Graceful shutdown exceeded 5s — forcing exit")
    except Exception:
        log.exception("Error during graceful shutdown before restart")


def is_service_managed() -> bool:
    """True when a host service manager (NSSM / systemd / Docker) will relaunch us.

    When True, a plain ``os._exit(0)`` is enough to restart: the manager
    spawns a replacement. When False (typical dev session), we must spawn
    a replacement ourselves before exiting or the user is left with a
    dead server.

    Detection signals:
      OPENAVC_SERVICE_MANAGED=1  — set by the installer/service unit (canonical)
      INVOCATION_ID env var      — set by systemd
      PID 1 or /.dockerenv       — running inside a Docker container
    """
    if os.environ.get("OPENAVC_SERVICE_MANAGED") == "1":
        return True
    if os.environ.get("INVOCATION_ID"):
        return True
    try:
        if os.getpid() == 1:
            return True
    except OSError:
        pass
    if os.path.exists("/.dockerenv"):
        return True
    return False


def spawn_replacement() -> None:
    """Launch a fresh copy of this process, detached from the current one.

    Used for a restart in a dev session, where no service manager will
    relaunch us. The child reproduces the original invocation
    (:func:`_relaunch_command`). OPENAVC_RESTARTING tells it to retry its port
    pre-flight briefly: the parent's ``os._exit(0)`` releases the listening
    socket essentially immediately, but on Windows there's a brief window
    where the new bind can race the old one.

    The child's stdout/stderr go to ``data_dir/logs/restart-child.log`` so a
    silent failure on Windows (the child has no console window) leaves a
    trail; the path and parent PID are logged before spawning so the
    breadcrumb can be matched to the relaunch. A failure is logged, never
    raised: the process exits either way.
    """
    try:
        cmd = _relaunch_command()
        env = {**os.environ, "OPENAVC_RESTARTING": "1"}
        child_log, child_log_path = _open_child_log(cmd)
        child = _start_detached(cmd, env, child_log)
        if child_log_path is not None:
            log.info(
                "Spawned replacement process pid=%s; child output -> %s",
                child.pid,
                child_log_path,
            )
        else:
            log.info("Spawned replacement process pid=%s", child.pid)
    except Exception:
        log.exception("Failed to spawn replacement process; exiting without restart")


def _relaunch_command() -> list[str]:
    """The original invocation (``sys.orig_argv``: ``python -m openavc.main``,
    the frozen exe, with ``--simulator``, etc.), relaunched through the
    interpreter that is actually running us.

    On macOS framework builds (Homebrew) the interpreter re-execs itself
    through Python.app at startup, so orig_argv[0] is the bare framework
    binary with no trace of the venv (the __PYVENV_LAUNCHER__ handoff var is
    stripped from the environment too): a child spawned from it can't import
    anything installed in the venv. sys.executable still points at the venv
    interpreter, and on Windows/Linux the two agree, so preferring it is a
    no-op there. A frozen exe keeps its argv untouched: sys.executable IS
    the bundle exe.
    """
    cmd = list(getattr(sys, "orig_argv", None) or ([sys.executable] + sys.argv))
    if cmd and _relaunches_through_executable():
        cmd[0] = sys.executable
    return cmd


def _relaunches_through_executable() -> bool:
    return bool(sys.executable) and not getattr(sys, "frozen", False)


def _open_child_log(cmd: list[str]):
    """The restart log, opened for append with a breadcrumb naming this
    process: ``(stream, path)``. Appended, not truncated, so successive
    restarts in one dev session leave readable history. ``(DEVNULL, None)``
    when it cannot be opened, and the child's output is lost."""
    try:
        log_dir = system_config.get_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        child_log_path = log_dir / "restart-child.log"
        child_log = open(child_log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        child_log.write(
            f"\n--- spawning replacement (parent pid={os.getpid()}, "
            f"argv={cmd}) at {datetime.now().isoformat()} ---\n"
        )
        child_log.flush()
    except OSError as exc:
        log.warning("Could not open restart-child log: %s; child output will be lost", exc)
        return subprocess.DEVNULL, None
    return child_log, child_log_path


def _start_detached(cmd: list[str], env: dict, stdout):
    """Start the child in a process group of its own (Windows) or a session
    of its own (POSIX), so it outlives this process.

    The Windows child gets CREATE_NO_WINDOW, a hidden console, NOT
    DETACHED_PROCESS (no console at all). The difference matters: console
    children of the replacement (ping during a discovery scan, ssh, pip)
    attach to whatever console their parent has. With a hidden console they
    stay hidden; with no console, Windows gives each one its own visible
    console window, and a discovery scan then pops one window per address.
    """
    if sys.platform == "win32":
        return subprocess.Popen(
            cmd,
            env=env,
            creationflags=(
                subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
        )
    return subprocess.Popen(
        cmd,
        env=env,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )
