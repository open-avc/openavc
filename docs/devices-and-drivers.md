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
6. Configure connection settings (host, port, etc.). Defaults are pre-filled from the driver. A device that supports serial shows a connection picker with three choices: **Network (IP)**, **Direct serial** (pick a serial port detected on this server, including USB-to-serial adapters), or **Through a bridge** (route it over a serial-to-Ethernet bridge such as a Global Cache iTach). See [Connecting Devices Through a Bridge](connecting-through-a-bridge.md). A field marked with a red asterisk is required: the device is not added until it is filled in, and the message names the field.
7. Click **Add Device**

The Device ID is permanent and referenced everywhere. Choose a convention and stick with it. Common patterns:

| Convention | Example |
|-----------|---------|
| `type_location` | `projector_main`, `display_lobby` |
| `type_number` | `projector_1`, `switcher_1` |
| `room_type` | `br201_projector`, `br201_switcher` |

## Serial and USB-to-Serial Connections

A driver that speaks RS-232 can connect either directly to a serial port on the machine running OpenAVC or [through a bridge](connecting-through-a-bridge.md). For a direct connection, choose **Direct serial** in the connection picker and pick the port from the list.

Inexpensive USB-to-serial adapters show up here automatically. Plug one into the OpenAVC server and it appears as a selectable port, named like `COM3` on Windows or `/dev/ttyUSB0` on Linux. The list shows the server's own ports, so the adapter must be plugged into the machine running OpenAVC, not the computer you are browsing from. Click **Refresh** after plugging one in, or choose **Enter path manually** for a port that is not attached yet.

When you pick a USB adapter that reports a serial number, OpenAVC saves that serial number and reconnects to the adapter even if the operating system assigns a different port name after a reboot or replug. The baud rate, parity, data bits, stop bits and flow control start at the driver's own settings (9600 8N1 with no flow control when the driver has none). Change any that the device's manual gives differently.

## Testing Commands

After adding a device, click it in the device list to open the detail panel:

- **Connection status**: green dot = connected, red dot = disconnected, amber dot = paused or restarting (hover for which). A device paused for a Device Audit or for Live Test in the Driver Builder says so on its page, with **Resume** to reconnect it.
- **Live state**: real-time values for all state variables (power, input, etc.). A fractional reading is rounded to the precision its driver declares; hover it for the full value.
- **Command testing**: select a command from the dropdown, fill any parameters, click **Send**. The line below says it was sent, and shows the device's answer when the driver returns one.
- **Raw log**: see the bytes sent and received for debugging
- **State Changes**: the device's recent changes, old and new value, including the ones made before you opened the tab

Always test commands here before using them in macros. This confirms the device is responding and the driver is parsing responses correctly.

## No-Code Commands and Responses

Some devices don't have a dedicated driver yet but speak a simple text protocol you can read from their manual. For these, add one of the **Generic** devices and define its commands and responses right on the device page. No driver file required.

The Generic devices are:

