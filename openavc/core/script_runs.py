"""
How a piece of a script's code is run: the time limit, the worker thread a
plain ``def`` runs on, and the hand-off of its calls back to the event loop.

A handler, timer callback or control-called function written as ``async def``
runs on the event loop, bounded by the handler limit. One written as a plain
``def`` runs here instead, so a blocking line in it (``time.sleep``, a
``requests.get`` against a host that is down, a long loop) holds up only its
own script rather than every device command, panel and Programmer at once:

- **One worker thread per script, one run at a time, in the order they were
  asked for.** Two presses run in the order they were pressed, a state handler
  sees the changes in the order they happened, and a slow run delays only the
  script it belongs to.
- **Every script-API call made off the loop runs on the loop**, and the calling
  thread waits for it (:func:`call_on_loop`). The state store, the bus, the
  device manager and the timers all assume the loop's thread: a ``state.set``
  on a worker would notify its listeners on the worker and drop the
  ``state.changed`` events, because there is no running loop there to schedule
  them on. Waiting is what keeps a run's own writes visible to its next line.
- **A run past the limit is abandoned, not stopped.** Python cannot kill a
  thread. The run is reported as timed out, the script's next run gets a fresh
  worker, and the old thread is put on a leash: its next call into the platform
  raises :class:`ScriptRunAbandoned` and unwinds it. One that never calls in
  again runs until it finishes. :data:`MAX_STUCK_RUNS` of those at once and the
  script's plain code is refused until it is reloaded, because each one may be
  holding a core.

The one thread that never hands a call to the loop is a script's top-level
load: the loop is blocked waiting for it (``ScriptEngine._exec_with_timeout``),
so a hand-off from there would wait out the whole load timeout.

Pure stdlib plus the logger, so ``script_api`` and ``script_engine`` can both
import it without a cycle.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import inspect
import queue
import threading
from typing import Any, Callable

from openavc.utils.logger import get_logger

log = get_logger(__name__)


class HandlerTimedOut(Exception):
    """A script's code ran past ``ScriptEngine.HANDLER_TIMEOUT``.

    Its own type because the limit is not the only ``TimeoutError`` a handler
    meets: a device command it sent that got no answer, or one it raised
    itself, is the handler's own failure and is recorded with its text and
    traceback like any other. Only the engine's limit reads "timed out after".
    """


class ScriptRunAbandoned(BaseException):
    """Raised inside a run's thread after the limit gave up on it, to unwind it.

    A ``BaseException`` for the reason ``ScriptLoadAbandoned`` is one: a handler
    that wraps its work in ``except Exception`` must not swallow the stop.
    """


class ScriptRunRefused(Exception):
    """A script's plain code was not run because earlier runs are still stuck."""


#: How many runs of one script may be left going past the limit before its
#: plain code is refused. Each may be a loop holding a core, and every one
#: competes with the event loop for the interpreter.
MAX_STUCK_RUNS = 3


async def run_within_handler_limit(awaitable: Any, limit: float) -> None:
    """Await a script's coroutine under the handler limit, raising
    :class:`HandlerTimedOut` only when that limit is what stopped it."""
    try:
        async with asyncio.timeout(limit) as scope:
            await awaitable
    except TimeoutError:
        if scope.expired():
            raise HandlerTimedOut() from None
        raise
    # The limit cancelled the body and something inside it caught the
    # cancellation and returned, so asyncio raised nothing. An awaited macro
    # does exactly that (it ends as "cancelled"), and without this a macro cut
    # off halfway left no error against the script that ran it.
    if scope.expired():
        raise HandlerTimedOut()


