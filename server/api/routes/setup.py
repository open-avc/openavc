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

The page auto-switches to /panel as soon as the loaded project has panel
content, so an appliance display flips from setup screen to room panel the
moment the integrator's UI arrives — live, no reboot, no kiosk toggle.
Open /setup?stay=1 to read the screen without being redirected.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasicCredentials

from server import host_control, runtime_flags
from server.api._engine import _get_engine
from server.api.auth import (
    _basic,
    auth_state,
    is_claimed,
    is_loopback_request,
    programmer_auth_satisfied,
)
from server.system_config import get_system_config
from server.version import __version__

router = APIRouter()
open_router = APIRouter()

# ssh_status() shells out to systemctl on a Pi; don't do that for every
# 3-second poll from the kiosk. Cached with a short TTL.
_SSH_CACHE_TTL = 10.0
_ssh_cache: tuple[float, dict] | None = None


def _ssh_info() -> dict:
    global _ssh_cache
    now = time.monotonic()
    if _ssh_cache is not None and now - _ssh_cache[0] < _SSH_CACHE_TTL:
        return _ssh_cache[1]
    info = host_control.ssh_status()
    _ssh_cache = (now, info)
    return info


def _effective_endpoint() -> tuple[str, int]:
    """Return (protocol, port) for the URLs shown on the setup screen.

    The setup screen's URL is read off a display and typed by hand, so prefer
    the form that is shortest to type and lands best. When TLS is on, its
    HTTP redirect listener upgrades a typed http URL to HTTPS — and to the
    certified hostname when a trusted certificate is active — so the http
    form is both shorter and lands with no browser warning whenever that is
    possible. Only when the redirect listener is disabled is the direct HTTPS
    URL the one to show. The port-80 listener (when actually bound — it is
    best-effort) lets the URL drop the port entirely.
    """
    cfg = get_system_config()
    tls_on = bool(cfg.get("tls", "enabled", False))
    if tls_on and not bool(cfg.get("tls", "redirect_http", True)):
        return "https", int(cfg.get("tls", "port", 8443))
    if runtime_flags.port80_active:
        return "http", 80
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

    if not (is_loopback_request(request) or programmer_auth_satisfied(request, credentials)):
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
  .netcfg-toggle {
    background: none; border: 1px solid #2a2a4a; border-radius: 10px;
    color: #8AB493; font-size: 0.8rem; font-family: inherit;
    padding: 0.6rem 1.25rem; width: 100%; cursor: pointer; margin-bottom: 1rem;
  }
  .iface-block { padding: 0.5rem 0; border-bottom: 1px solid #2a2a4a; }
  .iface-block:last-child { border-bottom: none; }
  .iface-title { font-size: 0.8rem; color: #fff; font-weight: 600; margin-bottom: 0.4rem; }
  .iface-state { color: #888; font-weight: 400; }
  .net-form { display: grid; grid-template-columns: 7.5rem 1fr; gap: 0.45rem 0.75rem; align-items: center; margin: 0.5rem 0; }
  .net-form label { color: #888; font-size: 0.8rem; }
  .net-form input[type=text], .net-form input[type=password] {
    background: #1a1a2e; border: 1px solid #2a2a4a; border-radius: 6px;
    color: #fff; font-family: 'SF Mono', 'Consolas', monospace; font-size: 0.8rem;
    padding: 0.45rem 0.6rem; width: 100%;
  }
  .net-form input[disabled] { opacity: 0.4; }
  .net-form .radio-row { display: flex; gap: 1.25rem; color: #ccc; font-size: 0.8rem; }
  .net-btn {
    background: #8AB493; border: none; border-radius: 8px; color: #fff;
    font-size: 0.8rem; font-family: inherit; font-weight: 500;
    padding: 0.5rem 1.25rem; cursor: pointer;
  }
  .net-btn.secondary { background: #2a2a4a; color: #ccc; }
  .net-btn[disabled] { opacity: 0.5; cursor: default; }
  .net-msg { font-size: 0.75rem; line-height: 1.4; margin-top: 0.5rem; }
  .net-msg.ok { color: #8AB493; }
  .net-msg.warn { color: #d0b070; }
  .net-msg.err { color: #d08080; }
  .wifi-list { list-style: none; margin-top: 0.5rem; }
  .wifi-list li {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.5rem 0.25rem; border-bottom: 1px solid #2a2a4a;
    color: #fff; font-size: 0.8rem; cursor: pointer;
  }
  .wifi-list li:last-child { border-bottom: none; }
  .wifi-meta { color: #888; font-size: 0.75rem; font-family: 'SF Mono', 'Consolas', monospace; }
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

  <button class="netcfg-toggle" id="netcfg-toggle" hidden>Network Settings</button>

  <div class="card" id="netcfg-card" hidden>
    <h2>Network Settings</h2>
    <div id="netcfg-interfaces"></div>
    <div class="net-msg" id="netcfg-msg"></div>
    <div id="netcfg-wifi" hidden>
      <div class="iface-title" style="margin-top:0.75rem">WiFi</div>
      <label style="display:flex;align-items:center;gap:0.5rem;color:#ccc;font-size:0.85rem;margin-bottom:0.5rem">
        <input type="checkbox" id="wifi-radio"> Enable WiFi
      </label>
      <div id="wifi-controls" hidden>
        <button class="net-btn secondary" id="wifi-scan">Scan for Networks</button>
        <ul class="wifi-list" id="wifi-list"></ul>
        <div class="net-form" id="wifi-join" hidden>
          <label id="wifi-join-label">Password</label>
          <input type="password" id="wifi-psk" autocomplete="off">
          <span></span>
          <span><button class="net-btn" id="wifi-connect">Connect</button>
          <button class="net-btn secondary" id="wifi-cancel">Cancel</button></span>
        </div>
        <div class="net-msg" id="wifi-msg"></div>
      </div>
      <div class="net-msg" id="wifi-radio-msg"></div>
    </div>
  </div>

  <div class="hint" id="hint" hidden>
    <p>This display will switch to the <strong>Panel UI</strong> automatically<br>once a project with panel content is loaded.</p>
  </div>

  <div class="version" id="version"></div>
</div>

<script>
(function () {
  'use strict';
  var failures = 0;
  // ?stay=1 disables the auto-switch to /panel (for reading device info
  // while a project is running).
  var stayHere = /[?&]stay=1/.test(window.location.search);

  function text(id, value) { document.getElementById(id).textContent = value; }
  function show(id, visible) { document.getElementById(id).hidden = !visible; }

  function render(s) {
    document.body.classList.remove('server-offline');

    if (s.panel_has_content && !stayHere) {
      window.location.replace('/panel');
      return;
    }

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

    if (net && netAvailable === null) probeNetcfg();
  }

  // --- Network settings (on-device configuration) ---
  // Shown only when /api/system/network answers (a host network backend
  // exists AND this caller is loopback or authenticated). This is how an
  // appliance gets onto a network it isn't on yet: static IP or WiFi
  // credentials entered on the device's own screen.
  var netAvailable = null;
  var wifiPick = null;

  function el(tag, cls, textValue) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (textValue !== undefined) node.textContent = textValue;
    return node;
  }

  function probeNetcfg() {
    netAvailable = false;
    fetch('/api/system/network', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        netAvailable = true;
        show('netcfg-toggle', true);
        renderNetcfg(data);
      })
      .catch(function () { netAvailable = false; });
  }

  function refreshNetcfg() {
    fetch('/api/system/network', { cache: 'no-store' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(renderNetcfg)
      .catch(function () {});
  }

  function renderNetcfg(data) {
    var container = document.getElementById('netcfg-interfaces');
    container.textContent = '';
    (data.interfaces || []).forEach(function (iface) {
      var block = el('div', 'iface-block');
      var title = el('div', 'iface-title',
        iface.device + ' (' + iface.type + ') ');
      title.appendChild(el('span', 'iface-state', iface.state +
        (iface.ip4 && iface.ip4.addresses.length ? ' · ' + iface.ip4.addresses.join(', ') : '')));
      block.appendChild(title);

      if (iface.type === 'ethernet' && !(data.capabilities && data.capabilities.ipv4 === false)) {
        if (!iface.connection) {
          // No editable profile — only blame the cable when the link is
          // actually down.
          var linkUp = iface.state === 'connected' ||
            iface.state.indexOf('connecting') === 0 ||
            !!(iface.ip4 && iface.ip4.addresses.length);
          block.appendChild(el('div', 'net-msg', linkUp
            ? iface.device + ' is connected, but its settings cannot be edited from this screen.'
            : 'No connection profile. Connect a cable and it will appear here.'));
        } else {
          block.appendChild(buildIpv4Form(iface, data.capabilities || {}));
        }
      }
      container.appendChild(block);
    });
    var hasWifi = !!(data.capabilities && data.capabilities.wifi);
    show('netcfg-wifi', hasWifi);
    if (hasWifi) {
      document.getElementById('wifi-radio').checked = data.wifi_enabled === true;
      show('wifi-controls', data.wifi_enabled !== false);
    }
  }

  function buildIpv4Form(iface, caps) {
    var cfg = iface.config || { method: 'auto', addresses: [], gateway: null, dns: [] };
    var form = el('div');
    var radios = el('div', 'net-form');
    var radioRow = el('div', 'radio-row');
    var name = 'method-' + iface.device;

    function radio(value, labelText, checked) {
      var lab = el('label');
      var input = document.createElement('input');
      input.type = 'radio'; input.name = name; input.value = value;
      input.checked = checked;
      lab.appendChild(input);
      lab.appendChild(document.createTextNode(' ' + labelText));
      return lab;
    }
    radioRow.appendChild(radio('auto', 'Automatic (DHCP)', cfg.method !== 'manual'));
    radioRow.appendChild(radio('manual', 'Static IP', cfg.method === 'manual'));
    radios.appendChild(el('label', null, 'Address mode'));
    radios.appendChild(radioRow);
    form.appendChild(radios);

    var fields = el('div', 'net-form');
    function input(labelText, value, placeholder) {
      fields.appendChild(el('label', null, labelText));
      var node = document.createElement('input');
      node.type = 'text'; node.value = value || ''; node.placeholder = placeholder || '';
      node.autocapitalize = 'off'; node.autocomplete = 'off';
      fields.appendChild(node);
      return node;
    }
    var addr = input('Address', cfg.addresses[0] || (iface.ip4 && iface.ip4.addresses[0]) || '', '192.168.1.50/24');
    var gw = input('Gateway', cfg.gateway || (iface.ip4 && iface.ip4.gateway) || '', '192.168.1.1');
    var dns = input('DNS', (cfg.dns && cfg.dns.length ? cfg.dns : (iface.ip4 ? iface.ip4.dns : [])).join(', '), '8.8.8.8, 1.1.1.1');
    form.appendChild(fields);

    var staticOnly = [addr, gw, dns];
    function syncFields() {
      var manual = form.querySelector('input[value=manual]').checked;
      staticOnly.forEach(function (f) { f.disabled = !manual; });
    }
    form.addEventListener('change', syncFields);
    syncFields();

    var applyBtn = el('button', 'net-btn', 'Apply');
    var msg = el('div', 'net-msg');
    form.appendChild(applyBtn);
    if (caps.ipv4_apply === 'reboot') {
      form.appendChild(el('div', 'net-msg', 'Applying a change restarts the device.'));
    }
    form.appendChild(msg);

    var pendingConfirm = false;
    applyBtn.addEventListener('click', function () {
      var manual = form.querySelector('input[value=manual]').checked;
      var body = {
        connection: iface.connection,
        method: manual ? 'manual' : 'auto',
        address: addr.value.trim() || null,
        gateway: gw.value.trim() || null,
        dns: dns.value.split(',').map(function (s) { return s.trim(); }).filter(Boolean),
        confirmed: pendingConfirm
      };
      applyBtn.disabled = true;
      msg.className = 'net-msg';
      msg.textContent = pendingConfirm ? 'Applying…' : 'Checking…';
      fetch('/api/system/network/ipv4', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
        .then(function (resp) {
          applyBtn.disabled = false;
          if (!resp.ok) {
            msg.className = 'net-msg err';
            msg.textContent = resp.body.detail || 'Invalid configuration.';
            pendingConfirm = false; applyBtn.textContent = 'Apply';
            return;
          }
          var b = resp.body;
          if (!pendingConfirm) {
            if (b.warnings && b.warnings.length) {
              msg.className = 'net-msg warn';
              msg.textContent = b.warnings.join(' ') + ' Tap again to apply anyway.';
              pendingConfirm = true; applyBtn.textContent = 'Apply Anyway';
              return;
            }
            pendingConfirm = true;
            applyBtn.click();
            return;
          }
          pendingConfirm = false; applyBtn.textContent = 'Apply';
          // The interface list re-renders after an apply, so the result
          // goes to the shared status line that survives the refresh.
          var shared = document.getElementById('netcfg-msg');
          if (b.ok && b.reboot) {
            // Saved to the boot configuration; the device is restarting.
            // Skip the refresh — the server is going down with it, and
            // this screen recovers on its own after the boot.
            shared.className = 'net-msg ok';
            shared.textContent = iface.device + ': saved. The device is restarting to apply the new settings.';
            return;
          }
          if (b.ok) {
            shared.className = 'net-msg ok';
            shared.textContent = iface.device + ': applied.';
          } else if (b.rolled_back) {
            shared.className = 'net-msg err';
            shared.textContent = iface.device + ': change failed and the previous settings were restored: ' + (b.error || '');
          } else {
            shared.className = 'net-msg err';
            shared.textContent = iface.device + ': ' + (b.error || 'failed to apply.');
          }
          refreshNetcfg();
        })
        .catch(function () {
          applyBtn.disabled = false;
          pendingConfirm = false; applyBtn.textContent = 'Apply';
          msg.className = 'net-msg err';
          msg.textContent = 'Request failed. If the address changed, this screen will recover on its own.';
          refreshNetcfg();
        });
    });
    return form;
  }

  document.getElementById('netcfg-toggle').addEventListener('click', function () {
    var card = document.getElementById('netcfg-card');
    card.hidden = !card.hidden;
    if (!card.hidden) refreshNetcfg();
  });

  document.getElementById('wifi-scan').addEventListener('click', function () {
    var btn = document.getElementById('wifi-scan');
    var msgEl = document.getElementById('wifi-msg');
    btn.disabled = true; btn.textContent = 'Scanning…';
    msgEl.className = 'net-msg'; msgEl.textContent = '';
    fetch('/api/system/network/wifi/scan', { method: 'POST' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (data) {
        btn.disabled = false; btn.textContent = 'Scan for Networks';
        var list = document.getElementById('wifi-list');
        list.textContent = '';
        (data.networks || []).forEach(function (n) {
          var item = el('li');
          item.appendChild(el('span', null, (n.in_use ? '✓ ' : '') + n.ssid));
          item.appendChild(el('span', 'wifi-meta',
            n.signal + '%' + (n.secured ? ' 🔒' : '')));
          item.addEventListener('click', function () { pickWifi(n); });
          list.appendChild(item);
        });
        if (!(data.networks || []).length) {
          msgEl.textContent = 'No networks found.';
        }
      })
      .catch(function () {
        btn.disabled = false; btn.textContent = 'Scan for Networks';
        msgEl.className = 'net-msg err';
        msgEl.textContent = 'Scan failed.';
      });
  });

  function pickWifi(n) {
    wifiPick = n;
    var join = document.getElementById('wifi-join');
    if (n.secured) {
      join.hidden = false;
      text('wifi-join-label', 'Password for ' + n.ssid);
      document.getElementById('wifi-psk').value = '';
      document.getElementById('wifi-psk').focus();
    } else {
      join.hidden = true;
      connectWifi();
    }
  }

  function connectWifi() {
    if (!wifiPick) return;
    var msgEl = document.getElementById('wifi-msg');
    var btn = document.getElementById('wifi-connect');
    btn.disabled = true;
    msgEl.className = 'net-msg';
    msgEl.textContent = 'Connecting to ' + wifiPick.ssid + '…';
    fetch('/api/system/network/wifi/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid: wifiPick.ssid, psk: document.getElementById('wifi-psk').value || null })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, body: j }; }); })
      .then(function (resp) {
        btn.disabled = false;
        if (resp.ok && resp.body.ok) {
          msgEl.className = 'net-msg ok';
          msgEl.textContent = 'Connected to ' + wifiPick.ssid + '.';
          document.getElementById('wifi-join').hidden = true;
          refreshNetcfg();
        } else {
          msgEl.className = 'net-msg err';
          msgEl.textContent = resp.body.error || resp.body.detail || 'Connection failed.';
        }
      })
      .catch(function () {
        btn.disabled = false;
        msgEl.className = 'net-msg err';
        msgEl.textContent = 'Connection failed.';
      });
  }

  document.getElementById('wifi-connect').addEventListener('click', connectWifi);
  document.getElementById('wifi-cancel').addEventListener('click', function () {
    document.getElementById('wifi-join').hidden = true;
    wifiPick = null;
  });

  document.getElementById('wifi-radio').addEventListener('change', function () {
    var box = this;
    var enabled = box.checked;
    var msgEl = document.getElementById('wifi-radio-msg');
    box.disabled = true;
    msgEl.className = 'net-msg';
    msgEl.textContent = enabled ? 'Turning WiFi on…' : 'Turning WiFi off…';
    show('wifi-controls', enabled);
    fetch('/api/system/network/wifi/radio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error('http')); })
      .then(function (d) {
        box.disabled = false;
        if (d && d.ok === false) {
          box.checked = !enabled;
          show('wifi-controls', !enabled);
          msgEl.className = 'net-msg err';
          msgEl.textContent = d.error || 'Could not change WiFi.';
          return;
        }
        msgEl.textContent = '';
        setTimeout(refreshNetcfg, 800);
      })
      .catch(function () {
        box.disabled = false;
        box.checked = !enabled;
        show('wifi-controls', !enabled);
        msgEl.className = 'net-msg err';
        msgEl.textContent = 'Could not change WiFi.';
      });
  });

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
