"""
OpenAVC ScriptEngine — loads user Python scripts with hot-reload.

Scripts live in a project's scripts/ directory and use decorators from
the 'openavc' package, which serves those names off openavc.core.script_api
through its own lazy __getattr__ (see openavc/__init__.py).

Lifecycle:
1. install() — wire proxy objects to real subsystem instances
2. configure() — bind each proxy to its subsystem
3. load_scripts() — import each script file, drain pending handlers,
   wrap with error protection, register on EventBus/StateStore
4. reload_scripts() — unregister old handlers, re-import, re-register
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import threading
import time
import traceback
import types
from collections.abc import Coroutine
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, NoReturn, TYPE_CHECKING

from openavc.core import script_api, script_failures, script_runs
# Defined beside the runner; importable from here, where they always were.
from openavc.core.script_runs import HandlerTimedOut
from openavc.core.script_runs import run_within_handler_limit as run_within_handler_limit
from openavc.utils.log_buffer import USER_SCRIPT_NAMESPACE
from openavc.utils.logger import get_logger
from openavc.utils.paths import safe_path_within

if TYPE_CHECKING:
    from openavc.core.device_manager import DeviceManager
    from openavc.core.event_bus import EventBus
    from openavc.core.macro_engine import MacroEngine
    from openavc.core.state_store import StateStore

log = get_logger(__name__)

# Stand-in for the Event object when test-binding an @on_event handler's
# signature at import time. Only its arity matters, never its value.
_EVENT_ARITY_PROBE = object()


# Tracks how deep a state-change -> @on_state_change -> (macro/state.set) ->
# state-change chain has recursed. Async state handlers fire as fresh
# fire-and-forget tasks, so a toggling value (a bool flip, a counter) slips
# past StateStore's unchanged-value short-circuit and would otherwise spawn
# tasks without bound. Propagated through the task hops via contextvars so the
# chain — not just one synchronous call stack — is what gets capped.
_state_handler_depth: ContextVar[int] = ContextVar("openavc_state_handler_depth", default=0)


def describe_parameters(fn: Callable) -> dict[str, Any]:
    """What a script function takes, in the shape the Builder draws.

    Keyword arguments only, because that is how a control calls one -- a
    binding stores parameters by name, so nothing about it is positional.
    A ``**kwargs`` function reports ``accepts_extra``, which is what lets the
    Builder offer a free-form row for a function that deliberately takes
    anything.

    ``type`` is reported only where the script actually says something: the
    author's annotation, or failing that the type of a non-None default. A
    guess would be worse than silence here -- the Builder picks a number input
    or a text input off this field, and the wrong one is a control that will
    not accept the value the device needs.
    """
    params: list[dict[str, Any]] = []
    accepts_extra = False
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # builtins and C functions have no signature
        return {"params": params, "accepts_extra": accepts_extra}

    for name, param in sig.parameters.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            accepts_extra = True
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.POSITIONAL_ONLY,
        ):
            continue
        entry: dict[str, Any] = {
            "name": name, "required": param.default is inspect.Parameter.empty,
        }
        if param.default is not inspect.Parameter.empty:
            entry["default"] = param.default
        annotation = param.annotation
        if annotation is not inspect.Parameter.empty:
            entry["type"] = getattr(annotation, "__name__", None) or str(annotation)
        elif param.default not in (inspect.Parameter.empty, None):
            entry["type"] = type(param.default).__name__
        params.append(entry)

    return {"params": params, "accepts_extra": accepts_extra}


class ScriptCallError(Exception):
    """A control named a script function and it could not be run.

    Carries a sentence for the glass, because the person who pressed the button
    is standing in the room: the name does not exist, two scripts define it, or
    the arguments do not fit its signature. An exception raised *inside* a
    function that ran is a different thing and keeps the handler treatment --
    logged, and a ``script.error`` event for the IDE -- with this raised on top
    so the press does not report success. Its text was written for whoever
    wrote the script, so the glass gets :data:`COULD_NOT_FINISH`, unless the
    function raised ``PanelMessage``, whose text is the author's sentence for
    the room. The exception it was raised from is the ``__cause__``.
    """


#: What the room is told when a function a control called raised, or ran too
#: long, and said nothing for the room itself.
COULD_NOT_FINISH = "This control couldn't finish. Contact support."


class LoadedScript:
    """One load of one script: THE record of what a loaded script started.

    A load begins when the script's file is imported and ends once, at its
    unload: an edit saved (the new version is a load of its own), the script
    disabled or removed, or the project reloaded. What the script starts
    belongs to the load, never to the script's id, so the two versions either
    side of a reload are never taken for each other:

    - its handlers: the event handlers and state subscriptions registered here;
    - its timers: ``script_api`` gives each ``after()``/``every()`` the load
      running when it was asked for;
    - its worker lane: ``script_runs`` keys a plain ``def``'s lane by it;
    - its runs: its top-level code and every handler, called function and
      timer callback run with it as the current script
      (``script_api.current_script_context``);
    - its last failure, kept by ``ScriptEngine._record_runtime_error``.

    Once unloaded it starts nothing more. A run already going finishes under
    its own limit, but a timer it asks for is not started and a run that had
    not begun (a handler queued by a change just before) is not run. A failure
    it meets is logged and kept off the next load's row.
    """

    __slots__ = (
        "script_id", "module", "event_handler_ids", "state_sub_ids",
        "handler_names", "runtime_error", "unloaded",
    )

    def __init__(self, script_id: str, module: types.ModuleType):
        self.script_id = script_id
        self.module = module
        self.event_handler_ids: list[str] = []
        self.state_sub_ids: list[str] = []
        # Names this script registered as an event/state handler. Kept because
        # a handler is exactly what a button must NOT be offered: it takes an
        # Event the bus supplies, so a control calling it by name could only
        # ever get the arguments wrong.
        self.handler_names: set[str] = set()
        # {"count", "handler", "event", "error", "traceback", "at"} for
        # failures AFTER the script loaded, from every door that runs its code
        # (ScriptEngine._record_runtime_error lists them). A load error means
        # the script is not running; one of these means it IS running and does
        # not work.
        self.runtime_error: dict[str, Any] | None = None
        self.unloaded = False

    def handler_count(self) -> int:
        """Its registered handlers, event and state."""
        return len(self.event_handler_ids) + len(self.state_sub_ids)

    def offered(self, name: str) -> Callable | None:
        """The function a control naming *name* runs here, or None.

        A function a control can call is a plain function the script defines:
        not private, not imported from somewhere else, and not one of its own
        handlers.
        """
        if name.startswith("_") or name in self.handler_names:
            return None
        fn = getattr(self.module, name, None)
        if inspect.isfunction(fn) and fn.__module__ == self.module.__name__:
            return fn
        return None

    def callable_functions(self) -> dict[str, Callable]:
        """Every function :meth:`offered` here, by name, in name order."""
        names = [name for name, _fn in inspect.getmembers(self.module, inspect.isfunction)]
        return {name: fn for name in names if (fn := self.offered(name)) is not None}



class ScriptEngine:
    """Loads user scripts, registers their decorated handlers, supports hot-reload."""

    def __init__(
        self,
        state: StateStore,
        events: EventBus,
        devices: DeviceManager,
        project_dir: Path,
        macro_engine: MacroEngine | None = None,
    ):
        self.state = state
        self.events = events
        self.devices = devices
        self.macro_engine = macro_engine
        self.project_dir = project_dir
        self.scripts_dir = project_dir / "scripts"

        # script_id -> the loaded script's record (LoadedScript): what it
        # registered and started and its last failure, so one script can be
        # torn down without disturbing its peers.
        self._scripts: dict[str, LoadedScript] = {}
        # The two below are about an id with no loaded script, so they are
        # kept beside the records rather than in one.
        self._load_errors: dict[str, str] = {}  # script_id -> error message
        # script_id -> {"attempts", "threads", "since"} for loads that timed
        # out. Abandoning a load thread does not stop it, so this is what lets
        # anything downstream say a runaway exists -- nothing did, and only a
        # restart cleared it.
        self._abandoned_loads: dict[str, dict[str, Any]] = {}

    def install(self) -> None:
        """Wire the script-API proxies to the real subsystems.

        There is nothing to inject into sys.modules any more: `openavc` is a
        real package and serves these names itself. All that is left is binding
        the proxies to live objects, which is what makes them usable, and
        naming the loop a call from another thread is handed to.
        """
        script_api.configure(self.devices, self.state, self.events, self.macro_engine)
        script_failures.bind(lambda failure: self._record_runtime_error(
            failure.script, failure.handler, failure.event, failure.error,
            failure.traceback,
        ))
        try:
            script_runs.bind_loop(asyncio.get_running_loop())
        except RuntimeError:
            pass  # no loop yet (a sync caller); the first run binds it
        log.info("Script API wired")

    def load_scripts(self, scripts: list[dict[str, Any]]) -> int:
        """
        Load script files and register their handlers.

        Args:
            scripts: List of script config dicts with 'id', 'file', 'enabled' keys.

        Returns:
            Number of handlers registered.
        """
        handler_count = 0
        self._load_errors.clear()
        for script_cfg in scripts:
            if not script_cfg.get("enabled", True):
                log.info(f"Script '{script_cfg['id']}' is disabled, skipping")
                continue

            script_file = script_cfg["file"]
            script_id = script_cfg["id"]
            script_path = self._resolve_script_path(script_id, script_file)
            if script_path is None:
                continue  # error already recorded in self._load_errors

            try:
                count = self._load_single_script(script_id, script_path)
                handler_count += count
                log.info(
                    f"Loaded script '{script_id}' ({script_file}) — "
                    f"{count} handler(s)"
                )
            except Exception as exc:  # Catch-all: loading user scripts can raise anything
                log.exception(f"Failed to load script '{script_id}' ({script_file})")
                self._load_errors[script_id] = str(exc)

        if handler_count:
            log.info(f"ScriptEngine: {handler_count} total handler(s) registered")
        return handler_count

    def _resolve_script_path(self, script_id: str, script_file: str) -> Path | None:
        """Resolve a script file to an absolute path *inside* the scripts dir.

        A project's ``scripts[].file`` is otherwise an unvalidated string, so a
        shared/imported project could point it at ``../../secret.py`` or an
        absolute path and have the engine read and ``exec()`` arbitrary on-disk
        Python at load time. ``safe_path_within`` rejects any path that escapes
        (via ``..``, an absolute path, or a symlink resolving outside). Records
        a load error and returns ``None`` on rejection or a missing file.
        """
        script_path = safe_path_within(self.scripts_dir, script_file)
        if script_path is None:
            msg = f"Script file path escapes the scripts directory: {script_file!r}"
            log.error(f"Refusing to load script '{script_id}': {msg}")
            self._load_errors[script_id] = msg
            return None
        if not script_path.exists():
            msg = f"Script file not found: {script_path}"
            log.error(msg)
            self._load_errors[script_id] = msg
            return None
        return script_path

    def get_load_errors(self) -> dict[str, str]:
        """Return dict of script_id -> error message for scripts that failed to load."""
        return dict(self._load_errors)

    def _record_runtime_error(
        self, script: LoadedScript, handler: str, event: str, error: str, tb: str,
    ) -> None:
        """Remember that a loaded script failed while running.

        Synchronous and never optional: the emit that goes with it needs an
        event loop and a listener, and this record is what answers the scripts
        view whether anyone was watching at the time. Keeps a count and the
        LAST failure rather than a list -- a handler that throws throws every
        time it fires, so the hundredth occurrence says nothing the first did
        not, and the count is what tells an integrator the button is dead
        rather than flaky.

        THE record every door that runs a loaded script's code reports to: an
        event or state handler, a control's call, refused or raised, and,
        through ``script_failures`` (bound by ``install``), a timer callback
        and an ``events.emit`` the bus dropped. Only the load that is running
        is recorded: a script that is not loaded has no row to show it on, and
        a version an edit has replaced, failing in a run still going at the
        swap, must not mark the version that replaced it.
        """
        if self._scripts.get(script.script_id) is not script:
            return
        if script.runtime_error is None:
            script.runtime_error = {"count": 0}
        record = script.runtime_error
        record["count"] += 1
        record["handler"] = handler
        record["event"] = event
        record["error"] = error
        record["traceback"] = tb
        record["at"] = time.time()

    def get_runtime_errors(self) -> dict[str, dict[str, Any]]:
        """Per script, how many times it has failed while running and the last one.

        Cleared when the script is reloaded or unloaded, because both mean the
        code that failed is no longer the code that is running. Nothing else
        clears it: a failure the integrator never saw must still be there when
        they open the view.
        """
        return {
            script_id: dict(script.runtime_error)
            for script_id, script in self._scripts.items()
            if script.runtime_error
        }

    def handler_count(self) -> int:
        """Total registered handlers across all loaded scripts (event + state)."""
        return sum(script.handler_count() for script in self._scripts.values())

    # Timeout for script top-level execution (seconds)
    SCRIPT_LOAD_TIMEOUT = 10
    # Time limit for one run of a handler, timer callback or called function
    # (seconds), plain def and async def alike
    HANDLER_TIMEOUT = 30
    # Max depth of a state-change -> async @on_state_change -> state-change
    # cascade before further async handlers in the chain are dropped. Bounds a
    # runaway toggle/counter loop (which evades the unchanged-value short-
    # circuit) while leaving room for legitimate multi-step reactions.
    MAX_STATE_HANDLER_DEPTH = 8

    def _load_single_script(self, script_id: str, script_path: Path) -> int:
        """Import a single script file and register its handlers/timers."""
        script, event_handlers, state_handlers = self._import_script(
            script_id, script_path
        )
        return self._register_script(script, event_handlers, state_handlers)

    def _import_script(
        self, script_id: str, script_path: Path
    ) -> tuple[LoadedScript, list, list]:
        """Phase 1 of loading: execute the script's top-level code and drain its
        decorated handlers, WITHOUT registering anything on the bus yet.

        Separated from registration so a hot-reload can import the new version
        first and only swap it in if the import succeeds — a failed re-import
        leaves the running version untouched. The module is not published to
        ``sys.modules`` here; ``_register_script`` does that on commit so a
        half-imported (or timed-out) script is never visible.
        """
        # NOT `openavc.user_scripts.<id>`: `openavc` is the shipped package now,
        # so that spelling would park user code inside it as a real subpackage.
        # This name is deliberately outside it, and it is also what the log
        # categoriser matches on to tell a script's output from the platform's.
        module_name = f"{USER_SCRIPT_NAMESPACE}.{script_id}"

        # Drop anything a previous (possibly leaked/timed-out) load left parked
        # in the module-level pending buffers before this script runs.
        script_api.drain_pending()
        script_api.discard_pending_timers()

        # Read source and exec directly (bypasses .pyc caching for hot-reload)
        source = script_path.read_text(encoding="utf-8")
        code = compile(source, str(script_path), "exec")

        module = types.ModuleType(module_name)
        module.__file__ = str(script_path)
        script = LoadedScript(script_id, module)

        try:
            self._exec_with_timeout(script, code)
            event_handlers, state_handlers = script_api.drain_pending()
            self._check_event_handler_signatures(event_handlers)
        except BaseException:
            # A failed/timed-out import must not leave its timers parked to be
            # materialized later, and is a load that has ended: a thread its
            # top-level code started gets nothing more of it.
            script.unloaded = True
            script_api.discard_pending_timers()
            raise

        return script, event_handlers, state_handlers

    @staticmethod
    def _check_event_handler_signatures(event_handlers: list) -> None:
        """Refuse @on_event handlers that can't accept a single Event argument.

        Checked here, in phase 1, so nothing is registered and a hot-reload
        keeps the running version. The alternative is a TypeError raised deep
        inside the handler call the first time the event happens to fire —
        which reads as "my script silently doesn't work" rather than "change
        the signature".

        Older scripts took ``(event_name, payload)``. That form is gone;
        naming it in the message is the whole point of checking.
        """
        bad: list[str] = []
        for pattern, handler in event_handlers:
            try:
                sig = inspect.signature(handler)
            except (ValueError, TypeError):
                # Not introspectable (a builtin or C callable). Let it run and
                # fail at call time rather than refuse something that works.
                continue
            try:
                sig.bind(_EVENT_ARITY_PROBE)
            except TypeError:
                name = getattr(handler, "__name__", "anonymous")
                bad.append(f"{name}{sig} for '{pattern}'")

        if bad:
            raise ValueError(
                "Event handlers take a single argument, the Event object — "
                "e.g. `async def handler(event):` then read `event.name` and "
                "`event.payload`. These do not: " + "; ".join(bad)
            )

    def _exec_with_timeout(self, script: LoadedScript, code: Any) -> None:
        """Run a script's top-level code in a daemon thread bounded by a timeout.

        The thread isolates the event loop from an infinite loop in module-level
        code (`while True: pass`). It is a real daemon thread: if the script is
        genuinely stuck, the thread is abandoned and, because daemon threads are
        NOT joined at interpreter shutdown, the server can still exit cleanly.
        (The previous ThreadPoolExecutor worker was non-daemon and was joined at
        exit, so one runaway script hung shutdown forever.)

        Abandoning is not stopping, which is the other half. The thread goes on
        the script API's leash, so it unwinds the next time it touches the
        platform — the loop body of every runaway that can do harm. One that
        touches nothing cannot be stopped at all (Python cannot kill a thread),
        so it is recorded instead and reported by ``get_abandoned_loads``.

        The code runs as *script*, so a timer it starts is this load's, and its
        ``cancel_all_timers()`` reaches no further than this load's own.
        """
        script_id = script.script_id
        box: dict[str, BaseException] = {}
        done = threading.Event()

        def _runner() -> None:
            # The loop is blocked on done.wait() below, so this thread's
            # script-API calls must run here rather than be handed to it.
            script_runs.mark_load_thread()
            try:
                with script_api.current_script_context(script):
                    exec(code, script.module.__dict__)
            except script_api.ScriptLoadAbandoned:
                log.info(
                    f"Abandoned load thread for script '{script_id}' unwound "
                    f"at its next platform call"
                )
            except BaseException as exc:  # re-raised on the caller thread below
                box["exc"] = exc
            finally:
                script_api.release_load_thread(threading.current_thread())
                # This thread is still alive while it runs its own finally, so
                # say so explicitly rather than counting itself as a runaway.
                self._publish_abandoned_count(finished=threading.current_thread())
                done.set()

        thread = threading.Thread(
            target=_runner, name=f"script-load-{script_id}", daemon=True
        )
        thread.start()
        if not done.wait(timeout=self.SCRIPT_LOAD_TIMEOUT):
            script_api.abandon_load_thread(thread, script_id)
            self._record_abandoned_load(script_id, thread)
            self._publish_abandoned_count()
            log.warning(
                f"Script '{script_id}' timed out during loading "
                f"(>{self.SCRIPT_LOAD_TIMEOUT}s) — abandoning its load thread "
                f"(a daemon thread, so it will not block server shutdown). It "
                f"stops at its next call into the platform; top-level code that "
                f"calls nothing keeps running until the server restarts"
            )
            raise RuntimeError(
                f"Script '{script_id}' timed out during loading "
                f"(>{self.SCRIPT_LOAD_TIMEOUT}s) — possible infinite loop "
                f"in top-level code"
            )
        if self._abandoned_loads.pop(script_id, None) is not None:
            self._publish_abandoned_count()
        exc = box.get("exc")
        if exc is not None:
            raise exc

    def _record_abandoned_load(
        self, script_id: str, thread: threading.Thread
    ) -> None:
        """Remember a load we gave up on, so the scripts view can say so.

        Keeps every attempt's thread: a retry that times out again leaves two,
        and whether ANY of them is still alive is the thing worth showing.
        """
        record = self._abandoned_loads.setdefault(
            script_id, {"attempts": 0, "threads": [], "since": time.time()}
        )
        record["attempts"] += 1
        record["threads"].append(thread)

    def _publish_abandoned_count(
        self, finished: threading.Thread | None = None
    ) -> None:
        """Keep ``system.abandoned_script_loads`` current.

        The scripts view is where this belongs, but it is a view of the
        project's scripts -- delete the script and the warning goes with it
        while the thread keeps going. A state key outlives that, and is
        readable from the State tab, a panel label, and the cloud.

        ``finished`` is a thread that is unwinding right now: it is still alive
        while it runs its own ``finally``, and counting it would leave the key
        reading 1 for a runaway that has just stopped.
        """
        running = sum(
            1 for record in self._abandoned_loads.values()
            if any(
                t.is_alive() and t is not finished for t in record["threads"]
            )
        )
        self.state.set("system.abandoned_script_loads", running, source="system")

    def get_abandoned_loads(self) -> dict[str, dict[str, Any]]:
        """Loads this engine gave up on, and whether one is still running.

        ``running`` is read from the threads themselves rather than cached, so
        a load that unwinds on the leash stops being reported as running without
        anything having to notice. The record survives until the script loads
        cleanly again: a runaway nothing can stop is exactly what a restart is
        needed for, and it must not disappear on its own.
        """
        return {
            script_id: {
                "attempts": record["attempts"],
                "running": any(t.is_alive() for t in record["threads"]),
                "since": record["since"],
            }
            for script_id, record in self._abandoned_loads.items()
        }

    def _register_script(
        self,
        script: LoadedScript,
        event_handlers: list,
        state_handlers: list,
    ) -> int:
        """Phase 2 of loading: publish the load, in place of whichever was
        loaded under its id, and register its handlers and any timers it
        created at top level. Everything is kept on the record, so the script
        can later be torn down on its own."""
        self.unload_script(script.script_id)
        sys.modules[script.module.__name__] = script.module
        self._scripts[script.script_id] = script
        for _pattern, handler in (*event_handlers, *state_handlers):
            handler_name = getattr(handler, "__name__", "")
            if handler_name:
                script.handler_names.add(handler_name)

        for pattern, handler in event_handlers:
            wrapped = self._wrap_event_handler(handler, script)
            script.event_handler_ids.append(self.events.on(pattern, wrapped))

        for pattern, handler in state_handlers:
            wrapped = self._wrap_state_handler(handler, script)
            script.state_sub_ids.append(self.state.subscribe(pattern, wrapped))

        # Materialize any timers the script registered at top level. They were
        # parked during the off-loop exec; create them on the loop now as this
        # load's, so a per-script reload can cancel them.
        script_api.materialize_pending_timers(script)

        return script.handler_count()

    def unload_all(self) -> None:
        """Unload every script, then cancel any timer started outside one."""
        count = sum(self.unload_script(script_id) for script_id in list(self._scripts))
        script_runs.close_all()
        timer_count = script_api.stop_all_timers()
        if timer_count:
            log.info(f"Cancelled {timer_count} timer(s) started outside any script")
        if count:
            log.info(f"Unloaded {count} handler(s)")

    def unload_script(self, script_id: str) -> int:
        """End a script's load: unregister its handlers, subscriptions, timers
        and module, and start nothing more it asks for, leaving every other
        script untouched. Returns the handler count removed.

        Its failures go with it, being no longer registered to fail again. A
        single-script reload comes through here between importing the new
        version and registering it, so a successful reload starts clean while a
        failed one keeps the record of the version still running.
        """
        script = self._scripts.pop(script_id, None)
        if script is None:
            return 0
        # First: a run of it still going asks for nothing more from here on.
        script.unloaded = True
        for hid in script.event_handler_ids:
            self.events.off(hid)
        for sid in script.state_sub_ids:
            self.state.unsubscribe(sid)
        sys.modules.pop(script.module.__name__, None)

        script_runs.close_script(script)
        timer_count = script_api.cancel_script_timers(script)
        count = script.handler_count()
        if count or timer_count:
            log.info(
                f"Unloaded script '{script_id}' "
                f"({count} handler(s), {timer_count} timer(s))"
            )
        return count

    def get_callable_functions(self) -> list[dict[str, Any]]:
        """Return the functions a control can call, and what each one takes.

        A callable function is a plain function the script defines: not
        private, not imported from somewhere else, and **not one of its own
        decorated handlers**. That last exclusion is the one worth saying out
        loud. A handler takes the ``Event`` the bus hands it when its pattern
        fires; a control calling it by name has no Event to give, so offering
        one here would put a name in the Builder's dropdown that can only ever
        be called wrong. The exclusion was claimed in this docstring long
        before anything did it, which is how every function in the shipped
        starter project came to be offered as a button target.

        Each entry carries the parameters the Builder draws an editor for --
        name, whether it is required, its default, and its annotation where the
        author wrote one -- so a control's parameters are picked from the
        signature rather than typed from memory, the same way a device
        command's are picked from the driver.
        """
        return [
            {
                "script": script_id,
                "function": name,
                "doc": (inspect.getdoc(fn) or "")[:200],
                **describe_parameters(fn),
            }
            for script_id, script in self._scripts.items()
            for name, fn in script.callable_functions().items()
        ]

    def find_callable(self, name: str, script: str = "") -> list[tuple[str, Callable]]:
        """Every loaded function of this name, as (script_id, function).

        More than one is possible and is not an error here: two scripts may
        each define ``set_lights``. Which one a control meant is the caller's
        problem, and ``script`` narrows it when the control recorded one.
        """
        found: list[tuple[str, Callable]] = []
        for script_id, loaded in self._scripts.items():
            if script and script_id != script:
                continue
            fn = loaded.offered(name)
            if fn is not None:
                found.append((script_id, fn))
        return found

    async def call_function(
        self, name: str, params: dict[str, Any] | None = None, script: str = "",
    ) -> None:
        """Call a script function by name, the way a control asks for it.

        This is the other door into a script. A handler is called BY the bus
        when its pattern fires and reads an ``Event``; this is called by a
        person pressing a control, and reads ordinary arguments -- so a button,
        a slider and a list row can all reach one function and tell it which
        one they were.

        Everything that goes wrong raises ``ScriptCallError`` carrying a
        sentence, because the press path turns one into a message on the glass.
        The three that are the author's to fix -- no such function, two scripts
        defining it, arguments that do not fit the signature -- are caught
        BEFORE anything runs, so a mistyped name never half-runs a room, and
        are recorded against each loaded script they name (``_refuse_call``).
        What happens once the function is running gets the handler treatment
        as well: logged, recorded, and a ``script.error`` event, while the
        glass reads ``COULD_NOT_FINISH`` (or the sentence a ``PanelMessage``
        carries).

        Runs the way a handler does (``_wrap_event_handler``): a plain ``def``
        on the script's worker thread, an ``async def`` on the loop, both
        under ``HANDLER_TIMEOUT``. An emit already awaits its handlers, so
        nothing here waits on a press for longer than the event path did.
        """
        params = params or {}
        matches = self.find_callable(name, script)
        if not matches:
            where = f" in script '{script}'" if script else ""
            await self._refuse_call(name, [script] if script else [], (
                f"No script function named '{name}'{where}. Check the function "
                f"is defined in an enabled script and is not an event handler."
            ))
        if len(matches) > 1:
            scripts = sorted(script_id for script_id, _fn in matches)
            await self._refuse_call(name, scripts, (
                f"More than one script defines '{name}' ({', '.join(scripts)}). "
                f"Rename one of them so this control can only mean one function."
            ))

        script_id, fn = matches[0]
        loaded = self._scripts[script_id]
        try:
            inspect.signature(fn).bind(**params)
        except TypeError as exc:
            wanted = describe_parameters(fn)["params"]
            takes = ", ".join(str(entry["name"]) for entry in wanted) or "no arguments"
            given = ", ".join(sorted(params)) or "none"
            await self._refuse_call(name, [script_id], (
                f"'{name}' cannot be called with those parameters: {exc}. "
                f"It takes {takes}; this control passed {given}."
            ), exc)

        with script_api.current_script_context(loaded):
            try:
                await script_runs.run_script_code(
                    loaded, fn, (), params, self.HANDLER_TIMEOUT,
                )
            except script_api.PanelMessage as exc:
                # The author refusing in the room's words, on purpose: not a
                # script error, so nothing for the Scripts view to mark.
                sentence = str(exc).strip()
                log.info(f"Script '{script_id}' function '{name}' told the panel: {sentence}")
                raise ScriptCallError(sentence or COULD_NOT_FINISH) from exc
            except HandlerTimedOut as exc:
                msg = (
                    f"Script '{script_id}' function '{name}' timed out after "
                    f"{self.HANDLER_TIMEOUT}s"
                )
                log.error(msg)
                await self._emit_script_error(loaded, name, f"script.call.{name}", msg, "")
                raise ScriptCallError(COULD_NOT_FINISH) from exc
            except script_runs.ScriptRunRefused as exc:
                log.error(f"Script '{script_id}' function '{name}': {exc}")
                await self._emit_script_error(
                    loaded, name, f"script.call.{name}", str(exc), "",
                )
                raise ScriptCallError(COULD_NOT_FINISH) from exc
            except Exception as exc:  # Catch-all: isolates user script errors from engine
                log.exception(f"Error in script '{script_id}' function '{name}'")
                await self._emit_script_error(
                    loaded, name, f"script.call.{name}", str(exc),
                    traceback.format_exc(),
                )
                raise ScriptCallError(COULD_NOT_FINISH) from exc

    async def _refuse_call(
        self,
        name: str,
        script_ids: list[str],
        sentence: str,
        cause: BaseException | None = None,
    ) -> NoReturn:
        """Refuse a control's call before the function runs, and record it
        against each loaded script it names: the place the fix goes, whether
        that is the control or the script."""
        log.error(f"Control's call to script function '{name}' refused: {sentence}")
        for script_id in script_ids:
            loaded = self._scripts.get(script_id)
            if loaded is not None:
                await self._emit_script_error(
                    loaded, name, f"script.call.{name}", sentence, "",
                )
        raise ScriptCallError(sentence) from cause

    async def _emit_script_error(
        self, script: LoadedScript, handler: str, event: str, error: str, tb: str,
    ) -> None:
        """Record a script failure, report it on the bus, and never fail doing it.

        The record comes first because it is the half that outlives the moment:
        the event reaches whoever happens to be listening, the record answers
        the scripts view whenever it is next asked.
        """
        self._record_runtime_error(script, handler, event, error, tb)
        try:
            await self.events.emit("script.error", {
                "script_id": script.script_id,
                "handler": handler,
                "event": event,
                "error": error,
                "traceback": tb,
            })
        except Exception:  # Catch-all: error event emission must not raise
            pass

    def reload_scripts(self, scripts: list[dict[str, Any]]) -> int:
        """Hot-reload the whole project: unload everything, then re-load."""
        log.info("Reloading scripts...")
        self.unload_all()
        return self.load_scripts(scripts)

    def reload_script(self, script_cfg: dict[str, Any]) -> dict[str, Any]:
        """Hot-reload a single script in isolation.

        Only this script is torn down and rebuilt; every other script's
        handlers and timers (and their ``every()`` phase) keep running. The new
        version is imported BEFORE the old one is unloaded, so if the re-import
        fails the previously loaded version stays active — a peer's edit can no
        longer silently kill a working script. Mirrors the Python-driver reload
        contract: returns ``{"status", "handlers"?, "error"?,
        "old_script_preserved"?}``.
        """
        script_id = script_cfg["id"]
        self._load_errors.pop(script_id, None)
        was_loaded = script_id in self._scripts

        if not script_cfg.get("enabled", True):
            self.unload_script(script_id)
            return {"status": "unloaded", "handlers": 0}

        script_path = self._resolve_script_path(script_id, script_cfg["file"])
        if script_path is None:
            return {
                "status": "error",
                "error": self._load_errors.get(script_id, "invalid script path"),
                "old_script_preserved": was_loaded,
            }

        # Phase 1: import the new version without disturbing the running one.
        try:
            script, event_handlers, state_handlers = self._import_script(
                script_id, script_path
            )
        except Exception as exc:  # Catch-all: user scripts can raise anything
            log.exception(f"Failed to reload script '{script_id}'")
            self._load_errors[script_id] = str(exc)
            return {
                "status": "error",
                "error": str(exc),
                "old_script_preserved": was_loaded,
            }

        # Phase 2: import succeeded — swap the old version out for the new.
        count = self._register_script(script, event_handlers, state_handlers)
        log.info(f"Reloaded script '{script_id}' — {count} handler(s)")
        return {"status": "reloaded", "handlers": count}

    def _wrap_event_handler(
        self, handler: Callable, script: LoadedScript
    ) -> Callable:
        """Wrap an event handler with error protection.

        Handlers take a single ``Event`` argument. Anything that cannot
        accept one was already refused at import time by
        ``_check_event_handler_signatures``. A plain ``def`` runs on the
        script's worker thread (``script_runs``), so a blocking line in it
        holds up this script and nothing else; the emit still waits for it,
        the way it waits for an ``async def``.
        """
        script_id = script.script_id

        async def wrapped(event: str, payload: dict[str, Any]) -> None:
            with script_api.current_script_context(script):
                try:
                    from openavc.core.script_api import Event
                    await script_runs.run_script_code(
                        script, handler, (Event(event, payload),), None,
                        self.HANDLER_TIMEOUT,
                    )
                except script_runs.ScriptRunRefused as exc:
                    handler_name = getattr(handler, "__name__", "anonymous")
                    log.error(
                        f"Script '{script_id}' handler '{handler_name}' for "
                        f"'{event}': {exc}"
                    )
                    await self._emit_script_error(
                        script, handler_name, event, str(exc), ""
                    )
                except HandlerTimedOut:
                    handler_name = getattr(handler, "__name__", "anonymous")
                    msg = (
                        f"Script '{script_id}' handler '{handler_name}' timed out "
                        f"after {self.HANDLER_TIMEOUT}s for event '{event}'"
                    )
                    log.error(msg)
                    await self._emit_script_error(
                        script, handler_name, event, msg, ""
                    )
                except Exception as exc:  # Catch-all: isolates user script errors from engine
                    handler_name = getattr(handler, "__name__", "anonymous")
                    log.exception(
                        f"Error in script '{script_id}' event handler "
                        f"for '{event}'"
                    )
                    await self._emit_script_error(
                        script, handler_name, event, str(exc),
                        traceback.format_exc(),
                    )

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped

    def _wrap_state_handler(
        self, handler: Callable, script: LoadedScript
    ) -> Callable:
        """Wrap a state-change handler with error protection.

        StateStore listeners receive (key, old_value, new_value, source).
        User handlers receive (key, old_value, new_value) — source is omitted
        for simplicity.

        On the loop, every handler runs as its own task after ``state.set``
        returns: an ``async def`` on the loop, a plain ``def`` on the script's
        worker thread, both under ``HANDLER_TIMEOUT``. Because each is a task,
        the state-change cascade depth bounds a plain handler's feedback loop
        the same way it bounds an async one's. The wrapper hands the run back
        to the state store as a coroutine, so the store holds the task and a
        stop waits for a handler still running (StateStore.flush_pending_events).
        """
        script_id = script.script_id

        def wrapped(
            key: str, old_value: Any, new_value: Any, source: str,
        ) -> Coroutine[Any, Any, None] | None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # Written from a thread with no loop (a sync caller, a plugin's
                # own thread): run it right here, as before there was a runner.
                self._run_state_handler_here(
                    handler, script, key, old_value, new_value
                )
                return None

            # Bound the state-change cascade so a toggling value (which evades
            # StateStore's unchanged-value short-circuit) can't spawn tasks
            # without limit.
            depth = _state_handler_depth.get()
            if depth >= self.MAX_STATE_HANDLER_DEPTH:
                handler_name = getattr(handler, "__name__", "anonymous")
                msg = (
                    f"Script '{script_id}' state handler '{handler_name}' dropped "
                    f"for '{key}' — max state-change cascade depth "
                    f"({self.MAX_STATE_HANDLER_DEPTH}) reached. Possible "
                    f"recursive state-change loop."
                )
                log.warning(msg)
                self._schedule_state_error(script, handler, key, msg, "")
                return None

            return self._run_state_handler(
                handler, (key, old_value, new_value), depth + 1, script, key,
            )

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped

    def _run_state_handler_here(
        self,
        handler: Callable,
        script: LoadedScript,
        key: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        """Run a state handler on the calling thread, which has no loop.

        A plain ``def`` runs to completion here; an ``async def`` has nothing
        to run on and is closed unrun, which is recorded as its failure.
        """
        script_id = script.script_id
        try:
            with script_api.current_script_context(script):
                result = handler(key, old_value, new_value)
        except Exception as exc:  # Catch-all: isolates a sync handler's error
            log.exception(
                f"Error in script '{script_id}' state handler for '{key}'"
            )
            self._schedule_state_error(
                script, handler, key, str(exc), traceback.format_exc()
            )
            return
        if asyncio.iscoroutine(result):
            result.close()  # never awaited — avoid the "coroutine" warning
            handler_name = getattr(handler, "__name__", "anonymous")
            msg = (
                f"'{handler_name}' did not run: '{key}' was set from a thread "
                f"with no event loop, and an async def handler needs one. A "
                f"plain def handler runs there."
            )
            log.error(f"Script '{script_id}' state handler {msg}")
            self._schedule_state_error(script, handler, key, msg, "")

    async def _run_state_handler(
        self,
        handler: Callable,
        args: tuple,
        depth: int,
        script: LoadedScript,
        key: str,
    ) -> None:
        """Run one @on_state_change handler under the limit, surfacing errors.

        ``depth`` is published in a contextvar so it propagates through the
        task hops (and the worker thread's hand-offs) a state-change cascade
        creates — that's what lets ``MAX_STATE_HANDLER_DEPTH`` cap the whole
        chain and not just one call stack. A timeout or exception is
        re-emitted as ``script.error``, honouring the documented guarantee
        that handler errors surface.
        """
        script_id = script.script_id
        depth_token = _state_handler_depth.set(depth)
        handler_name = getattr(handler, "__name__", "anonymous")
        try:
            with script_api.current_script_context(script):
                await script_runs.run_script_code(
                    script, handler, args, None, self.HANDLER_TIMEOUT,
                )
        except asyncio.CancelledError:
            raise
        except script_runs.ScriptRunRefused as exc:
            log.error(
                f"Script '{script_id}' state handler '{handler_name}' for "
                f"'{key}': {exc}"
            )
            await self._emit_state_error_async(script, handler_name, key, str(exc), "")
        except HandlerTimedOut:
            msg = (
                f"Script '{script_id}' state handler '{handler_name}' timed out "
                f"after {self.HANDLER_TIMEOUT}s for '{key}'"
            )
            log.error(msg)
            await self._emit_state_error_async(script, handler_name, key, msg, "")
        except Exception as exc:  # Catch-all: isolates user script errors
            log.exception(
                f"Error in script '{script_id}' state handler for '{key}'"
            )
            await self._emit_state_error_async(
                script, handler_name, key, str(exc), traceback.format_exc()
            )
        finally:
            _state_handler_depth.reset(depth_token)

    def _schedule_state_error(
        self, script: LoadedScript, handler: Callable, key: str, error: str, tb: str
    ) -> None:
        """Schedule a state-handler ``script.error`` emit from a sync context.

        A state listener runs on whichever thread called ``state.set``, so
        there may be no loop to emit on. The record is not conditional on one:
        it is written here in that case, which is exactly when nothing else
        will ever say the handler failed.
        """
        handler_name = getattr(handler, "__name__", "anonymous")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._record_runtime_error(
                script, handler_name, f"state_change:{key}", error, tb
            )
            return  # no loop — nothing to emit on
        loop.create_task(
            self._emit_state_error_async(script, handler_name, key, error, tb)
        )

    async def _emit_state_error_async(
        self, script: LoadedScript, handler_name: str, key: str, error: str, tb: str
    ) -> None:
        """Record and emit ``script.error`` for a state handler; never raises."""
        await self._emit_script_error(
            script, handler_name, f"state_change:{key}", error, tb
        )
