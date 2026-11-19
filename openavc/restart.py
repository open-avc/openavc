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
After a start that failed, openavc/start_retry.py arms the door too (Start
again on the splash page and a restart from the cloud take it), and ends the
process for a retry through ``exit_to_retry``: the same stop, a different exit
code (``RETRY_EXIT_CODE``), and nothing said on the run-state record, whose
failed phase stands for the tray until the next start writes its own.

An applied update or rollback ends the process through the same door
(``exit_for_update``, the updater's ``_end_process``): the same stop, the
exit code the install's service manager reads, and a last step of its own
after the stop (the rollback's device logins put back). It is never refused:
the update is applied by then. An exit or a stop already under way takes it
on (``_Ending``), so whatever ends the process first ends it the way the
update needs; a door not armed yet ends nothing, and the update takes effect
at the next start.

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
from typing import Any, Callable

from openavc import system_config
from openavc.core.event_bus import detach_emit_chain
from openavc.core.stop_stages import STOP_SECONDS, stop_deadline
from openavc.utils.logger import get_logger

# Logged under the entry point's name, where these lines have always been filed.
log = get_logger("openavc.main")

# How long after the stop's deadline the watchdog ends the process anyway.
WATCHDOG_MARGIN_SECONDS = 2.0
# From the start of the stop to the watchdog.
WATCHDOG_SECONDS = STOP_SECONDS + WATCHDOG_MARGIN_SECONDS
# How long the watchdog gives an exit's last step (``exit_for_update``)
# before it ends the process without it.
LAST_STEP_SECONDS = 1.0
# The longest a process can take to end once its stop begins, and so keep its
# port after it has spawned its replacement: the stop's deadline, the
# watchdog, the last step's bound. The replacement retries its port for
# longer than this (openavc.main), and the Windows installer an update
# schedules starts after it (updater/rollback.py ``INSTALLER_LEAD_SECONDS``).
LONGEST_EXIT_SECONDS = WATCHDOG_SECONDS + LAST_STEP_SECONDS

# The ways a restart can be asked for, and how long each waits before the stop
# begins: "graceful" waits so the request's own answer and log line get out
# first, "hard" does not. The cloud's wire contract names the same two
# (its "restart_modes" vocabulary; tests/test_cloud_wire_contract.py holds
# them equal), so the portal refuses any other before it is sent.
WAIT_SECONDS: dict[str, int] = {"graceful": 2, "hard": 0}
MODES: tuple[str, ...] = tuple(WAIT_SECONDS)
# What a request that names no mode gets.
DEFAULT_MODE = "graceful"

# What an applied update or rollback ends with where an installer, not the
# service manager, starts the version it applied: NSSM leaves the service
# stopped on it (installer/install-service.bat, "AppExit 42 Exit") and starts
# it again on any other code. Which install ends with it is the updater's
# rule (updater/platform.py ``update_exit``); tests/test_update_exit_code.py
# reads install-service.bat against it.
LEAVE_STOPPED_EXIT_CODE = 42

# What a process ends with when a failed start is tried again
# (``exit_to_retry``): not 0, so a service manager records a failure, and not
# LEAVE_STOPPED_EXIT_CODE, which NSSM would read as leave the service stopped.
# EX_TEMPFAIL: a failure that may pass, so try again.
RETRY_EXIT_CODE = 75

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


class _Ending:
    """How this process ends, whatever ends it: the exit code, and a last
    step to run before it. An update or rollback that ends the process
    (``exit_for_update``) sets both, and an exit or a stop already under way
    ends the process with them, so its service manager reads the update's
    code and the rollback's logins are put back."""

    def __init__(self) -> None:
        self.code = 0
        self.last: Callable[[Callable[[], None]], None] | None = None
        # An update or rollback has set the two.
        self.taken_on = False
        # Who runs the last step: the stop's end or the watchdog, once.
        self._once = threading.Lock()
        self._last_ran = False

    def take_on(self, code: int, last) -> None:
        self.code = code
        self.last = last
        self.taken_on = True

    def ends_with(self, code: int) -> None:
        """The code an exit of the door's own ends with (the failed start's
        retry), unless an update has set its own."""
        if not self.taken_on:
            self.code = code

    def end(self) -> None:
        os._exit(self.code)

    def finish(self) -> None:
        """Run the last step, then end. The last step is handed the end and
        may call it itself (the rollback's, holding the project save lock);
        a last step that raises is logged, and the process ends anyway."""
        with self._once:
            if self.last is not None and not self._last_ran:
                self._last_ran = True
                try:
                    self.last(self.end)
                except Exception:
                    log.exception("The last step before the exit failed")
        self.end()

    def finish_from_watchdog(self) -> None:
        """The watchdog's end: the last step gets ``LAST_STEP_SECONDS`` on a
        thread of its own (the event loop may be what hangs), and the process
        ends whether it is done or not, so the exit never waits on it."""
        if self.last is not None:
            helper = threading.Thread(target=self.finish, daemon=True)
            helper.start()
            helper.join(LAST_STEP_SECONDS)
        self.end()


_ending = _Ending()


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
    up, and openavc/start_retry.py after a start that failed."""
    global _engine, _run_state
    _engine = engine
    _run_state = run_state


def reset() -> None:
    """Disarm the door and forget any exit in flight (tests)."""
    global _engine, _run_state, _ending
    _engine = None
    _run_state = None
    _exiting.clear()
    _ending = _Ending()


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
    _start_exit(wait, engine)
    return ExitAnswer(accepted=True, mode=mode, wait_seconds=wait)


def exit_for_update(source: str, code: int, last=None) -> bool:
    """End the process for an update or rollback just applied (``source``
    names it in the log): the stop a graceful restart runs, after its wait so
    the answer to whoever asked gets out, then ``last`` (called with the
    process's end, which it may call itself), then the exit with ``code``.

    Never refused, since the update is applied already: an exit or a stop
    already under way takes on ``code`` and ``last`` and ends the process
    with them (a service stop through ``stop_for_service``). False, and
    nothing done, only when the door is not armed yet (the server's start is
    still running); the update then takes effect at the next start."""
    engine = _engine
    if engine is None:
        log.warning(
            "Not restarting for the %s: the server has not finished starting", source,
        )
        return False
    _ending.take_on(code, last)
    if _exiting or engine.stopping:
        log.warning(
            "The %s ends the process with the stop already under way (exit %d)", source, code,
        )
        return True
    wait = WAIT_SECONDS[DEFAULT_MODE]
    log.warning(
        "Restart for the %s (exit %d); stopping in %d s", source, code, wait,
    )
    _start_exit(wait, engine)
    return True


async def stop_for_service(engine) -> None:
    """The lifespan's stop, at a service stop: the engine's shutdown within
    the one deadline. An update or rollback that reached ``exit_for_update``
    while it ran then ends the process here, with its last step and its
    code; otherwise the lifespan ends as it does."""
    await engine.shutdown(stop_deadline())
    log.info("OpenAVC stopped")
    if _ending.taken_on:
        _ending.finish()


def _start_exit(wait: int, engine) -> None:
    if _run_state is not None:
        _run_state.stopping()
    exiting = asyncio.create_task(_exit_after(wait, engine))
    _exiting.add(exiting)
    exiting.add_done_callback(_exiting.discard)


def _refuse(source: str, mode: Any, refusal: str, sentence: str) -> ExitAnswer:
    log.warning("Restart refused (source=%s, mode=%r): %s", source, mode, sentence)
    return ExitAnswer(
        accepted=False, mode=str(mode), refusal=refusal, sentence=sentence,
    )


def exit_to_retry() -> bool:
    """End the process after a start that failed, for its service manager to
    start it again (openavc/start_retry.py decides when): the stop an accepted
    restart runs, exiting with ``RETRY_EXIT_CODE``. Nothing is said on the
    run-state record, so its failed phase stands for the tray and the menu
    bar until the next start writes its own. False, and nothing done, when the
    door is not armed or an exit or a stop is already under way (Start again
    pressed during the wait, a service stop)."""
    engine = _engine
    if engine is None:
        return False
    if _exiting or engine.stopping:
        return False
    log.warning("Startup failed; stopping so the service manager starts it again")
    _ending.ends_with(RETRY_EXIT_CODE)
    exiting = asyncio.create_task(_stop_and_exit(engine))
    _exiting.add(exiting)
    exiting.add_done_callback(_exiting.discard)
    return True


async def _exit_after(wait: int, engine) -> None:
    await asyncio.sleep(wait)
    await _stop_and_exit(engine)


async def _stop_and_exit(engine) -> None:
    # A root of its own: the shutdown's events run at the depth a service
    # stop's do, not one level under the request's emit.
    detach_emit_chain()
    deadline = stop_deadline()
    # Read when it fires: an update may take the exit on during the stop.
    threading.Timer(WATCHDOG_SECONDS, lambda: _ending.finish_from_watchdog()).start()

    if not is_service_managed():
        spawn_replacement()

    try:
        await engine.shutdown(deadline)
    except Exception:
        log.exception("Error during graceful shutdown before restart")
    _ending.finish()


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
