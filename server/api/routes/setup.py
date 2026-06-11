"""Device setup screen.

Served at GET /setup (no /api prefix). This is the screen an appliance
deployment (Pi image kiosk, dedicated panel) shows on its own display until
the device is programmed: connection info, the Programmer URL, and the
first-run claim instructions. It replaces the static info page the Pi kiosk
script used to generate — the data here is live, so plugging in an ethernet
cable or claiming the instance updates the screen without a reboot.

The page polls GET /api/setup/status. Host/network identifiers (IP, hostname,
URLs) follow the same disclosure rule as /api/status — authenticated callers
get them, anonymous remote callers on a claimed instance do not — with one
addition: loopback callers always get them, because a loopback caller is the
device's own kiosk browser rendering the device's own address.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasicCredentials

from server import host_control
from server.api._engine import _get_engine
from server.api.auth import _basic, auth_state, is_claimed, programmer_auth_satisfied
from server.system_config import get_system_config
from server.version import __version__

router = APIRouter()
open_router = APIRouter()

# ssh_status() shells out to systemctl on a Pi; don't do that for every
# 3-second poll from the kiosk. Cached with a short TTL.
_SSH_CACHE_TTL = 10.0
_ssh_cache: tuple[float, dict] | None = None


def _is_loopback(request: Request) -> bool:
    client = request.client
    return client is not None and client.host in ("127.0.0.1", "::1", "localhost")


def _ssh_info() -> dict:
    global _ssh_cache
    now = time.monotonic()
    if _ssh_cache is not None and now - _ssh_cache[0] < _SSH_CACHE_TTL:
        return _ssh_cache[1]
    info = host_control.ssh_status()
    _ssh_cache = (now, info)
    return info


def _effective_endpoint() -> tuple[str, int]:
    """Return (protocol, port) the way the Pi kiosk script resolves them:
    the HTTPS port when TLS is on, the HTTP port otherwise."""
    cfg = get_system_config()
    if bool(cfg.get("tls", "enabled", False)):
        return "https", int(cfg.get("tls", "port", 8443))
    return "http", int(cfg.get("network", "http_port", 8080))


def _base_url(proto: str, host: str, port: int) -> str:
    if (proto == "http" and port == 80) or (proto == "https" and port == 443):
        return f"{proto}://{host}"
    return f"{proto}://{host}:{port}"


def _panel_has_content(engine) -> bool:
    """Whether the loaded project has anything for the panel to show.

    This is the signal an appliance display uses to decide between the setup
    screen and the panel: at least one UI element on a page (or a master
    element). The shipped seed project has a single empty page, so a fresh
    appliance stays on the setup screen until the integrator builds UI.
    """
    project = getattr(engine, "project", None)
    if project is None:
        return False
    ui = getattr(project, "ui", None)
    if ui is None:
        return False
    if any(page.elements for page in ui.pages):
        return True
    return bool(ui.master_elements)


@open_router.get("/setup/status")
async def setup_status(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> dict[str, Any]:
    """Live data for the device setup screen.

    Open so the kiosk browser can poll it before the instance is claimed.
    The ``network`` block (IP, hostname, access URLs, SSH state) is included
    only for loopback or authenticated callers — same anti-reconnaissance
    posture as /api/status.
    """
    engine = _get_engine()
    state = auth_state()

    project_name = None
    if engine.project is not None:
        project_name = engine.project.project.name

    payload: dict[str, Any] = {
        "state": state,
        "claimed": is_claimed(),
        "project_name": project_name,
        "panel_has_content": _panel_has_content(engine),
        "version": __version__,
        "network": None,
    }

    if not (_is_loopback(request) or programmer_auth_satisfied(request, credentials)):
        return payload

    # Re-detect rather than serve the startup cache: a device that boots
    # before its network is up must show the address as soon as the cable
    # goes in. Detection blocks (route lookup + gethostname), so off-loop.
    local_ip, hostname = await asyncio.to_thread(engine.refresh_network_info)
    online = local_ip != "127.0.0.1"
    proto, port = _effective_endpoint()

    # Prefer the routable IP; fall back to mDNS when there is a usable
    # hostname. "localhost" means the host has no real name (e.g. a chroot).
    mdns_name = hostname if hostname and hostname != "localhost" else None
    if online:
        base = _base_url(proto, local_ip, port)
    elif mdns_name:
        base = _base_url(proto, f"{mdns_name}.local", port)
    else:
        base = None

    payload["network"] = {
        "online": online,
        "ip": local_ip if online else None,
        "hostname": mdns_name,
        "protocol": proto,
        "port": port,
        "programmer_url": f"{base}/programmer" if base else None,
        "panel_url": f"{base}/panel" if base else None,
        "ssh": await asyncio.to_thread(_ssh_info),
    }
    return payload


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#1a1a2e">
<title>OpenAVC Setup</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center;
    padding: 1.5rem;
    -webkit-font-smoothing: antialiased;
  }
  .container { width: 100%; max-width: 560px; text-align: center; }
  .logo {
    font-size: 2rem; font-weight: 700; color: #8AB493;
    margin-bottom: 0.15rem; letter-spacing: -0.5px;
  }
  .subtitle { font-size: 0.85rem; color: #888; margin-bottom: 1.5rem; }
  .headline { font-size: 1.15rem; font-weight: 600; color: #fff; margin-bottom: 0.5rem; }
  .lede { font-size: 0.85rem; color: #aaa; line-height: 1.5; margin-bottom: 1.25rem; }
  .lede strong { color: #8AB493; font-family: 'SF Mono', 'Consolas', 'Liberation Mono', monospace; }
  .card {
    background: #16213e; border: 1px solid #2a2a4a; border-radius: 10px;
    padding: 1rem 1.25rem; margin-bottom: 1rem; text-align: left;
  }
  .card h2 {
    font-size: 0.75rem; text-transform: uppercase; letter-spacing: 1px;
    color: #8AB493; margin-bottom: 0.75rem;
  }
  .field {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 0.4rem 0; border-bottom: 1px solid #2a2a4a;
  }
  .field:last-child { border-bottom: none; }
  .label { color: #888; font-size: 0.8rem; flex-shrink: 0; margin-right: 1rem; }
  .value {
    font-family: 'SF Mono', 'Consolas', 'Liberation Mono', monospace;
    font-size: 0.8rem; color: #fff; text-align: right; word-break: break-all;
  }
  .btn {
    display: inline-block; padding: 14px 40px; border-radius: 12px;
    font-size: 1rem; font-weight: 500; text-decoration: none;
    background: #8AB493; color: #fff; margin-bottom: 1rem;
  }
  .hint {
    background: #1a2340; border: 1px solid #2a2a4a; border-radius: 10px;
    padding: 1rem 1.25rem; text-align: center;
  }
  .hint p { color: #888; font-size: 0.8rem; line-height: 1.4; }
  .hint strong { color: #8AB493; }
  .version { margin-top: 1.25rem; font-size: 0.7rem; color: #555; }
  .offline-banner {
    display: none;
    background: #3a2a2a; border: 1px solid #5a3a3a; border-radius: 10px;
    color: #d0a0a0; font-size: 0.8rem; padding: 0.75rem 1rem; margin-bottom: 1rem;
  }
  body.server-offline .offline-banner { display: block; }
  body.server-offline .card, body.server-offline .hint { opacity: 0.5; }
  [hidden] { display: none !important; }
</style>
</head>
<body>
<div class="container">
  <div class="logo">OpenAVC</div>
  <div class="subtitle">Room Control System</div>

  <div class="offline-banner">Waiting for the OpenAVC server&hellip; this screen will recover automatically.</div>

  <div class="headline" id="headline">Starting&hellip;</div>
  <p class="lede" id="lede"></p>
  <a class="btn" id="open-panel" href="/panel" hidden>Open Panel</a>

  <div class="card" id="network-card" hidden>
    <h2>Network</h2>
    <div class="field" id="row-ip"><span class="label">IP Address</span><span class="value" id="net-ip"></span></div>
    <div class="field" id="row-host" hidden><span class="label">Hostname</span><span class="value" id="net-host"></span></div>
    <div class="field"><span class="label">Port</span><span class="value" id="net-port"></span></div>
  </div>

  <div class="card" id="access-card" hidden>
    <h2>Access From Another Computer</h2>
    <div class="field"><span class="label">Programmer</span><span class="value" id="url-programmer"></span></div>
    <div class="field"><span class="label">Panel</span><span class="value" id="url-panel"></span></div>
    <div class="field" id="row-ssh" hidden><span class="label">SSH</span><span class="value" id="ssh-state"></span></div>
  </div>

  <div class="hint" id="hint" hidden>
    <p>To show the <strong>Panel UI</strong> on this display, enable <strong>Kiosk Mode</strong><br>in the Programmer under Settings.</p>
  </div>

  <div class="version" id="version"></div>
</div>

<script>
(function () {
  'use strict';
  var failures = 0;

  function text(id, value) { document.getElementById(id).textContent = value; }
  function show(id, visible) { document.getElementById(id).hidden = !visible; }

  function render(s) {
    document.body.classList.remove('server-offline');

    if (s.state === 'setup') {
      text('headline', 'Set up this controller');
    } else if (s.panel_has_content) {
      text('headline', s.project_name ? '"' + s.project_name + '" is running' : 'Project is running');
    } else {
      text('headline', 'Ready to program');
    }
    show('open-panel', !!s.panel_has_content);
    show('hint', !s.panel_has_content);

    var net = s.network;
    show('network-card', !!net);
    show('access-card', !!(net && net.programmer_url));

    var lede = document.getElementById('lede');
    if (!net) {
      lede.textContent = 'Connection details are shown on the device itself, or sign in to view them here.';
    } else if (!net.online && !net.programmer_url) {
      lede.textContent = 'No network connection detected. Connect an Ethernet cable. This screen updates automatically.';
    } else {
      var url = net.programmer_url;
      if (s.state === 'setup') {
        lede.innerHTML = 'From a laptop on the same network, open <strong></strong> and create the admin password to claim this controller.';
        lede.querySelector('strong').textContent = url;
      } else if (!s.panel_has_content) {
        lede.innerHTML = 'From a laptop on the same network, open <strong></strong> and sign in to program this controller.';
        lede.querySelector('strong').textContent = url;
      } else {
        lede.textContent = '';
      }
    }

    if (net) {
      show('row-ip', true);
      text('net-ip', net.online ? net.ip : 'No connection detected');
      show('row-host', !!net.hostname);
      if (net.hostname) text('net-host', net.hostname + '.local');
      text('net-port', String(net.port));
      if (net.programmer_url) {
        text('url-programmer', net.programmer_url);
        text('url-panel', net.panel_url);
      }
      var ssh = net.ssh || {};
      show('row-ssh', !!ssh.supported);
      if (ssh.supported) {
        text('ssh-state', ssh.enabled ? (net.hostname ? 'ssh openavc@' + net.hostname + '.local' : 'On') : 'Off (enable in Settings > Security)');
      }
    }

    text('version', s.version ? 'v' + s.version : '');
  }

  function tick() {
    fetch('/api/setup/status', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (s) { failures = 0; render(s); })
      .catch(function () {
        failures += 1;
        if (failures >= 3) document.body.classList.add('server-offline');
      });
  }

  tick();
  setInterval(tick, 3000);
})();
</script>
</body>
</html>
"""


@router.get("/setup", response_class=HTMLResponse)
async def setup_page() -> HTMLResponse:
    """The device setup screen. All live data arrives via /api/setup/status,
    so the page itself is static and renders even while the engine is busy."""
    return HTMLResponse(_PAGE)
