# Getting Started with OpenAVC

OpenAVC is an open-source control platform for AV spaces. Install it on a PC, mini PC, or server, open a browser, and start building. No proprietary processors or licenses required.

## Installation

> **Installer coming soon.** A one-click Windows installer (.exe) and a Linux install script are in development. For now, install from source using the steps below. It takes about 5 minutes.

### What You'll Need

OpenAVC runs on Python and uses a web-based interface. Install these three tools first:

| Tool | What It Does | Where to Get It |
|------|-------------|-----------------|
| **Python 3.11+** | Runs the OpenAVC server | [python.org/downloads](https://www.python.org/downloads/) |
| **Node.js 18+** | Builds the programming interface | [nodejs.org](https://nodejs.org/) |
| **Git** | Downloads the OpenAVC source code | [git-scm.com](https://git-scm.com/) |

When installing Python, check **"Add Python to PATH"** when prompted. For Node.js, the default installer settings are fine.

### Download and Set Up

Open a terminal (PowerShell on Windows, Terminal on Mac/Linux) and run:

```bash
git clone https://github.com/open-avc/openavc.git
cd openavc
pip install -r requirements.txt
cd web/programmer && npm install && npm run build && cd ../..
```

That's it. OpenAVC is ready to run.

## Start OpenAVC

The easiest way to start everything:

```bash
python dev.py
```

This single command:

1. Builds the Programmer IDE frontend
2. Starts a PJLink projector simulator (for testing without hardware)
3. Starts the OpenAVC server on port 8080

## Manual Launch

When you need more control, start components individually:

```bash
# Start the PJLink simulator (optional, for testing)
python -m tests.simulators.pjlink_simulator

# Start the server (in another terminal)
python -m server.main
```

## Access the Web UIs

| URL | Purpose |
|-----|---------|
| http://localhost:8080/panel | End-user touch panel |
| http://localhost:8080/programmer | Programming IDE |
| http://localhost:8080/api/status | REST API status check |

## First Steps: Explore the Demo

When you start OpenAVC for the first time, a set of starter projects are available in the **Project Library**. The "Simple Projector" starter project includes a PJLink projector, and dev.py also starts a PJLink simulator on your machine so you can test without real hardware.

### 1. Open the Programmer IDE

Navigate to http://localhost:8080/programmer in your browser.

### 2. Open a Starter Project

Click **Program** in the sidebar. At the bottom, you'll see the **Project Library** with starter projects (Simple Projector, Conference Room, Classroom, Advanced AV Suite). Click **Simple Projector** and then **Open** to load it.

### 3. Explore the Sidebar

The sidebar has these sections:

- **Dashboard.** System status at a glance.
- **Program.** Create and manage projects.
- **Devices.** Connected equipment, driver library, and network discovery.
- **State.** Variables, device states, and activity feed.
- **UI Builder.** Visual drag-and-drop panel designer.
- **Macros.** Sequence-based automation.
- **Scripts.** Python scripting with Monaco editor.
- **Plugins.** Install and configure system plugins.
- **Inter-System.** Communication between OpenAVC instances.
- **AI Assistant.** AI-powered help and automation.
- **Cloud.** Cloud platform connection and monitoring.
- **Log.** Real-time system log and state changes.

### 4. Check Device State

Click **Devices** in the sidebar. You'll see the PJLink projector. Since the simulator is running locally, the device should connect automatically and show a green indicator. Click it to see its live state (power, input, lamp hours) and test commands.

### 5. Test a Command

In the Device View, select the projector and use the command testing panel. Choose "power_on" and click Send. Watch the state update in real-time.

### 6. Open the Panel UI

Navigate to http://localhost:8080/panel in another tab. This is what end users see on a touchscreen. Press the buttons and watch commands flow through the system.

## Next Steps

- [Programmer Overview](programmer-overview.md). Learn the IDE and core concepts
- [Devices and Drivers](devices-and-drivers.md). Add equipment and manage drivers
- [UI Builder](ui-builder.md). Design touch panel pages
- [Macros and Triggers](macros-and-triggers.md). Build automation without code
- [Scripting Guide](scripting-guide.md). Write Python automation scripts
- [Creating Drivers](creating-drivers.md). Build drivers for your AV equipment
