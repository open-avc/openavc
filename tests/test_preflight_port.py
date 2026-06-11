"""Tests for the startup port pre-flight check (server.main._preflight_port).

The pre-flight must mirror the socket options uvicorn binds its real listener
with, so it neither false-fails on a TIME_WAIT'd port from a just-exited
server (the rapid-restart case) nor masks a genuinely occupied port.
"""

import os
import socket

import pytest

from server import config
from server.main import _preflight_port


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((config.BIND_ADDRESS, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_free_port_passes():
    assert _preflight_port(_free_port(), retries=1) is None


def test_live_listener_is_still_detected():
    """A real listener on the port must fail the pre-flight even though it now
    sets SO_REUSEADDR (which only tolerates TIME_WAIT, not a live bind)."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((config.BIND_ADDRESS, 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert _preflight_port(port, retries=1) is not None
    finally:
        listener.close()


def test_sets_reuseaddr_on_posix(monkeypatch):
    """The pre-flight test socket must set SO_REUSEADDR on POSIX so it matches
    asyncio/uvicorn's real listening socket and tolerates a TIME_WAIT port."""
    if os.name == "nt":
        pytest.skip("Windows SO_REUSEADDR has hijack semantics; gated off there")

    port = _free_port()
    seen: list[tuple] = []
    real_socket = socket.socket

    class Spy:
        def __init__(self, *args, **kwargs):
            self._s = real_socket(*args, **kwargs)

        def setsockopt(self, level, opt, value):
            seen.append((level, opt, value))
            return self._s.setsockopt(level, opt, value)

        def bind(self, addr):
            return self._s.bind(addr)

        def close(self):
            return self._s.close()

    # _preflight_port does `import socket as _sock; _sock.socket(...)`, so
    # patching the module attribute reaches it.
    monkeypatch.setattr(socket, "socket", Spy)
    _preflight_port(port, retries=1)
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in seen
