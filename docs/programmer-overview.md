# Programmer IDE Overview

A tour of the OpenAVC Programmer IDE: what each section does and how to approach building a room control system.

> The Programmer IDE is at http://localhost:8080/programmer. See [Getting Started](getting-started.md) if you haven't installed OpenAVC yet.

## Overview

The Programmer IDE is a web-based design environment with these sidebar sections:
- **Dashboard**: System status at a glance
- **Program**: Create and manage projects
- **Devices**: Add equipment, test commands, browse drivers, and discover devices
- **State**: Variables, device states, and activity feed
- **UI Builder**: Visual drag-and-drop panel designer
- **Macros**: Build command sequences without code
- **Scripts**: Python scripting with Monaco editor
- **Plugins**: Install, enable, and configure plugins (MQTT, Stream Deck, etc.)
- **Inter-System**: Inter-system communication
- **AI Assistant**: AI-powered help and automation
- **Cloud**: Cloud platform connection and monitoring
- **Log**: Real-time system log and state changes

If you have used Crestron SIMPL or Extron Global Configurator, the workflow will feel familiar: add hardware, define logic, build a touch panel, and test it live. The difference is that everything runs in a browser and there is no proprietary hardware required.

## Understanding State, Variables, and Events

Before diving into the IDE features, it helps to understand three core concepts:

**Device States** are live values reported by your hardware: power status, input selection, volume levels, lamp hours. These update automatically as devices send data. You never set these yourself. State keys look like `device.projector.power` or `device.dsp.level`. You can bind UI elements directly to device states.

**Variables** are values you create for your program logic. Things like room mode, system status text, or custom flags. Use variables when you need to track something no device reports. State keys look like `var.room_active` or `var.current_source`. Variables can optionally be *bound* to a device state key with a value map, so they auto-sync without writing code. Variables can be marked as persistent so their values survive server restarts.

**Events** are one-shot notifications. A button was pressed, a schedule fired, the system started. Events trigger actions but don't store a value. If nothing is listening when an event fires, it's lost. State changes (both device and variable) are durable. You can always read the current value.

**The reactive rule:** Every state change automatically notifies everything that depends on it. No polling, no loops, no "watching" in the traditional AV sense. If a projector turns on, every macro trigger, UI binding, and script listening for that change fires instantly.

### When to use what

1. **Need to show a device value on the panel?** Bind the UI element directly to the device state key (e.g., `device.projector.power`).
2. **Need a friendly version of a device value?** Create a variable bound to the device state with a value map (e.g., `on` → `Ready`).
3. **Need to track something no device reports?** Create a manual variable (e.g., `var.room_mode`).
4. **Need to react when something happens?** Use a macro trigger on the state change, or an event trigger for button presses and schedules.
5. **Need to control multiple similar devices together?** Create a device group and use a Group Command macro step to send commands to all of them at once.
6. **Need different behavior depending on system state?** Use conditional macro steps (if/else branching) or skip-if guards on individual steps.

## Dashboard

The Dashboard is the landing page of the Programmer IDE, giving you a system status overview at a glance.

- **Device grid**: shows all configured devices with color-coded connection indicators (green = connected, red = disconnected, gray = disabled)
- **Active triggers**: lists triggers that are currently enabled so you can see what automation is running
- **Script count and trigger count**: quick summary of how much logic is in the project
- **Cloud status**: shows whether the system is paired to OpenAVC Cloud and the connection state
- **Uptime**: how long the server has been running since last restart
- **Tracked variables**: any variable with "Show on Dashboard" enabled displays its live value here, useful for monitoring room state without opening the Variables view
- **Recent activity**: a feed of recent system log entries so you can spot errors or confirm actions without switching to the Log view

## Program

A project is a single `.avc` file (JSON format) that defines everything about a room: devices, UI layout, macros, scripts, variables, schedules, and triggers. Projects are stored in `projects/<name>/project.avc`.

The Program view shows:
- Project name and description
- Import/export project files
- System settings (theme, accent color)

To start a new room, click **Program** in the sidebar, give it a name (e.g., "Board Room 201"), and save. This creates the `.avc` file and a `scripts/` folder for any Python automation you add later.

The **Project Library** (visible at the bottom of the Program view) stores saved project files for reuse. OpenAVC ships with three starter projects (Simple Projector, Conference Room, Classroom) that you can open, modify, or delete like any other project.

- **New**: start with a blank project
- **Save As**: save the current project to the library for later reuse
- **Open**: load a saved project (replaces the running one)
- **Duplicate**: copy a saved project under a new name
- **Import/Export**: download project files as `.avc` or `.zip`, upload files from other instances
- **Delete**: remove a saved project from the library

OpenAVC automatically creates a backup before important operations like opening a different project, creating a blank project, AI changes, and cloud config pushes. Backups are ZIP files that include the project file, scripts, and assets, so restoring always returns you to a complete working state.

### Backups

The **Backups** section at the bottom of the Program view lists your backup history with the reason each was created. Click **Restore** on any backup to replace the current project with that snapshot. You can also click **Create Backup** to save a manual checkpoint at any time. A periodic auto-backup runs every 30 minutes if the project has been modified.

## Log View

Two tabs for monitoring and debugging:

### System Log

Real-time log stream filterable by:
- **Level**: debug, info, warning, error
- **Source**: device, script, macro, trigger, system

The log shows timestamped entries as they happen. Filter to "error" level to quickly find problems. Filter to a specific device name to see all communication with that device.

### State Changes

Timestamped list of all state changes showing:
- **Key**: the state variable that changed (e.g., `device.projector_main.power`)
- **Old value**: previous value
- **New value**: current value
- **Source**: what caused the change (device poll, macro, script, UI)

This tab is essential for debugging bindings and triggers. If a button feedback is not updating, check here to see if the variable is actually changing. If a trigger is not firing, verify that the state change you expect is actually happening.

## Typical Workflow

Here is the recommended order for building a new room:

1. **Create a project** in the Program view with a descriptive name
2. **Add all devices** and test commands individually to confirm connectivity
3. **Create variables** for tracking room state (active, current source, etc.)
4. **Build macros** for system on, system off, and each source selection
5. **Add triggers** for automatic behaviors (scheduled shutdown, auto-off)
6. **Design the UI** with pages, buttons, labels, and status indicators
7. **Wire bindings** connecting UI elements to macros, commands, and state
8. **Test in preview mode** with live equipment
9. **Export the project** as a backup

Steps 4-7 are iterative. You will go back and forth between macros, variables, and UI bindings as you refine the system.

## See Also

- [Devices and Drivers](devices-and-drivers.md). Adding equipment, testing commands, driver library, device discovery.
- [UI Builder](ui-builder.md). Visual panel designer for touch panels.
- [Macros and Triggers](macros-and-triggers.md). Command sequences and automation conditions.
- [Variables and State](variables-and-state.md). User variables, device states, and activity monitoring.
- [Scripting Guide](scripting-guide.md). Complete Python scripting API.
