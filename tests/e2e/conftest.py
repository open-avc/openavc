"""E2E test fixtures for the Programmer IDE.

Each test boots a real ``python -m openavc.main`` subprocess pointed at a
temp project + temp data dir, listening on a free localhost port. The
``E2ETestController`` driver (copied into ``driver_repo/`` for the test
session) declares one child entity type and synthesizes an ``initial_children``
count at connect time without any real network I/O. A JSON control file
lets tests trigger runtime add/remove ops by bumping its ``seq``.

Browser installation
--------------------
Playwright bundles browsers separately from its Python package. Before
running these tests for the first time::

    pip install -e .[dev]
    python -m playwright install chromium

CI does the same in a single step; locally, the missing-browser error
message points back to this comment.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest

from tests import gates

# Playwright drives a real browser and is an optional dev dependency, so it is
# deliberately not installed in the automated test job. When it isn't
# importable (that job, or any box without the dev extras) skip collecting the
# e2e tests entirely, so a missing import doesn't abort the whole pytest run.
# They still run anywhere Playwright is installed -- locally:
# `pip install -e .[dev] && python -m playwright install chromium`, then
# `pytest tests/e2e/`. A run that installs it says so with
# OPENAVC_REQUIRE_E2E=1 and then a missing Playwright is an error, not a
# silently empty directory.
try:
    import playwright  # noqa: F401
except ImportError:
    gates.fail_if_required(gates.E2E, "playwright is not installed")
    collect_ignore_glob = ["test_*.py"]

OPENAVC_ROOT = Path(__file__).resolve().parents[2]
DRIVER_SRC = Path(__file__).with_name("_controller_driver_src.py")
INSTALLED_DRIVER_NAME = "e2e_test_controller.py"


# ---------------------------------------------------------------------------
# Session-scoped driver install
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _install_test_driver():
    """No-op session marker kept for backward compatibility.

    The synthetic controller driver is installed *per server* into each
    subprocess's own ``{data_dir}/driver_repo/`` (see ``_start_server``),
    not into the shared workspace ``driver_repo``. The data_dir is where
    ``DRIVER_REPO_DIR`` resolves after the repo relocation, so the loader
    discovers it directly with no migration step.

    Earlier this fixture copied the driver into the workspace
    ``APP_DIR/driver_repo``. That broke once ``migrate_legacy_repos()``
    began *moving* legacy-location content into the first server's
    data_dir on startup: the first server drained the workspace copy and
    every later server in the session saw a "Missing drivers" project.
    Installing per-server sidesteps the legacy-move path entirely.
    """
    yield


# ---------------------------------------------------------------------------
# Per-test server subprocess
# ---------------------------------------------------------------------------

def _pick_free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_ready(url: str, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if body.get("ready"):
                    return
                if body.get("error"):
                    raise RuntimeError(f"Engine startup error: {body['error']}")
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
        time.sleep(0.25)
    raise RuntimeError(
        f"Server at {url} did not become ready within {timeout}s "
        f"(last={last_err})"
    )


def _build_project(
    *,
    initial_children: int,
    device_id: str = "ctrl1",
    device_name: str = "Test Controller",
) -> dict[str, Any]:
    return {
        "openavc_version": "0.5.0",
        "project": {
            "id": "e2e_test_project",
            "name": "E2E Test Project",
            "description": "",
            "created": "2026-01-01T00:00:00",
            "modified": "2026-01-01T00:00:00",
        },
        "devices": [
            {
                "id": device_id,
                "driver": "e2e_test_controller",
                "name": device_name,
                "config": {"initial_children": initial_children},
                "enabled": True,
                "pending_settings": {},
                "child_entities": {},
            },
        ],
        "device_groups": [],
        "connections": {},
        "driver_dependencies": [],
        "plugin_dependencies": [],
        "plugins": {},
        "variables": [],
        "macros": [],
        "ui": {
            "settings": {"theme": "dark"},
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "grid": {"columns": 12, "rows": 8}, "elements": [],
            }],
            "master_elements": [],
            "page_groups": [],
        },
        "scripts": [],
    }


class _ServerHandle:
    def __init__(self, base_url: str, control_file: Path, device_id: str,
                 data_dir: Path, project_path: Path,
                 process: subprocess.Popen):
        self.base_url = base_url
        self.control_file = control_file
        self.device_id = device_id
        self.data_dir = data_dir
        self.project_path = project_path
        self.process = process
        self._next_seq = 1

    def write_ops(self, operations: list[dict[str, Any]]) -> None:
        """Atomically write a new ops batch with a fresh seq."""
        self._next_seq += 1
        payload = {"seq": self._next_seq, "operations": operations}
        tmp = self.control_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.control_file)


@pytest.fixture
def openavc_server(tmp_path: Path, _install_test_driver):
    """Boot a fresh openavc server subprocess seeded with the e2e controller.

    Default seed: ``initial_children = 0`` so each test can pick its own
    fixture size via ``server_with(initial_children=N)``. The bare
    ``openavc_server`` fixture still works for tests that want the empty
    default.
    """
    yield from _start_server(tmp_path, initial_children=0)


@pytest.fixture
def server_factory(tmp_path: Path, _install_test_driver):
    """Factory variant — call ``server_factory(initial_children=N)`` to spawn
    a server seeded with N pre-registered children. Yields a single handle
    per call; multiple calls within one test reuse the same temp_path
    namespace but spawn distinct subprocesses on fresh ports.
    """
    handles: list[_ServerHandle] = []
    processes: list[subprocess.Popen] = []

    def _make(*, initial_children: int = 0) -> _ServerHandle:
        gen = _start_server(
            tmp_path / f"srv-{uuid.uuid4().hex[:8]}",
            initial_children=initial_children,
        )
        handle = next(gen)
        handles.append(handle)
        processes.append(handle.process)
        # Stash the generator so we can step it at teardown.
        handle._gen = gen  # type: ignore[attr-defined]
        return handle

    yield _make

    for h in handles:
        try:
            next(h._gen)  # type: ignore[attr-defined]
        except StopIteration:
            pass


def _start_server(tmp_root: Path, *, initial_children: int):
    tmp_root.mkdir(parents=True, exist_ok=True)
    data_dir = tmp_root / "data"
    data_dir.mkdir(exist_ok=True)

    # Install the synthetic controller driver into THIS server's own
    # driver_repo (which is where DRIVER_REPO_DIR resolves under the temp
    # data_dir). Pre-populating the target means migrate_legacy_repos()
    # skips it, so the driver is never moved out from under a sibling
    # server — each subprocess in the session is self-contained.
    driver_repo = data_dir / "driver_repo"
    driver_repo.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DRIVER_SRC, driver_repo / INSTALLED_DRIVER_NAME)

    project_path = tmp_root / "project.avc"
    project_path.write_text(
        json.dumps(_build_project(initial_children=initial_children), indent=2),
        encoding="utf-8",
    )

    control_file = tmp_root / "control.json"
    control_file.write_text(
        json.dumps({"seq": 1, "operations": []}), encoding="utf-8",
    )

    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = {
        **os.environ,
        "OPENAVC_PORT": str(port),
        "OPENAVC_BIND": "127.0.0.1",
        "OPENAVC_PROJECT": str(project_path),
        "OPENAVC_DATA_DIR": str(data_dir),
        "OPENAVC_E2E_CONTROL_FILE": str(control_file),
        # Force a quiet startup — no cloud, no kiosk, no auth.
        "OPENAVC_CLOUD_ENABLED": "false",
        "OPENAVC_RATE_LIMIT_ENABLED": "false",
        # Prevent the subprocess from inheriting any parent PYTHONPATH that
        # shadows the openavc source tree.
        "PYTHONUNBUFFERED": "1",
    }
    # PowerShell-launched parents sometimes leave OPENAVC_DATA_DIR pointed at
    # a session-scoped temp from the in-process test suite; force ours through.

    log_path = tmp_root / "server.log"
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "openavc.main"],
        cwd=str(OPENAVC_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_for_ready(f"{base_url}/api/startup-status", timeout=30.0)
    except Exception:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            tail = "<log unreadable>"
        raise RuntimeError(f"Server failed to start. Log tail:\n{tail}")

    handle = _ServerHandle(
        base_url=base_url,
        control_file=control_file,
        device_id="ctrl1",
        data_dir=data_dir,
        project_path=project_path,
        process=proc,
    )

    try:
        yield handle
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        log.close()


# ---------------------------------------------------------------------------
# Playwright browser-context defaults
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Pin viewport so the IDE renders the desktop layout consistently."""
    return {
        **browser_context_args,
        "viewport": {"width": 1440, "height": 900},
    }
