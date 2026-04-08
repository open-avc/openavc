# Plugins

Plugins extend OpenAVC with system-wide integrations, control surfaces, sensors, and utility services. Unlike drivers (which translate a single device protocol), plugins operate across the entire system, connecting to external platforms, adding physical control surfaces, or bridging other ecosystems.

## Plugins vs Drivers

| | Drivers | Plugins |
|---|---------|---------|
| **Scope** | One device, one protocol | System-wide |
| **Purpose** | Translate commands and state for a specific piece of hardware | Add capabilities that span devices, connect external systems, or add physical interfaces |
| **Examples** | PJLink projector, Extron switcher, Samsung display | MQTT bridge, Elgato Stream Deck, Dante DDM, occupancy analytics |

**Rule of thumb:** If it talks to one device over TCP/serial/HTTP, it's a driver. If it bridges systems, adds a control surface, or provides a service, it's a plugin.

---

## Installing Plugins

### From the Community Repository

1. Open the **Plugins** view in the Programmer IDE sidebar
2. Click the **Browse** tab
3. Find the plugin you want (use search or filter by category)
4. Click **Install**

The plugin is downloaded and saved to `plugin_repo/`. It appears in the Installed tab as disabled.

### Category Filters

| Category | What It Contains |
|----------|-----------------|
| Control Surfaces | Physical button panels, fader banks, keypads (Stream Deck, X-Keys, MIDI) |
| Integrations | Protocol bridges and external platform connections (MQTT, Dante, Home Assistant) |
| Sensors | Environmental inputs (occupancy, temperature, ambient light) |
| Utility | Analytics, voice control bridges, logging services |

### Platform Compatibility

Plugins declare which platforms they support. The Browse view filters by your current platform by default. Use the "Show all platforms" toggle to see plugins for other platforms. These show a "Not available for [platform]" badge.

| Platform | Description |
|----------|-------------|
| Windows x64 | Windows development machines, AV rack PCs |
| Linux x64 | Docker, VM, rack servers |
| Linux ARM64 | Raspberry Pi 4/5, ARM servers |
| All | Pure Python plugins with no platform-specific dependencies |

---

## Enabling and Configuring Plugins

### Enable a Plugin

1. Go to the **Plugins** view, **Installed** tab
2. Click the toggle next to the plugin name

If the plugin requires configuration before it can start (for example, an MQTT broker URL), a **Setup Dialog** appears with the required fields. Fill them in and click Save.

If the plugin has sensible defaults for all settings, it starts immediately.

### Configure a Plugin

Click a plugin in the Installed list to open its detail view. The **Configuration** tab shows all available settings, auto-rendered from the plugin's configuration schema.

Settings include standard field types (text, numbers, toggles, dropdowns) and OpenAVC-specific types:

| Field Type | Description |
|------------|-------------|
| Text | Free-form string input |
| Number | Integer or decimal with optional min/max |
| Toggle | On/off boolean |
| Dropdown | Select from a list of options |
| State Key | Pick a state key from the system (e.g., `device.projector.power`) |
| Macro | Pick a macro from the project |
| Device | Pick a device from the project |

Configuration changes are auto-saved. If the plugin is running, it restarts automatically with the new settings.

### Disable a Plugin

Click the toggle to disable. The plugin stops and all its runtime effects (state keys, event subscriptions, background tasks) are cleaned up automatically. State keys under `plugin.<id>.*` are fully deleted from the state store (not just set to `None`), so they will not appear in state snapshots while the plugin is stopped. Configuration is preserved, and re-enabling restores everything.

---

## Plugin Status

Each plugin shows a status indicator:

| Status | Icon | Meaning |
|--------|------|---------|
| Running | Green circle | Plugin is active and healthy |
| Stopped | Gray circle | Plugin is disabled (config preserved) |
| Error | Red circle | Plugin crashed or failed its health check |
| Missing | Yellow triangle | Project references a plugin that isn't installed |
| Incompatible | Orange triangle | Plugin is installed but doesn't support the current platform |

### Missing Plugins

