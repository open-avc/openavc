# Devices and Drivers

How to add AV equipment to your project, test connectivity, and manage drivers.

> See [Getting Started](getting-started.md) if you haven't installed OpenAVC yet.

## Adding a Device

Use the search box at the top of the device list to filter by name, ID, or driver.

1. Click **Devices** in the sidebar
2. Click **Add Device**
3. Select a driver from the dropdown (e.g., "PJLink Class 1 Projector")
4. Enter a Device ID (e.g., `projector_main`). This is the name used in macros and scripts, so keep it short and descriptive.
5. Enter a display name (e.g., "Main Projector"). This is what appears in the UI.
6. Configure connection settings (host, port, etc.). Defaults are pre-filled from the driver.
7. Click **Add**

The Device ID is permanent and referenced everywhere. Choose a convention and stick with it. Common patterns:

| Convention | Example |
|-----------|---------|
| `type_location` | `projector_main`, `display_lobby` |
| `type_number` | `projector_1`, `switcher_1` |
| `room_type` | `br201_projector`, `br201_switcher` |

## Testing Commands

After adding a device, click it in the device list to open the detail panel:

- **Connection status**: green dot = connected, red dot = disconnected
- **Live state**: real-time values for all state variables (power, input, etc.)
- **Command testing**: select a command from the dropdown, fill any parameters, click **Send**
- **Raw log**: see the bytes sent and received for debugging

Always test commands here before using them in macros. This confirms the device is responding and the driver is parsing responses correctly.

## Device State

Every device exposes state variables at `device.<id>.<property>`. Examples:

- `device.projector_main.power`: "off", "on", "warming", "cooling"
- `device.projector_main.input`: "hdmi1", "hdmi2", "vga1"
- `device.projector_main.lamp_hours`: integer
- `device.switcher_1.input_1_output`: "1", "2", "3"
- `device.dsp_1.mic_1_mute`: true, false

These state keys are the building blocks of your system. They appear in UI bindings, macros, triggers, and scripts. You do not need to memorize them. The IDE provides autocomplete and pickers everywhere a state key is needed.

## Device Management

The device detail panel includes several management actions:

- **Enable/Disable.** Temporarily disable a device without removing it from the project. Disabled devices do not connect or poll.
- **Test Connection.** Test network reachability (ping/TCP connect) without using the driver, useful for diagnosing network issues.
- **Reconnect.** Force an immediate reconnect attempt on a disconnected device.
- **Duplicate.** Create a copy of the device with a new ID, pre-filled with the same driver and settings.
- **Device Log.** Filtered log view showing only activity for the selected device (commands sent, responses received, errors).

## Device Groups

The **Groups** tab in the Devices view lets you create named groups of devices. Groups serve two purposes:

1. **Macro commands.** Use a "Group Command" macro step to send a command to every device in the group at once. For example, power on all projectors with a single step instead of one step per projector. Commands are sent concurrently, and offline devices are skipped automatically.

2. **Organization.** Devices in the same group are visually grouped together in the device list.

A device can belong to multiple groups (e.g., a display might be in both "All Displays" and "Conference Room A").

## Device Settings

Some devices have configurable values that live on the hardware itself, things like NDI source name, device hostname, tally mode, or video format. These are different from connection config (which is stored in the project file).

If a driver defines device settings, they appear in a **Device Settings** section in the device detail panel:

- Each setting shows its current value (read from the device via polling), label, and help text
- Click a setting to edit it. The new value is pushed directly to the device.
- If the device is offline, settings are queued as "pending" and automatically applied when the device reconnects
- Settings marked as `setup` in the driver are prompted when you first add the device to the project

## Bulk Operations

Select multiple devices using the checkboxes in the device list to perform batch operations: enable, disable, or delete several devices at once.

## Driver Library

The Driver Library is inside the **Devices** view. Click **Devices** in the sidebar, then select the **Drivers** tab at the top. It has three sub-tabs: **Installed**, **Create**, and **Browse Community**.

### Installed Tab

The Installed tab shows all drivers available on this system. This includes default drivers that ship with OpenAVC and any community or custom drivers you have installed.

The left panel lists all registered drivers. Click a driver to see its details:

