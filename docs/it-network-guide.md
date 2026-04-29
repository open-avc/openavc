# OpenAVC Network & Security Cut Sheet

**Audience:** IT administrators, network engineers, and security teams.

**Scope:** Network requirements, traffic profiles, security posture, and firewall rules for OpenAVC deployments. This is not an installation guide. It is a technical reference for evaluating and approving OpenAVC on a managed network.

---

## What Is OpenAVC?

OpenAVC is an open-source AV room control platform that runs on standard hardware (Windows or Linux PCs, VMs, Docker, Raspberry Pi). It controls projectors, displays, audio DSPs, video switchers, cameras, and lighting over standard IP protocols (TCP, UDP, HTTP, serial).

OpenAVC is a single application with a built-in web server. There is no external database, no application server, and no background services beyond the application itself. Configuration, automation, and device state are stored in local files on the host machine.

---

## Deployment Scenarios

### Existing PC in the room

The most common deployment. Most AV spaces already have a PC at the lectern, behind the display, or in the rack for digital signage, lecture capture, or room scheduling. OpenAVC runs on that existing PC alongside whatever else it does. It is a lightweight application, not a full operating system, and coexists with other software without conflict.

The same PC that presents content can also control the projector, switch inputs, and adjust audio. The touch panel interface can run right in a browser on that PC's display, or on a separate tablet or touch screen in the room. If nobody needs a touch panel at all (e.g., everything is triggered automatically by schedules or sensors), OpenAVC runs quietly in the background with no visible UI.

**Network impact:** Minimal. The PC is already on the network. OpenAVC adds only targeted TCP connections to the specific AV devices in the room. No new hardware, no new network drops.

### Dedicated controller (mini PC or Raspberry Pi)

For spaces where there is no existing PC, or where a dedicated appliance is preferred, OpenAVC runs on a small-form-factor PC (Intel NUC, Lenovo Tiny, HP Mini, etc.) or a Raspberry Pi. The Pi image can optionally drive a touch panel display directly via HDMI.

**Network impact:** One new device on the room's network segment. Communicates only with AV devices on its local subnet and serves a web interface to touch panels or browsers in that room. No traffic leaves the room unless the optional cloud service is enabled.

### Server or VM on the building network

OpenAVC runs on an existing server, VM, or Docker host. It controls AV equipment across multiple rooms from a central location.

**Network impact:** The server needs IP reachability to every AV device it controls, and devices presenting the touch panel UI need HTTP access to the server. If rooms are on isolated VLANs, firewall rules or routing must allow the server to reach those VLANs on the specific ports listed below.

---

## Network Requirements

### Listening ports (inbound to the OpenAVC host)

| Port | Protocol | Purpose | Required? |
|------|----------|---------|-----------|
| **8080** | TCP (HTTP) | Web interface and REST API | Yes |
| **19500** | TCP (HTTP) | Device Simulator UI (development/testing only) | No |
| **19872** | UDP | ISC auto-discovery (multi-instance setups only) | No |

**Port 8080** is the only port that must be accessible for a standard single-room deployment. This is configurable via the `OPENAVC_PORT` environment variable or `system.json`.

**Port 19500** is used by the device simulator during development and testing. It is only active when the simulator is running. It does not need to be accessible from other machines.

**Port 19872** is used only when multiple OpenAVC instances need to discover each other on the same LAN (inter-system communication). It can be disabled entirely.

The application does not listen on any other ports by default. There is no SSH server, no SNMP agent, no embedded database port, and no proprietary discovery protocol that accepts inbound connections.

### Who can access the web interface

By default, OpenAVC only accepts connections from the machine it is running on (it binds to `127.0.0.1`, localhost). This means:

