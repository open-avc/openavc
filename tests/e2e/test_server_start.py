"""A browser-suite server starts on a port of its own, or says why it cannot.

The harness picks a free port and hands the number to a server process, which
binds it a moment later. Anything on the machine can take the port in
between. These tests take it on purpose and check the harness notices, starts
again on another port, and never mistakes another server for its own.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from urllib.request import urlopen

from tests.e2e import conftest


def _port_of(handle) -> int:
    return urlparse(handle.base_url).port


def _instance_id_answered(handle) -> str:
    with urlopen(f"{handle.base_url}/api/status", timeout=5.0) as resp:
        return json.loads(resp.read().decode("utf-8"))["instance_id"]


def _first_pick(monkeypatch, port: int) -> None:
    """Make the harness's first pick the given port, then pick as usual."""
    real = conftest._pick_free_port
    picks = [port]

    def pick(bind: str = "127.0.0.1") -> int:
        return picks.pop(0) if picks else real(bind)

    monkeypatch.setattr(conftest, "_pick_free_port", pick)


def _start(tmp_path):
    gen = conftest._start_server(tmp_path, initial_children=0)
    return gen, next(gen)


def _stop(gen) -> None:
    for _ in gen:
        pass


def test_a_port_taken_before_the_server_binds_it_is_replaced(tmp_path, monkeypatch):
    with socket.create_server(("127.0.0.1", 0)) as taken:
        port = taken.getsockname()[1]
        _first_pick(monkeypatch, port)
        gen, handle = _start(tmp_path)
        try:
            assert _port_of(handle) != port
            # Started again somewhere the failed start left nothing behind.
            assert handle.project_path.parent != tmp_path
            ours = (handle.project_path.parent / ".instance_id").read_text().strip()
            assert _instance_id_answered(handle) == ours
        finally:
            _stop(gen)


class _OtherServer(BaseHTTPRequestHandler):
    """Another OpenAVC instance, as far as the harness's questions go."""

    def do_GET(self):  # noqa: N802 (http.server's name)
        if self.path == "/api/startup-status":
            body = {"ready": True, "error": None}
        elif self.path == "/api/status":
            body = {"instance_id": "another-server"}
        else:
            self.send_error(404)
            return
        payload = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


def test_another_server_answering_on_the_port_is_not_taken_for_this_one(
    tmp_path, monkeypatch,
):
    other = ThreadingHTTPServer(("127.0.0.1", 0), _OtherServer)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    try:
        port = other.server_address[1]
        _first_pick(monkeypatch, port)
        gen, handle = _start(tmp_path)
        try:
            assert _port_of(handle) != port
            assert _instance_id_answered(handle) != "another-server"
        finally:
            _stop(gen)
    finally:
        other.shutdown()
        other.server_close()


def test_a_bind_lost_after_the_engine_started_is_recognised(tmp_path):
    """The second way to lose the port: the start-up check passed, the engine
    started, then the web server's own bind failed. Nothing is recorded but
    the log line, so the line is produced by a real failed bind here rather
    than copied into the test."""
    with socket.create_server(("127.0.0.1", 0)) as taken:
        port = taken.getsockname()[1]

        async def bind_it():
            await asyncio.start_server(lambda r, w: None, "127.0.0.1", port)

        # A thread of its own: the browser fixtures keep an event loop
        # running on this one.
        failures: list[OSError] = []

        def attempt() -> None:
            try:
                asyncio.run(bind_it())
            except OSError as exc:
                failures.append(exc)

        worker = threading.Thread(target=attempt)
        worker.start()
        worker.join()
        assert failures, "binding a taken port succeeded"
        logged = f"ERROR:    {failures[0]}\n"

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log_path = tmp_path / "server.log"
    log_path.write_text(logged, encoding="utf-8")
    assert conftest._lost_the_port(data_dir, log_path, "127.0.0.1", port)
    # The same line about some other address or port is not this server's.
    assert not conftest._lost_the_port(data_dir, log_path, "127.0.0.1", port + 1)
    assert not conftest._lost_the_port(data_dir, log_path, "10.0.0.5", port)


def test_any_other_start_up_error_is_not_mistaken_for_a_lost_port(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log_path = tmp_path / "server.log"
    log_path.write_text("Traceback (most recent call last):\n", encoding="utf-8")
    (data_dir / "startup-error.json").write_text(
        json.dumps({"error": "tls_error", "message": "x"}), encoding="utf-8",
    )
    assert not conftest._lost_the_port(data_dir, log_path, "127.0.0.1", 40123)
    (data_dir / "startup-error.json").write_text(
        json.dumps({"error": "port_in_use", "message": "x"}), encoding="utf-8",
    )
    assert conftest._lost_the_port(data_dir, log_path, "127.0.0.1", 40123)
