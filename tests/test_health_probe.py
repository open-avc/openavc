"""The container's health check (openavc/health_probe.py): healthy only while
the server in the container says it is ready, asked where the server recorded
it listens.

The probe is served the run-state record's own answer at each phase, by a
stand-in HTTP server on a free port, so a port the probe did not read from the
status folder would find nothing there.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from openavc import config, health_probe, status_folder
from openavc.core.start_stages import StartRun
from openavc.middleware.startup_splash import RunState


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    for name in ("OPENAVC_PORT", "OPENAVC_TLS_ENABLED", "OPENAVC_TLS_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAVC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "TLS_ENABLED", False)
    return tmp_path


def _ready() -> dict:
    record = RunState()
    record.started(StartRun())
    return record.answer()


def _starting() -> dict:
    return RunState().answer()


def _stopping() -> dict:
    record = RunState()
    record.started(StartRun())
    record.stopping()
    return record.answer()


def _failed() -> dict:
    run = StartRun()
    with run.stage("loading the project"):
        pass
    record = RunState()
    record.stopped(run, RuntimeError("project unreadable"))
    return record.answer()


@pytest.fixture
def server(data_dir, monkeypatch):
    """A stand-in for the server: answers /api/startup-status with
    ``server.answer``, and records where it listens as the server does."""
    asked: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            asked.append(self.path)
            if self.path != "/api/startup-status":
                self.send_error(404)
                return
            body = json.dumps(httpd.answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.answer = _ready()
    httpd.asked = asked
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(config, "HTTP_PORT", httpd.server_address[1])
    status_folder.write_server_address()
    # The configured port is somewhere else: only the recorded one answers.
    monkeypatch.setattr(config, "HTTP_PORT", 1)
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_a_ready_server_is_healthy(server, capsys):
    assert health_probe.main() == 0
    assert capsys.readouterr().out.strip() == "ready"
    assert server.asked == ["/api/startup-status"]


@pytest.mark.parametrize("answer, said", [(_starting, "starting"), (_stopping, "stopping")])
def test_a_server_short_of_ready_is_unhealthy_and_says_where_it_is(server, capsys, answer, said):
    server.answer = answer()

    assert health_probe.main() == 1
    assert capsys.readouterr().out.strip() == said


def test_a_failed_start_is_unhealthy_and_says_why(server, capsys):
    server.answer = _failed()

    assert health_probe.main() == 1
    assert capsys.readouterr().out.strip() == "failed: Loading the project failed: project unreadable"


def test_a_server_that_does_not_answer_is_unhealthy(data_dir, monkeypatch, capsys):
    import socket

    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    monkeypatch.setattr(config, "HTTP_PORT", port)
    status_folder.write_server_address()

    assert health_probe.main() == 1
    assert capsys.readouterr().out.startswith(
        f"not answering at http://127.0.0.1:{port}/api/startup-status"
    )


def test_with_https_on_it_asks_the_https_port(data_dir, monkeypatch):
    """The HTTP port then carries a redirect at most, or nothing."""
    monkeypatch.setattr(config, "HTTP_PORT", 8090)
    monkeypatch.setattr(config, "TLS_ENABLED", True)
    monkeypatch.setattr(config, "TLS_PORT", 8444)
    status_folder.write_server_address()
    monkeypatch.setattr(config, "TLS_ENABLED", False)

    url, context = health_probe.status_url()

    assert url == "https://127.0.0.1:8444/api/startup-status"
    assert context is not None


def test_before_the_first_start_it_asks_the_configured_port(data_dir, monkeypatch):
    monkeypatch.setattr(config, "HTTP_PORT", 8090)

    assert health_probe.status_url() == ("http://127.0.0.1:8090/api/startup-status", None)
