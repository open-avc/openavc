"""The simulator UI is reachable from another machine, on this server's origin.

The IDE is normally open on a laptop while the server runs on a Pi or a mini
PC in the rack. Anything the server hands the browser has to be meaningful
*there*, and ``http://localhost:19500`` is not: it names the laptop. These
tests pin the shape that fixes it -- a same-origin path, proxied to a
simulator that stays bound to loopback -- and the rule that keeps it fixed.
"""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openavc.api import simulator_proxy

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture()
def client(monkeypatch):
    """The proxy mounted with auth stubbed out; auth itself is tested elsewhere."""
    monkeypatch.setattr(simulator_proxy, "require_programmer_auth", lambda: None)
    app = FastAPI()
    app.include_router(simulator_proxy.router)
    app.include_router(simulator_proxy.open_router)
    app.include_router(simulator_proxy.ws_router)
    return TestClient(app)


def test_browser_is_sent_a_path_not_a_host():
    """The published location must be relative.

    An absolute URL would have to name a host, and the server does not know
    which of its addresses the browser used -- LAN IP, mDNS name, or a cloud
    tunnel domain. A path sidesteps the question entirely.
    """
    path = simulator_proxy.simulator_ui_path()
    assert path.startswith("/")
    assert "://" not in path
    assert "localhost" not in path


def test_bare_mount_redirects_so_relative_assets_resolve(client):
    r = client.get("/simulator", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/simulator/"


def test_a_stopped_simulator_answers_plainly(client, monkeypatch):
    """Not an error: the UI is only useful while the simulator runs, and the
    message has to tell the user how to start it.

    The port is pinned to a closed one rather than trusting 19500 to be idle --
    a developer with a simulator actually running would otherwise reach it and
    watch this fail for no reason.
    """
    monkeypatch.setattr(simulator_proxy, "active_simulator_ui_port", lambda: 9)
    r = client.get("/simulator/")
    assert r.status_code == 503
    assert "not running" in r.text.lower()
    assert "start" in r.text.lower()


def test_the_proxy_target_is_loopback_only():
    """The simulator's control API is unauthenticated -- it can mutate device
    state and stop the process. It is reachable only because this server, which
    does require a credential, is the one forwarding. If the target ever became
    a non-loopback address, that API would be exposed directly."""
    assert re.match(r"^http://127\.0\.0\.1:\d+/", simulator_proxy._target("anything"))


def test_every_published_ui_url_is_a_path():
    """Both endpoints that hand the button a location must hand it a path.

    `start` and `status` feed the same button, and fixing only `start` left
    `status` returning the loopback address the server polls itself on --
    caught on real hardware after the first fix looked complete. Assert the
    published value, not the file it came from.
    """
    from openavc.core.simulation import SimulationManager

    mgr = object.__new__(SimulationManager)
    mgr._active = True
    mgr._starting = False
    mgr._sim_ui_url = "http://127.0.0.1:19500"  # correct for internal polling
    mgr._sim_ports = {}
    mgr._process = None

    published = mgr.status()["ui_url"]
    assert published == "/simulator/"
    assert "://" not in published, (
        f"status() published {published!r}; a browser on another machine "
        "cannot follow an address that names this one"
    )

    # The internal address is untouched -- the server still needs it.
    assert mgr._sim_ui_url == "http://127.0.0.1:19500"


def test_no_browser_facing_url_names_localhost():
    """The regression guard for this whole class of bug.

    `localhost` in a string the SERVER computes and the BROWSER follows means
    "the machine running the browser", which is only the same machine in
    development. This is the check that was missing when the simulator button
    shipped pointing at localhost:19500 -- so it scans the places that hand a
    location to the front end, rather than trusting review to notice.
    """
    offenders = []
    watched = [
        REPO_ROOT / "openavc" / "core" / "simulation.py",
        REPO_ROOT / "openavc" / "api" / "simulator_proxy.py",
        REPO_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "layout" / "Sidebar.tsx",
    ]
    # A scheme makes it a destination rather than a mention: `http://localhost`
    # is something a browser would follow, "works on localhost" is prose. Only
    # the first kind can send a laptop to itself.
    destination = re.compile(r"(https?|wss?)://localhost")
    # The server talking to its own subprocess over loopback is correct and stays.
    internal = re.compile(r"_sim_ui_url|_target\(|sim_api")
    # ``http://localhost:19500`` in double backticks is RST literal markup --
    # the docs explaining this very bug have to be able to name it. A real
    # destination is in quotes, or in a single-backtick template literal.
    quoted = re.compile(r"``[^`]*(https?|wss?)://localhost[^`]*``")
    for path in watched:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not destination.search(line):
                continue
            if line.lstrip().startswith(("#", "//", "*")) or internal.search(line):
                continue
            if quoted.search(line):
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "A browser-facing location names localhost, which is the user's own "
        "machine whenever the IDE is open from anywhere but the server:\n  "
        + "\n  ".join(offenders)
    )


def test_the_shell_is_open_and_the_control_api_is_not():
    """The split that removes the browser's own dialog without exposing anything.

    A new window has no credential. If the SHELL required one, the 401 would
    land on a top-level navigation and Chrome would answer it with its native
    sign-in prompt, over our app -- the exact popup this change exists to
    remove. So the shell is open and every action behind it is not.
    """
    shell_auth = [d for d in simulator_proxy.open_router.routes[0].dependencies]
    assert not shell_auth, "the shell must not require a credential"

    api_deps = simulator_proxy.router.dependencies
    assert api_deps, "the simulator control API must require a credential"

    # And the split is by path, declared API-first so it wins the match.
    paths = [r.path for r in simulator_proxy.router.routes]
    assert any("/api/" in p for p in paths), paths


def test_the_open_shell_is_read_only():
    """Open plus writable would be a hole. The shell serves files; anything
    that changes state is on the authenticated router."""
    for route in simulator_proxy.open_router.routes:
        assert set(route.methods) <= {"GET", "HEAD"}, (
            f"{route.path} is served without a credential and accepts {route.methods}"
        )