async def run_script_code(
    script_id: str | None,
    fn: Callable,
    args: tuple = (),
    kwargs: dict[str, Any] | None = None,
    limit: float = 30,
) -> None:
    """Run one handler, timer callback or called function under ``limit``.

    An ``async def`` runs on the loop. Anything else runs on the script's
    worker thread; if it hands back a coroutine (``lambda: devices.send(...)``
    as a timer callback does), that is awaited on the loop in the time left.
    Raises :class:`HandlerTimedOut` when the limit stops it.
    """
    kwargs = kwargs or {}
    loop = asyncio.get_running_loop()
    # With no live loop named yet (the engine was installed from sync code),
    # the one running a script's code is where its other threads hand calls
    # to: an async handler's asyncio.to_thread, a thread the script started.
    # A live one is never replaced, so a handler run by some other loop (a
    # plugin's own, writing state directly) cannot take the hand-off with it.
    bound = _loop
    if bound is None or bound.is_closed() or not bound.is_running():
        bind_loop(loop)
    if inspect.iscoroutinefunction(fn):
        await run_within_handler_limit(fn(*args, **kwargs), limit)
        return
    deadline = loop.time() + limit
    result = await _run_plain(script_id, fn, args, kwargs, limit)
    if asyncio.iscoroutine(result):
        await run_within_handler_limit(result, max(0.0, deadline - loop.time()))


# --- The hand-off back to the loop ---

_loop: asyncio.AbstractEventLoop | None = None
_thread_role = threading.local()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Name the loop the script API hands off-loop calls to."""
    global _loop
    _loop = loop


def mark_load_thread() -> None:
    """Mark the calling thread as a script's top-level load (never hands off)."""
    _thread_role.load = True


def hand_off_loop() -> asyncio.AbstractEventLoop | None:
    """The loop a script-API call on this thread must run on, or None to run
    it right here: on the loop itself, on a load thread, or with no loop."""
    loop = _loop
    if loop is None or loop.is_closed() or not loop.is_running():
        return None
    try:
        if asyncio.get_running_loop() is loop:
            return None
    except RuntimeError:
        pass
    if getattr(_thread_role, "load", False):
        return None
    return loop


def call_on_loop(
    loop: asyncio.AbstractEventLoop,
    fn: Callable,
    args: tuple,
    kwargs: dict[str, Any],
) -> Any:
    """Run ``fn`` on ``loop`` in this thread's context and wait for its result.

    The context goes with it, so the loop side sees the script id a timer is
    attributed to, the state-change cascade depth and the macro call chain
    exactly as the run had them.
    """
    done: concurrent.futures.Future = concurrent.futures.Future()

    def run() -> None:
        if not done.set_running_or_notify_cancel():
            return
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # Catch-all: re-raised on the calling thread
            done.set_exception(exc)
        except BaseException as exc:
            done.set_exception(exc)
            raise
        else:
            done.set_result(result)

    loop.call_soon_threadsafe(run, context=contextvars.copy_context())
    return done.result()


# --- The leash on an abandoned run ---

# Keyed by the Thread itself, never by ident (CPython reuses idents). Written on
# the loop thread, read on the workers; single dict operations.
_abandoned_runs: dict[threading.Thread, str] = {}


def stop_if_abandoned() -> None:
    """Unwind the calling thread if the limit gave up on its run."""
    if not _abandoned_runs:
        return
    script_id = _abandoned_runs.get(threading.current_thread())
    if script_id is None:
        return
    raise ScriptRunAbandoned(
        f"Script '{script_id}' ran past its time limit and is being stopped "
        f"at its next platform call"
    )


# --- One worker thread per script ---


class _Worker:
    """A daemon thread running one script's plain code, a job at a time.

    Daemon because a stuck job must not hold up server shutdown: a non-daemon
    thread is joined at exit, so one runaway would hang it forever.
    """

    def __init__(self, script_id: str | None):
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self.thread = threading.Thread(
            target=self._serve, name=f"script-{script_id or 'timers'}", daemon=True,
        )
        self.thread.start()

    def submit(
        self, ctx: contextvars.Context, fn: Callable, args: tuple, kwargs: dict,
    ) -> concurrent.futures.Future:
        done: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((done, ctx, fn, args, kwargs))
        return done

    def stop(self) -> None:
        """Exit once the current job, if any, is finished."""
        self._jobs.put(None)

    def _serve(self) -> None:
        me = threading.current_thread()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    return
                done, ctx, fn, args, kwargs = job
                job = None
                if not done.set_running_or_notify_cancel():
                    continue
                try:
                    result = ctx.run(fn, *args, **kwargs)
                except BaseException as exc:  # handed to whoever awaits the run
                    done.set_exception(exc)
                else:
                    done.set_result(result)
                done = ctx = fn = args = kwargs = result = None
                if me in _abandoned_runs:
                    return
        finally:
            _abandoned_runs.pop(me, None)