- **Overview.** What the driver controls and which devices it supports.
- **Setup instructions.** Step-by-step guide for connecting and configuring the device.
- **Configuration.** The settings you need to provide (IP address, port, display ID, etc.).
- **Commands.** All available commands with descriptions.
- **State variables.** What state the driver exposes (power, volume, input, etc.).
- **Uninstall.** Remove the driver from the system (disabled if a device in the current project uses it).

### Create Tab

Build custom drivers without code using the visual editor:

1. **General.** Name, manufacturer, model, category, description.
2. **Transport.** TCP, serial, or HTTP connection settings (port, baud rate, auth, etc.).
3. **State Variables.** Declare what state the driver exposes with types and defaults.
4. **Commands.** Define commands with send strings and parameter substitution.
5. **Responses.** Regex-based response parsing to extract state from device replies.
6. **Polling.** Automatic status queries on a timer (power, input, lamp hours).
7. **Device Settings.** Configurable values that live on the hardware (NDI name, video format, etc.).
8. **Simulator.** Define simulated behavior for testing without real hardware.

For most IP-controlled devices with a documented protocol, the Driver Builder can produce a working driver in 15-30 minutes. For serial devices, set the transport to serial and define the baud rate, data bits, and stop bits in the Transport tab.

See [Creating Drivers](creating-drivers.md) for the complete driver development guide covering all three methods: no-code YAML definitions, the visual Driver Builder, and Python driver classes.

### Browse Community Tab

Search and install drivers from the [community driver library](https://github.com/open-avc/openavc-drivers):

1. Click the **Browse Community** tab
2. Search by manufacturer, model, or category
3. Click a driver card to see full details (description, protocols, ports, transport)
4. Click **Install** for one-click download and installation
5. The driver is immediately available in the Installed tab and the Add Device dropdown

Available categories include projectors, displays, matrix switchers, DSPs, cameras, lighting, and more. If a driver does not exist for your equipment, you can build one.

### Uninstalling Drivers

To remove a driver you no longer need:

1. Open the **Installed** tab in Driver Library
2. Click the driver in the left panel
3. Scroll to the bottom and click **Uninstall Driver**

You cannot uninstall a driver while a device in the project is using it. Remove the device first, then uninstall the driver. You can always reinstall from Browse Community.

## Device Discovery

The Discovery panel is inside the **Devices** view. Click **Devices** in the sidebar, then select the **Discovery** tab. It scans your network for AV devices and helps you add them to your project without manually entering IP addresses and driver settings.

### Running a Scan

1. Click **Devices** in the sidebar, then select the **Discovery** tab
2. Select the subnet(s) to scan (auto-detected from your network interfaces)
3. Click **Start Scan**

The scan runs through several phases: ping sweep, port scanning, protocol probes, passive discovery (mDNS/SSDP), SNMP queries, and hostname resolution. Progress is shown in real time.

### Working with Results

Each discovered device shows:

- **IP address and hostname** (if resolved)
- **Manufacturer** (from MAC address OUI lookup)
- **Open ports** and detected protocols
- **Suggested driver** with a confidence score
- **Device details** from protocol responses (model, firmware, etc.)

Click a device card to see full details. If a matching driver is found:

- Click **Add to Project** to add the device with pre-filled driver and config
- If the driver is a community driver you haven't installed, click **Install & Add** to install the driver and add the device in one step

### Scan Options

- **Scan Depth.** Choose how deep the scanner goes:
  - **Quick.** Fast re-scan. Ping sweep, port scanning, protocol probes, and short passive listening window. Skips NetBIOS name resolution and SNMP Entity MIB.
  - **Standard** (recommended). Full scan including NetBIOS/SMB for Windows device names and SNMP Entity MIB for detailed hardware info. Longer passive listening window for mDNS and SSDP devices.
  - **Thorough.** Extended port range, longest passive listening window. Takes longer but finds everything on the network.
- **SNMP.** Enable SNMP v2c queries for richer device identification (community string configurable).
- **Reduce network load.** Slows down the scan to reduce traffic. Use this on networks with strict IDS/IPS policies or where IT has asked you to scan carefully.
- **Extra Subnets.** Add subnets beyond the auto-detected ones.
- **Export.** Download scan results as a text report.

## See Also

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow
- [UI Builder](ui-builder.md). Visual panel designer for touch panels
- [Creating Drivers](creating-drivers.md). Build device drivers (YAML, Driver Builder, and Python)
