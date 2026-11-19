"""A stop the server heard and finished ends the process cleanly.

The Windows service sends Ctrl+C to stop the server, and a server run in a
terminal is stopped the same way. uvicorn finishes the stop and then raises
the signal it caught again, so the interpreter's own handling runs; for Ctrl+C
that is a KeyboardInterrupt with nothing left to catch it. Every stop of the
Windows service ended with a traceback in the service's error log
("Failed to execute script 'main' due to unhandled exception!") and exit code
1, after a stop that had already finished.

Run as a subprocess with a real uvicorn server through ``main.main()``, so
the signal goes through the handlers it really meets.
A POSIX SIGINT is what Ctrl+C delivers on Windows; a test cannot send a
Windows console Ctrl+C to a child it does not share a console with.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import openavc.main as main

REPO = Path(__file__).resolve().parents[1]

SERVER = textwrap.dedent(
    """
    import socket
    import sys
    from pathlib import Path

    import uvicorn

    import openavc.main as main
    from openavc import config, listen


    async def app(scope, receive, send):
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})


    config.TLS_ENABLED = False
    config.PORT80_REDIRECT = False
    main._main_http_config = lambda: uvicorn.Config(app, lifespan="off", log_level="info")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    Path(sys.argv[1]).write_text(str(sock.getsockname()[1]))
    # main() as the server runs it, on this socket instead of a listen plan's
    listen.start = lambda: None
    main._bind_primary_port = lambda plan: ([sock], plan)
    main._settle_listening = lambda plan: None
    main.status_folder.write_server_address = lambda: None
    main.main()
    """
)


def _port_it_listens_on(proc: subprocess.Popen, port_file: Path) -> int:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if port_file.exists() and port_file.read_text():
            return int(port_file.read_text())
        if proc.poll() is not None:
            raise AssertionError(proc.communicate()[1])
        time.sleep(0.1)
    raise AssertionError("the server never said its port")


def _wait_until_it_answers(port: int) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as conn:
                conn.sendall(b"GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
                if conn.recv(64).startswith(b"HTTP/1.1 200"):
                    return
        except OSError:
            pass
        time.sleep(0.1)
    raise AssertionError("the server never answered")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGINT stands in for the console's Ctrl+C")
def test_ctrl_c_ends_the_server_cleanly_once_its_stop_has_run(tmp_path):
    script = tmp_path / "serve.py"
    script.write_text(SERVER)
    env = {**os.environ, "PYTHONPATH": str(REPO), "OPENAVC_DATA_DIR": str(tmp_path / "data")}
    port_file = tmp_path / "port"
    proc = subprocess.Popen(
        [sys.executable, str(script), str(port_file)], cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        _wait_until_it_answers(_port_it_listens_on(proc, port_file))
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    assert "Finished server process" in err, err   # the stop ran
    assert "Traceback" not in err, err
    assert proc.returncode == 0, err


def test_an_interrupt_before_the_server_heard_a_stop_still_ends_the_start(monkeypatch):
    """Ctrl+C while the server is still starting is not a finished stop: it
    goes on to end the process, as it always did."""
    from openavc import listen, listen_sockets, open_files

    def interrupted(_sockets):
        raise KeyboardInterrupt

    # main() as it runs, with nothing switched on in this shared process
    monkeypatch.setattr(open_files, "raise_open_file_limit", lambda: None)
    monkeypatch.setattr(listen_sockets.push_listeners, "enable", lambda port: None)
    monkeypatch.setattr(listen_sockets, "_stop_heard", False)
    monkeypatch.setattr(listen, "start", lambda: None)
    monkeypatch.setattr(main, "_bind_primary_port", lambda plan: ([], plan))
    monkeypatch.setattr(main, "_settle_listening", lambda plan: None)
    monkeypatch.setattr(main.status_folder, "clear_startup_error", lambda: None)
    monkeypatch.setattr(main.status_folder, "write_server_address", lambda: None)
    monkeypatch.setattr(main, "_serve", interrupted)
    with pytest.raises(KeyboardInterrupt):
        main.main()