class _Lane:
    """One script's queue of plain-code runs."""

    __slots__ = ("script_id", "loop", "lock", "worker", "closed", "stuck")

    def __init__(self, script_id: str | None, loop: asyncio.AbstractEventLoop):
        self.script_id = script_id
        self.loop = loop
        self.lock = asyncio.Lock()  # FIFO: runs start in the order they arrived
        self.worker: _Worker | None = None
        self.closed = False
        self.stuck: list[threading.Thread] = []


_lanes: dict[str | None, _Lane] = {}


def _consume(task: asyncio.Task) -> None:
    """Retrieve a run's outcome nobody waits for any more, so it is not logged."""
    if not task.cancelled():
        task.exception()


async def _run_plain(
    script_id: str | None, fn: Callable, args: tuple, kwargs: dict, limit: float,
) -> Any:
    loop = asyncio.get_running_loop()
    lane = _lanes.get(script_id)
    if lane is None or lane.loop is not loop:
        lane = _lanes[script_id] = _Lane(script_id, loop)
    # The run is its own task, and whoever asked for it awaits it shielded.
    # Cancelling the asker (a timer cancelled from inside its own callback,
    # the emit that fired a handler) must not cut the run short: inline, the
    # rest of a plain def always ran, so a callback that cancels its timer and
    # then writes state still has to write it.
    run = loop.create_task(_run_in_lane(lane, fn, args, kwargs, limit))
    run.add_done_callback(_consume)
    return await asyncio.shield(run)


async def _run_in_lane(
    lane: _Lane, fn: Callable, args: tuple, kwargs: dict, limit: float,
) -> Any:
    async with lane.lock:
        if lane.closed:
            return None  # the script was unloaded while this waited its turn
        lane.stuck = [t for t in lane.stuck if t.is_alive()]
        if len(lane.stuck) >= MAX_STUCK_RUNS:
            raise ScriptRunRefused(
                f"Not run: {len(lane.stuck)} earlier runs of this script are "
                f"still going after the {limit:g} s limit. Find the line that "
                f"blocks, fix it and reload the script, or restart the server."
            )
        worker = lane.worker
        if worker is None:
            worker = lane.worker = _Worker(lane.script_id)
        done = worker.submit(contextvars.copy_context(), fn, args, kwargs)
        try:
            async with asyncio.timeout(limit) as scope:
                return await asyncio.wrap_future(done)
        except TimeoutError:
            if not scope.expired():
                raise  # the code's own TimeoutError, not the limit
            _abandon(lane, worker)
            raise HandlerTimedOut() from None
        except asyncio.CancelledError:
            if not done.done():
                _abandon(lane, worker)
            raise


def _abandon(lane: _Lane, worker: _Worker) -> None:
    _abandoned_runs[worker.thread] = lane.script_id or ""
    lane.stuck.append(worker.thread)
    worker.stop()
    if lane.worker is worker:
        lane.worker = None
    log.warning(
        f"Script '{lane.script_id}': a run went past its time limit and was "
        f"left on thread {worker.thread.name}; it stops at its next platform call"
    )


def close_script(script_id: str | None) -> None:
    """The script is being unloaded: run nothing more of its plain code.

    A run already going finishes under its own limit, the way an async
    handler's task does; runs still waiting their turn are dropped.
    """
    lane = _lanes.pop(script_id, None)
    if lane is None:
        return
    lane.closed = True
    if lane.worker is not None:
        lane.worker.stop()
        lane.worker = None


def close_all() -> None:
    """Close every script's lane (a whole-project unload)."""
    for script_id in list(_lanes):
        close_script(script_id)
