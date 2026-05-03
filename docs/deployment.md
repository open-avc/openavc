# Deployment Guide

How to deploy OpenAVC to production hardware for your AV spaces.

> For initial setup and testing, see [Getting Started](getting-started.md). This guide covers deploying to production.

## Where OpenAVC Runs

OpenAVC runs on any hardware with Python 3.11+. Choose the deployment mode that fits your environment:

| Mode | Description | Best For |
|------|-------------|----------|
| **Windows PC** | Install on a rack PC or mini PC using the Windows installer (.exe). Runs as a Windows service with a system tray app. | AV racks with a Windows PC, smaller installations |
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
| Docker | `/data` (volume mount) |
| Development | `./data` (relative to repo root) |

Override with the `OPENAVC_DATA_DIR` environment variable.

**What lives in the data directory:**

```
{data_dir}/
├── projects/          # .avc project files + scripts
├── drivers/           # Community and custom drivers
├── backups/           # Automatic pre-update backups
├── logs/              # Log files (rotated)
├── system.json        # System configuration
└── update-cache/      # Downloaded update packages (temp)
```

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
| Linux package | Yes | Downloads archive, writes instruction file, restarts. A helper script applies the update before the service starts. |
| Docker | No | Shows notification with `docker compose pull` command |
| Git/dev | No | Shows notification with `git pull` instructions |

**Pre-update backups:** Before applying any update, OpenAVC automatically backs up your projects, drivers, and system.json to the `backups/` directory.

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
ProtectSystem=strict
ReadWritePaths=/var/lib/openavc /var/log/openavc /opt/openavc/driver_repo /opt/openavc/plugin_repo
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

On the Raspberry Pi image, an `openavc-info.service` also displays the IP address and access URLs on the HDMI console at boot, so you can find the device on the network even without mDNS.

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

- Communication is HTTP by default, suitable for isolated AV VLANs
- HTTPS can be enabled with a reverse proxy (nginx, caddy) if needed
- ISC (inter-system communication) uses a shared auth key for system-to-system traffic
- The default bind address is localhost only. Change to `0.0.0.0` in system.json when you need network access.

## See Also

- [Getting Started](getting-started.md). Installation and first run
- [Programmer Overview](programmer-overview.md). IDE walkthrough
- [System Updates](updates.md). Update management and rollback
- [Network & Security Cut Sheet](it-network-guide.md). IT network requirements and firewall rules
- [Device Simulator](simulator.md). Test without real hardware
