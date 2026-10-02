"""The browser suite's servers never start on a port Chromium refuses.

A server on such a port (2049, say, which the OS handed out once) fails its
test with net::ERR_UNSAFE_PORT before anything is tested.
"""

from __future__ import annotations

from tests.e2e import conftest


class _FakeSocket:
    def __init__(self, ports: list[int]):
        self._ports = ports
        self._port = 0

    def bind(self, _address) -> None:
        self._port = self._ports.pop(0)

    def getsockname(self):
        return ("127.0.0.1", self._port)

    def close(self) -> None:
        pass


def test_a_port_chromium_refuses_is_passed_over(monkeypatch):
    ports = [2049, 6000, 40123]
    monkeypatch.setattr(conftest.socket, "socket", lambda *_a, **_k: _FakeSocket(ports))
    assert conftest._pick_free_port() == 40123
