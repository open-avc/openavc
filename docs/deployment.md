# Deployment Guide

How to deploy OpenAVC to production hardware for your AV spaces.

> For initial setup and testing, see [Getting Started](getting-started.md). This guide covers deploying to production.

## Where OpenAVC Runs

OpenAVC runs on any hardware with Python 3.11+. Choose the deployment mode that fits your environment:

| Mode | Description | Best For |
|------|-------------|----------|
| **Windows PC** | Install on a rack PC or mini PC using the Windows installer (.exe). Runs as a Windows service with a system tray app. | AV racks with a Windows PC, smaller installations |
| **Mac** | Install with the macOS installer (.pkg). Runs as a background service with a menu-bar app. | Mac-based spaces (churches, schools, studios) |
| **Linux Server** | Install via script on any Linux machine. Runs as a system service. | IT-managed infrastructure, dedicated AV servers |
| **Docker** | One container per space, orchestrated with docker-compose. | Enterprise IT, multi-space servers |
| **VM** | Install in a virtual machine. One or many instances per VM. | Organizations with virtualization infrastructure |
| **Mini PC / SBC** | Dedicated hardware (NUC, mini PC, single-board computer). One instance per space. | Permanent installations, spaces needing serial/GPIO |

All modes are functionally identical. Serial port control (RS-232/485) requires physical hardware access or USB adapters. IP-only control works in all modes including Docker and VM.

## Installation

See [Getting Started](getting-started.md) for detailed installation steps covering all four methods:

