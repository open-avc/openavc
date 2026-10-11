"""The web listeners' sockets, bound for each listen choice
(openavc/listen_sockets.py).

These bind real sockets: on loopback everywhere, and on this machine's own
network address where the runner has one (skipped where it has none). The
address no machine holds is 192.0.2.1, from the range reserved for
documentation, so the "adapter is gone" case is real too.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket

import httpx
import pytest
import uvicorn

from openavc import listen, listen_sockets
from openavc.listen import Adapter, ListenChoice, resolve
from openavc.listen_sockets import ListenBindError, bind_for_plan, classify

NOBODYS_ADDRESS = "192.0.2.1"


def _own_network_address() -> str | None:
    """A non-loopback IPv4 this machine can listen on, or None."""
    from openavc.discovery.network_scanner import get_interface_ips

    for ip in get_interface_ips():
        if listen.address_available(ip):
            return ip
    return None


def _adapter_plan(*ips: str):
    adapter = Adapter("Ethernet", "50-EB-F6-D2-FC-29", tuple((ip, f"{ip}/32") for ip in ips), True)
    return resolve(ListenChoice("adapter", adapter.identity()), [adapter])


def _bound_addresses(sockets) -> list[str]:
    return sorted(s.getsockname()[0] for s in sockets)


class TestClassifying:
    @pytest.mark.parametrize("code, kind", [
        (errno.EADDRNOTAVAIL, "not_available"),
        (10049, "not_available"),
        (errno.EADDRINUSE, "in_use"),
        (10048, "in_use"),
        (errno.EACCES, "forbidden"),
        (10013, "forbidden"),
        (errno.ECONNREFUSED, "other"),
    ])
    def test_each_failure_is_told_apart(self, code, kind):
        assert classify(OSError(code, "test")) == kind

    def test_an_address_this_computer_lacks_is_not_available_here(self):
        with pytest.raises(OSError) as caught:
            listen_sockets.bind_socket(NOBODYS_ADDRESS, 0)
        assert classify(caught.value) == "not_available"


class TestBinding:
    def test_this_computer_only_binds_loopback(self):
        sockets, plan = bind_for_plan(resolve(ListenChoice("this_computer"), []), 0)
        try:
            assert _bound_addresses(sockets) == ["127.0.0.1"]
            assert plan.problem is None
        finally:
            listen_sockets.close_all(sockets)

    def test_any_network_binds_the_wildcard_alone(self):
        sockets, _ = bind_for_plan(resolve(ListenChoice("any"), []), 0)
        try:
            assert _bound_addresses(sockets) == ["0.0.0.0"]
        finally:
            listen_sockets.close_all(sockets)

    def test_a_missing_address_falls_back_to_loopback_with_a_reason(self):
        sockets, plan = bind_for_plan(_adapter_plan(NOBODYS_ADDRESS), 0)
        try:
            assert _bound_addresses(sockets) == ["127.0.0.1"]
            assert plan.local_only
            assert plan.problem.code == "adapter_not_connected"
            assert plan.dropped == (NOBODYS_ADDRESS,)
        finally:
            listen_sockets.close_all(sockets)

    def test_without_fallback_a_missing_address_is_an_error(self):
        with pytest.raises(ListenBindError) as caught:
            bind_for_plan(_adapter_plan(NOBODYS_ADDRESS), 0, fallback=False)
        assert caught.value.kind == "not_available"

    def test_one_adapter_binds_its_address_and_loopback_on_one_port(self):
        own = _own_network_address()
        if own is None:
            pytest.skip("this machine has no network address to listen on")
        probe = listen_sockets.bind_socket("127.0.0.1", 0)
        port = probe.getsockname()[1]
        probe.close()
        sockets, plan = bind_for_plan(_adapter_plan(own), port)
        try:
            assert _bound_addresses(sockets) == sorted([own, "127.0.0.1"])
            assert {s.getsockname()[1] for s in sockets} == {port}
            assert plan.problem is None
        finally:
            listen_sockets.close_all(sockets)

    def test_a_live_listener_is_in_use(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        try:
            with pytest.raises(ListenBindError) as caught:
                bind_for_plan(resolve(ListenChoice("this_computer"), []), port)
            assert caught.value.kind == "in_use"
            assert caught.value.error_type == "port_in_use"
            assert caught.value.message("HTTP port", "OPENAVC_PORT").startswith(
                f"HTTP port {port} is already in use."
            )
        finally:
            holder.close()

    def test_a_restart_waits_for_the_port_to_come_free(self, monkeypatch):
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        waits: list[float] = []

        def sleep(seconds: float) -> None:
            waits.append(seconds)
            holder.close()  # the previous process exits

        monkeypatch.setattr(listen_sockets.time, "sleep", sleep)
        sockets, _ = bind_for_plan(resolve(ListenChoice("this_computer"), []), port, retries=5)
        try:
            assert waits == [0.5]
            assert _bound_addresses(sockets) == ["127.0.0.1"]
        finally:
            listen_sockets.close_all(sockets)

    def test_reuseaddr_only_on_posix(self, monkeypatch):
        """POSIX: matches asyncio's listener, so a port in TIME_WAIT from a
        server that just exited is accepted. Windows: never, because there it
        lets a second socket take a port in use."""
        seen: list[tuple] = []
        real = socket.socket

        class Spy(real):
            def setsockopt(self, level, opt, value):
                seen.append((level, opt, value))
                return super().setsockopt(level, opt, value)

        monkeypatch.setattr(listen_sockets.socket, "socket", Spy)
        listen_sockets.bind_socket("127.0.0.1", 0).close()
        reuse = (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        assert (reuse in seen) == (os.name != "nt")


async def _ok(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


async def _serve_and_fetch(sockets, urls: list[str]) -> list[str]:
    server = listen_sockets.quiet_server(
        uvicorn.Server(uvicorn.Config(_ok, lifespan="off", log_level="warning"))
    )
    task = asyncio.create_task(server.serve(sockets=sockets))
    while not server.started:
        await asyncio.sleep(0.02)
    results: list[str] = []
    async with httpx.AsyncClient(timeout=2.0) as client:
        for url in urls:
            try:
                results.append((await client.get(url)).text)
            except httpx.HTTPError:
                results.append("refused")
    server.should_exit = True
    await task
    return results


class TestServing:
    """One server answering on every socket of the plan, and nowhere else."""

    def test_one_adapter_answers_there_and_on_loopback(self):
        own = _own_network_address()
        if own is None:
            pytest.skip("this machine has no network address to listen on")
        probe = listen_sockets.bind_socket(own, 0)
        port = probe.getsockname()[1]
        probe.close()
        sockets, _ = bind_for_plan(_adapter_plan(own), port)
        got = asyncio.run(_serve_and_fetch(sockets, [
            f"http://{own}:{port}/", f"http://127.0.0.1:{port}/",
        ]))
        assert got == ["ok", "ok"]

    def test_this_computer_only_refuses_the_network(self):
        own = _own_network_address()
        if own is None:
            pytest.skip("this machine has no network address to try")
        sockets, _ = bind_for_plan(resolve(ListenChoice("this_computer"), []), 0)
        port = sockets[0].getsockname()[1]
        got = asyncio.run(_serve_and_fetch(sockets, [
            f"http://127.0.0.1:{port}/", f"http://{own}:{port}/",
        ]))
        assert got == ["ok", "refused"]


class TestPushListeners:
    """Device updates keep arriving on the web port whoever may open the panel."""

    def test_nothing_binds_until_the_server_enables_it(self):
        pushes = listen_sockets.PushListeners()
        listen.set_current(resolve(ListenChoice("this_computer"), []))
        asyncio.run(pushes.ensure(NOBODYS_ADDRESS))
        assert pushes.failure(NOBODYS_ADDRESS) is None
        assert not pushes.serves(NOBODYS_ADDRESS)

    def test_an_address_the_choice_serves_needs_no_listener(self):
        pushes = listen_sockets.PushListeners()
        pushes.enable(0)
        listen.set_current(resolve(ListenChoice("any"), []))
        asyncio.run(pushes.ensure("10.20.30.5"))
        assert pushes.serves("10.20.30.5")
        assert pushes._servers == {}

    def test_an_address_this_computer_lacks_is_reported(self):
        pushes = listen_sockets.PushListeners()
        pushes.enable(0)
        listen.set_current(resolve(ListenChoice("this_computer"), []))
        asyncio.run(pushes.ensure(NOBODYS_ADDRESS))
        assert pushes.failure(NOBODYS_ADDRESS)
        assert not pushes.serves(NOBODYS_ADDRESS)

    def test_the_push_only_listener_answers_push_and_nothing_else(self, monkeypatch):
        own = _own_network_address()
        if own is None:
            pytest.skip("this machine has no network address to listen on")
        from openavc.transport import http_listener

        received: list[bytes] = []

        async def run() -> list[int]:
            pushes = listen_sockets.PushListeners()
            probe = listen_sockets.bind_socket(own, 0)
            port = probe.getsockname()[1]
            probe.close()
            pushes.enable(port)
            listen.set_current(resolve(ListenChoice("this_computer"), []))
            sub = await http_listener.subscribe(
                "acme_widget", own, lambda request: received.append(request.body), "acme_widget"
            )
            await pushes.ensure(own)
            assert pushes.serves(own)
            server, _task = pushes._servers[own]
            while not server.started:
                await asyncio.sleep(0.02)
            async with httpx.AsyncClient(timeout=2.0) as client:
                pushed = await client.post(f"http://{own}:{port}/api/push/acme_widget", content=b"hi")
                other = await client.get(f"http://{own}:{port}/programmer")
            await sub.close()
            await pushes.stop()
            return [pushed.status_code, other.status_code]

        assert asyncio.run(run()) == [200, 404]
        assert received == [b"hi"]
