"""The sockets the web listeners serve on, bound for a listen plan.

``main()`` binds every socket a listener needs itself and hands them to one
``uvicorn.Server.serve(sockets=...)``, so one server, and one lifespan, answers
on every address of the choice (``openavc/listen.py``): ``0.0.0.0``, or an
adapter's address(es) plus ``127.0.0.1``. uvicorn's own ``host=`` takes one
address, and a second server for the same app would start the engine twice.

Binding first is also the pre-flight. A bind that succeeds is the listener, so
there is no test-bind-then-rebind window, and a failure is told apart here:

``not_available`` (``EADDRNOTAVAIL``, WinError 10049)
    This computer does not hold the address now: the adapter was unplugged or
    renewed onto another address. The address is dropped and the plan falls
    back (``ListenPlan.without``), never a port-in-use error and never a
    restart loop.
``in_use`` (``EADDRINUSE``, 10048)
    Another program, or another copy of OpenAVC, holds the port.
``forbidden`` (``EACCES``, 10013)
    Windows has reserved the port for other software (Hyper-V, WSL and
    Docker reserve ranges at boot, in a different place every boot), or a
    port below 1024 without the right to use it.

The options match what asyncio sets on its own listening sockets:
``SO_REUSEADDR`` on POSIX, so a port in TIME_WAIT from a server that just
exited is accepted while a live listener is still refused; never on Windows,
where the option lets a second socket take a port another one is using.

**Device updates on the web port** (Settings decision: HTTP push keeps working
whoever may open the panel). A device that posts to ``/api/push/`` reaches
this computer on the address that routes to it, or the control interface;
when the choice does not listen there, :class:`PushListeners` binds that one
address too and serves ``/api/push/`` alone on it, which already accepts only
the device's own address. Nothing else is answered there.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import socket
import time
from typing import Any

from openavc.utils.logger import get_logger

log = get_logger(__name__)

NOT_AVAILABLE = "not_available"
IN_USE = "in_use"
FORBIDDEN = "forbidden"
OTHER = "other"

_NOT_AVAILABLE_CODES = frozenset({errno.EADDRNOTAVAIL, 10049})
_IN_USE_CODES = frozenset({errno.EADDRINUSE, 10048})
_FORBIDDEN_CODES = frozenset({errno.EACCES, 10013})


def classify(err: OSError) -> str:
    """Which of the failures above ``err`` is."""
    codes = {err.errno, getattr(err, "winerror", None)}
    if codes & _NOT_AVAILABLE_CODES:
        return NOT_AVAILABLE
    if codes & _IN_USE_CODES:
        return IN_USE
    if codes & _FORBIDDEN_CODES:
        return FORBIDDEN
    return OTHER


class ListenBindError(Exception):
    """A listener could not bind; ``kind`` is one of the classifications."""

    def __init__(self, kind: str, host: str, port: int, err: OSError) -> None:
        super().__init__(f"{host}:{port}: {err}")
        self.kind = kind
        self.host = host
        self.port = port
        self.err = err

    @property
    def error_type(self) -> str:
        """The startup-error code the tray and the menu bar report."""
        return {
            IN_USE: "port_in_use",
            FORBIDDEN: "port_reserved" if os.name == "nt" else "port_not_permitted",
        }.get(self.kind, "listen_error")

    def message(self, label: str, env_var: str) -> str:
        """The sentence for the console, the startup-error file and the tray."""
        fix = (
            "To fix this, change the port in Settings > Network,\n"
            f"or set the {env_var} environment variable."
        )
        if self.kind == IN_USE:
            return (
                f"{label} {self.port} is already in use.\n"
                "Another application (or another copy of OpenAVC) is using this port.\n\n"
                + fix
            )
        if self.kind == FORBIDDEN:
            if os.name == "nt":
                return (
                    f"{label} {self.port} is reserved by Windows for other software, "
                    "so OpenAVC can't use it.\n\n" + fix
                )
            return (
                f"{label} {self.port} needs a permission this server doesn't have.\n\n"
                + fix
            )
        return f"{label} {self.port} could not be opened on {self.host}: {self.err}"


def bind_socket(host: str, port: int) -> socket.socket:
    """One IPv4 listening socket, bound and not yet listening (asyncio's
    ``create_server`` calls ``listen()`` and sets it non-blocking)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    return sock


def close_all(sockets: list[socket.socket]) -> None:
    for sock in sockets:
        with contextlib.suppress(OSError):
            sock.close()


