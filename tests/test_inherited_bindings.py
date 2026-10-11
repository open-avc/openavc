"""Linux host descriptors preserve hostname and alternate IPv6 spellings."""

from contextlib import ExitStack
import os
import socket
import sys
from types import SimpleNamespace

import pytest

from openavc import main
from tests.listen_helpers import plan_for


def _plan(monkeypatch, *hosts):
    from openavc import listen, listen_safety_net
    plan = SimpleNamespace(hosts=hosts, choice=plan_for("this_computer").choice, problem=None)
    monkeypatch.setattr(listen, "start", lambda: plan)
    monkeypatch.setattr(listen_safety_net, "note_listening", lambda *args: None)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Firmware listener inheritance is supported on Linux")


@pytest.mark.parametrize("mode", ["http", "tls", "http-redirect"])
def test_localhost_passes_both_families_and_retains_both_parent_sockets(monkeypatch, mode):
    with ExitStack() as stack:
        ipv6 = stack.enter_context(socket.socket(socket.AF_INET6, socket.SOCK_STREAM))
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.bind(("::1", 0))
        port = ipv6.getsockname()[1]
        ipv4 = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        ipv4.bind(("127.0.0.1", port))
        parents = [ipv6, ipv4]
        for parent in parents:
            parent.listen()
        descriptors = [os.dup(parent.fileno()) for parent in parents]
        monkeypatch.setenv("OPENAVC_LISTEN_FD", ",".join(map(str, descriptors)))
        _plan(monkeypatch, "::1", "127.0.0.1")
        monkeypatch.setattr(main.config, "HTTP_PORT", port)
        monkeypatch.setattr(main.config, "TLS_PORT", port)
        monkeypatch.setattr(main.config, "TLS_ENABLED", mode == "tls")
        monkeypatch.setattr(main.config, "PORT80_REDIRECT", mode == "http-redirect")
        monkeypatch.setattr(main, "_write_server_address", lambda: None)
        monkeypatch.setattr(main, "_clear_startup_error", lambda: None)
        actual = []

        async def asynchronous(listeners):
            actual.extend(listener.getsockname() for listener in listeners)

        def plain(*, sockets):
            actual.extend(listener.getsockname() for listener in sockets)

        monkeypatch.setattr(main, "_run_tls", asynchronous)
        monkeypatch.setattr(main, "_run_http", asynchronous)
        monkeypatch.setattr(main.uvicorn, "Server", lambda config: SimpleNamespace(run=plain))
        main.main()
        assert actual == [parent.getsockname() for parent in parents]
        for parent in parents:
            assert parent.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        for fd in descriptors:
            with pytest.raises(OSError):
                os.fstat(fd)


def test_equivalent_ipv6_spelling_matches_bound_socket(monkeypatch):
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as parent:
        parent.bind(("::1", 0))
        parent.listen()
        descriptor = os.dup(parent.fileno())
        monkeypatch.setenv("OPENAVC_LISTEN_FD", str(descriptor))
        _plan(monkeypatch, "0:0:0:0:0:0:0:1")
        monkeypatch.setattr(main.config, "HTTP_PORT", parent.getsockname()[1])
        monkeypatch.setattr(main.config, "TLS_ENABLED", False)
        monkeypatch.setattr(main.config, "PORT80_REDIRECT", False)
        monkeypatch.setattr(main, "_write_server_address", lambda: None)
        monkeypatch.setattr(main, "_clear_startup_error", lambda: None)
        actual = []
        monkeypatch.setattr(main.uvicorn, "Server", lambda config: SimpleNamespace(run=lambda **kwargs: actual.extend(kwargs["sockets"])))
        main.main()
        assert len(actual) == 1 and actual[0].fileno() == -1


def test_duplicate_descriptor_is_refused_and_closed_once(monkeypatch):
    with socket.socket() as parent:
        parent.bind(("127.0.0.1", 0))
        parent.listen()
        descriptor = os.dup(parent.fileno())
        monkeypatch.setenv("OPENAVC_LISTEN_FD", f"{descriptor},{descriptor}")
        _plan(monkeypatch, "127.0.0.1")
        monkeypatch.setattr(main.config, "HTTP_PORT", parent.getsockname()[1])
        monkeypatch.setattr(main.config, "TLS_ENABLED", False)
        with pytest.raises(RuntimeError, match="distinct descriptors"):
            main.main()
        with pytest.raises(OSError):
            os.fstat(descriptor)
