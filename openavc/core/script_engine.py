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
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from openavc.core import script_api
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
    so the press does not report success.
    """



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

        # Tracking for unregistration on reload — keyed by script_id so a
        # single script can be torn down without disturbing its peers.
        self._event_handler_ids: dict[str, list[str]] = {}  # script_id -> handler ids
        self._state_sub_ids: dict[str, list[str]] = {}      # script_id -> sub ids
        self._loaded_modules: dict[str, str] = {}  # script_id -> module_name
        self._load_errors: dict[str, str] = {}  # script_id -> error message
        # script_id -> names this script registered as an event/state handler.
        # Kept because a handler is exactly what a button must NOT be offered:
        # it takes an Event the bus supplies, so a control calling it by name
        # could only ever get the arguments wrong.
        self._handler_names: dict[str, set[str]] = {}
        # script_id -> {"attempts", "threads", "since"} for loads that timed
        # out. Abandoning a load thread does not stop it, so this is what lets
        # anything downstream say a runaway exists -- nothing did, and only a
        # restart cleared it.
        self._abandoned_loads: dict[str, dict[str, Any]] = {}

    def install(self) -> None:
        """Wire the script-API proxies to the real subsystems.

        There is nothing to inject into sys.modules any more: `openavc` is a
        real package and serves these names itself. All that is left is binding
        the proxies to live objects, which is what makes them usable.
        """
        script_api.configure(self.devices, self.state, self.events, self.macro_engine)
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

    def handler_count(self) -> int:
        """Total registered handlers across all loaded scripts (event + state)."""
        return sum(len(ids) for ids in self._event_handler_ids.values()) + sum(
            len(ids) for ids in self._state_sub_ids.values()
        )

    # Timeout for script top-level execution (seconds)
    SCRIPT_LOAD_TIMEOUT = 10
    # Timeout for async event/state handlers (seconds)
    HANDLER_TIMEOUT = 30
    # Max depth of a state-change -> async @on_state_change -> state-change
    # cascade before further async handlers in the chain are dropped. Bounds a
    # runaway toggle/counter loop (which evades the unchanged-value short-
    # circuit) while leaving room for legitimate multi-step reactions.
    MAX_STATE_HANDLER_DEPTH = 8

    def _load_single_script(self, script_id: str, script_path: Path) -> int:
        """Import a single script file and register its handlers/timers."""
        event_handlers, state_handlers, module = self._import_script(
            script_id, script_path
        )
        return self._register_script(script_id, module, event_handlers, state_handlers)

    def _import_script(
        self, script_id: str, script_path: Path
    ) -> tuple[list, list, types.ModuleType]:
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

        try:
            self._exec_with_timeout(script_id, code, module)
        except BaseException:
            # A failed/timed-out import must not leave its timers parked to be
            # materialized later.
            script_api.discard_pending_timers()
            raise

        event_handlers, state_handlers = script_api.drain_pending()
        self._check_event_handler_signatures(event_handlers)
        return event_handlers, state_handlers, module

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

    def _exec_with_timeout(
        self, script_id: str, code: Any, module: types.ModuleType
    ) -> None:
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
        """
        box: dict[str, BaseException] = {}
        done = threading.Event()

        def _runner() -> None:
            try:
                exec(code, module.__dict__)
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
        script_id: str,
        module: types.ModuleType,
        event_handlers: list,
        state_handlers: list,
    ) -> int:
        """Phase 2 of loading: publish the module and register its handlers and
        any timers it created at top level. Tracks everything under
        ``script_id`` so the script can later be torn down on its own."""
        sys.modules[module.__name__] = module
        self._loaded_modules[script_id] = module.__name__
        event_ids = self._event_handler_ids.setdefault(script_id, [])
        state_ids = self._state_sub_ids.setdefault(script_id, [])
        names = self._handler_names.setdefault(script_id, set())
        for _pattern, handler in (*event_handlers, *state_handlers):
            handler_name = getattr(handler, "__name__", "")
            if handler_name:
                names.add(handler_name)
        count = 0

        for pattern, handler in event_handlers:
            wrapped = self._wrap_event_handler(handler, script_id)
            event_ids.append(self.events.on(pattern, wrapped))
            count += 1

        for pattern, handler in state_handlers:
            wrapped = self._wrap_state_handler(handler, script_id)
            state_ids.append(self.state.subscribe(pattern, wrapped))
            count += 1

        # Materialize any timers the script registered at top level. They were
        # parked during the off-loop exec; create them on the loop now and
        # attribute them to this script so a per-script reload can cancel them.
        script_api.materialize_pending_timers(script_id)

        return count

    def unload_all(self) -> None:
        """Unregister every script's handlers, timers, and modules."""
        count = 0
        for ids in self._event_handler_ids.values():
            for hid in ids:
                self.events.off(hid)
                count += 1
        for ids in self._state_sub_ids.values():
            for sid in ids:
                self.state.unsubscribe(sid)
                count += 1

        # Remove script modules from sys.modules
        for module_name in self._loaded_modules.values():
            sys.modules.pop(module_name, None)

        self._event_handler_ids.clear()
        self._state_sub_ids.clear()
        self._loaded_modules.clear()
        self._handler_names.clear()

        # Cancel all dynamic timers
        timer_count = script_api.cancel_all_timers()
        if timer_count:
            log.info(f"Cancelled {timer_count} active timer(s)")

        if count:
            log.info(f"Unloaded {count} handler(s)")

    def unload_script(self, script_id: str) -> int:
        """Unregister a single script's handlers, subscriptions, timers, and
        module — leaving every other script untouched. Returns the handler
        count removed."""
        count = 0
        for hid in self._event_handler_ids.pop(script_id, []):
            self.events.off(hid)
            count += 1
        for sid in self._state_sub_ids.pop(script_id, []):
            self.state.unsubscribe(sid)
            count += 1

        module_name = self._loaded_modules.pop(script_id, None)
        if module_name:
            sys.modules.pop(module_name, None)
        self._handler_names.pop(script_id, None)

        timer_count = script_api.cancel_script_timers(script_id)
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
        results: list[dict[str, Any]] = []
        for script_id, module_name in self._loaded_modules.items():
            module = sys.modules.get(module_name)
            if not module:
                continue
            handlers = self._handler_names.get(script_id, set())
            for name, obj in inspect.getmembers(module, inspect.isfunction):
                # Skip private, dunder, and imported stdlib functions
                if name.startswith("_"):
                    continue
                if getattr(obj, "__module__", "") != module_name:
                    continue
                if name in handlers:
                    continue
                results.append({
                    "script": script_id,
                    "function": name,
                    "doc": (inspect.getdoc(obj) or "")[:200],
                    **describe_parameters(obj),
                })
        return results

    def find_callable(self, name: str, script: str = "") -> list[tuple[str, Callable]]:
        """Every loaded function of this name, as (script_id, function).

        More than one is possible and is not an error here: two scripts may
        each define ``set_lights``. Which one a control meant is the caller's
        problem, and ``script`` narrows it when the control recorded one.
        """
        found: list[tuple[str, Callable]] = []
        for script_id, module_name in self._loaded_modules.items():
            if script and script_id != script:
                continue
            module = sys.modules.get(module_name)
            if not module:
                continue
            if name in self._handler_names.get(script_id, set()):
                continue
            fn = getattr(module, name, None)
            if (
                inspect.isfunction(fn)
                and not name.startswith("_")
                and getattr(fn, "__module__", "") == module_name
            ):
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
        BEFORE anything runs, so a mistyped name never half-runs a room. What
        happens once the function is running gets the handler treatment as
        well: logged, and a ``script.error`` event so the IDE's script surface
        shows it beside every other script failure.

        Timeout and inline-sync behaviour deliberately match
        ``_wrap_event_handler``: an emit already awaits its handlers, so
        nothing here waits on a press for longer than the event path did.
        """
        params = params or {}
        matches = self.find_callable(name, script)
        if not matches:
            where = f" in script '{script}'" if script else ""
            raise ScriptCallError(
                f"No script function named '{name}'{where}. Check the function "
                f"is defined in an enabled script and is not an event handler."
            )
        if len(matches) > 1:
            scripts = ", ".join(sorted(script_id for script_id, _fn in matches))
            raise ScriptCallError(
                f"More than one script defines '{name}' ({scripts}). Rename one "
                f"of them so this control can only mean one function."
            )

        script_id, fn = matches[0]
        try:
            inspect.signature(fn).bind(**params)
        except TypeError as exc:
            wanted = describe_parameters(fn)["params"]
            takes = ", ".join(str(entry["name"]) for entry in wanted) or "no arguments"
            given = ", ".join(sorted(params)) or "none"
            raise ScriptCallError(
                f"'{name}' cannot be called with those parameters: {exc}. "
                f"It takes {takes}; this control passed {given}."
            ) from exc

        with script_api.current_script_context(script_id):
            try:
                result = fn(**params)
                if asyncio.iscoroutine(result):
                    await asyncio.wait_for(result, timeout=self.HANDLER_TIMEOUT)
                # A plain `def` has already run inline by this point, for the
                # same reason a sync handler does: the state/devices/events
                # proxies must run on the loop thread.
            except asyncio.TimeoutError as exc:
                msg = (
                    f"Script '{script_id}' function '{name}' timed out after "
                    f"{self.HANDLER_TIMEOUT}s"
                )
                log.error(msg)
                await self._emit_script_error(script_id, name, f"script.call.{name}", msg, "")
                raise ScriptCallError(msg) from exc
            except Exception as exc:  # Catch-all: isolates user script errors from engine
                log.exception(f"Error in script '{script_id}' function '{name}'")
                await self._emit_script_error(
                    script_id, name, f"script.call.{name}", str(exc),
                    traceback.format_exc(),
                )
                raise ScriptCallError(f"'{name}' failed: {exc}") from exc

    async def _emit_script_error(
        self, script_id: str, handler: str, event: str, error: str, tb: str,
    ) -> None:
        """Report a script failure on the bus, and never fail doing it."""
        try:
            await self.events.emit("script.error", {
                "script_id": script_id,
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
        was_loaded = script_id in self._loaded_modules

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
            event_handlers, state_handlers, module = self._import_script(
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
        self.unload_script(script_id)
        count = self._register_script(script_id, module, event_handlers, state_handlers)
        log.info(f"Reloaded script '{script_id}' — {count} handler(s)")
        return {"status": "reloaded", "handlers": count}

    def _wrap_event_handler(
        self, handler: Callable, script_id: str
    ) -> Callable:
        """Wrap an event handler with error protection.

        Handlers take a single ``Event`` argument. Anything that cannot
        accept one was already refused at import time by
        ``_check_event_handler_signatures``.
        """
        events_ref = self.events

        async def wrapped(event: str, payload: dict[str, Any]) -> None:
            with script_api.current_script_context(script_id):
                try:
                    from openavc.core.script_api import Event
                    result = handler(Event(event, payload))
                    if asyncio.iscoroutine(result):
                        await asyncio.wait_for(result, timeout=self.HANDLER_TIMEOUT)
                    # A synchronous handler has, by this point, already run to
                    # completion inline on the event loop. It is NOT offloaded
                    # to a worker thread: the state/devices/events proxies it
                    # calls must run on the loop thread (scheduling state
                    # events, async listeners, device I/O), so threading it
                    # would silently break that propagation. The cost is that a
                    # blocking sync handler stalls the loop — keep sync handlers
                    # quick and use an async handler with `await` for slow work.
                except asyncio.TimeoutError:
                    handler_name = getattr(handler, "__name__", "anonymous")
                    msg = (
                        f"Script '{script_id}' handler '{handler_name}' timed out "
                        f"after {self.HANDLER_TIMEOUT}s for event '{event}'"
                    )
                    log.error(msg)
                    try:
                        await events_ref.emit("script.error", {
                            "script_id": script_id,
                            "handler": handler_name,
                            "event": event,
                            "error": msg,
                            "traceback": "",
                        })
                    except Exception:
                        pass
                except Exception as exc:  # Catch-all: isolates user script errors from engine
                    handler_name = getattr(handler, "__name__", "anonymous")
                    log.exception(
                        f"Error in script '{script_id}' event handler "
                        f"for '{event}'"
                    )
                    try:
                        await events_ref.emit("script.error", {
                            "script_id": script_id,
                            "handler": handler_name,
                            "event": event,
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        })
                    except Exception:  # Catch-all: error event emission must not raise
                        pass

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped

    def _wrap_state_handler(
        self, handler: Callable, script_id: str
    ) -> Callable:
        """Wrap a state-change handler with error protection.

        StateStore listeners receive (key, old_value, new_value, source).
        User handlers receive (key, old_value, new_value) — source is omitted
        for simplicity.
        """

        def wrapped(key: str, old_value: Any, new_value: Any, source: str) -> None:
            try:
                with script_api.current_script_context(script_id):
                    result = handler(key, old_value, new_value)
            except Exception as exc:  # Catch-all: isolates a sync handler's error
                log.exception(
                    f"Error in script '{script_id}' state handler for '{key}'"
                )
                self._schedule_state_error(
                    script_id, handler, key, str(exc), traceback.format_exc()
                )
                return

            if not asyncio.iscoroutine(result):
                return

            # Async handler. Bound the state-change cascade so a toggling value
            # (which evades StateStore's unchanged-value short-circuit) can't
            # spawn fire-and-forget tasks without limit (H-068).
            depth = _state_handler_depth.get()
            if depth >= self.MAX_STATE_HANDLER_DEPTH:
                result.close()  # never awaited — avoid the "coroutine" warning
                handler_name = getattr(handler, "__name__", "anonymous")
                msg = (
                    f"Script '{script_id}' state handler '{handler_name}' dropped "
                    f"for '{key}' — max state-change cascade depth "
                    f"({self.MAX_STATE_HANDLER_DEPTH}) reached. Possible "
                    f"recursive state-change loop."
                )
                log.warning(msg)
                self._schedule_state_error(script_id, handler, key, msg, "")
                return

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                result.close()  # no loop (sync test) — nothing to run it on
                return
            loop.create_task(
                self._run_state_coro(result, depth + 1, script_id, handler, key)
            )

        wrapped.__name__ = getattr(handler, "__name__", "anonymous")
        wrapped.__qualname__ = f"{script_id}.{wrapped.__name__}"
        return wrapped

    async def _run_state_coro(
        self,
        coro: Any,
        depth: int,
        script_id: str,
        handler: Callable,
        key: str,
    ) -> None:
        """Run an async @on_state_change body under a timeout, surfacing errors.

        ``depth`` is published in a contextvar so it propagates through the
        fire-and-forget task hops a state-change cascade creates — that's what
        lets ``MAX_STATE_HANDLER_DEPTH`` cap the whole chain and not just one
        synchronous call stack (H-068). A timeout or exception in the coroutine
        body is re-emitted as ``script.error`` (M-118), honouring the documented
        guarantee that handler errors surface (the previous fire-and-forget
        ``create_task`` swallowed them and applied no timeout).
        """
        depth_token = _state_handler_depth.set(depth)
        handler_name = getattr(handler, "__name__", "anonymous")
        try:
            with script_api.current_script_context(script_id):
                await asyncio.wait_for(coro, timeout=self.HANDLER_TIMEOUT)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            msg = (
                f"Script '{script_id}' state handler '{handler_name}' timed out "
                f"after {self.HANDLER_TIMEOUT}s for '{key}'"
            )
            log.error(msg)
            await self._emit_state_error_async(script_id, handler_name, key, msg, "")
        except Exception as exc:  # Catch-all: isolates user script errors
            log.exception(
                f"Error in script '{script_id}' state handler for '{key}'"
            )
            await self._emit_state_error_async(
                script_id, handler_name, key, str(exc), traceback.format_exc()
            )
        finally:
            _state_handler_depth.reset(depth_token)

    def _schedule_state_error(
        self, script_id: str, handler: Callable, key: str, error: str, tb: str
    ) -> None:
        """Schedule a state-handler ``script.error`` emit from a sync context."""
        handler_name = getattr(handler, "__name__", "anonymous")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync test) — nothing to emit on
        loop.create_task(
            self._emit_state_error_async(script_id, handler_name, key, error, tb)
        )

    async def _emit_state_error_async(
        self, script_id: str, handler_name: str, key: str, error: str, tb: str
    ) -> None:
        """Emit ``script.error`` for a state handler; never raises."""
        try:
            await self.events.emit("script.error", {
                "script_id": script_id,
                "handler": handler_name,
                "event": f"state_change:{key}",
                "error": error,
                "traceback": tb,
            })
        except Exception:  # Catch-all: error event emission must not raise
            pass
