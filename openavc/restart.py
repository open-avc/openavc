"""How the server restarts itself.

THE door every restart request takes: ``request_exit(mode, source)``, called
by the REST route (Settings), the cloud agent's restart command, the listen
watcher following the chosen adapter, and the listen safety net going back to
the previous choice. It decides once, for all of them: the mode (``MODES``;
none said is graceful), whether the server is up to restart (``openavc.main``
arms the door with ``accept_restarts`` once the engine is up), and whether a
restart or a stop is already under way. Each asker answers from what it
decided (``ExitAnswer``): the REST route's status and delay, the cloud's
result. A request is refused only here, and the same way at every door.

An accepted request ends this process. Whatever manages it (NSSM, systemd,
launchd, Docker's restart policy, appliance hardware's device supervisor)
starts the next one; a dev session (``python -m openavc.main``) has none, so
the process spawns its own replacement first. Which of the two holds is
``is_service_managed``, THE answer to who brings the server back, which the
updater asks too.

The stop is the engine's ``shutdown``, the same one the lifespan runs at a
service stop (core/engine_lifecycle.py), within the same deadline
(core/stop_stages.py ``STOP_SECONDS``). It runs in a task of its own: the
asker may be one of the pieces the shutdown ends (the listen watcher, the
cloud agent's command handling), which would otherwise cancel the stop and the
exit with them.

The rule worth knowing before changing it: the exit never depends on the event
loop being healthy. ``EventBus.emit`` nests ``asyncio.gather()`` when handlers
emit events, and cancelling that chain at shutdown can recurse past Python's
stack limit, leaving the stop unable to finish. So a watchdog thread exits the
process a little after the stop's deadline whatever the stop is doing, and the
replacement is spawned before the stop is tried, so a dev session comes back
even when the watchdog is what ends it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openavc import system_config
from openavc.core.event_bus import detach_emit_chain
from openavc.core.stop_stages import STOP_SECONDS, stop_deadline
from openavc.utils.logger import get_logger

# Logged under the entry point's name, where these lines have always been filed.
log = get_logger("openavc.main")

# How long after the stop's deadline the watchdog ends the process anyway.
WATCHDOG_MARGIN_SECONDS = 2.0
# The longest a process ending for a restart can keep its port after it has
# spawned its replacement: the stop's deadline, then the watchdog. The
# replacement retries its port for longer than this (openavc.main).
LONGEST_EXIT_SECONDS = STOP_SECONDS + WATCHDOG_MARGIN_SECONDS

# The ways a restart can be asked for, and how long each waits before the stop
# begins: "graceful" waits so the request's own answer and log line get out
# first, "hard" does not. The cloud's wire contract names the same two
# (its "restart_modes" vocabulary; tests/test_cloud_wire_contract.py holds
# them equal), so the portal refuses any other before it is sent.
WAIT_SECONDS: dict[str, int] = {"graceful": 2, "hard": 0}
MODES: tuple[str, ...] = tuple(WAIT_SECONDS)
# What a request that names no mode gets.
DEFAULT_MODE = "graceful"

# Why a request was refused, for the asker's door to answer with: the REST
# route turns each into its HTTP status.
UNKNOWN_MODE = "unknown_mode"
NOT_READY = "not_ready"
UNDER_WAY = "under_way"

# The engine an accepted request stops, set once the server is up
# (``accept_restarts``); until then every request is refused.
_engine: Any = None
# The server's run-state record (middleware/startup_splash.py), which an
# accepted request says stopping on at once, its wait included.
_run_state: Any = None

# The exit under way, held here so it is not collected while it runs; while
# it is here, every further request is refused.
_exiting: set[asyncio.Task] = set()


@dataclass(frozen=True)
class ExitAnswer:
    """What ``request_exit`` decided, for the asker to answer with.

    Accepted: ``mode`` is the one in force and ``wait_seconds`` how long
    before the stop begins. Refused: ``refusal`` is ``UNKNOWN_MODE``,
    ``NOT_READY`` or ``UNDER_WAY``, and ``sentence`` says why, for whoever
    asked."""

    accepted: bool
    mode: str
    wait_seconds: int = 0
    refusal: str = ""
    sentence: str = ""


def accept_restarts(engine, run_state=None) -> None:
    """Arm the door: from now on an accepted request stops ``engine`` and
    says stopping on ``run_state``, the server's run-state record (none on a
    server that keeps none). ``openavc.main`` calls it once the engine is
    up."""
    global _engine, _run_state
    _engine = engine
    _run_state = run_state


def reset() -> None:
    """Disarm the door and forget any exit in flight (tests)."""
    global _engine, _run_state
    _engine = None
    _run_state = None
    _exiting.clear()


def request_exit(mode: Any, source: str) -> ExitAnswer:
    """Restart the server, or say why not: THE door every restart request
    takes (this module's docstring names them).

    ``mode`` is the asker's word for how (``MODES``; ``None``, not said, is
    ``DEFAULT_MODE``) and ``source`` names the asker in the log. An accepted
    request starts the exit in a task of its own and returns at once, so the
    asker can answer; the stop begins ``wait_seconds`` later and the process
    ends through ``os._exit``. Anything this refuses is logged and changes
    nothing.
    """
    if mode is None:
        mode = DEFAULT_MODE
    engine = _engine
    if not isinstance(mode, str) or mode not in WAIT_SECONDS:
        return _refuse(
            source, mode, UNKNOWN_MODE,
            f"{mode!r} is not a restart mode. Use {' or '.join(MODES)}.",
        )
    if engine is None:
        return _refuse(
            source, mode, NOT_READY,
            "The server is still starting. Try the restart again once it is up.",
        )
    if _exiting:
        return _refuse(source, mode, UNDER_WAY, "A restart is already under way.")
    if engine.stopping:
        return _refuse(source, mode, UNDER_WAY, "The server is already stopping.")
    wait = WAIT_SECONDS[mode]
    log.warning(
        "Restart requested (source=%s, mode=%s); stopping %s",
        source, mode, f"in {wait} s" if wait else "now",
    )
    if _run_state is not None:
        _run_state.stopping()
    exiting = asyncio.create_task(_exit_after(wait, engine))
    _exiting.add(exiting)
    exiting.add_done_callback(_exiting.discard)
    return ExitAnswer(accepted=True, mode=mode, wait_seconds=wait)


def _refuse(source: str, mode: Any, refusal: str, sentence: str) -> ExitAnswer:
    log.warning("Restart refused (source=%s, mode=%r): %s", source, mode, sentence)
    return ExitAnswer(
        accepted=False, mode=str(mode), refusal=refusal, sentence=sentence,
    )


async def _exit_after(wait: int, engine) -> None:
    await asyncio.sleep(wait)
    await _stop_and_exit(engine)


async def _stop_and_exit(engine) -> None:
    # A root of its own: the shutdown's events run at the depth a service
    # stop's do, not one level under the request's emit.
    detach_emit_chain()
    deadline = stop_deadline()
    threading.Timer(LONGEST_EXIT_SECONDS, lambda: os._exit(0)).start()

    if not is_service_managed():
        spawn_replacement()

    try:
        await engine.shutdown(deadline)
    except Exception:
        log.exception("Error during graceful shutdown before restart")
    os._exit(0)


def is_service_managed() -> bool:
    """Whether something outside this process starts the next one once it
    ends: THE answer to who brings the server back, asked by a restart here
    and by the updater before an update or rollback that ends in an exit.

    True: ending the process is the whole restart, and whatever manages it
    starts the next one. False (a dev session): nothing will, so a restart
    spawns the replacement itself, and the updater does not offer to update.

    What says so, first match wins:
      OPENAVC_SERVICE_MANAGED=1  the installers' service units and the Docker
                                 image (the canonical signal)
      INVOCATION_ID              any systemd unit, an integrator's own included
      the deployment             appliance hardware (its device supervisor)
                                 and Docker (the container's restart policy),
                                 from the one deployment detection
                                 (updater/platform.py)
      PID 1                      a container of any other kind
    """
    if os.environ.get("OPENAVC_SERVICE_MANAGED") == "1":
        return True
    if os.environ.get("INVOCATION_ID"):
        return True
    if _deployed_under_a_supervisor():
        return True
    try:
        return os.getpid() == 1
    except OSError:
        return False


def _deployed_under_a_supervisor() -> bool:
    # Imported here: the updater package loads its manager, which asks this
    # module.
    from openavc.updater.platform import DeploymentType, detect_deployment_type

    return detect_deployment_type() in (
        DeploymentType.ANDROID_APPLIANCE,
        DeploymentType.DOCKER,
    )


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