When you open a project that uses a plugin you don't have installed, the plugin list shows it with a yellow warning icon and "(not installed)" label. The detail view shows a banner:

> **Plugin Required.** This project uses "MQTT Bridge" which is not installed.

Two options:
- **Install from Community.** Opens the Browse tab to find and install the plugin.
- **Remove Plugin Config.** Removes the plugin reference from the project.

After installing a missing plugin, click **Activate** to start it without restarting the server.

---

## Surface Configurator

Control surface plugins (Stream Deck, MIDI controllers, X-Keys) include a visual Surface Configurator. This is a graphical editor that mirrors the physical layout of your hardware.

### Grid Surfaces

For button grids like the Elgato Stream Deck. The configurator shows the exact layout (e.g., 4 columns x 2 rows for Neo). Click any button to configure it:

- **Label.** Text shown on the button.
- **Button Mode.** How the button behaves (see below).
- **Press Action.** What happens when pressed (macro, device command, set variable, navigate page).
- **Visual Feedback.** State-driven appearance changes: pick a state key, set a condition, and choose active/inactive colors and labels.

Grid surfaces support **pages**. Tab through multiple pages of button assignments using the page tabs at the top.

#### Button Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| Tap | Fires action on press (default) | Most buttons |
| Toggle | Fires On or Off action based on current state | Power on/off, mute/unmute |
| Hold Repeat | Fires action repeatedly while held (configurable interval) | Volume ramp, camera pan/tilt |
| Tap / Hold | Short press = tap action, long press = long press action | Quick vs advanced action |

**Toggle** is state-aware. You pick a state key to watch, set the value that means "on," and configure separate On and Off actions. The button reads the state to decide which action to fire. You can also set On Label and Off Label so the button text changes automatically (e.g., "ON" when active, "OFF" when inactive).

#### Conditional Labels

The Visual Feedback section supports separate label text for active and inactive states. Toggle mode also has its own On/Off labels built in. Both update the physical button display in real time as state changes.

### Strip Surfaces

For linear controllers like MIDI fader banks. Shows a horizontal row of faders. Each fader can be bound to a state key for position feedback.

### Custom Surfaces

For controllers with mixed control types (buttons, faders, encoders). Controls are positioned freely on a canvas. Each control type has its own configuration:

| Control | Configuration |
|---------|--------------|
| Button | Press action, label, button mode, visual feedback (colors + conditional labels) |
| Fader | State key binding, min/max range, label |
| Encoder | Increment/decrement macros, label |
| Indicator | State key binding, color map, label (read-only) |

### Routing Matrices

For audio/video routing plugins (Dante, NDI). Shows a grid of inputs (columns) and outputs (rows). Click a crosspoint to route or unroute. Rows and columns are populated dynamically from live state.

---

## Uninstalling Plugins

1. **Disable** the plugin first (if it's running)
2. Go to the **Browse** tab
3. Click **Uninstall** on the plugin

The plugin files are removed from `plugin_repo/` and its configuration is removed from the project. Uninstalling a running plugin is blocked. You must disable it first.

---

## Project Portability

Plugins are tracked in the project file just like drivers. When you export a project, installed plugins are bundled in the export. When you import a project on another machine:

- Bundled plugins are installed automatically
- Plugins not bundled show as "Missing" with an option to install from the community repository
- Platform-incompatible plugins show a warning (e.g., "Plugin 'Stream Deck' is not compatible with Linux ARM64")

---

## Using AI Assistants

If you use an AI coding assistant (Claude, ChatGPT, Copilot, etc.), you can point it to the `AGENTS.md` file in the [community plugin repository](https://github.com/open-avc/openavc-plugins/blob/main/AGENTS.md). It contains the complete Plugin API, manifest format, configuration schema, and examples in a format optimized for LLM agents. The repository also includes a `validate.py` script that your assistant can use to check its work.

---

## See Also

- [Creating Plugins](creating-plugins.md). Developer guide for building your own plugins
- [Devices and Drivers](devices-and-drivers.md). Adding and managing hardware
- [Creating Drivers](creating-drivers.md). Building device drivers
