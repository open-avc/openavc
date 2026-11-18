"""THE run-state record, and what the server answers while the engine is
still starting.

The record (``RunState``, at ``app.state.run_state``) is how the start went:
whether it is done, the sentence for the stage that stopped it, and each
whole stage it went on past (a ``StartProblem``: the driver library, the
plugin system, the script engine), worded once in core/start_stages.py.
``openavc.main``'s lifespan makes it and its start writes it; a device, a
plugin or a script that did not start says so on its own surface instead.
``/api/startup-status`` answers it (``startup_status``): from here while the
start runs, from the route once it is ready, with the same fields.

The HTTP listener comes up before the engine (``openavc.main`` starts the
engine in the background so a browser gets an answer at once). Until the
record is ready, the rest of the API answers 503 (the engine has no project
yet), and every other path gets the splash page itself, which polls
``/api/startup-status`` and reloads once the engine is ready or shows the
error that stopped it. An app whose lifespan made no record passes
everything through.
"""

from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from openavc.core.start_stages import StartProblem, StartRun


class RunState:
    """How this process's start went: ``ready`` once it finished, ``error``
    for the stage that stopped it, and ``problems``, the whole stages it went
    on past. Answered to anyone who asks, so it holds only sentences, never
    an exception's text from a stage the start went on past."""

    def __init__(self) -> None:
        self.ready = False
        self.error: str | None = None
        self.problems: tuple[StartProblem, ...] = ()

    def started(self, run: StartRun) -> None:
        """The start finished, past what it went on past."""
        self.problems = tuple(run.problems)
        self.ready = True

    def stopped(self, run: StartRun, exc: BaseException) -> None:
        """The start stopped at ``run``'s stage with ``exc``."""
        self.problems = tuple(run.problems)
        self.error = run.stopped_by(exc)

    def answer(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "error": self.error,
            "problems": [problem.answer() for problem in self.problems],
        }


def _record(app) -> RunState | None:
    return getattr(app.state, "run_state", None)


def startup_status(app) -> dict[str, Any]:
    """``/api/startup-status``'s answer: the record, or ready with nothing
    to say on an app whose lifespan made none."""
    record = _record(app)
    if record is None:
        return {"ready": True, "error": None, "problems": []}
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
    """Serves a loading page for UI routes while the engine is initializing."""

    async def dispatch(self, request: Request, call_next):
        record = _record(request.app)
        if record is None or record.ready:
            return await call_next(request)

        path = request.url.path

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