| Method | Install Command / Action |
|--------|--------------------------|
| **Windows Installer** | Download from [GitHub Releases](https://github.com/open-avc/openavc/releases) and run the `.exe` |
| **macOS Installer** | Download the `.pkg` from [GitHub Releases](https://github.com/open-avc/openavc/releases) (Apple Silicon or Intel) and double-click it |
| **Docker** | `curl -fsSL https://raw.githubusercontent.com/open-avc/openavc/main/installer/docker-compose.yml -o docker-compose.yml && docker compose up -d` |
| **Linux** | `curl -sSL https://get.openavc.com \| sudo bash` |
| **From Source** | `git clone`, `pip install`, `npm run build`, `python -m server.main` |

## Network Configuration

| Port | Protocol | Purpose | Required? |
|------|----------|---------|-----------|
| 8080 | HTTP/WS | Web UI, REST API, WebSocket | Yes |
| 19500 | HTTP/WS | Simulator UI (development/testing only) | No |
| 19872 | UDP | ISC auto-discovery (multi-instance setups only) | No |

Ensure port 8080 is accessible from:
- Touchscreens and tablets (Panel UI)
- Programmer workstations (Programmer IDE)
- Any external integrations using the REST API

For multi-instance setups using ISC auto-discovery, allow UDP broadcast on port 19872 within the same subnet. For cross-subnet ISC, configure peer addresses manually and allow TCP on each instance's HTTP port.

## Data Directory

OpenAVC separates application code from user data. The application directory contains server code and built frontends. The data directory contains your projects, drivers, configuration, and backups. Updates replace application files but never touch the data directory.

**Data directory locations by platform:**

| Platform | Data Directory |
|----------|---------------|
| Linux | `/var/lib/openavc` |
| Windows | `C:\ProgramData\OpenAVC` |
| macOS | `/Library/Application Support/OpenAVC` |
| Docker | `/data` (volume mount) |
| Development | `./data` (relative to repo root) |

Override with the `OPENAVC_DATA_DIR` environment variable.

**What lives in the data directory:**

```
{data_dir}/
├── projects/          # .avc project files + scripts
├── driver_repo/       # Community and custom drivers (installed from the IDE)
├── plugin_repo/       # Community and custom plugins (installed from the IDE)
├── backups/           # Automatic pre-update backups
├── logs/              # Log files (rotated)
├── system.json        # System configuration
└── update-cache/      # Downloaded update packages (temp)
```

`driver_repo/` and `plugin_repo/` live under the data directory so the content
you install from the Programmer IDE survives application upgrades. On Docker
this is essential — `/app` is rewritten by every image pull, but `/data` is on
a mounted volume.

## System Configuration

System-level configuration controls the server itself: networking, authentication, logging, updates, and cloud connectivity. It is separate from project configuration and stored in the data directory so it persists across updates.

**Location:** `{data_dir}/system.json` (created with defaults on first startup if missing).

```json
{
    "network": {
        "http_port": 8080,
        "bind_address": "127.0.0.1",
        "control_interface": ""
    },
    "auth": {
        "programmer_password": "",
        "api_key": "",
        "panel_lock_code": ""
    },
    "isc": {
        "enabled": true,
        "discovery_enabled": true,
        "auth_key": ""
    },
    "logging": {
        "level": "info",
        "file_enabled": true,
        "max_size_mb": 50,
        "max_files": 5
    },
    "updates": {
        "check_enabled": true,
        "channel": "stable",
        "auto_check_interval_hours": 1,
        "auto_backup_before_update": true,
        "notify_only": false
    },
    "cloud": {
        "enabled": false,
        "endpoint": "wss://cloud.openavc.com/agent/v1",
        "system_key": "",
        "system_id": ""
    },
    "kiosk": {
        "enabled": false,
        "target_url": "http://localhost:8080/panel",
        "cursor_visible": false
    },
    "tls": {
        "enabled": false,
        "port": 8443,
        "auto_generate": true,
        "cert_file": "",
        "key_file": "",
        "redirect_http": true
    }
}
```

**Configuration priority:** Environment variables override system.json values. This lets Docker and CI environments inject config without modifying the file.

| system.json path | Environment Variable | Default |
|---|---|---|
| `network.http_port` | `OPENAVC_PORT` | `8080` |
| `network.bind_address` | `OPENAVC_BIND` | `127.0.0.1` |
| `network.control_interface` | `OPENAVC_CONTROL_INTERFACE` | `""` |
| `auth.programmer_password` | `OPENAVC_PROGRAMMER_PASSWORD` | `""` |
| `auth.api_key` | `OPENAVC_API_KEY` | `""` |
| `auth.panel_lock_code` | `OPENAVC_PANEL_LOCK_CODE` | `""` |
| `logging.level` | `OPENAVC_LOG_LEVEL` | `info` |
| `updates.check_enabled` | `OPENAVC_UPDATE_CHECK` | `true` |
| `updates.channel` | `OPENAVC_UPDATE_CHANNEL` | `stable` |
| `cloud.enabled` | `OPENAVC_CLOUD_ENABLED` | `false` |
| `cloud.endpoint` | `OPENAVC_CLOUD_ENDPOINT` | `wss://cloud.openavc.com/agent/v1` |
| `cloud.system_key` | `OPENAVC_CLOUD_SYSTEM_KEY` | `""` |
| `cloud.system_id` | `OPENAVC_CLOUD_SYSTEM_ID` | `""` |
| `tls.enabled` | `OPENAVC_TLS_ENABLED` | `false` |
| `tls.port` | `OPENAVC_TLS_PORT` | `8443` |
| `tls.auto_generate` | `OPENAVC_TLS_AUTO_GENERATE` | `true` |
| `tls.cert_file` | `OPENAVC_TLS_CERT_FILE` | `""` |
| `tls.key_file` | `OPENAVC_TLS_KEY_FILE` | `""` |
| `tls.redirect_http` | `OPENAVC_TLS_REDIRECT_HTTP` | `true` |

You can also read and modify system configuration through the REST API:

- `GET /api/system/config` returns the current configuration (sensitive fields redacted)
- `PATCH /api/system/config` updates individual sections and saves to disk

> **Bind address security:** The default bind address is `127.0.0.1` (localhost only) for Linux and from-source installations. The Windows installer and Docker pre-configure `0.0.0.0` (network-accessible) since these deployments typically serve touch panels on other devices. To allow network access, set `bind_address` to `0.0.0.0` in system.json or via the `OPENAVC_BIND` environment variable. When bound to `0.0.0.0` without authentication configured, the server logs a prominent warning at startup.

## Updates

OpenAVC checks for updates automatically (every hour by default) via the GitHub Releases API. No data is sent to GitHub.

**Check for updates:** `GET /api/system/updates/check`

When an update is available, the response includes the version, changelog, and whether the installation supports self-update.

**Deployment types and update behavior:**

| Deployment | Self-Update | What Happens |
|---|---|---|
| Windows installer | Yes | Downloads and runs new installer silently |
| macOS installer | Yes | Downloads an archive, writes an instruction file, restarts. The launchd wrapper swaps the app bundle before the server starts. |
| Linux package | Yes | Downloads archive, writes instruction file, restarts. A helper script applies the update before the service starts. |
| Docker | No | Shows notification with `docker compose pull` command |
| Git/dev | No | Shows notification with `git pull` instructions |

**Pre-update backups:** Before applying any update, OpenAVC automatically backs up your projects, drivers, and system.json to the `backups/` directory. The backup covers the data directory only. On Linux, logs live at `/var/log/openavc` (outside the data directory) and are rotated separately, so they are not part of the pre-update backup — by design, since logs aren't needed to restore a working system.

**Rollback:** If the server fails to start after an update, it automatically rolls back to the previous version. You can also manually rollback via `POST /api/system/updates/rollback`.

## Linux Service

On Linux, OpenAVC runs as a systemd service that starts automatically on boot:

```ini
# /etc/systemd/system/openavc.service
[Unit]
Description=OpenAVC Room Control Server
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=openavc
Group=openavc
WorkingDirectory=/opt/openavc
ExecStart=/opt/openavc/venv/bin/python -m server.main
Restart=always
RestartSec=5
Environment=OPENAVC_DATA_DIR=/var/lib/openavc
Environment=OPENAVC_LOG_DIR=/var/log/openavc
Environment=OPENAVC_PROJECT=/var/lib/openavc/projects/default/project.avc
Environment=OPENAVC_BIND=0.0.0.0
NoNewPrivileges=true
AmbientCapabilities=CAP_NET_RAW
ProtectSystem=strict
ReadWritePaths=/var/lib/openavc /var/log/openavc -/opt/openavc/driver_repo -/opt/openavc/plugin_repo
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl enable openavc
sudo systemctl start openavc
```

**Why the unit grants `CAP_NET_RAW`:** device discovery's ICMP ping sweep
needs to open an ICMP/raw socket to find live hosts. The service runs as the
unprivileged `openavc` user, and `NoNewPrivileges=true` strips the file
capability from `/bin/ping` when it execs — so the sweep can only send echo
requests if the process holds `CAP_NET_RAW` directly. The installer's unit
grants it (scoped to OpenAVC alone). If you hand-write your own unit, keep
this line: without it the kernel's default `ping_group_range` excludes the
service's group and scans silently return zero devices. (LXC containers and
minimal hosts hit the same wall — this one line fixes them all, with no
host-wide sysctl change.)

## macOS Service

On macOS, the `.pkg` installer sets everything up for you. The server runs as a
system LaunchDaemon (`com.openavc.server`) that starts at boot, and a menu-bar
app runs as a per-user LaunchAgent (`com.openavc.menubar`) that starts at login.
The installer also creates the data directory at `/Library/Application Support/OpenAVC`
and seeds the default project.

The menu-bar app shows server status and device count and provides quick links
to the Programmer IDE and Panel, an update check, start/stop/restart controls,
and an **Uninstall OpenAVC** item (all of which prompt for your password, since
the server runs as root).

To control the service manually from Terminal:

```bash
# Status
sudo launchctl print system/com.openavc.server
# Restart
sudo launchctl kickstart -k system/com.openavc.server
# Stop / start
sudo launchctl bootout system/com.openavc.server
sudo launchctl bootstrap system /Library/LaunchDaemons/com.openavc.server.plist
```

Updates and rollback work the same as on Windows and Linux: the server downloads
the new build, and on restart the daemon's wrapper swaps the app bundle in place
(keeping the previous version for one-step rollback).

### Uninstalling on macOS

Because OpenAVC runs as a background service, dragging the app to the Trash is
not enough on its own. Use one of these:

- **Menu bar:** click the OpenAVC menu-bar icon and choose **Uninstall OpenAVC**,
  then enter your password. This stops the service and the menu-bar app, removes
  the application, and keeps your projects and settings.
- **Terminal:** run the bundled uninstaller.

```bash
# Keep your projects and settings
sudo bash /Applications/OpenAVC.app/Contents/Resources/macos-uninstall.sh

# Remove everything, including projects, settings, and logs
sudo bash /Applications/OpenAVC.app/Contents/Resources/macos-uninstall.sh --purge
```

Your projects and settings live in `/Library/Application Support/OpenAVC`. They
are kept unless you pass `--purge`, so reinstalling picks up where you left off.

## Docker

Download the maintained compose file and start the container. This is the supported install path -- the compose file pins the network and capability settings discovery needs, so don't try to translate it back into `docker run` flags or strip pieces out:

```bash
curl -fsSL https://raw.githubusercontent.com/open-avc/openavc/main/installer/docker-compose.yml -o docker-compose.yml
docker compose up -d          # Start
docker compose pull           # Update to latest
docker compose up -d          # Restart with new image
```

For serial/USB device passthrough, uncomment the `devices:` block in the compose file.

### Why the compose file uses host networking and NET_RAW

Device discovery, mDNS, and SSDP all need the container to be reachable on, and able to send packets to, the same physical network as your AV equipment. With Docker's default bridge network the container sits behind NAT on a private 172.x subnet and cannot see your LAN, so scans return zero devices. `network_mode: host` puts the container directly on the host's network stack so discovery works. `cap_add: NET_RAW` lets OpenAVC's unprivileged user run the ICMP ping sweep that finds live hosts.

### Docker Desktop on Windows or Mac

Docker Desktop runs the Linux container inside a WSL2 (Windows) or HyperKit (Mac) virtual machine that does not share the host's LAN, so device discovery cannot work even with `network_mode: host`. If you need discovery on Windows or Mac, use the native installer instead. Docker Desktop is fine for evaluating the software or for IP-only deployments where you'll add devices manually.

### Multi-space deployments

For multiple rooms on a single host, use separate containers with different ports and data volumes. Multi-room layouts must use bridge networking (host mode would conflict on port 8080), which means **device discovery is not available in multi-room mode** and devices must be added manually by IP. If discovery matters, run one OpenAVC container per physical host using the single-room compose above, or set up macvlan networking so each container gets its own LAN IP.

```yaml
services:
  room-101:
    image: ghcr.io/open-avc/openavc:latest
    ports: ["8081:8080"]
    volumes: ["room-101-data:/data"]
  room-102:
    image: ghcr.io/open-avc/openavc:latest
    ports: ["8082:8080"]
    volumes: ["room-102-data:/data"]
```

## First Boot (Raspberry Pi Image)

The Raspberry Pi image is ready to run the moment it boots. There is nothing to install and no terminal to open. The flow from a freshly flashed card to a working device is:

1. **Flash and boot.** Write the `openavc-<version>-pi.img.xz` image to an SD card, insert it, connect an HDMI display and a network cable, and power on. The first boot runs a one-time setup (creating the data directory and starting the service), so allow an extra minute before the device is ready.

2. **Read the device's address off the screen.** With no project loaded yet, the HDMI display shows the **setup screen**: the device's IP address, the Programmer and Panel URLs, and what to do next. The IP also prints on the HDMI text console during boot, so you can find the device even without a display manager. On networks with mDNS, the device is also reachable at `http://openavc.local:8080`.

3. **Open the Programmer and create a password.** Browse to `http://openavc.local:8080/programmer` (or the IP from the screen) from a computer on the same network. Because shipped devices are closed by default, the first visit shows a **Create admin password** screen. Set a password here. This claims the device, and the same password becomes the operating-system login for the `openavc` user (see [Raspberry Pi: OS login and SSH](#raspberry-pi-os-login-and-ssh)).

4. **Build your project.** Add devices, design the panel, and save. As soon as the project has panel content, the on-device display switches from the setup screen to the Panel UI on its own. You never have to touch the device to finish setup.

What ships locked down on a fresh image:

- **No usable OS password, and SSH off.** The `openavc` account is locked until you set the admin password in step 3, and `sshd` does not start. Enable SSH later from **Settings > Security** if you need remote console access.
- **The admin surface is closed.** Until you complete step 3, the Programmer and REST API require the credential you are about to set. The Panel UI is always open, so end users never see a login.

To set the IP, hostname, or WiFi without attaching a keyboard, see [Changing the device's network settings](#changing-the-devices-network-settings). To force the display back to the setup screen while a project is running, open `/setup?stay=1`.

## Touchscreen Kiosk Setup

For dedicated touchscreen displays, enable kiosk mode in system.json:

```json
{
    "kiosk": {
        "enabled": true,
        "target_url": "http://localhost:8080/panel",
        "cursor_visible": false
    }
}
```

On Linux with a desktop environment, the `openavc-panel.service` auto-launches Chromium in kiosk mode. The Raspberry Pi image includes this pre-configured. On other Linux installs, create the service manually:

```ini
# /etc/systemd/system/openavc-panel.service
[Unit]
Description=OpenAVC Panel Kiosk Display
After=openavc.service graphical.target
Wants=openavc.service

[Service]
Type=simple
User=openavc
Environment=DISPLAY=:0
ExecStart=/opt/openavc/scripts/panel-kiosk.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical.target
```

The `panel-kiosk.sh` script reads the kiosk settings from `system.json`, waits for the server to be ready, hides the cursor (for touch-only panels), and launches Chromium in fullscreen kiosk mode. Touch input via USB HID (including HDMI monitors with a USB touch cable) is handled by the Linux kernel automatically.

While kiosk mode is off (the default on a fresh device), the display shows the **setup screen** (`/setup`) instead of the panel: the device's IP address, the Programmer and Panel URLs, and first-run instructions. The screen updates live — connecting a network cable or claiming the device refreshes it automatically, no reboot needed. Once your project has panel content, the display switches to the Panel UI on its own; you never have to touch the device to complete setup. (To read the setup screen while a project is running, open `/setup?stay=1`.)

### Changing the device's network settings

On the Pi appliance (and any Linux install running NetworkManager), OpenAVC can configure the machine's own network — no SSH or terminal needed:

- **From the device's screen:** the setup screen has a **Network Settings** section with a DHCP/static form and WiFi scan-and-join. This works before the device has any network connection at all, which is how you bring a device onto a static-only network or a WiFi-only site. Text entry needs a keyboard (USB, or the on-screen keyboard on tablet hardware).
- **From a laptop:** Programmer > Settings > Network > **This Device's Network** offers the same controls plus a hostname field. When you change the address you're connected through, the Programmer becomes unreachable at the old address — the confirmation dialog shows the new URL to reconnect to.

How a change takes effect depends on the hardware. On the Pi appliance and Linux installs, changes apply immediately, and a static IP that fails to activate is rolled back to the previous configuration automatically, so a typo can't strand the device off the network. On appliance hardware where the network is managed by the device firmware, the form notes that applying a change restarts the device — settings are saved to the boot configuration and the device comes back up on the new address; if the new settings are wrong, fix them from the device's own setup screen. On deployments where OpenAVC doesn't manage the host (Windows, Docker, Linux without NetworkManager), these controls don't appear — configure the host's network as you normally would.

On the Raspberry Pi image, an `openavc-info.service` also displays the IP address and access URLs on the HDMI console at boot, so you can find the device on the network even without mDNS.

### Raspberry Pi: OS login and SSH

The Pi appliance image ships with the operating-system login locked down:

- There is **no default OS password**. The `openavc` Linux account is locked in the image, so there is no `openavc/openavc` shared login to exploit. The kiosk display still auto-starts (auto-login does not use a password).
- The admin password you set on the first-run **Create admin password** screen becomes the OS console/SSH login for the `openavc` user as well — one credential for both. Changing it in **Settings > Security** re-syncs the OS login.
- **SSH is off by default.** Enable it with the **Enable SSH** toggle in **Settings > Security** when you need remote console access; log in as `openavc` with the admin password. Turn it off from the same toggle.

This applies only to the Pi appliance image. A generic Linux `install.sh` host runs OpenAVC as an unprivileged service account and does not manage the OS login or `sshd` — set those up yourself as usual.

## Authentication

Authentication is optional. When the server is only accessible locally (bind address `127.0.0.1`), no credentials are needed. When the server is accessible on the network (`0.0.0.0`), you should set at least a programmer password to prevent unauthorized changes to your project.

The Panel UI is never password-protected. End users can always open the touch panel without logging in.

### When to set each credential

| Setting | Environment Variable | When to use it |
|---------|---------------------|----------------|
| `auth.programmer_password` | `OPENAVC_PROGRAMMER_PASSWORD` | **Set this when the server is network-accessible** and you want to prevent other people on the network from opening the Programmer IDE and modifying your project. The browser will prompt for a password. This is for humans logging in via a browser. |
| `auth.api_key` | `OPENAVC_API_KEY` | **Set this if you have third-party integrations** (control scripts, middleware, or external software) that connect to the OpenAVC REST API or WebSocket. Provide the key to those systems via the `X-API-Key` header. Not needed unless you are building custom integrations. |
| `auth.panel_lock_code` | `OPENAVC_PANEL_LOCK_CODE` | **Set this if the panel runs on a public-facing display** and you want to prevent users from navigating away from the touch panel UI. |

You do not need to set both programmer password and API key. Either one protects the Programmer IDE and API. The password is for humans (browser login), the API key is for machines (HTTP headers). If both are set, either credential is accepted.

### What gets protected

When at least one credential is configured:
- `/api/status`, `/api/health`, and `/api/library` remain open (no auth)
- All other REST endpoints require HTTP Basic or `X-API-Key`
- The `/programmer` static files require HTTP Basic credentials
- Panel WebSocket connections remain open; programmer WebSocket connections require a `?token=` query param or `X-API-Key` header
- The Panel UI at `/panel` is always accessible (it's a touch screen, not a config tool)

## HTTPS

HTTPS is off by default. OpenAVC typically runs on an isolated AV VLAN where plain HTTP is the convention (Crestron and Extron web UIs also default to HTTP). Turn HTTPS on when you need it: corporate or higher-ed networks that block HTTP, public Wi-Fi between the panel and the server, or browser features that require a secure context (clipboard, notifications).

### Enabling from the Programmer IDE

The supported path:

1. Open the Programmer IDE and go to **Settings > Security**.
2. Toggle **Enable HTTPS** on.
3. Leave **Auto-generate (recommended)** selected unless your organization has its own CA.
4. Click **Save**.
5. Restart the server (the banner tells you a restart is required).
6. Reopen the IDE at `https://<host>:8443/programmer`. The browser shows a one-time warning the first time it sees the self-signed cert; click through, or install the CA cert on each device that needs warning-free access (see "Installing the CA" below).

The Security card shows live cert details (subject, issuer, fingerprint, SAN list, expiry, warnings) once the TLS listener is running.

### Enabling without the IDE

For headless deployments, set the environment variables before starting the server:

```bash
export OPENAVC_TLS_ENABLED=true
export OPENAVC_TLS_PORT=8443                 # optional, defaults to 8443
```

Or edit `{data_dir}/system.json`:

```json
"tls": {
    "enabled": true,
    "port": 8443
}
```

Then restart the server. On first start with TLS on, OpenAVC generates a self-signed CA and server cert under `{data_dir}/tls/`. The cert is valid for 10 years and covers `localhost`, `127.0.0.1`, the OS hostname, and every LAN IPv4 the host has at generation time. It is regenerated automatically if the primary local IP changes later.

### Providing your own certificate

If your organization has an internal CA and you'd rather use a cert signed by it, switch to **Use my own certificate** in the IDE (or set `tls.auto_generate` to false in `system.json`) and point `tls.cert_file` / `tls.key_file` at the PEM files. Both must be absolute paths on the server's filesystem. Wildcard certs work as long as the SAN matches the hostname clients use.

If the cert is missing, unreadable, malformed, or expired, OpenAVC refuses to start the TLS listener and writes a precise error to the startup log. It does **not** silently fall back to HTTP. Fix the cert configuration and restart.

### HTTP-to-HTTPS redirect

When HTTPS is enabled, OpenAVC also runs a tiny HTTP listener on the original port (8080 by default) that 301/308-redirects every request to the HTTPS URL. This keeps old bookmarks, printed QR codes, and panel apps pointed at `http://` working without any user action. Disable it in Settings > Security if you want to take port 8080 down entirely.

### Installing the CA on panel devices

Auto-generated certs are signed by an internal CA that no client trusts out of the box. Until you install the CA, browsers and the panel apps show a warning. To install it warning-free:

1. From any browser on the same network, visit `https://<server>:8443/api/certificate` (no auth required) — or click **Download CA certificate** in Settings > Security.
2. Transfer the downloaded `openavc-ca.crt` to the panel device (email, AirDrop, USB).
3. **iOS:** open the file, then **Settings > General > VPN & Device Management > Install Profile**. After install, also enable trust under **Settings > General > About > Certificate Trust Settings**.
4. **Android:** open the file via **Settings > Security > Encryption & credentials > Install a certificate > CA certificate** (path varies by manufacturer).
5. **Windows / macOS / Linux:** add the cert to the system trust store (or browser trust store, depending on the browser).

Once the CA is trusted, future cert regenerations (e.g., the server gets a new LAN IP) keep working without re-installing trust.

### Reverse-proxy deployments

If you front OpenAVC with nginx, Caddy, or another reverse proxy that terminates TLS for you, leave OpenAVC's `tls.enabled` at false and let the proxy do the work. Set `tls.redirect_http` to false if you want OpenAVC to skip its own redirect listener (the proxy will handle that too).

## Health Check

`GET /api/health` returns server health with no authentication required. Use this for monitoring tools, load balancers, and container orchestration health checks.

```json
{
    "status": "healthy",
    "version": "0.5.2",
    "uptime_seconds": 3600.5,
    "devices": { "total": 5, "connected": 4, "error": 1 },
    "cloud": { "connected": true }
}
```

## Security Notes

- Communication is HTTP by default, suitable for isolated AV VLANs.
- HTTPS is available as a built-in opt-in (see the HTTPS section above). Auto-generated self-signed cert by default, or supply your own cert/key for environments with an internal CA. Reverse-proxy TLS is still supported for deployments that prefer it.
- ISC (inter-system communication) uses a shared auth key for system-to-system traffic, and switches to `wss://` automatically when peers advertise HTTPS.
- The default bind address is localhost only. Change to `0.0.0.0` in system.json when you need network access.

## See Also

- [Getting Started](getting-started.md). Installation and first run
- [Programmer Overview](programmer-overview.md). IDE walkthrough
- [System Updates](updates.md). Update management and rollback
- [Network & Security Cut Sheet](it-network-guide.md). IT network requirements and firewall rules
- [Device Simulator](simulator.md). Test without real hardware
