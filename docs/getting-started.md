# Getting Started with OpenAVC

OpenAVC is an open-source control platform for AV spaces. Install it on a PC, mini PC, or server, open a browser, and start building. No proprietary processors or licenses required.

## Choose Your Installation Method

| Method | Best For | Time |
|--------|----------|------|
| [Windows Installer](#windows-installer) | AV rack PCs, mini PCs, laptops | 2 minutes |
| [Docker](#docker) | Servers, multi-room deployments | 2 minutes |
| [Linux Install Script](#linux-install-script) | Dedicated Linux machines | 3 minutes |
| [From Source](#install-from-source) | Development, contributing | 5 minutes |

All methods are functionally identical. Choose whatever fits your environment.

## Windows Installer

The fastest way to get started on Windows.

1. Download `OpenAVC-Setup-x.x.x.exe` from [GitHub Releases](https://github.com/open-avc/openavc/releases)
2. Run the installer and follow the prompts
3. OpenAVC starts automatically as a Windows service

**What the installer does:**

- Installs to `C:\Program Files\OpenAVC`
- Stores project data in `C:\ProgramData\OpenAVC`
- Creates a Windows service (`openavc`) that starts automatically on boot
- Adds a system tray app for quick access to the web interface
- Opens Windows Firewall for port 8080
- Creates Start Menu and optional Desktop shortcuts

After installation, open **http://localhost:8080/programmer** in your browser to start building.

> The Windows installer pre-configures OpenAVC to accept connections from other devices on the network (tablets, phones, other PCs). If you only need local access, no configuration changes are needed.

## Docker

Run OpenAVC in a container with persistent data:

```bash
docker run -d \
  --name openavc \
  -p 8080:8080 \
  -v openavc-data:/data \
  --restart unless-stopped \
  openavc/openavc:latest
```

Or with Docker Compose:

```yaml
services:
  openavc:
    image: openavc/openavc:latest
    container_name: openavc
    ports:
      - "8080:8080"
    volumes:
      - openavc-data:/data
    restart: unless-stopped

volumes:
  openavc-data:
```

```bash
docker compose up -d
```

The container binds to `0.0.0.0` by default (network-accessible). Access at **http://\<host-ip\>:8080/programmer**.

For serial/USB device passthrough, add `devices: ["/dev/ttyUSB0:/dev/ttyUSB0"]` to the compose file.

## Linux Install Script

One command to install on any Linux machine:

```bash
curl -sSL https://get.openavc.com | sudo bash
```

This installs OpenAVC to `/opt/openavc`, creates a systemd service, and starts the server. Data is stored in `/var/lib/openavc`. The service starts automatically on boot.

After installation:

```bash
sudo systemctl status openavc    # Check status
sudo journalctl -u openavc -f    # View logs
```

Access at **http://localhost:8080/programmer**. By default, the server only accepts local connections. To allow network access from tablets and other devices, set the bind address:

```bash
sudo systemctl edit openavc
# Add: Environment=OPENAVC_BIND=0.0.0.0
sudo systemctl restart openavc
```

## Install from Source

For development or when you want full control over the installation.

### Prerequisites

| Tool | What It Does | Where to Get It |
|------|-------------|-----------------|
| **Python 3.11+** | Runs the OpenAVC server | [python.org/downloads](https://www.python.org/downloads/) |
| **Node.js 18+** | Builds the web interfaces | [nodejs.org](https://nodejs.org/) |
| **Git** | Downloads the OpenAVC source code | [git-scm.com](https://git-scm.com/) |

When installing Python, check **"Add Python to PATH"** when prompted. For Node.js, the default installer settings are fine.

### Download and Build

Open a terminal (PowerShell on Windows, Terminal on Mac/Linux) and run:

```bash
git clone https://github.com/open-avc/openavc.git
cd openavc
pip install -r requirements.txt
cd web/programmer && npm install && npm run build && cd ../..
cd web/simulator && npm install && npm run build && cd ../..
```

### Start the Server

```bash
python -m server.main
```

This starts the OpenAVC server on port 8080, bound to localhost only.

## Access the Web Interface

Once OpenAVC is running, open a browser:

| URL | Purpose |
|-----|---------|
| http://localhost:8080/programmer | Programmer IDE (where you build projects) |
| http://localhost:8080/panel | End-user touch panel |
| http://localhost:8080/api/health | Health check endpoint |

> **First-time startup:** When the server first starts, it initializes in the background. You may briefly see a loading page while the engine starts up. This only takes a few seconds.

## First Steps: Explore the Demo

When you start OpenAVC for the first time, a set of starter projects are available in the **Project Library**.

### 1. Open the Programmer IDE

Navigate to http://localhost:8080/programmer in your browser.

### 2. Open a Starter Project

Click **Program** in the sidebar. At the bottom, you'll see the **Project Library** with starter projects (Simple Projector, Conference Room, Classroom, Advanced AV Suite). Click **Simple Projector** and then **Open** to load it.

### 3. Explore the Sidebar

The sidebar has these sections:

- **Dashboard.** System status overview with device grid, active triggers, tracked variables, and panel access URLs.
- **Program.** Create, save, and manage projects. Import/export. Backups.
- **Devices.** Connected equipment, driver library, device groups, and network discovery.
- **State.** Variables, device states, and activity feed.
- **UI Builder.** Visual drag-and-drop panel designer.
- **Macros.** Sequence-based automation with triggers.
- **Scripts.** Python scripting with Monaco editor.
- **Plugins.** Install and configure system plugins.
- **Inter-System.** Communication between OpenAVC instances.
- **AI Assistant.** AI-powered help and automation (requires cloud connection).
- **Cloud.** Cloud platform connection and monitoring.
- **Log.** Real-time system log and state changes.
- **Settings.** Server configuration (port, bind address, logging).
- **Updates.** Check for and install OpenAVC updates.

At the bottom of the sidebar, the **Simulate Devices** button (play icon) starts the device simulator so you can test without real hardware.

### 4. Start the Simulator

Click the **play button** at the bottom of the sidebar. This starts virtual devices that respond to commands just like real hardware. The Simulator UI opens in a new browser tab where you can interact with the virtual devices from the "hardware side."

### 5. Check Device State

Click **Devices** in the sidebar. You'll see the devices from the starter project. With the simulator running, they should connect automatically and show green indicators. Click a device to see its live state (power, input, lamp hours) and test commands.

### 6. Test a Command

In the Device View, select the projector and use the command testing panel. Choose "power_on" and click Send. Watch the state update in real-time.

### 7. Open the Panel UI

Navigate to http://localhost:8080/panel in another tab. This is what end users see on a touchscreen. Press the buttons and watch commands flow through the system.

**Accessing from a tablet or phone:** By default, the server only accepts connections from the local machine (except for Windows Installer and Docker, which accept network connections out of the box). To open the panel on another device:

1. Go to **Settings** in the Programmer IDE sidebar
2. Change the **bind address** to `0.0.0.0`
3. Save and restart the server
4. The **Dashboard** will show the panel URL with your machine's IP address (e.g., `http://192.168.1.100:8080/panel`) that you can open on any device on the same network

## Environment Variables

These environment variables override the corresponding `system.json` settings. Useful for Docker, systemd, and scripted deployments.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAVC_PORT` | `8080` | HTTP server port |
| `OPENAVC_BIND` | `127.0.0.1` | Bind address (`0.0.0.0` for network access) |
| `OPENAVC_PROGRAMMER_PASSWORD` | (none) | Password for Programmer IDE access |
| `OPENAVC_API_KEY` | (none) | API key for REST/WebSocket integrations |
| `OPENAVC_LOG_LEVEL` | `info` | Log level (debug, info, warning, error) |
| `OPENAVC_DATA_DIR` | (platform default) | Override data directory location |
| `OPENAVC_LOG_DIR` | (platform default) | Override log directory location |

See the [Deployment Guide](deployment.md) for the full configuration reference.

## Next Steps

- [Programmer Overview](programmer-overview.md). Learn the IDE and core concepts
- [Devices and Drivers](devices-and-drivers.md). Add equipment and manage drivers
- [Device Simulator](simulator.md). Test without real hardware
- [UI Builder](ui-builder.md). Design touch panel pages
- [Macros and Triggers](macros-and-triggers.md). Build automation without code
- [Scripting Guide](scripting-guide.md). Write Python automation scripts
- [Deployment Guide](deployment.md). Production deployment, configuration, and security
- [Creating Drivers](creating-drivers.md). Build drivers for your AV equipment
