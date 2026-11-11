"""The server raises its own open-files limit, and nothing it runs is held to 1023.

Every connected device holds a socket. Started by systemd the server gets a soft
limit of 1024 open files, by launchd 256, so a large project stops connecting
new devices near that many. The soft limit is the process's own to raise up to
the hard limit, which the server does at startup. Once it has, descriptor
numbers pass 1023, which ``select.select`` cannot watch.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys

import pytest

from openavc import open_files

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no open-files limit")


class _Limits:
    """Stands in for the resource module: one process's soft and hard limit."""

    RLIMIT_NOFILE = 7
    RLIM_INFINITY = 2**63 - 1

    def __init__(self, soft: int, hard: int, refuse_above: int | None = None) -> None:
        self.soft, self.hard, self.refuse_above = soft, hard, refuse_above
        self.sets: list[tuple[int, int]] = []

    def getrlimit(self, which: int) -> tuple[int, int]:
        assert which == self.RLIMIT_NOFILE
        return self.soft, self.hard

    def setrlimit(self, which: int, limits: tuple[int, int]) -> None:
        assert which == self.RLIMIT_NOFILE
        if self.refuse_above is not None and limits[0] > self.refuse_above:
            raise ValueError("not allowed to raise maximum limit")
        self.sets.append(limits)
        self.soft = limits[0]


def _with(monkeypatch, limits: _Limits) -> _Limits:
    monkeypatch.setitem(sys.modules, "resource", limits)
    return limits


def test_a_systemd_service_limit_is_raised(monkeypatch):
    limits = _with(monkeypatch, _Limits(1024, 524288))
    assert open_files.raise_open_file_limit() == (1024, open_files.WANTED)
    assert limits.sets == [(open_files.WANTED, 524288)]


def test_a_launchd_service_limit_is_raised(monkeypatch):
    limits = _with(monkeypatch, _Limits(256, _Limits.RLIM_INFINITY))
    assert open_files.raise_open_file_limit() == (256, open_files.WANTED)
    assert limits.sets == [(open_files.WANTED, _Limits.RLIM_INFINITY)]


def test_a_hard_limit_set_lower_on_purpose_is_the_ceiling(monkeypatch):
    limits = _with(monkeypatch, _Limits(1024, 4096))
    assert open_files.raise_open_file_limit() == (1024, 4096)
    assert limits.sets == [(4096, 4096)]


def test_a_limit_already_higher_is_left_alone(monkeypatch):
    for soft in (1048576, _Limits.RLIM_INFINITY):
        limits = _with(monkeypatch, _Limits(soft, _Limits.RLIM_INFINITY))
        assert open_files.raise_open_file_limit() == (soft, soft)
        assert limits.sets == []


def test_a_refused_raise_tries_a_smaller_one(monkeypatch):
    limits = _with(monkeypatch, _Limits(256, _Limits.RLIM_INFINITY, refuse_above=10240))
    assert open_files.raise_open_file_limit() == (256, 10240)
    assert limits.sets == [(10240, _Limits.RLIM_INFINITY)]


def test_a_platform_with_no_limit_says_so(monkeypatch):
    monkeypatch.setitem(sys.modules, "resource", None)  # import resource -> ImportError
    assert open_files.raise_open_file_limit() is None


@posix_only
def test_the_real_limit_is_raised_from_a_service_managers_256():
    """In a process of its own, so the suite's own limit is never lowered."""
    script = (
        "import resource\n"
        "_, hard = resource.getrlimit(resource.RLIMIT_NOFILE)\n"
        "resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))\n"
        "from openavc.open_files import raise_open_file_limit\n"
        "before, after = raise_open_file_limit()\n"
        "print(before, after, resource.getrlimit(resource.RLIMIT_NOFILE)[0])\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ).stdout.split()
    before, after, now = (int(x) for x in out)
    assert before == 256
    assert after == now >= 10240


def test_the_server_raises_the_limit_before_it_binds(monkeypatch):
    from openavc import listen, main as server_main

    order: list[str] = []

    class _Stop(Exception):
        pass

    def bind_first(*_a, **_k):
        order.append("listen")
        raise _Stop

    monkeypatch.setattr(open_files, "raise_open_file_limit", lambda: order.append("raise") or (1024, 1024))
    monkeypatch.setattr(listen, "start", bind_first)
    with pytest.raises(_Stop):
        server_main.main()
    assert order == ["raise", "listen"]


@posix_only
def test_ssdp_hears_a_reply_on_a_socket_numbered_past_1023():
    import resource

    from openavc.discovery.ssdp_scanner import SSDPScanner

    high = 1500
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= high:
        pytest.skip("this process cannot open a descriptor that high")
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    os.dup2(listener.fileno(), high)
    listener.close()
    receiving = socket.socket(fileno=high)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    heard: list[str] = []
    scanner = SSDPScanner()
    scanner._process_response = lambda data, ip: heard.append(ip)
    scanner._search_sock = receiving
    scanner._running = True
    try:
        sender.sendto(b"HTTP/1.1 200 OK\r\n\r\n", receiving.getsockname())
        asyncio.run(scanner._listen(1.0))
    finally:
        receiving.close()
        sender.close()
    assert heard == ["127.0.0.1"]


def test_nothing_in_the_server_waits_on_sockets_with_select():
    """select.select cannot watch a descriptor past 1023, which the raised
    limit makes reachable; selectors and the event loop have no such ceiling."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "openavc")
    users = []
    for folder, _dirs, files in os.walk(root):
        for name in (n for n in files if n.endswith(".py")):
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                if "select.select" in f.read():
                    users.append(os.path.relpath(path, root))
    assert users == []
