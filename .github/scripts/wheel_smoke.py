#!/usr/bin/env python3
"""Smoke test: the built wheel bundles the web UI, themes and starter project, and serves them.

Run from a *clean* venv that has ONLY the installed openavc wheel (no source
tree on the path, no test deps) — see the `wheel-smoke` CI job. Stdlib only, so
it runs against the wheel's runtime dependencies alone.

Guards a real failure mode: a wheel built from a tree that was never
`npm run build`-ed installs fine but ships an empty or absent web UI. The static
checks catch a missing bundle; the live check confirms the installed package can
actually start, load the starter project on a first run, and serve /programmer,
/panel, and the API. A wheel without the starter project starts an empty
"Recovery Project" instead.

IMPORTANT: this must import `openavc` from the installed wheel, not the source
tree. It lives under .github/scripts/ (not the repo root) so Python's script-dir
entry on sys.path does not shadow the wheel with the checkout's openavc/ package.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def fail(msg: str) -> None:
    print(f"WHEEL SMOKE FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


# --- 1. Static: the wheel bundles the UI + themes, and the package imports ---

from openavc.system_config import (  # noqa: E402 — imported here to prove the wheel resolves it
    THEMES_DIR,
    WEB_PANEL_DIR,
    WEB_PROGRAMMER_DIR,
    get_seed_project_path,
)

prog_index = WEB_PROGRAMMER_DIR / "index.html"
if not prog_index.is_file():
    fail(f"programmer UI missing from wheel: {prog_index} not found "
         "(was the wheel built without `npm run build`?)")
if "assets/" not in prog_index.read_text(encoding="utf-8"):
    fail("programmer index.html has no built-asset reference — stale or empty build")
if not (WEB_PANEL_DIR / "panel.js").is_file():
    fail(f"panel UI missing from wheel: {WEB_PANEL_DIR / 'panel.js'} not found")
if not any(THEMES_DIR.glob("*.json")):
    fail(f"no themes bundled in wheel under {THEMES_DIR}")
seed = get_seed_project_path()
if seed is None:
    fail("no starter project in the wheel: a first run would start an empty Recovery Project")
seed_name = json.loads(seed.read_text(encoding="utf-8"))["project"]["name"]

import openavc.main  # noqa: E402,F401 — proves the package imports from the wheel

print(f"static OK: programmer UI + panel + themes + starter project ({seed}) bundled; openavc.main imports")


# --- 2. Live: start the server from the wheel and confirm it SERVES the UI ---

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


port = _free_port()
base = f"http://127.0.0.1:{port}"
env = dict(os.environ, OPENAVC_BIND="127.0.0.1", OPENAVC_PORT=str(port))
openavc_bin = Path(sys.executable).with_name("openavc")
proc = subprocess.Popen([str(openavc_bin)], env=env)


def _get(path: str, timeout: float = 5.0):
    return urllib.request.urlopen(base + path, timeout=timeout)


try:
    # The startup splash middleware 503s UI routes until the engine is ready,
    # so wait on /api/startup-status before checking the UI.
    deadline = time.time() + 90
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            fail(f"server exited during startup with code {proc.returncode}")
        try:
            with _get("/api/startup-status") as r:
                if json.load(r).get("ready"):
                    ready = True
                    break
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(1)
    if not ready:
        fail("server never became ready within 90s")

    with _get("/programmer/") as r:
        body = r.read().decode("utf-8", "replace")
        if r.status != 200 or 'id="root"' not in body or "assets/" not in body:
            fail(f"/programmer did not serve the built UI (status {r.status})")

    with _get("/panel/panel.js") as r:
        if r.status != 200:
            fail(f"/panel/panel.js not served (status {r.status})")

    with _get("/api/health") as r:
        if r.status != 200:
            fail(f"/api/health not 200 (status {r.status})")

    # A first run on an empty data folder starts from the starter project. Read
    # from the file the server wrote: the project route needs a credential.
    from openavc.config import PROJECT_PATH

    loaded = json.loads(Path(PROJECT_PATH).read_text(encoding="utf-8"))["project"]["name"]
    if loaded != seed_name:
        fail(f"a first run started {loaded!r}, not the starter project {seed_name!r}")

    print(f"live OK: server on :{port} loads the starter project and serves /programmer, /panel, /api/health")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

print("WHEEL SMOKE OK")