- **Generic TCP Device**: any networked device with a text protocol.
- **Generic Serial Device**: any RS-232/RS-485 device, directly on a serial port or [through a bridge](connecting-through-a-bridge.md).
- **Generic HTTP/REST Device**: any device with an HTTP API.
- **IR Device**: anything driven by an infrared remote (a TV, cable box, or AVR), through an IR bridge. Instead of commands and responses, you build a set of IR codes: learn them from the remote, paste Pronto hex, or search a database. See [Connecting Devices Through a Bridge](connecting-through-a-bridge.md#controlling-an-ir-device-through-a-bridge).

After adding a Generic device, open it and find the **Commands & Responses** section.

### Commands

Each command is a button you can fire from the device, use in a macro, or call from a script. Add a row for each one:

- **Label**: the friendly name (for example, "Power On").
- **ID**: the short name used in macros and scripts. It fills in automatically from the label.
- **Send**: the exact string the device expects (for example, `PWR ON`).

Set the **Line ending** once (CR, LF, or CRLF). It is added to every command, so you don't type it on each row, and it is used to split incoming replies.

To enter a value when you fire a command, put a placeholder in curly braces. `VOL {level}` creates a command that asks for a level and sends `VOL 42`.

Commands are sent as text, but you can include raw bytes with `\xHH` escapes: `\x1B` sends the ESC byte (0x1B), `\xFF` sends 0xFF, and `\r` `\n` `\t` send carriage return, line feed, and tab. This covers text protocols and fixed binary commands. Protocols that need a value computed from the message (a checksum, CRC, or length prefix) aren't a fit for a Generic device. Build a driver for those.

For HTTP devices, a command is a method, a path, and an optional JSON body instead of a single string.

**Pasting a list**: click **Paste** and enter one command per line as `Label = string to send`. This fills the table quickly when you're copying a handful of commands out of a manual.

### Responses

A response turns a reply from the device into a live value you can show on a panel or react to in a macro or trigger. Add a row, pick what the reply looks like, and name the variable to set:

- **Contains text**: when the reply contains a fixed string, set the variable to a fixed value. A reply containing `PWR ON` sets `power` to `on`. Add one row per state.
- **Has a number after**: grab the number that follows a prefix. `VOL=42` sets `volume` to `42`.
- **Has text after**: grab the rest of the line after a prefix. `NAME=Main Stage` sets `name` to `Main Stage`.
- **Has a JSON field**: for a JSON reply, read one field by name. A dot reaches inside: `status.power` reads `power` from `{"status": {"power": "on"}}` and sets the variable to `on`.
- **Matches (advanced)**: for anything else, write a regular expression and pick the capture group.

The variable appears on the device right away and is usable everywhere as `$device.<id>.<name>`: in macro steps, in triggers, and in UI Builder bindings (pick it from the `$` picker on a command parameter or a control's **Value** binding). See [Variables and State](variables-and-state.md) for how `$` references work.

In simulation, a command with a value changes the simulated device when its ID or its placeholder names a response's variable, and the simulator answers with that response's reply. With the `volume` response above, `VOL {level}` with the ID `set_volume`, or `VOL {volume}`, sets `volume` to 42 and gets back `VOL=42`.

### Polling for live status

Most devices don't announce changes on their own. To keep a status value live (for example, the projector's real power state), you poll it: send a "get status" command on a timer and let its response rule update the variable.

1. Set a **Poll Interval** (in seconds) when you add or edit the device, alongside the connection settings. 0 turns polling off.
2. In the Commands table, check **Poll** on the commands you want sent on that interval (typically your status queries).

For example, a "Get Power Status" command checked for polling, with an interval of 15, asks the device for its power state every 15 seconds, and your power response rule keeps the variable current. Leave Poll unchecked on action commands like Power On.

In simulation, an HTTP device answers each command checked for Poll with the current values of its **Has a JSON field** rows, so a value you change in the Simulator reaches the device at the next poll.

### Send raw

Use the **Send raw** box to type a string and send it immediately, without saving it as a command. This is handy for trying a command from the manual before you add it, or for one-off diagnostics. The line ending is added automatically.

### Testing with the simulator

A Generic device works with the [Device Simulator](simulator.md): start simulation and the simulator stands in for the real device using the commands and responses you defined. Drive a variable from the simulator's controls and your panel and macros react to it. Commands that echo a status string (for example, a device that replies `PWR ON` to confirm power) round-trip automatically. Commands that trigger some other reply on the real device won't be answered by the simulator, since the device-side behavior isn't part of what you defined. Drive those values from the simulator controls instead.

## Quick Actions

Some drivers promote the commands you reach for most to a row of **Quick Action** buttons at the top of the device detail panel, so you don't have to hunt through the full Send Command list. A button either fires its command immediately, opens a short dialog when the command needs values, or asks you to confirm first (for anything disruptive, like a reboot). The full Send Command list below still contains everything.

Which buttons appear is up to the driver. A button may also be hidden until it's relevant — for example, only showing while the device is offline. If a driver doesn't declare any, there's simply no strip. Driver authors add them with `quick_actions` / `actions` (see [Creating Drivers](creating-drivers.md)).

One button shows up on its own: if the device has its own web page (it answers on a standard web port), an **Open Web UI** button appears and opens that page in a new browser tab. No driver setup is needed for most equipment.

## Device State

Every device exposes state variables at `device.<id>.<property>`. Examples:

- `device.projector_main.power`: "off", "on", "warming", "cooling"
- `device.projector_main.input`: "hdmi1", "hdmi2", "vga1"
- `device.projector_main.lamp_hours`: integer
- `device.switcher_1.input_1_output`: "1", "2", "3"
- `device.dsp_1.mic_1_mute`: true, false

These state keys are the building blocks of your system. They appear in UI bindings, macros, triggers, and scripts. You do not need to memorize them. The IDE provides autocomplete and pickers everywhere a state key is needed. The Programmer shows true and false as Yes and No; the stored value does not change.

## Child Entities

Some devices are really a controller for many sub-units. An AV-over-IP matrix manages hundreds of encoders and decoders, a DSP has dozens of zones, a presentation switcher has video-wall presets. When a device's driver supports this, its detail panel gains a **Child Entities** tab. Devices without sub-units don't show the tab.

The tab groups the sub-units by type (Encoders, Decoders, Zones, and so on), each in its own list with a live count. For each type you get:

- **A searchable, scrollable list.** Filter by ID, label, or the device-reported name. The list stays fast even with thousands of entries.
- **Summary columns** chosen by the driver, plus an online indicator, so you can scan status at a glance.
- **Inline labels.** Click a row's label to give the sub-unit a friendly name ("Lobby TV", "Stage Camera"). Your label is saved in the project and is separate from the name the device reports for itself. Where a device names its own sub-units -- AV-over-IP endpoints, DSP blocks -- that name is what the row shows, in grey, until you type one of your own over it. Either way it is the name that shows up in pickers when you build panels, macros, and routing.
- **Refresh from Device.** Re-poll the controller so newly added or removed sub-units appear without reloading. It tells you what it found, including when the answer is nothing.
- **Monitor on any reading.** Expand a row and every reading the sub-unit publishes carries the same **Monitor** tag as a device's own state, with the label, unit and range the driver declares already filled in. That is how a per-channel level or a per-zone fault gets onto the Dashboard and the cloud health card. See [Monitor a reading](variables-and-state.md#monitor-a-reading).

### Reading the status mark

Every row carries one mark, and it has three states:

- **Filled green** — in service.
- **A red ring** — something is wrong. Hover it for the reason: not answering (go and find it), or answering but not running (power-cycle it). Rows in this state are lifted to the top of the list, counted on the type's tab as "N down", and named on the device page above.
- **A grey ring** — nothing is connected in this position. Some drivers model slots rather than channels: the extension positions on a chained mixer, the card slots on a frame. An empty one is not a fault, so it is not counted, not lifted, and never in the banner.

While a device itself is offline, every sub-unit under it reads as unavailable and says so. They come back as the device does. The sub-units stay listed throughout — panels and macros bound to them keep working and go quiet, instead of pointing at something that has vanished.

The readings stay too, marked **last heard** rather than left looking current. A panel blanks a reading it cannot trust, because a control in a room must not assert a number that is no longer true; this page is where you work out what happened, and "it was 511 when we lost it" is usually the thing you came for. The same mark appears on the device's own Live State list. What is *not* marked is everything the platform knows regardless — whether the device is connected, the fault reason, a sub-unit's online state, the name you gave it — because those are true right now, and are how you know the rest is stale.

A device's sub-units are listed as soon as the driver knows how many there are, which for most drivers is before the device has ever been reached. You can build panels and macros against a room's equipment before any of it is racked.

**Some settings fill themselves in.** A setting marked **From the device** in the device form (a model, a channel count, a MAC address) is what you pick while the equipment is not there yet, and what the simulator plays. When the real device connects, what it reports is saved into that setting, so a wrong pick is corrected and the value is kept after a restart. Simulation never changes it.

Some drivers cover a whole family of frames, so how many sub-units *this* one has is a setting on the device rather than something it reports — a matrix switcher with an **Input Count** and an **Output Count**, for example. Until you fill those in there are no sub-units to list, and no amount of connecting or refreshing will produce any. The empty list names the setting to fill in when that is the case, and the sub-units appear as soon as you save it.

Each sub-unit's state is addressable everywhere a state key is, using the pattern `device.<id>.<type>.<local_id>.<property>` (for example `device.matrix_main.encoder.005.signal_present`). You rarely type these by hand. The pickers in UI bindings, macros, triggers, and scripts surface them for you.

Commands that act on a specific sub-unit (route this decoder to that encoder, recall this preset) take the sub-unit as a parameter and present a dropdown of the available children when you run them.

## Device Management

The device detail panel includes several management actions:

- **Edit.** Change the device's name, driver, or settings. Picking another driver keeps the device's IP address (or the serial port or bridge port it is connected through) and its login, and fills in the rest from the new driver's defaults; picking the device's own driver again brings back its saved settings. What you save is what the form shows: a connection setting you clear, or leave behind by switching to another connection type, is removed. A required setting (red asterisk) left blank is refused, as in Add Device.
- **Enable/Disable.** Temporarily disable a device without removing it from the project. Disabled devices do not connect or poll.
- **Test.** Opens a connection to the address and port the device connects to, without using the driver (a TCP connection, an HTTP request, or the serial port), and shows that address, such as "Connected to 10.0.0.5:4000 (3 ms)". A success means something answered at that address, not that it is the right device: whether the device itself replies shows on its card. A UDP device has no connection to open, so the test only checks that its address has a route. While simulation runs, the test reaches the simulator, not the equipment.
- **Reconnect.** Force an immediate reconnect attempt on a disconnected device.
- **Duplicate.** Create a copy of the device with a new ID, pre-filled with the same driver and settings.
- **Device Log.** Filtered log view showing only activity for the selected device (commands sent, responses received, errors). It opens with the device's most recent lines, including ones from before the page was opened: OpenAVC keeps the last 100 lines of each device while it runs, apart from the other devices' lines.

A panel command aimed at disabled equipment reports that the equipment is
unavailable, using its configured display name. If the device has been removed,
the panel uses a general equipment message. Technical device IDs remain in the
Programmer's logs for diagnosis.

### When a device won't connect

An offline device shows the reason on its card and detail view (can't reach it, connection refused, not responding, and so on). For a network fault OpenAVC keeps retrying in the background for as long as the device is in the project. There is no cut-off: a display unplugged on Friday comes back by itself on Monday without anyone opening the Programmer. The first couple of attempts are a second or two apart and it then settles to one every five seconds. When the device rejects its username or password, the retries stop until the login is changed under **Edit** or **Reconnect** is pressed.

**A device that never answers is offline too.** A connection that opens does not prove the device is there: something else can be listening on that address and port. When a driver polls a device over TCP, UDP or OSC and nothing comes back from three polls in a row, the card shows the device as not responding, and says when nothing has answered at all, which usually means the wrong address or port, or a driver that does not match the device. A driver with its own keep-alive check takes the device offline the same way when the check goes unanswered, on any connection; over serial, only that check does. Either way, OpenAVC keeps retrying, and the device stays offline until it answers again, rather than showing connected again on every retry. While its connection is open it still takes commands, so a display that goes quiet in standby can still be switched on from a panel or a macro, and a macro step set to skip when the device is offline still sends it.

**A device whose replies the driver cannot read stays connected, and the log says so.** When three polls in a row bring replies and the driver reads none of them, the log shows a warning with the last reply, or says that none of the replies ends the way the driver expects. That is usually the wrong port (a telnet login prompt, a web server) or a driver that does not match the device. If the driver's own keep-alive check then takes the device offline, the card says the same.

**A device with no IP address is not dialed.** Its card says "No address is set for this device. Enter its IP address or hostname in the device's Connection Settings." and it is not retried until the address is added (adding it under **Edit** connects it).

**A device that was told to restart is not reported as a fault.** Some equipment drops off the network when you power it on, reboot it or reset it, and a driver can declare how long that takes. For that long the card says the device is restarting and counts down instead of showing an offline reason, nothing counts it as an error, and no alert fires. It clears the moment the device answers again. If the device does not come back, the countdown ends and the real reason appears; if something turns up that a person has to fix, like a rejected login, that appears immediately.

The exception is a **rejected login**: retrying the same credentials can't succeed, and many devices lock out the controller's IP address after a few failed attempts, so after one failed try OpenAVC stops and waits instead of retrying. The same happens when the password is changed on a device that is already connected: the first refused login or status check takes it offline. Fix what the card names: a username or password is changed under **Edit** (saving it triggers a fresh attempt), and a device that pairs instead, such as a TV that shows a prompt, names its own step. **Reconnect** tries once more. Drivers that ship with a vendor's default credentials still get that first try automatically.

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
- If the device is offline, settings are queued as "pending" and sent when the device reconnects. A pending setting stays pending until the device reports the new value back, so a write that never reached the hardware is sent again on the next connection instead of being dropped
- Settings marked as `setup` in the driver are prompted when you first add the device to the project

## Bulk Operations

Select multiple devices using the checkboxes in the device list to perform batch operations: enable, disable, or delete several devices at once.

## Driver Library

The Driver Library is inside the **Devices** view. Click **Devices** in the sidebar, then select the **Drivers** tab at the top. It has three sub-tabs: **Installed**, **Create**, and **Browse Community**.

### Installed Tab

The Installed tab shows all drivers available on this system. This includes default drivers that ship with OpenAVC and any community or custom drivers you have installed.

The left panel lists all registered drivers. Click a driver to see its details. The detail header carries the per-driver actions:

- **Open in Builder** — open a YAML driver in the Driver Builder for editing.
- **Customize a Copy** — built-in drivers are read-only; this clones one into your library so you can edit the copy.
- **Open in Code Editor** — open a Python driver in the script editor.
- **Uninstall** — remove the driver from the system. The button is disabled while a device in the current project uses it; the detail panel lists those devices so you know what to move first.

A driver with a newer version in the community library shows **Update to v&lt;version&gt;** on its row, and the top of the list counts the updates available. See [Updating Drivers](#updating-drivers).

The body of the detail panel shows:

- **Overview.** What the driver controls and which devices it supports.
- **Setup instructions.** Step-by-step guide for connecting and configuring the device.
- **Configuration.** The settings you need to provide (IP address, port, display ID, etc.).
- **Commands.** All available commands with descriptions.
- **State variables.** What state the driver exposes (power, volume, input, etc.).

### Create Tab

Build custom drivers without code using the visual editor:

1. **General.** Name, manufacturer, category, description and setup help.
2. **Connection.** The transport (TCP, serial, UDP, OSC, HTTP) and its settings, sign-in, and what is sent on connect.
3. **Behavior.** State variables, commands, responses, polling and device settings.
4. **Discovery.** How a network scan recognizes the device.
5. **Simulation.** Simulated behavior for testing without real hardware.
6. **Test.** Send the driver's commands to a device and read what comes back.

For most IP-controlled devices with a documented protocol, the Driver Builder can produce a working driver in 15-30 minutes. For serial devices, set the transport to serial and choose Serial under Transport on the Connection tab and set the baud rate, parity, data bits and stop bits there.

See [Creating Drivers](creating-drivers.md) for the complete driver development guide covering all three methods: no-code YAML definitions, the visual Driver Builder, and Python driver classes.

### Browse Community Tab

Search and install drivers from the [community driver library](https://github.com/open-avc/openavc-drivers):

1. Click the **Browse Community** tab
2. Search by manufacturer, model, or category
3. Click a driver card to see full details (description, protocols, ports, transport)
4. Click **Install** for one-click download and installation
5. The driver is immediately available in the Installed tab and the Add Device dropdown

Available categories include projectors, displays, matrix switchers, DSPs, cameras, lighting, and more. If a driver does not exist for your equipment, you can build one.

Installs come from the official driver library and nowhere else, and the download is checked against the SHA-256 the library publishes for each file before anything is written to disk. A driver whose contents don't match is refused with an error on its card rather than installed. See the [network and security guide](it-network-guide.md) for what this check does and does not prove.

The refresh button beside the search box fetches the library's list from GitHub again, so a driver published or updated in the last few minutes appears at its new version.

### Updating Drivers

Installed drivers stay at the version you installed until you update them. When the community library has a newer version, **Update** appears in three places:

- On the driver's row in the **Installed** tab, as **Update to v&lt;version&gt;**, with a count of the updates available at the top of the list. The driver's detail header shows the installed and available versions with an **Update** button.
- On the page of each device that uses the driver, under the driver name, for example "Driver update available: v1.5.0 (installed v1.4.3)".
- On the driver's card in **Browse Community**.

A newer version that needs a later release of OpenAVC shows **Needs OpenAVC v&lt;version&gt;** instead of the button. Update OpenAVC first, then update the driver.

Updating a driver replaces its companion files too, so a driver's simulator and discovery logic stay in step with the driver itself. If a newer version stops shipping one, the old copy is removed rather than left behind.

Devices already using the driver are rebuilt on the new version as part of the update, so there is no need to restart the server to pick it up. If a device does not come back, its name appears in a message and you can reconnect it from Devices.

For a few minutes after a driver is updated in the library, GitHub can serve the new file alongside the previous list of hashes, or the other way around. OpenAVC checks a download that doesn't match against a fresh copy of the list before refusing it. If they still disagree, the update is refused with "This driver was updated a few minutes ago and cannot be installed yet. Try again in five minutes."

### Uninstalling Drivers

To remove a driver you no longer need:

1. Open the **Installed** tab in Driver Library
2. Click the driver in the left panel
3. Click **Uninstall** in the detail header, then confirm

You cannot uninstall a driver while a device in the project is using it. The Uninstall button is disabled in that case, and the detail panel lists the devices that reference the driver. Remove or reassign those devices first, then uninstall. You can always reinstall from Browse Community.

### Drivers a Project Needs

When you open a project, OpenAVC checks every device's driver against the registered drivers on this system. Devices whose driver isn't installed are marked **orphaned** — they appear in the device list grayed out, with a yellow status dot and "(not installed)" next to the driver name.

If any orphaned devices are found and their drivers are available in the community catalog, a **Missing Drivers** prompt appears. It lists each missing driver with the device count it affects, separated into two groups:

- **Available from community** — checked by default. Click **Install N drivers** to download them all from the community library in one step. As soon as each driver is registered, every device waiting on it activates and starts connecting.
- **Not in community catalog** — drivers the project references that aren't in the public library (private drivers, typos, drivers from another source). These need to be reassigned to a different driver, or the driver file uploaded manually from the Drivers tab.

Click **Skip** to dismiss without installing — the orphaned devices stay marked, and you can install their drivers later from Browse Community or the per-device banner.

Each orphaned device also shows a **Driver Not Installed** banner in its detail panel with two actions:

- **Install from Community** — installs that single driver directly. The device activates as soon as the install completes.
- **Reassign Driver** — opens the device editor so you can switch to a driver that is installed.

If the driver isn't in the community catalog, the banner says so and offers **Browse Drivers** (to upload manually) plus **Reassign Driver** instead of the install button.

## Device Discovery

The Discovery panel is inside the **Devices** view. Click **Devices** in the sidebar, then select the **Discovery** tab. It scans your network for AV devices and helps you add them to your project without manually entering IP addresses and driver settings.

### Running a Scan

1. Click **Devices** in the sidebar, then select the **Discovery** tab
2. Select the subnet(s) to scan (auto-detected from every network adapter that has a link; an unplugged adapter is left out even if it keeps a fixed address. With an adapter chosen under **Reach devices through** in **Settings > Network**, only that adapter's subnet)
3. Click **Scan**

The scan runs through several phases: ping sweep, port scanning, device probes (TCP/UDP), passive discovery (mDNS/SSDP), SNMP queries, and hostname resolution. Progress is shown in real time.

### Working with Results

Each discovered device lands in one of three states:

- **Identified**: a fingerprint matched a driver. The device card shows the driver and a one-line evidence string (e.g. "mDNS announcement on `_pjlink._tcp.local.`"). Click **Add to Project** to add it with a pre-filled config: its address, and, when the driver has a MAC Address setting (the ones that wake a display or projector with Wake-on-LAN), the MAC the scan read. A device across a router from this server shows no MAC in the scan; enter that one under Edit Device. When the driver needs a setting the scan cannot read, such as a username and password, the card shows that field and the button reads **Confirm & Add**; the device is added once it is filled in. When more than one driver fits the same device (two drivers' fingerprints both matched, or a cross-vendor probe matched alongside a vendor-specific driver), each of them is in the card's dropdown, a manufacturer's own driver ahead of a generic one such as PJLink, and the line above it ends "(also matches ...)". Between two of one manufacturer's drivers that recognize the device equally well, the one that lists the model the device reported comes first, then the one for the kind of equipment it reported (a projector, a display). The device is named by the manufacturer and model the scan read, or by its driver's name when the scan read no model. A device the project already has shows **In the project as** with **Go to Device** instead.
- **Possible**: hints (MAC address prefix, hostname pattern, open port, manufacturer alias) narrowed the device to a candidate list, but no fingerprint identified it outright. Confirm the right driver and add.
- **Unknown**: the device is on the network but no driver matched. The card shows what we know (IP, MAC, OUI vendor, open ports). Pick a driver manually or hide the device.

Each card also shows IP / hostname, manufacturer, category (the identifying driver's category, or the one its MAC address prefix suggests), open ports, model and firmware (when the device reports them), and **Why this match?**, which lists every signal observed during the scan (mDNS announcement, SSDP announcement, TCP/UDP probe response, MAC address prefix, hostname match, SNMP enterprise number, manufacturer name, open port). **Audit this device** on a result opens a [device audit](device-audit.md) of it.

If the suggested driver is a community driver you haven't installed yet, click **Install & Add** to install it and add the device in one step. A community driver that needs a later release of OpenAVC shows **Needs OpenAVC v&lt;version&gt;** beside a greyed **Install & Add**: update OpenAVC first, then add the device.

### Scan Options

- **Scan Depth.** Choose how thorough the scan is. This sets what the scanner
  *does*, not how long it runs — a scan takes as long as your network needs,
  and each stage gets its own time from the number of addresses to sweep and
  how many devices answer. A small network finishes quickly at any depth.
  - **Quick.** Fast re-scan. Ping sweep, port scanning, device probes, and short passive listening window. Skips NetBIOS name resolution and SNMP Entity MIB.
  - **Standard** (recommended). Full scan including NetBIOS/SMB for Windows device names and SNMP Entity MIB for detailed hardware info. Longer passive listening window for mDNS and SSDP devices.
  - **Thorough.** Extended port range, longest passive listening window. Finds the most, and is worth using on an unfamiliar network.

  On a very large subnet a stage may not be able to cover everything in the
  time available. When that happens the scan covers what it can, tells you
  exactly what it skipped, and labels the results incomplete rather than
  quietly reporting a full sweep. Scanning a smaller subnet is usually the
  better answer than re-running the same scan.
- **SNMP.** Enable SNMP v2c queries for richer device identification (community string configurable).
- **Reduce network load.** Slows down the scan to reduce traffic. Use this on networks with strict IDS/IPS policies or where IT has asked you to scan carefully.
- **Extra Subnets.** Add subnets beyond the auto-detected ones.
- **Export.** Download scan results as a text report.

### Devices that need configuration before they show up

Some devices ship with their network control surface disabled by default. If the scan doesn't find a device you know is on the network, check whether it needs to be enabled in the device's setup software:

- **Biamp Tesira / TesiraFORTÉ.** Open the device's System Manager, go to Network Settings, and turn on both *Discovery* and *Telnet*. Both default to off. Leave *System Security* off too — the OpenAVC driver expects an unauthenticated Telnet session, which matches the standard Tesira deployment when the DSP is on a private AV VLAN.
- **QSC Q-SYS Core.** External control is on by default on TCP 1702. If the design has been hardened with a username/password, enter those in the driver config.
- **Yamaha MTX/MRX.** RCP must be enabled in MTX-MRX Editor under System → Remote Control. The device allows up to 8 simultaneous network sessions.
- **Symetrix Composer DSPs.** Ensure the controller numbers you want to drive have the *Push* checkbox enabled in Tools → Controller Manager, then compile and push the design to the unit.

After enabling the relevant network feature on the device, re-run the scan. If the device still doesn't appear, you can always click **Add Device** and enter the IP, port, and driver manually.

## Device Audit

To test one device and its driver in detail and write a report you can send to the driver's author, use **Audit a Device** on the **Drivers** tab, or **Audit this device** on a device's page. See [Device Audit](device-audit.md).

## See Also

- [Device Audit](device-audit.md). Test one device and its driver, and write a report about it

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow
- [UI Builder](ui-builder.md). Visual panel designer for touch panels
- [Creating Drivers](creating-drivers.md). Build device drivers (YAML, Driver Builder, and Python)
- [Connecting Devices Through a Bridge](connecting-through-a-bridge.md). Route serial devices over an IP-to-serial bridge
- [OpenAVC Built Into Equipment](manufacturer-equipment.md). Add a product with OpenAVC built in, and product hardware