def bind_for_plan(
    plan: Any,
    port: int,
    *,
    retries: int = 1,
    pause: float = 0.5,
    fallback: bool = True,
) -> tuple[list[socket.socket], Any]:
    """Bind every host of ``plan`` on ``port``. Returns the sockets and the
    plan they serve: ``plan`` itself, or ``plan.without(...)`` the adapter
    addresses the OS no longer allows (``fallback``; the main listener only:
    the others bind the plan it settled on).

    An address in use is retried ``retries`` times, ``pause`` apart: a
    restarting server's predecessor can hold the port for a moment after it
    exits. Anything else raises :class:`ListenBindError` at once.
    """
    attempts = max(1, retries)
    for attempt in range(attempts):
        sockets: list[socket.socket] = []
        dropped: list[str] = []
        failure: ListenBindError | None = None
        for host in plan.hosts:
            try:
                sockets.append(bind_socket(host, port))
            except OSError as err:
                kind = classify(err)
                if kind == NOT_AVAILABLE and fallback and host in plan.network_addresses:
                    dropped.append(host)
                    continue
                failure = ListenBindError(kind, host, port, err)
                break
        if failure is None:
            return sockets, (plan.without(dropped) if dropped else plan)
        close_all(sockets)
        if failure.kind != IN_USE or attempt == attempts - 1:
            raise failure
        time.sleep(pause)
    raise AssertionError("unreachable")  # pragma: no cover


@contextlib.contextmanager
def _no_signals():
    yield


def quiet_server(server: Any) -> Any:
    """Only the main server installs signal handlers; otherwise the servers
    race to register them and the last one overrides the rest. The others
    stop through ``_serve_until_first_exit`` when the main one does."""
    server.capture_signals = _no_signals
    return server


class PushListeners:
    """``/api/push/`` on the device-facing addresses the choice does not
    listen on. Off until ``main()`` enables it, so nothing binds a port in a
    process that is not the server (a test, a tool)."""

    def __init__(self) -> None:
        self._enabled = False
        self._http_port = 0
        self._tls_files: tuple[str, str] | None = None
        self._servers: dict[str, tuple[Any, asyncio.Task]] = {}
        self._failed: dict[str, str] = {}
        self._lock: asyncio.Lock | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self, http_port: int) -> None:
        self._enabled = True
        self._http_port = http_port

    def use_tls(self, certfile: str, keyfile: str) -> None:
        """The HTTPS-only case: devices are given an https callback on the
        HTTPS port (``http_listener.callback_url``), so this listener has to
        answer TLS with the same certificate."""
        self._tls_files = (certfile, keyfile)

    def reset(self) -> None:
        """Forget everything (tests); a running listener is not stopped."""
        self.__init__()

    def _port(self) -> tuple[int, bool]:
        from openavc import config

        if config.TLS_ENABLED and not config.TLS_REDIRECT_HTTP:
            return config.TLS_PORT, True
        return self._http_port or config.HTTP_PORT, False

    def serves(self, host: str) -> bool:
        """Whether a device posting to ``host`` reaches the push registry."""
        from openavc import listen

        return listen.current().serves(host) or host in self._servers

    def failure(self, host: str) -> str | None:
        """Why ``host`` could not be listened on, if it could not."""
        return self._failed.get(host)

    async def ensure(self, host: str) -> None:
        """Listen for pushes on ``host`` when nothing does yet."""
        if not self._enabled or not host or self.serves(host):
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.serves(host):
                return
            await self._start(host)

    async def _start(self, host: str) -> None:
        import uvicorn

        from openavc.transport.http_listener import push_only_app

        port, tls = self._port()
        try:
            sock = bind_socket(host, port)
        except OSError as err:
            self._failed[host] = str(err)
            log.warning(
                "Device updates to %s:%d can't be received: %s", host, port, err,
            )
            return
        options: dict[str, Any] = {}
        if tls and self._tls_files:
            options = {"ssl_certfile": self._tls_files[0], "ssl_keyfile": self._tls_files[1]}
        server = quiet_server(uvicorn.Server(uvicorn.Config(
            push_only_app(),
            lifespan="off",
            log_level="warning",
            **options,
        )))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        self._servers[host] = (server, task)
        self._failed.pop(host, None)
        log.info("Receiving device updates on %s:%d", host, port)

    async def stop(self) -> None:
        for server, task in list(self._servers.values()):
            server.should_exit = True
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, timeout=2.0)
        self._servers.clear()


push_listeners = PushListeners()
