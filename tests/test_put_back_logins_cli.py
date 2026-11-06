"""`python -m openavc.main --put-back-logins`, the way back where the app has
no Rollback (Docker: the image tag changed back by hand).

Run as the container runs it, in a process of its own: it writes each saved
login back into the project and the Project Library copies, starts no server,
and refuses while OpenAVC answers on its port.
"""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from openavc.core.device_logins import SAVED_LOGIN, DeviceLogins

REPO = Path(__file__).resolve().parents[1]
DRIVER = "acme_widget"


def _document(password, devices=None, connections=None):
    return {
        "openavc_version": "0.16.0",
        "project": {"id": "hall", "name": "Hall"},
        "devices": devices or [{"id": "proj1", "driver": DRIVER, "name": "Projector", "config": {}}],
        "connections": connections or {"proj1": {"host": "192.0.2.10", "password": password}},
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(data: Path, project: Path, port: int) -> subprocess.CompletedProcess:
    env = {**os.environ, "OPENAVC_DATA_DIR": str(data), "OPENAVC_PROJECT": str(project),
           "OPENAVC_PORT": str(port)}
    return subprocess.run(
        [sys.executable, "-m", "openavc.main", "--put-back-logins"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )


def _setup(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    DeviceLogins(data).save("proj1", DRIVER, {"password": "Running-Pass"})
    DeviceLogins(data).save("proj1", DRIVER, {"password": "Copy-Pass"}, library="room_b")
    project = data / "projects" / "default" / "project.avc"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps(_document(SAVED_LOGIN)), encoding="utf-8")
    copy = data / "saved_projects" / "room_b" / "project.avc"
    copy.parent.mkdir(parents=True)
    copy.write_text(json.dumps(_document(SAVED_LOGIN)), encoding="utf-8")
    return data, project, copy


def test_it_writes_every_saved_login_back_and_starts_nothing(tmp_path):
    data, project, copy = _setup(tmp_path)

    done = _run(data, project, _free_port())

    assert done.returncode == 0, done.stdout + done.stderr
    assert "Put back 2 logins" in done.stdout
    assert json.loads(project.read_text())["connections"]["proj1"]["password"] == "Running-Pass"
    # A library copy gets the running project's login first, as opening it would.
    assert json.loads(copy.read_text())["connections"]["proj1"]["password"] == "Running-Pass"
    assert "Uvicorn running" not in done.stderr


def test_it_refuses_while_openavc_is_running(tmp_path):
    data, project, _copy = _setup(tmp_path)
    before = project.read_bytes()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        done = _run(data, project, port)

    assert done.returncode == 1
    assert "OpenAVC is still running" in done.stdout
    assert project.read_bytes() == before


def test_a_login_nothing_saved_stands_for_is_named(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    DeviceLogins(data).save("proj1", DRIVER, {"password": "Running-Pass"})
    project = data / "projects" / "default" / "project.avc"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps(_document(
        SAVED_LOGIN,
        devices=[
            {"id": "proj1", "driver": DRIVER, "name": "Projector", "config": {}},
            {"id": "proj2", "driver": DRIVER, "name": "Second", "config": {}},
        ],
        connections={"proj1": {"host": "192.0.2.10", "password": SAVED_LOGIN},
                     "proj2": {"host": "192.0.2.11", "password": SAVED_LOGIN}},
    )), encoding="utf-8")

    done = _run(data, project, _free_port())

    assert done.returncode == 0, done.stdout + done.stderr
    assert "Put back 1 of 2 logins" in done.stdout
    assert "enter them again" in done.stdout
