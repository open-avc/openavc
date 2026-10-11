"""A host-owned socket keeps the ordinary listener/certificate startup path."""

import os
import socket
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openavc import main
from tests.listen_helpers import plan_for


def _plan(monkeypatch, *hosts):
    from openavc import listen, listen_safety_net
    plan = SimpleNamespace(hosts=hosts, choice=plan_for("this_computer").choice, problem=None)
    monkeypatch.setattr(listen, "start", lambda: plan)
    monkeypatch.setattr(listen_safety_net, "note_listening", lambda *args: None)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Firmware listener inheritance is supported on Linux")


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
@pytest.mark.parametrize("mode", ["http", "tls", "http-redirect"])
def test_valid_listener_uses_normal_startup_and_preserves_parent_copy(monkeypatch, host, mode):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as parent:
        parent.bind((host, 0))
        parent.listen()
        child_fd = os.dup(parent.fileno())
        monkeypatch.setenv("OPENAVC_LISTEN_FD", str(child_fd))
        _plan(monkeypatch, host)
        monkeypatch.setattr(main.config, "HTTP_PORT", parent.getsockname()[1])
        monkeypatch.setattr(main.config, "TLS_PORT", parent.getsockname()[1])
        monkeypatch.setattr(main.config, "TLS_ENABLED", mode == "tls")
        monkeypatch.setattr(main.config, "PORT80_REDIRECT", mode == "http-redirect")
        addresses, routes = Mock(), []
        monkeypatch.setattr(main, "_write_server_address", addresses)
        monkeypatch.setattr(main, "_clear_startup_error", Mock())

        async def tls(listeners):
            routes.append(("tls", listeners[0].getsockname()))

        async def http(listeners):
            routes.append(("http-redirect", listeners[0].getsockname()))

        def plain(*, sockets):
            routes.append(("http", sockets[0].getsockname()))

        monkeypatch.setattr(main, "_run_tls", tls)
        monkeypatch.setattr(main, "_run_http", http)
        monkeypatch.setattr(main.uvicorn, "Server", lambda config: SimpleNamespace(run=plain))
        main.main()
        assert routes == [(mode, parent.getsockname())]
        addresses.assert_called_once_with()
        assert parent.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        with pytest.raises(OSError):
            os.fstat(child_fd)


@pytest.mark.parametrize("fault", ["wrong-port", "wrong-address", "not-listening", "udp"])
def test_wrong_listener_refuses_before_server_start(monkeypatch, fault):
    kind = socket.SOCK_DGRAM if fault == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, kind) as parent:
        parent.bind(("127.0.0.1", 0))
        if fault not in {"not-listening", "udp"}:
            parent.listen()
        child_fd = os.dup(parent.fileno())
        monkeypatch.setenv("OPENAVC_LISTEN_FD", str(child_fd))
        _plan(monkeypatch, "0.0.0.0" if fault == "wrong-address" else "127.0.0.1")
        monkeypatch.setattr(main.config, "HTTP_PORT", parent.getsockname()[1] + (1 if fault == "wrong-port" else 0))
        monkeypatch.setattr(main.config, "TLS_ENABLED", False)
        started = Mock()
        monkeypatch.setattr(main.uvicorn, "Server", started)
        with pytest.raises(RuntimeError, match="does not match"):
            main.main()
        started.assert_not_called()
        with pytest.raises(OSError):
            os.fstat(child_fd)
