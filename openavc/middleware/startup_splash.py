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
poll. After a failed start, ``held`` says it stays on the splash page until
someone presses Start again, and ``retry_in`` how many seconds until the
process ends for its service manager to start it again (openavc/start_retry.py
decides which; appliance hardware's device supervisor reads both). ``/api/health`` answers 503 with the phase whenever the phase is not
ready (``health_answer``), so a monitor, a container's health check and the
tray hear of a start, a failed start and a stop alike. A page on this
server's own network may read ``/api/startup-status`` from another address
(``readable_from``): the Programmer's restart dialog waits on it at the port
or address a restart moves it to. A page from the internet may not.

The HTTP listener comes up before the engine (``openavc.main`` starts the
engine in the background so a browser gets an answer at once). Until the
start has finished, the rest of the API answers 503 (the engine has no
project yet), and every other path gets the splash page itself, which polls
``/api/startup-status`` and reloads once the engine is ready or shows the
error that stopped it, then keeps asking, so a start the service manager makes
afterwards reaches a screen already showing the failure. Its Start again
button posts to ``START_AGAIN_PATH``, let through before the start has
finished (it answers only a failed start), as a device's push is (below). Once it has finished, the
API is served on through the stop: a save made in a restart's wait still
reaches its route. An app whose lifespan made no record passes everything
through.

Every other way in asks the same record. A device's push
(``http_listener.PUSH_PREFIX``) is answered by the push registry in every
phase, as the push-only and redirect listeners answer it: a device subscribes
while the start is still running, and its first event is not refused. A
WebSocket (a client's ``/ws``, an ISC peer's ``/isc/ws``) is accepted and
then closed with ``NOT_READY_CLOSE`` and the phase until the server is ready
(``turn_away_until_ready``): this middleware sees HTTP only. The engine's
``stopping`` is the record's (``Engine.attach_server``), so the restart door
refuses a restart once a stop has begun, however it began.
"""

from __future__ import annotations

import ipaddress
import math
import socket
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

from openavc.core.start_stages import StartProblem, StartRun
from openavc.transport.http_listener import PUSH_PREFIX
from openavc.utils.logger import get_logger
from openavc.version import __version__

log = get_logger(__name__)

# Where the process is in its life, the words every reader is answered with.
STARTING = "starting"
READY = "ready"
FAILED = "failed"
STOPPING = "stopping"
PHASES: tuple[str, ...] = (STARTING, READY, FAILED, STOPPING)

STATUS_PATH = "/api/startup-status"

# Start again on the splash page of a failed start (api/routes/system.py),
# and the header the page sends with it: a page on another site cannot send
# one, so only OpenAVC's own page can press it.
START_AGAIN_PATH = "/api/start-again"
START_AGAIN_HEADER = "X-OpenAVC-Start-Again"

# What a WebSocket (a client's, an ISC peer's) is closed with, once accepted,
# while the server is not ready for it: the socket's own 503. The close's
# reason is the phase, so a client can tell a start, which ends in seconds,
# from a failed start or a stop.
NOT_READY_CLOSE = 4503

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
    of the callers. ``stopping``: a stop was heard before the record was
    made (uvicorn listens for one before the lifespan runs), said at once."""

    def __init__(self, report: Report | None = None, *, stopping: bool = False) -> None:
        self.phase = STARTING
        self.error: str | None = None
        self.problems: tuple[StartProblem, ...] = ()
        self.version = __version__
        # The start has finished: the API is served from here, on through the
        # stop. Not the phase, which a stop moves on from ready.
        self._finished = False
        # After a failed start: whether it stays on the splash page, and when
        # (time.monotonic()) the process ends to be started again.
        self._held = False
        self._retry_at: float | None = None
        self._report = report
        self._tell()
        if stopping:
            self.stopping()

    @property
    def ready(self) -> bool:
        return self.phase == READY

    @property
    def is_stopping(self) -> bool:
        """The server is on its way down (``stopping`` says it)."""
        return self.phase == STOPPING

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

    def hold(self) -> None:
        """The failed start stays on the splash page until Start again."""
        self._held, self._retry_at = True, None

    def retrying(self, seconds: float) -> None:
        """The process ends in ``seconds`` for its service manager to start
        it again."""
        self._held, self._retry_at = False, time.monotonic() + seconds

    def _retry_in(self) -> int | None:
        if self._retry_at is None or self.phase != FAILED:
            return None
        # To the millisecond first: a clock read twice in the same tick
        # leaves a float's rounding, which would count a whole second more.
        return max(0, math.ceil(round(self._retry_at - time.monotonic(), 3)))

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
            "held": self._held and self.phase == FAILED,
            "retry_in": self._retry_in(),
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


def not_ready_phase(app) -> str | None:
    """The phase a new connection to ``app`` is turned away for (starting,
    failed or stopping), or None: ready, or an app whose lifespan made no
    record."""
    record = _record(app)
    if record is None or record.ready:
        return None
    return record.phase


async def turn_away_until_ready(ws, subprotocol: str | None = None) -> bool:
    """When ``ws``'s app is not ready for it, accept it (with ``subprotocol``,
    the one the client offered for its credential) and close it with
    ``NOT_READY_CLOSE`` and the phase; True when it did. Accepted first, so a
    browser reads the code: a close before the handshake reaches it as 1006,
    which it cannot tell from a dropped network."""
    phase = not_ready_phase(ws.app)
    if phase is None:
        return False
    await ws.accept(subprotocol=subprotocol)
    await ws.close(code=NOT_READY_CLOSE, reason=phase)
    return True


def startup_status(app) -> dict[str, Any]:
    """``/api/startup-status``'s answer: the record, or ready with nothing
    to say on an app whose lifespan made none."""
    record = _record(app)
    if record is None:
        return {
            "phase": READY, "ready": True, "version": __version__,
            "error": None, "problems": [], "held": False, "retry_in": None,
        }
    return record.answer()


def readable_from(origin: str, host: str) -> bool:
    """Whether a page at ``origin`` may read the status from another address,
    for a request sent to ``host`` (its Host header): a page on this server's
    own network, which can reach the server itself anyway. That is the name
    the request was sent to (another port or scheme of it), this computer's
    own name, or an address that is not on the internet (loopback, private,
    link-local, shared). A page from the internet may not."""
    name = _page_host(origin)
    if name is None:
        return False
    if name == _host_name(host) or name in _own_names():
        return True
    try:
        return not ipaddress.ip_address(name).is_global
    except ValueError:
        return False


def _page_host(origin: str) -> str | None:
    try:
        url = urlsplit(origin)
        name = url.hostname
    except ValueError:
        return None
    if url.scheme not in ("http", "https") or not name:
        return None
    return name.lower()


def _host_name(host: str) -> str:
    try:
        return (urlsplit(f"//{host}").hostname or "").lower()
    except ValueError:
        return ""


def _own_names() -> set[str]:
    name = socket.gethostname().lower()
    short = name.split(".")[0]
    return {"localhost", name, short, f"{short}.local"} - {"", ".local"}


def _open_to_this_network(request: Request, response):
    """``response`` (the status) readable by the page that asked, when
    ``readable_from`` says it may, with no credentials offered: the status is
    open, and the app's CORS layer adds Allow-Credentials to every answer."""
    origin = request.headers.get("origin")
    if origin and readable_from(origin, request.headers.get("host", "")):
        response.headers["Access-Control-Allow-Origin"] = origin
        if "origin" not in response.headers.get("vary", "").lower():
            response.headers.add_vary_header("Origin")
        if "access-control-allow-credentials" in response.headers:
            del response.headers["access-control-allow-credentials"]
    return response


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
  .failed .message { opacity: 1; font-weight: 600; }
  .error {
    color: #F44336; margin-top: 1rem;
    max-width: 500px; word-break: break-word; white-space: pre-line;
  }
  .next { margin-top: 1rem; opacity: 0.6; }
  button {
    margin-top: 1.5rem; padding: 0.75rem 1.75rem;
    font: inherit; font-weight: 600; color: #1a1a2e;
    background: #8AB493; border: 0; border-radius: 6px; cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<div class="container" id="container">
  <div class="logo">Open<span>AVC</span></div>
  <div class="spinner" id="spinner"></div>
  <div class="message" id="message">Starting up...</div>
  <div class="error" id="error" hidden></div>
  <div class="next" id="next" hidden></div>
  <button type="button" id="again" hidden>Start again</button>
</div>
<script>
(function() {
  var box = document.getElementById('container');
  var msg = document.getElementById('message');
  var spinner = document.getElementById('spinner');
  var error = document.getElementById('error');
  var next = document.getElementById('next');
  var again = document.getElementById('again');
  var failedBefore = false;

  function starting(text) {
    box.className = 'container';
    spinner.hidden = false;
    msg.textContent = text;
    error.hidden = true;
    next.hidden = true;
    again.hidden = true;
  }

  function failed(d) {
    failedBefore = true;
    box.className = 'container failed';
    spinner.hidden = true;
    msg.textContent = 'OpenAVC could not start';
    error.textContent = d.error;
    error.hidden = false;
    if (d.held) {
      next.textContent = 'Fix the problem above, then press Start again.';
      next.hidden = false;
      again.hidden = false;
    } else if (d.retry_in !== null && d.retry_in !== undefined) {
      next.textContent = 'Trying again in ' + d.retry_in + (d.retry_in === 1 ? ' second.' : ' seconds.');
      next.hidden = false;
      again.hidden = true;
    } else {
      next.hidden = true;
      again.hidden = true;
    }
  }

  // Keeps asking after a failure too: the next start reaches this screen.
  function check() {
    fetch('/api/startup-status', { cache: 'no-store' }).then(function(r) {
      return r.json();
    }).then(function(d) {
      if (d.ready) {
        location.reload();
        return;
      }
      if (d.phase === 'failed' && d.error) {
        failed(d);
      } else {
        starting(failedBefore ? 'Starting again...' : 'Starting up...');
      }
      setTimeout(check, 1000);
    }).catch(function() {
      if (failedBefore) {
        starting('Starting again...');
      }
      setTimeout(check, 1000);
    });
  }

  again.addEventListener('click', function() {
    again.disabled = true;
    fetch('/api/start-again', {
      method: 'POST', cache: 'no-store', headers: { 'X-OpenAVC-Start-Again': '1' }
    }).then(function(r) {
      return r.json().then(function(d) {
        if (r.ok) {
          starting('Starting again...');
        } else {
          next.textContent = d.detail || 'Start again did not work. Restart the OpenAVC service.';
          next.hidden = false;
        }
      });
    }).catch(function() {
      starting('Starting again...');
    }).then(function() {
      again.disabled = false;
    });
  });

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
        if path == STATUS_PATH:
            # From here until the start has finished, then from its route.
            answer = await call_next(request) if record.serving else JSONResponse(record.answer())
            return _open_to_this_network(request, answer)
        if record.serving:
            return await call_next(request)
        return await _before_the_start_finishes(request, call_next)


async def _before_the_start_finishes(request: Request, call_next):
    """The answer to anything else asked before the start has finished."""
    path = request.url.path
    # Start again, reached before the start has finished: it answers only a
    # failed start.
    if path == START_AGAIN_PATH and request.method == "POST":
        return await call_next(request)
    # A device's push, answered by the push registry whatever the phase.
    if path.startswith(PUSH_PREFIX):
        return await call_next(request)
    # Block API routes during startup (engine.project is None, etc.)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Server is starting up"}, status_code=503)
    # Serve splash page for all UI routes
    return HTMLResponse(_STARTUP_PAGE, status_code=503)
