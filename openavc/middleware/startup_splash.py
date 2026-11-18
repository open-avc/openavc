"""THE run-state record: where this process is in its life, and what the
server answers while it is not ready.

The record (``RunState``, at ``app.state.run_state``) holds the phase
(``PHASES``: starting, ready, failed to start, stopping), the sentence for the
stage that stopped a start, each whole stage the start went on past (a
``StartProblem``: the driver library, the plugin system, the script engine,
worded once in core/start_stages.py), and the version that started.
``openavc.main``'s lifespan makes it and writes each phase to the status
folder the tray and the menu bar read (status_folder.write_run_state); its
start says ready or failed, and stopping is said by the first of the stop
signal (listen_sockets.hears_the_stop), an accepted restart (openavc/restart.py)
and the lifespan's end. Stopping is final: a start that ends after it began
leaves it said. A device, a plugin or a script that did not start says so on
its own surface instead.

``/api/startup-status`` answers it (``startup_status``): from here while the
start runs, from the route once it has finished, with the same fields.
``ready`` is the phase being ready, for the IDE's wait and the splash page's
poll. ``/api/health`` answers 503 with the phase whenever the phase is not
ready (``health_answer``), so a monitor, a container's health check and the
tray hear of a start, a failed start and a stop alike.

The HTTP listener comes up before the engine (``openavc.main`` starts the
engine in the background so a browser gets an answer at once). Until the
start has finished, the rest of the API answers 503 (the engine has no
project yet), and every other path gets the splash page itself, which polls
``/api/startup-status`` and reloads once the engine is ready or shows the
error that stopped it. Once it has finished, the API is served on through the
stop: a save made in a restart's wait still reaches its route. An app whose
lifespan made no record passes everything through.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from openavc.core.start_stages import StartProblem, StartRun
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger(__name__)

# Where the process is in its life, the words every reader is answered with.
STARTING = "starting"
READY = "ready"
FAILED = "failed"
STOPPING = "stopping"
PHASES: tuple[str, ...] = (STARTING, READY, FAILED, STOPPING)

# What the record tells at each phase it reaches: the phase, the version that
# started, and the start's error with a failed start (None otherwise).
Report = Callable[[str, str, "str | None"], None]


class RunState:
    """Where this process is in its life: ``phase`` (``PHASES``), ``error``
    for the stage that stopped the start, ``problems``, the whole stages it
    went on past, and ``version``, the one that started. Answered to anyone
    who asks, so it holds only sentences, never an exception's text from a
    stage the start went on past.

    ``report`` is told each phase as it is reached, this first one included
    (``openavc.main`` passes the status folder's writer); a report that
    raises is logged, never raised, since the stop signal's handler is one
    of the callers."""

    def __init__(self, report: Report | None = None) -> None:
        self.phase = STARTING
        self.error: str | None = None
        self.problems: tuple[StartProblem, ...] = ()
        self.version = __version__
        # The start has finished: the API is served from here, on through the
        # stop. Not the phase, which a stop moves on from ready.
        self._finished = False
        self._report = report
        self._tell()

    @property
    def ready(self) -> bool:
        return self.phase == READY

    @property
    def serving(self) -> bool:
        """Whether the API is served: from the end of a start that finished,
        on through the stop."""
        return self._finished

    def started(self, run: StartRun) -> None:
        """The start finished, past what it went on past."""
        self.problems = tuple(run.problems)
        self._finished = True
        self._reach(READY)

    def stopped(self, run: StartRun, exc: BaseException) -> None:
        """The start stopped at ``run``'s stage with ``exc``."""
        self.problems = tuple(run.problems)
        self.error = run.stopped_by(exc)
        self._reach(FAILED)

    def stopping(self) -> None:
        """The server is on its way down; said once, whoever says it first."""
        self._reach(STOPPING)

    def _reach(self, phase: str) -> None:
        if self.phase in (phase, STOPPING):
            return
        self.phase = phase
        self._tell()

    def _tell(self) -> None:
        if self._report is None:
            return
        try:
            self._report(self.phase, self.version, self.error if self.phase == FAILED else None)
        except Exception:
            log.exception("Could not record that the server is %s", self.phase)

    def answer(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "ready": self.ready,
            "version": self.version,
            "error": self.error,
            "problems": [problem.answer() for problem in self.problems],
        }

    def health_answer(self) -> dict[str, Any]:
        """``/api/health``'s 503 body while the phase is not ready."""
        return {"status": self.phase, "version": self.version}


def _record(app) -> RunState | None:
    return getattr(app.state, "run_state", None)


def say_stopping(app) -> None:
    """``app``'s record says stopping, when its lifespan has made one."""
    record = _record(app)
    if record is not None:
        record.stopping()


def startup_status(app) -> dict[str, Any]:
    """``/api/startup-status``'s answer: the record, or ready with nothing
    to say on an app whose lifespan made none."""
    record = _record(app)
    if record is None:
        return {
            "phase": READY, "ready": True, "version": __version__,
            "error": None, "problems": [],
        }
    return record.answer()

_STARTUP_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>OpenAVC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e; color: #fff;
    font-family: 'Inter', system-ui, -apple-system, sans-serif;
    display: flex; align-items: center; justify-content: center;
    height: 100vh; overflow: hidden;
  }
  .container { text-align: center; }
  .logo {
    font-size: 2rem; font-weight: 700; letter-spacing: 0.02em;
    margin-bottom: 2rem; opacity: 0.95;
  }
  .logo span { color: #8AB493; }
  .spinner {
    width: 36px; height: 36px; margin: 0 auto 1.5rem;
    border: 3px solid rgba(255,255,255,0.1);
    border-top-color: #8AB493;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .message { font-size: 1rem; opacity: 0.6; }
  .error {
    color: #F44336; opacity: 1; margin-top: 1rem;
    max-width: 500px; word-break: break-word;
  }
</style>
</head>
<body>
<div class="container">
  <div class="logo">Open<span>AVC</span></div>
  <div class="spinner" id="spinner"></div>
  <div class="message" id="message">Starting up...</div>
</div>
<script>
(function() {
  var msg = document.getElementById('message');
  var spinner = document.getElementById('spinner');
  function check() {
    fetch('/api/startup-status').then(function(r) { return r.json(); }).then(function(d) {
      if (d.ready) {
        location.reload();
      } else if (d.error) {
        spinner.style.display = 'none';
        msg.textContent = 'Startup failed';
        var err = document.createElement('div');
        err.className = 'error';
        err.textContent = d.error;
        msg.parentNode.appendChild(err);
      } else {
        setTimeout(check, 500);
      }
    }).catch(function() {
      setTimeout(check, 1000);
    });
  }
  setTimeout(check, 500);
})();
</script>
</body>
</html>
"""


class StartupSplashMiddleware(BaseHTTPMiddleware):
    """Serves a loading page for UI routes while the engine is initializing,
    and ``/api/health``'s 503 whenever the server is not ready."""

    async def dispatch(self, request: Request, call_next):
        record = _record(request.app)
        if record is None:
            return await call_next(request)

        path = request.url.path
        if path == "/api/health" and not record.ready:
            return JSONResponse(record.health_answer(), status_code=503)
        if record.serving:
            return await call_next(request)

        # Startup status endpoint — always available
        if path == "/api/startup-status":
            return JSONResponse(record.answer())

        # Block API routes during startup (engine.project is None, etc.)
        if path.startswith("/api/"):
            return JSONResponse(
                {"detail": "Server is starting up"},
                status_code=503,
            )

        # Serve splash page for all UI routes
        return HTMLResponse(_STARTUP_PAGE, status_code=503)