**If the touch panel runs on the same PC** (a browser open on the PC's own screen), no configuration change is needed. Open a browser to `http://localhost:8080/panel` and it works immediately. This is the simplest setup and is common for lectern PCs or kiosk displays where the control interface is on the same machine running OpenAVC.

**If a separate device needs to access the touch panel** (a wall-mounted tablet, a phone, a room scheduling display, or a browser on any other computer), those devices need to reach OpenAVC over the network. This requires changing the bind address so OpenAVC listens on its network interface instead of only localhost.

To allow network access, set the `OPENAVC_BIND` environment variable before starting the application:

- **Windows (PowerShell):** `$env:OPENAVC_BIND = "0.0.0.0"` then start the application
- **Windows (Command Prompt):** `set OPENAVC_BIND=0.0.0.0` then start the application
- **Linux:** `export OPENAVC_BIND=0.0.0.0` then start the application, or add it to the systemd service file
- **Docker:** Pass `-e OPENAVC_BIND=0.0.0.0` to `docker run` (Docker deployments do this by default)
- **Windows Installer:** The installed service is pre-configured to accept network connections

Setting the bind address to `0.0.0.0` means OpenAVC will accept connections on all network interfaces. When you do this, you should also set credentials to protect the configuration interface. Set both a username and a password:

- **Windows (PowerShell):**
  - `$env:OPENAVC_PROGRAMMER_USERNAME = "your-username"`
  - `$env:OPENAVC_PROGRAMMER_PASSWORD = "your-password"`
- **Linux:**
  - `export OPENAVC_PROGRAMMER_USERNAME=your-username`
  - `export OPENAVC_PROGRAMMER_PASSWORD=your-password`
- **Docker:** Pass `-e OPENAVC_PROGRAMMER_USERNAME=your-username -e OPENAVC_PROGRAMMER_PASSWORD=your-password` to `docker run`

The application logs a warning at startup if it is bound to all interfaces without a password configured. The end-user touch panel remains accessible without credentials; only the configuration interface (Programmer) is protected.

---

## Outbound Traffic (OpenAVC host to the network)

### AV device control

OpenAVC initiates outbound TCP and UDP connections to AV equipment. The specific ports depend entirely on which devices are configured in the project. OpenAVC only communicates with devices explicitly defined in the project configuration, using the ports those devices expect. It does not scan or probe the network during normal operation.

The table below lists common AV control ports. This is not exhaustive. AV manufacturers use a wide range of proprietary and standard ports, and new drivers may use ports not listed here.

| Port | Protocol | Device type | Example |
|------|----------|-------------|---------|
| 23 | TCP (Telnet) | Switchers, DSPs, amplifiers | Extron, Biamp, QSC, Kramer, Shure |
| 80 | TCP (HTTP) | Devices with web APIs | Panasonic cameras, REST-based devices |
| 443 | TCP (HTTPS) | Devices with secure web APIs | Newer AV-over-IP devices |
| 1515 | TCP | Samsung commercial displays | Samsung MDC protocol |
| 1688 | TCP | Crestron-compatible devices | Crestron CIP |
| 4352 | TCP | Projectors | PJLink standard |
| 5000 | TCP | Switchers, DSPs | Kramer Protocol 3000 |
| 5900 | TCP | Remote desktop/preview | VNC |
| 7142 | TCP | AMX-compatible devices | AMX ICSP |
| 9090 | TCP | Devices with web APIs | Alternate HTTP management |
| 10500 | TCP | PTZ cameras | Sony VISCA over IP |
| 49152 | TCP | Audio DSPs | Biamp Tesira |
| 52000 | TCP | Audio DSPs | QSC Q-SYS |
| 61000 | TCP | Wireless microphones | Shure DCS |
| 161 | UDP | SNMP-managed devices | Read-only status query (discovery only) |
| 1400 | TCP | Audio | Sonos UPnP |

An OpenAVC instance controlling only PJLink projectors and a Biamp DSP, for example, will only generate traffic on ports 4352 and 49152. If your network policy requires explicit allow-listing, the exact ports in use for a given deployment can be determined from the project's device configuration.

### Device discovery (on-demand only)

OpenAVC includes a network discovery feature to help AV integrators find devices during initial setup. Discovery is never automatic. It runs only when an integrator explicitly starts a scan from the Programmer interface, and it stops when the scan completes (typically under 60 seconds).

During a discovery scan, OpenAVC will:

1. **Ping sweep** the local subnet(s) using ICMP echo requests
2. **TCP port scan** responding hosts on the AV ports listed above
3. **SNMP query** (v2c, community string `public`, read-only) on port 161
4. **mDNS query** on multicast group 224.0.0.251:5353
5. **SSDP M-SEARCH** on multicast group 239.255.255.250:1900
6. **Protocol probes** on open ports (PJLink status query, Extron identification, etc.)

All discovery traffic is confined to the local subnet(s) detected on the host's network interfaces. It does not scan remote subnets, public IP ranges, or addresses outside the host's directly-connected networks. Virtual and VPN adapters are excluded automatically.

### Internet access (optional)

OpenAVC does not require internet access for normal operation. All device control, automation, and UI serving works entirely offline.

The following features require outbound internet access if enabled:

| Destination | Port | Protocol | Purpose | Can be disabled? |
|-------------|------|----------|---------|-----------------|
| `api.github.com` | 443 | HTTPS | Check for software updates | Yes (`updates.check_enabled: false`) |
| `github.com` | 443 | HTTPS | Download updates and community drivers | Yes (manual install alternative) |
| `cloud.openavc.com` | 443 | WSS | Cloud management platform (see below) | Yes (disabled by default) |

If your network policy blocks outbound HTTPS, all local functionality is unaffected. Update checks will fail silently, and the cloud agent (if enabled) will retry with exponential backoff up to a 5-minute interval, then remain dormant until connectivity is restored.

---

## Inter-System Communication (ISC)

When deploying multiple OpenAVC instances (e.g., one per room), ISC allows them to share state and coordinate. ISC is optional and can be disabled entirely.

| Parameter | Value |
|-----------|-------|
| Discovery protocol | UDP broadcast on port 19872 |
| Discovery range | Local broadcast domain only (255.255.255.255) |
| Peer communication | WebSocket on each instance's HTTP port (path: `/isc/ws`) |
| Authentication | Optional shared key (configurable per project) |
| Beacon interval | Every 5 seconds |
| Auto-discovery scope | Same subnet/VLAN only |
| Cross-subnet support | Manual peer addressing (no broadcast required) |

**To disable ISC entirely**, set `isc.enabled: false` in `system.json`. No UDP traffic will be sent or received on port 19872.

**For cross-VLAN multi-instance setups**, disable auto-discovery and configure peer addresses manually. This requires only TCP access to each instance's HTTP port (default 8080), with no UDP broadcast traffic.

---

## Authentication and Access Control

### Web interface and API

OpenAVC is typically deployed on an isolated AV VLAN where the controller is not reachable from the general corporate network. In this configuration, authentication is not required and is disabled by default. For deployments where the controller is reachable from a broader network, authentication should be enabled before exposing the interface.

| Method | Configuration | When to use |
|--------|--------------|-------------|
| HTTP Basic (username + password) | `OPENAVC_PROGRAMMER_USERNAME` and `OPENAVC_PROGRAMMER_PASSWORD` env vars, or `auth.programmer_username` and `auth.programmer_password` in `system.json` | Set this when the server is network-accessible and you want to prevent unauthorized access to the Programmer IDE. The browser prompts for both username and password. This is for humans logging in via a browser. |
| API key (token) | `OPENAVC_API_KEY` env var or `auth.api_key` in `system.json` | Set this if you have third-party integrations (control scripts, middleware, or external software) that connect to the REST API or WebSocket. Provide the key via the `X-API-Key` header. Not needed unless you are building custom integrations. |

You do not need to set both. Either one protects the Programmer IDE and API endpoints. The username/password is for humans (browser login), the API key is for machines (HTTP headers). If both are set, either credential is accepted.

If a programmer password is set without a username, any username entered at the browser prompt is accepted as long as the password matches. Setting a username is recommended when authentication is required.

When authentication is enabled:
- The **Panel** (end-user touch interface) remains accessible without credentials
- The **Programmer** (configuration interface) requires the username and password
- All configuration-changing API endpoints require authentication
- Username and password comparisons both use constant-time algorithms to prevent timing attacks

### TLS/HTTPS

OpenAVC serves HTTP only. It does not terminate TLS. For HTTPS, place a reverse proxy (nginx, Caddy, Apache, HAProxy) in front of the application. This allows you to use your own certificates and manage TLS independently of the application.

### Rate limiting

Rate limiting is enabled by default on the HTTP REST API for remote clients. Requests from localhost (127.0.0.1, ::1) are exempt, since the primary use case is a single user on the same machine. Remote clients are subject to the limits below. Rate limiting does not apply to the touch panel UI (which uses WebSocket) or to the command pipeline between the server and AV hardware — commands are sent to devices the instant they are received.

| Tier | Limit | Applies to |
|------|-------|-----------|
| Open | 120 requests/min per IP | Status, health check, library endpoints |
| Standard | 60 requests/min per IP | General API operations |
| Strict | 10 requests/min per IP | Device command API, discovery, cloud operations |

Failed authentication attempts count against the strict tier, providing brute-force protection. Volume ramps, rapid command sequences, and multi-room control from the touch panel are unaffected by these limits.

---

## Data Storage and Privacy

### What is stored locally

| Data | Location | Sensitive? |
|------|----------|-----------|
| Project configuration (devices, macros, UI layouts) | `project.avc` (JSON) | Low. Contains device IP addresses and connection parameters. |
| System configuration | `system.json` | Medium. May contain auth passwords and API keys in plaintext. Protect with filesystem permissions. |
| Persistent variables | `state.json` | Low. Key-value pairs for automation state. |
| Application logs | `logs/` directory | Low. Standard application logs. Configurable rotation (default: 50 MB, 5 files). |
| Cloud pairing data | `cloud.json` | High. Contains system key for cloud authentication. Protect with filesystem permissions. |

### What is NOT stored

- No user credentials for the web interface are stored in a database (the password is a single config value)
- No personal data, user accounts, or usage analytics
- No device credentials beyond what is configured in the project file (e.g., PJLink passwords)
- No data is written outside the application's data directory

### Filesystem permissions

The application runs as a standard user process. It does not require administrator/root privileges. It reads and writes only within its own data directory:

| Platform | Default data path |
|----------|------------------|
| Windows | `C:\ProgramData\OpenAVC\` |
| Linux | `/var/lib/openavc/` |
| Docker | `/data/` (mounted volume) |
| Development | `./data/` (relative to repo) |

---

## Cloud Platform (Optional)

The OpenAVC Cloud platform provides remote monitoring, fleet management, and AI-assisted configuration for organizations managing multiple AV spaces. **Cloud connectivity is entirely optional and disabled by default.** All local functionality works without it.

### Connection details

| Parameter | Value |
|-----------|-------|
| Endpoint | `wss://cloud.openavc.com/agent/v1` |
| Protocol | WebSocket over TLS 1.2+ |
| Direction | **Outbound only.** The device initiates the connection. No inbound ports required. |
| Port | 443 (standard HTTPS) |
| Certificate validation | Required. Standard CA-signed certificate. |
| Keepalive | Server-initiated ping/pong over the WebSocket |
| Reconnection | Exponential backoff: 5s, 10s, 20s, 40s ... up to 5 minutes |

### Authentication

The cloud connection uses a multi-step challenge-response handshake. The system's secret key never crosses the network.

1. Device sends a `hello` message identifying itself (system ID, version, hostname)
2. Cloud responds with a random 32-byte challenge nonce
3. Device computes an HMAC-SHA256 proof using a locally-derived authentication key (HKDF-SHA256 from the system key) and sends it back
4. Cloud independently derives the same key from the stored key hash and verifies the proof
5. On success, cloud issues a session with a unique signing key

All subsequent messages are signed with HMAC-SHA256 using the session-specific signing key. Messages with invalid or missing signatures are rejected.

**Key derivation:** HKDF-SHA256 (RFC 5869), stdlib-only implementation (no external crypto libraries)
**Message signing:** HMAC-SHA256 with constant-time verification
**System key:** 64 bytes, cryptographically random, generated locally, never transmitted. Cloud stores only the SHA-256 hash.

### What data is sent to the cloud

When cloud is enabled, the following data is transmitted:

| Data | Frequency | Content |
|------|-----------|---------|
| Heartbeat | Every 30 seconds | CPU %, memory %, disk %, uptime, device count, connected device count, error count, WebSocket client count, temperature (if available) |
| State changes | Batched every 2 seconds | Device state key-value changes (e.g., `device.projector1.power: "on"`) |
| Alerts | As they occur (max 10/min) | Alert ID, severity, category, message |
| Logs | As they occur (max 100/min) | Log level, source, message |

**What is NOT sent:** Project files, device configurations, system keys, authentication credentials, network topology, or any data from other systems on the network.

### What the cloud can do

The cloud platform can send the following commands to a connected device:

| Command | Description |
|---------|-------------|
| Device command | Execute a device command already defined in the local project (e.g., "turn on projector") |
| Diagnostic | Request a network diagnostic (ping, TCP check, traceroute) from the device's perspective |
| Software update | Trigger a software update check and install |
| Remote access tunnel | Open a proxied connection to the local web interface |
| Alert rules | Push alert rule definitions to be evaluated locally |

All remote commands are scoped to actions already available through the local API. The cloud cannot execute arbitrary code, access the filesystem, or modify the network configuration. All commands include the requesting user's identity for audit purposes.

### Remote access tunnels

The cloud platform can open a remote access tunnel to the device's web interface. This works by proxying HTTP traffic through a secondary WebSocket connection, similar to how tools like ngrok or Cloudflare Tunnel work.

- The device initiates the tunnel connection (outbound to cloud on port 443)
- No inbound ports are opened on the device or the network
- Tunnel connections are authenticated with a per-tunnel token
- Tunnels are closed when the remote session ends or when the device disconnects

### Disabling cloud entirely

Cloud is disabled by default. To confirm it is disabled, verify that `cloud.enabled` is `false` in `system.json` (or that the `OPENAVC_CLOUD_ENABLED` environment variable is not set to `true`). When disabled, no cloud-related code runs and no outbound connections to `cloud.openavc.com` are made.

---

## Firewall Rule Summary

### Minimum (single room, no internet)

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| Web UI access | Inbound | Touch panels / browsers | OpenAVC host | 8080/tcp | HTTP |
| AV device control | Outbound | OpenAVC host | AV device IPs | Per device (see table above) | TCP |

### Typical (with updates and discovery)

Add to the minimum rules:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| ICMP (discovery) | Outbound | OpenAVC host | Local subnet | ICMP | Echo request |
| SNMP (discovery) | Outbound | OpenAVC host | Local subnet | 161/udp | SNMP v2c |
| mDNS (discovery) | Outbound | OpenAVC host | 224.0.0.251 | 5353/udp | mDNS |
| SSDP (discovery) | Outbound | OpenAVC host | 239.255.255.250 | 1900/udp | SSDP |
| Update checks | Outbound | OpenAVC host | api.github.com | 443/tcp | HTTPS |

### With cloud platform

Add to the typical rules:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| Cloud connection | Outbound | OpenAVC host | cloud.openavc.com | 443/tcp | WSS |

### Multi-instance (ISC)

Add if running multiple OpenAVC instances that need to coordinate:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| ISC discovery | Both | OpenAVC hosts | Broadcast (255.255.255.255) | 19872/udp | UDP |
| ISC peer WebSocket | Both | OpenAVC hosts | Other OpenAVC hosts | 8080/tcp | HTTP (WS) |

---

## VLAN and Network Segmentation Recommendations

OpenAVC is designed to work well on segmented networks. A common and recommended topology for managed environments:

**AV Control VLAN** - Contains the OpenAVC host and all AV equipment (projectors, DSPs, switchers, cameras). This VLAN can be fully isolated from the corporate network. If OpenAVC and all controlled devices are on this VLAN, no cross-VLAN rules are needed for device control.

**User/Presentation VLAN** - If users need to access the touch panel UI from devices on a different VLAN (tablets, room PCs), allow inbound TCP on the OpenAVC host's HTTP port (8080 by default) from that VLAN.

**Internet/WAN** - Only required for update checks and cloud connectivity. Can be fully blocked if these features are not needed.

OpenAVC does not use UPnP port mapping, NAT traversal, or any technique that modifies network infrastructure. It operates strictly as a client or server on the ports listed in this document.

---

## Security Posture Summary

| Aspect | Default | Notes |
|--------|---------|-------|
| Inbound ports | 1 (HTTP 8080) | Configurable. Only port required for operation. |
| Bind address | Localhost only | Only the host PC can access the UI. Change via `OPENAVC_BIND` env var to allow tablets/other devices. |
| Authentication | Off | Appropriate for isolated AV VLANs. Enable for broader networks. |
| TLS | Not built-in | Use a reverse proxy for HTTPS if needed |
| Outbound internet | Not required | Only for optional updates and cloud |
| Cloud connectivity | Disabled by default | Opt-in with explicit configuration |
| Privileged access | None required | Runs as standard user, no root/admin |
| External dependencies at runtime | None | No external database, message broker, or third-party service required |
| Discovery scans | On-demand only | Never automatic. Integrator must manually initiate. |
| Broadcast traffic | ISC only (if enabled) | UDP on port 19872. Disabled by setting `isc.enabled: false`. |
| Data exfiltration risk | Low | Only sends data externally if cloud is explicitly enabled. All cloud data is itemized above. |

---

## Quick-Start: Evaluating OpenAVC on Your Network

If you want to try OpenAVC before committing to a deployment, you can have it running on any Windows or Linux machine in under five minutes. The fastest way to evaluate it is on a PC you already have.

### Option 1: Windows Installer (no network impact)

1. Download the installer from [github.com/open-avc/openavc/releases](https://github.com/open-avc/openavc/releases)
2. Run the `.exe` installer
3. Open `http://localhost:8080/programmer` in a browser

OpenAVC is running as a Windows service. It generates zero network traffic until you add and connect to AV devices. You can explore the full Programmer interface, build a project, and test with the built-in simulator without any AV hardware. The touch panel UI is at `http://localhost:8080/panel`.

### Option 1b: From source (any platform, no network impact)

1. Install Python 3.11+ and Node.js 18+ on any Windows or Linux machine
2. Download OpenAVC from [github.com/open-avc/openavc](https://github.com/open-avc/openavc)
3. Open a terminal in the downloaded directory and run:

```
cd openavc
pip install -r requirements.txt
cd web/programmer && npm install && npm run build && cd ../..
python -m server.main
```

4. Open `http://localhost:8080/programmer` in a browser on the same machine

At this point OpenAVC is running on localhost only. It generates zero network traffic.

### Option 2: Connect to AV devices

If the PC running OpenAVC can already reach the AV equipment on the network (same subnet or routable), you can add devices in the Programmer interface by IP address. OpenAVC will open outbound TCP connections to those devices. No bind address change is needed for this, since device control is outbound traffic from the OpenAVC host.

If you also want a separate tablet or browser on another machine to access the touch panel, enable network access as described in the "Who can access the web interface" section above.

### Option 3: Docker

```
docker run -d \
  -p 8080:8080 \
  -e OPENAVC_PROGRAMMER_PASSWORD=your-password \
  -v openavc-data:/data \
  ghcr.io/open-avc/openavc
```

Access at `http://<host-ip>:8080/programmer`. Docker deployments accept network connections by default. The container's network requirements are identical to a bare-metal installation.

---

## Frequently Asked Questions

**Does it need Active Directory or LDAP integration?**
No. OpenAVC uses a simple password for the configuration interface and optional API keys for automation. There is no user database or directory integration.

**Does it phone home?**
Not by default. Update checks (to GitHub's public API) can be enabled or disabled. Cloud connectivity is a separate opt-in feature that is off by default.

**Does it need a database server?**
No. All data is stored in JSON files on the local filesystem. There is no PostgreSQL, MySQL, Redis, or any external data store.

**Does it need its own dedicated hardware?**
No. OpenAVC is a lightweight application, not an appliance. It runs alongside other software on any Windows or Linux machine. The most common deployment is on a PC already in the room (lectern PC, digital signage machine, room scheduling display, etc.). It can also run on an existing server, VM, or Docker host. Resource usage is minimal (typically under 100 MB RAM, negligible CPU when idle).

**Does it modify the host system?**
Minimally. The Windows installer creates a Windows service (via NSSM) and adds a firewall rule for port 8080. The Linux install script creates a systemd service and an `openavc` user. Docker and from-source installations make no system modifications. In all cases, application data is confined to a single data directory.

**What if we block all outbound internet?**
OpenAVC will work normally. Update checks will fail silently and cloud features (if configured) will be dormant. All AV control, automation, and UI functionality is fully local.

**Is the source code available for review?**
Yes. OpenAVC is MIT-licensed open source. The full source code, including the cloud agent that handles remote connectivity, is available at [github.com/open-avc/openavc](https://github.com/open-avc/openavc).

---

*Document version: 1.0. For the latest version, see [docs.openavc.com](https://docs.openavc.com).*
