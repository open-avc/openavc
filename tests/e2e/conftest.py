"""E2E test fixtures for a served instance -- the Programmer IDE and the panel.

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

# Ports Chromium refuses to load a page from (net::ERR_UNSAFE_PORT), above
# 1024: an OS can hand any of them out as a free port, and a server on one
# fails its test before it runs.
_CHROMIUM_UNSAFE_PORTS = frozenset({
    1719, 1720, 1723, 2049, 3659, 4045, 4190, 5060, 5061, 6000, 6566,
    6665, 6666, 6667, 6668, 6669, 6679, 6697, 10080,
})


def _pick_free_port(bind: str = "127.0.0.1") -> int:
    """A port free on ``bind`` right now, and only right now.

    The server is another process, and a bound socket cannot be handed to one
    on every platform, so the number is let go of here and bound there a
    moment later. Anything can take it in between; ``_start_server`` notices
    and starts again on another one.
    """
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((bind, 0))
        port = s.getsockname()[1]
        s.close()
        if port not in _CHROMIUM_UNSAFE_PORTS:
            return port


# How many times one server is started before the harness gives up on finding
# it a port. Losing one is rare; losing three in a row is not a race.
_PORT_ATTEMPTS = 3


class _PortLost(Exception):
    """The port given to a server was taken before the server could bind it."""


def _lost_the_port(data_dir: Path, log_path: Path, bind: str, port: int) -> bool:
    """Whether a server that exited did so because its port was taken.

    There are two gaps to lose it in. The start-up check binds the port,
    finds it in use and records ``port_in_use``. Or the check passes and
    releases it, the engine starts, and the web server's own bind fails, which
    records nothing and is only in the log, as asyncio's message naming the
    address and port.
    """
    try:
        record = json.loads(
            (data_dir / "status" / "startup-error.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        record = None
    if isinstance(record, dict) and record.get("error") == "port_in_use":
        return True
    try:
        log = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"error while attempting to bind on address ({bind!r}, {port})" in log


def _is_this_server(base_url: str, project_path: Path) -> bool:
    """Whether the server answering at ``base_url`` is the one started for
    ``project_path``.

    Every OpenAVC server answers ``/api/startup-status`` the same way, so a
    ready answer alone could come from another one that holds the port. Its
    instance id cannot: ``/api/status`` gives it to any caller, and a server
    keeps its id in ``.instance_id`` beside its project, which is in this
    launch's own directory.
    """
    try:
        with urlopen(f"{base_url}/api/status", timeout=5.0) as resp:
            answered = json.loads(resp.read().decode("utf-8")).get("instance_id")
        ours = (project_path.parent / ".instance_id").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return False
    return bool(answered) and answered == ours


def _wait_for_own_server(
    proc: subprocess.Popen,
    base_url: str,
    *,
    bind: str,
    port: int,
    data_dir: Path,
    log_path: Path,
    project_path: Path,
    timeout: float = 30.0,
) -> None:
    """Wait until this server is up on its port.

    Raises ``_PortLost`` when another socket took the port first, and
    ``RuntimeError`` for any other failure: as soon as the process exits, not
    at the timeout.
    """
    deadline = time.monotonic() + timeout
    last_err: str = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            if _lost_the_port(data_dir, log_path, bind, port):
                raise _PortLost(f"port {port} was taken before the server bound it")
            raise RuntimeError(f"the server exited with code {proc.returncode}")
        try:
            with urlopen(f"{base_url}/api/startup-status", timeout=1.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_err = repr(exc)
            time.sleep(0.25)
            continue
        if body.get("error"):
            raise RuntimeError(f"Engine startup error: {body['error']}")
        if body.get("ready"):
            if not _is_this_server(base_url, project_path):
                raise _PortLost(f"another server is answering on port {port}")
            return
        time.sleep(0.25)
    raise RuntimeError(
        f"Server at {base_url} did not become ready within {timeout}s "
        f"(last={last_err})"
    )


def _build_project(
    *,
    initial_children: int,
    device_id: str = "ctrl1",
    device_name: str = "Test Controller",
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The seed project every e2e server boots on.

    ``overrides`` replaces whole top-level sections (``variables``, ``ui``,
    ``macros``, ...) rather than merging into them, so a test that needs a
    panel worth touching writes those sections outright instead of patching
    this one field by field. The device and its driver stay put either way --
    they are what makes the state keys real.
    """
    project: dict[str, Any] = {
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
    if overrides:
        project.update(overrides)
    return project


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

    def _make(
        *,
        initial_children: int = 0,
        project_overrides: dict[str, Any] | None = None,
        bind: str = "127.0.0.1",
        env: dict[str, str] | None = None,
        existing_system: bool = False,
        drivers: dict[str, str] | None = None,
    ) -> _ServerHandle:
        gen = _start_server(
            tmp_path / f"srv-{uuid.uuid4().hex[:8]}",
            initial_children=initial_children,
            project_overrides=project_overrides,
            bind=bind,
            env=env,
            existing_system=existing_system,
            drivers=drivers,
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


def _start_server(
    tmp_root: Path,
    *,
    initial_children: int,
    project_overrides: dict[str, Any] | None = None,
    bind: str = "127.0.0.1",
    env: dict[str, str] | None = None,
    existing_system: bool = False,
    drivers: dict[str, str] | None = None,
):
    """Boot one server. ``bind`` is loopback unless a test needs the instance
    to see the browser as a device on the network (a loopback peer is the
    box's own screen, which the panel gate admits without approval); ``env``
    adds to or overrides the quiet-startup environment below, which is how a
    test sets a password. ``existing_system`` makes the server look like one
    that was already running before this release: the instance id file that
    a first start creates is there before this start, which is what the
    Panel access default reads. ``drivers`` maps file names to driver source
    written into the server's ``driver_repo/`` before it boots, so a project
    device can use one from the start."""
    lost = ""
    for attempt in range(_PORT_ATTEMPTS):
        # A start that lost its port may have run its engine first, and the
        # engine writes beside the project (the instance id among it, which
        # makes the next server there read as one that already existed), so
        # each new start gets an empty directory of its own.
        root = tmp_root if attempt == 0 else tmp_root / f"port-retry-{attempt}"
        try:
            handle, log = _launch(
                root,
                initial_children=initial_children,
                project_overrides=project_overrides,
                bind=bind,
                env=env,
                existing_system=existing_system,
                drivers=drivers,
            )
            break
        except _PortLost as exc:
            lost = str(exc)
    else:
        raise RuntimeError(
            f"Server lost its port {_PORT_ATTEMPTS} times in a row. "
            f"Last start:\n{lost}"
        )

    try:
        yield handle
    finally:
        proc = handle.process
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        log.close()


def _launch(
    root: Path,
    *,
    initial_children: int,
    project_overrides: dict[str, Any] | None,
    bind: str,
    env: dict[str, str] | None,
    existing_system: bool,
    drivers: dict[str, str] | None,
):
    """One start of one server in ``root``. Returns its handle and open log;
    raises ``_PortLost`` or ``RuntimeError`` with the log tail, having stopped
    the process."""
    root.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"
    data_dir.mkdir(exist_ok=True)
    if existing_system:
        # Beside project.avc, where core/isc.get_or_create_instance_id keeps it.
        (root / ".instance_id").write_text(str(uuid.uuid4()), encoding="utf-8")

    # Install the synthetic controller driver into THIS server's own
    # driver_repo (which is where DRIVER_REPO_DIR resolves under the temp
    # data_dir). Pre-populating the target means migrate_legacy_repos()
    # skips it, so the driver is never moved out from under a sibling
    # server — each subprocess in the session is self-contained.
    driver_repo = data_dir / "driver_repo"
    driver_repo.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DRIVER_SRC, driver_repo / INSTALLED_DRIVER_NAME)
    for filename, source in (drivers or {}).items():
        (driver_repo / filename).write_bytes(source.encode("utf-8"))

    project_path = root / "project.avc"
    project_path.write_text(
        json.dumps(
            _build_project(
                initial_children=initial_children,
                overrides=project_overrides,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )

    control_file = root / "control.json"
    control_file.write_text(
        json.dumps({"seq": 1, "operations": []}), encoding="utf-8",
    )

    port = _pick_free_port(bind)
    base_url = f"http://{bind}:{port}"

    env = {
        **os.environ,
        "OPENAVC_PORT": str(port),
        "OPENAVC_BIND": bind,
        "OPENAVC_PROJECT": str(project_path),
        "OPENAVC_DATA_DIR": str(data_dir),
        "OPENAVC_E2E_CONTROL_FILE": str(control_file),
        # Force a quiet startup — no cloud, no kiosk, no auth.
        "OPENAVC_CLOUD_ENABLED": "false",
        "OPENAVC_RATE_LIMIT_ENABLED": "false",
        # Prevent the subprocess from inheriting any parent PYTHONPATH that
        # shadows the openavc source tree.
        "PYTHONUNBUFFERED": "1",
        **(env or {}),
    }
    # PowerShell-launched parents sometimes leave OPENAVC_DATA_DIR pointed at
    # a session-scoped temp from the in-process test suite; force ours through.

    log_path = root / "server.log"
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "openavc.main"],
        cwd=str(OPENAVC_ROOT),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )

    try:
        _wait_for_own_server(
            proc, base_url, bind=bind, port=port, data_dir=data_dir,
            log_path=log_path, project_path=project_path,
        )
    except Exception as exc:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
        log.close()
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            tail = "<log unreadable>"
        if isinstance(exc, _PortLost):
            raise _PortLost(f"{exc}. Log tail:\n{tail}") from None
        raise RuntimeError(
            f"Server failed to start ({exc}). Log tail:\n{tail}"
        ) from None

    handle = _ServerHandle(
        base_url=base_url,
        control_file=control_file,
        device_id="ctrl1",
        data_dir=data_dir,
        project_path=project_path,
        process=proc,
    )
    return handle, log


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
