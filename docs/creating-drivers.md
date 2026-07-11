# Creating Device Drivers for OpenAVC

OpenAVC supports three ways to create device drivers, from easiest to most powerful:

1. **Driver Builder UI.** Visual wizard in the Programmer IDE. No code required.
2. **Driver Definition File (.avcdriver).** Write a YAML file by hand. No code required.
3. **Python Driver.** Full Python class for advanced protocols.

All three methods produce drivers that work identically at runtime. Choose the simplest method that covers your device's protocol.

> **You may not need a driver at all.** If you only have a handful of commands for a single device, add a **Generic** device (TCP, Serial, or HTTP) and define its commands and responses with the tables on the device page. See [No-Code Commands and Responses](devices-and-drivers.md#no-code-commands-and-responses). Create a driver when you want to reuse it across projects, share it with the community, or handle a protocol that needs real logic.

Python drivers can be created and edited directly in the **Code** view of the Programmer IDE with hot-reload support, so you can write, test, and iterate without restarting the server.

| Method | Skill Level | Best For |
|--------|-------------|----------|
| Driver Builder UI | Beginner | Text-based protocols (Extron SIS, Kramer, generic RS-232) |
| .avcdriver File | Intermediate | Text-based protocols, sharing drivers as files |
| Python Driver | Advanced | Binary protocols, complex state, custom auth schemes |

---

## Quick Decision Guide

**Can the device be controlled with text commands over TCP or serial?**
(e.g., sending `"POWR ON\r"` and getting back `"POWR=ON\r"`)

- **Yes:** Use the **Driver Builder UI** or a **.avcdriver definition**.
- **No, it uses HTTP/REST:** Use a **.avcdriver definition** with `transport: http`. HTTP commands use `method`, `path`, and `body` fields instead of raw command strings. See the HTTP section below.
- **No, it uses OSC (Open Sound Control):** Use the **Driver Builder UI** or a **.avcdriver definition** with `transport: osc`. OSC commands use `address` and `args` fields. See the OSC section below.
- **No, it uses a binary protocol:** Use a **Python driver**.
- **No, it uses UDP broadcast:** Use a **Python driver** (see the Wake-on-LAN driver as an example).

---

## Method 1: Driver Builder UI

The Driver Builder is a visual tool inside the Programmer IDE. Open it by clicking **Devices** in the sidebar, then selecting the **Drivers** tab and clicking the **Create** tab.

### How the editor is laid out

The editor has six tabs across the top, each grouping a single concern:

| Tab | What lives here |
|-----|-----------------|
| **General** | Identity (id, name, manufacturer, category, version, author, description), Help & Setup text, Publishing metadata (min platform version, protocols, tags, source URL) |
| **Connection** | Transport (TCP/serial/UDP/OSC/HTTP), Authentication, Push Notifications (`push`), Connection Watchdog (`liveness`), Connect Sequence (`on_connect`), Frame Parser, Configuration Fields (`config_schema`) |
| **Behavior** | State Variables, Commands, Responses, Polling, Device Settings |
| **Discovery** | Discovery fingerprints (mDNS, SSDP, AMX DDP beacon, TCP/UDP probes, Python file) and hints (OUI, hostname, open port, manufacturer alias, SNMP PEN) |
| **Simulation** | Simulator definition (initial state, controls, command handlers, error modes) |
| **Test** | Live tester — runs commands through the real driver runtime against a device |

The Connection and Behavior tabs use collapsible sub-sections. Each section header shows a count or status hint (`5 commands`, `enabled`, `none`) so you can scan a tab without expanding everything. Sections that are populated open by default; empty optional ones (Authentication, Connect Sequence, Frame Parser, Polling, Device Settings, Configuration Fields) collapse on a fresh driver to keep the surface clean.

Every section header carries a "Learn more" link that opens the matching part of these docs in a new tab.

### Validation

Validation runs on every keystroke. Issues appear as inline rows at the top of the affected tab, and the tab label shows a colored dot — red for errors (block save), amber for warnings (publish-quality, don't block).

What's flagged:

- **Driver ID**: missing, illegal characters, or duplicates of another saved driver.
- **Driver name**: missing.
- **Publish quality** (warnings): missing `description`, `version`, `author`, or `help.overview`.
- **Commands**: any `{placeholder}` in the wire string (send/path/body/headers/query_params/address/args) that doesn't resolve to a declared parameter or config field. Catches typos that would otherwise leave a literal `{level}` on the wire.
- **Parameter names**: illegal characters surface as an inline error instead of being silently stripped.

### Step-by-step walkthrough

#### 1. Create a new driver

Click **Create New Driver** in the left panel. The editor opens on the **General** tab.

The driver list distinguishes built-in drivers (shipped with the platform — lock icon, "built-in" tag) from user drivers (created or installed). Clicking a built-in offers **Customize a Copy** instead of opening it for in-place editing — built-in files are read-only.

#### 2. General tab: identity, help text, publishing

Fill in identity:

| Field | Example | Notes |
|-------|---------|-------|
| Driver ID | `extron_sw4` | Lowercase letters, digits, underscores. Renamable later if no devices in the current project reference it. |
| Driver Name | `Extron SW4 HD 4K` | Shown in the Add Device dialog. |
| Manufacturer | `Extron` | |
| Category | Switcher | Dropdown — pick the closest fit. |
| Version | `1.0.0` | Semver. Bump on every meaningful change. |
| Author | `Your Name` | |
| Description | `Controls Extron SW4 HD 4K HDMI switcher via RS-232 or TCP.` | One sentence. Shown in the catalog. |

Below the identity block, the **Help & Setup** section takes these markdown fields:

- **Overview** — what the device is, who uses it.
- **Setup Instructions** — step-by-step the integrator follows to get the device talking (IP setup, pairing, physical buttons).
- **Connection hint** (optional) — a short troubleshooting line shown on the device's offline banner when it can't connect. Use it for a device-specific cause the platform can't infer, such as a remote-access setting (SSH, a control port, an API toggle) the integrator must enable on the device first.

Overview and Setup appear in the Add Device dialog when someone picks this driver; the connection hint appears later, on the device's offline banner.

The **Publishing** section holds catalog metadata (`min_platform_version`, `protocols`, `tags`, `source_url`, default ports, simulated flag). Verified is server-controlled and read-only.

#### 3. Connection tab: how the driver talks to the device

The Transport section is required — pick TCP, serial, UDP, OSC, or HTTP. Transport-specific fields appear:

- **TCP**: default port, message delimiter, optional inter-command delay, TLS.
- **Serial**: baudrate, parity, bytesize, stopbits.
- **HTTP**: base URL form, default headers, auth (token / api key), timeout.
- **OSC**: dual UDP socket settings.
- **UDP**: default port.

Common to text protocols: **Message Delimiter** marks the end of each message. `\r` for most AV gear (Extron, Kramer, PJLink), `\r\n` for some network devices.

The other Connection sub-sections are optional:

- **Authentication** — for devices that present a `login:` / `password:` prompt over Telnet or SSH after connect (Lutron HomeWorks QS, some Cisco gear, legacy serial-over-IP gateways). Off by default.
- **Connect Sequence** (`on_connect`) — wire strings sent automatically on every connect. Common uses: enabling verbose/feedback mode (Extron `\x1b3CV\r\n`), initial state dumps (`< GET ALL >`), OSC subscriptions (`/xremote`).
- **Frame Parser** — advanced. Only for binary protocols framed by length prefix or fixed length. Most drivers leave this off and rely on the message delimiter instead.
- **Configuration Fields** (`config_schema`) — per-device settings users fill in on the Add Device dialog (display IDs, instance tags, custom passwords). Become `{placeholders}` in command strings.

#### 4. Behavior tab: state, commands, responses, polling, settings

Order matters here. Build them in the order they appear:

**State Variables** — the read-only properties the driver reports. Each entry has a type (string, integer, number, boolean, enum), a required label, optional help text, and for numerics optional min/max/step and a unit (used by the simulator and panel UI to auto-generate sliders, and by the UI Builder to match a bound control to the driver's range). Tick **Control** on the variables an integrator would bind a panel control to — the UI Builder's value picker lists those first.

| Variable ID | Label | Type | Notes |
|-------------|-------|------|-------|
| `input` | Current Input | Integer | |
| `volume` | Volume | Integer | min 0, max 100 |
| `mute` | Mute | Boolean | |

**Commands** — actions the driver can perform. Each command's shape depends on the transport:

- **TCP / serial / UDP**: a single **Send** field. `{param_name}` placeholders substitute parameter values; `{config_key}` placeholders substitute device config (e.g., `{set_id}`).
- **HTTP**: method, path, body, headers, query params. Every field supports `{placeholders}`.
- **OSC**: address + a typed argument list (`f`/`i`/`s`/`h`/`d`/`T`/`F`/`N`).

**Parameters** for each command let users fill in what to send. Each parameter has a type, optional required flag, label, help, default, and (numeric) min/max bounds, (enum) allowed values, or (free text) a `pattern` regex the value must match.

**Number formatting on the wire.** The runtime coerces each parameter to its declared type before substituting it, so an `integer` parameter always sends a whole number. A value of `26.0` (for example from a slider bound to the command) goes out as `26`, not `26.0`. For a `number` parameter, set **Decimals** to round to a fixed number of places (`decimals: 0` sends a whole number, `decimals: 1` sends one place). For finer control on a single placeholder, a format spec works inline: `{level:03d}` zero-pads (e.g. `007`), `{addr:02X}` hex-formats, and `{gain:.1f}` fixes one decimal place. Specs work even when the value arrives as a whole-number float.

**Escape sequences** in command strings: `\r`, `\n`, `\t`, `\\`, `\xHH` (hex byte, e.g. `\x1B` for ESC).

Example for an Extron switcher:

| Command ID | Label | Send | Parameters |
|------------|-------|------|------------|
| `set_input` | Set Input | `{input}!\r` | `input` (Integer) |
| `set_volume` | Set Volume | `{level}V\r` | `level` (Integer, min 0, max 100) |
| `mute_on` | Mute On | `1Z\r` | (none) |
| `mute_off` | Mute Off | `0Z\r` | (none) |
| `query_input` | Query Input | `!\r` | (none) |

**Responses** — patterns matched against incoming data. Capture groups update state variables.

| Regex Pattern | Mapping |
|---------------|---------|
| `In(\d+) All` | group 1 → `input` (integer) |
| `POWR=(\d)` | group 1 → `power` (string) with map `{"0": "off", "1": "on"}` |

The **set:** shorthand is also supported (`set: {mute: "$1"}` for capture-group references, `set: {signal: true}` for static literals). The builder preserves whichever form was loaded so byte-equal round-trips stay byte-equal.

**Polling** — periodic queries that keep state fresh on devices that don't push updates. List the command names (or raw query strings) to send each cycle. The cadence (seconds) is the **Poll Interval** field, stored as `default_config.poll_interval`.

**Device Settings** — writable values stored on the device hardware (labels, IDs, lock codes). Pending writes queue while the device is offline and replay on reconnect. Less common than state variables — most drivers don't need this.

#### 5. Discovery tab (optional)

Declarations the discovery engine uses to match found devices to this driver. The Discovery tab has four sections: **Fingerprints**, **Hints**, **Advanced**, and **Help**.

**Fingerprints** identify the driver alone — one match is enough. Each "Add" button appends an editable row:

- **mDNS service** — the service type the device announces (e.g. `_pjlink._tcp.local.`). Optional TXT-record filter when the service type is generic.
- **SSDP device-type** — the UPnP device-type URN (e.g. `urn:schemas-upnp-org:device:MediaRenderer:1`).
- **AMX DDP beacon** — `make` (required) and optional `model_pattern` glob.
- **TCP probe** — connect to a port, optionally send bytes, match the response. Set the port, choose `send_ascii` or `send_hex` (or omit for connect-only banner reads), and pick exactly one of `expect` (substring), `expect_regex`, or `expect_hex`. Set `tls: true` for an HTTPS-only device — the probe wraps the connection in TLS (without cert verification) before sending, so you can fingerprint the device's own landing page (e.g. send an HTTP `GET /` and `expect` a string from the returned HTML). Optional `extract_manufacturer` lifts a manufacturer string into the matcher's enrichment path so peer drivers can claim the device via `manufacturer_alias`. Free-form `extract` rules pull other metadata (model, version) out of the response.
- **UDP probe** — same shape as TCP probe, but broadcasts on the chosen port.
- **Python file** — sibling `<driver_id>_discovery.py` that implements multi-step handshakes, binary parsers, broadcast-then-per-host TCP follow-ups, or any wire format too dynamic for the declarative blocks. The escape-hatch when the other fingerprint types don't fit. The companion exposes `async def probe(ctx)` and emits evidence via `ctx.emit_broadcast(host, *, response, txt, port, matched_pattern)`, `ctx.emit_active(host, response, *, port, matched_pattern)`, or `ctx.emit_oui(mac, host, *, vendor)`. Pass `port=` and `matched_pattern=` so the scan-results "Why?" reveal can render the full `"UDP probe on port <p> matched <kind:value>"` / `"TCP probe on port <p> returned <excerpt>"` phrasing (binary TCP probes whose response excerpt would be gibberish fall back to `"TCP probe on port <p> matched <kind:value>"`). Bind every socket to `ctx.source_ip`; consult the engine's existing port-scan results via `ctx.hosts_by_open_port` instead of re-iterating subnets. Every `host` you emit for must be an IP within `ctx.target_subnets` — the engine ignores emits for hosts outside the scanned ranges. The platform runs the probe on its own worker thread and enforces a hard timeout (default 10 s, capped at 30 s); use async I/O throughout — a probe that blocks in synchronous calls stalls only itself, but gets cut off at the timeout instead of cancelled cleanly.

Each fingerprint has a per-row **Cross-vendor** toggle. Tick it when the same wire signal is emitted by more than one vendor's devices. The matcher will demote your driver to an alternative when a vendor-specific peer driver matches via hints.

**Hints** narrow candidates without identifying alone — combine to surface the device as *possible* with a candidate list:

- **OUI** — MAC vendor block (e.g. `00:0e:dd`).
- **Hostname pattern** — regex against reverse-DNS / NetBIOS name.
- **Open port** — vendor-specific TCP port the device leaves open. Generic web/SSH ports (22, 80, 443, 8000, 8080, 8443, 8888) are rejected — they would match every web/SSH device.
- **Manufacturer alias** — case-insensitive exact match against any manufacturer string the scan captured (probe response, AMX DDP `make`, etc.).
- **SNMP enterprise number** — the device's IANA Private Enterprise Number.

**Advanced** holds help text on the cross-vendor toggle and the convention for manual-only devices (leave Fingerprints and Hints empty — the driver is still installable manually from the Add Device dialog).

**Help** shows a one-screen example of a typical fingerprint + hint declaration and a link to the full schema reference (later in this guide).

Skip the Discovery tab entirely if the device has no network announcement and no useful hints — the driver is still installable manually.

#### 6. Simulation tab (optional)

Adds simulator support so the driver can be exercised without real hardware. Most drivers can rely on auto-generated simulation; the editor here lets you customize initial state, push behavior, command handlers, error modes, and (for richer simulators) state machines and controls. See [Writing Simulators](https://docs.openavc.com/writing-simulators/) for the full guide.

#### 7. Test tab: run it against a device

The test panel runs commands through the real `ConfigurableDriver` runtime — auth handshake and connect sequence run first, parameter substitution and response patterns work the same as production. Anything that works here will work when the driver is wired into a project device.

For each test:

- **Host / Port** — for TCP, HTTP, UDP, and OSC drivers. Defaults to the driver's `default_config.port`. Override per test.
- **Serial Port** — for serial drivers. Accepts a device path like `COM3` on Windows or `/dev/ttyUSB0` on Linux. Prefix with `SIM:` to talk to the built-in simulator.
- **Driver Config** — fields declared in `config_schema` (credentials, instance tags) appear here so you can fill them in without saving them to defaults.
- **Command** — pick a defined command from the dropdown. The form below shows its parameters with typed inputs, and a live wire-format preview shows the substituted string that will go on the wire.
- **Raw probe** — also available in the dropdown. Sends arbitrary bytes without auth or on_connect. Useful for one-off "what does this device say" checks.

Each result shows what was sent, every received chunk (with `\r` and `\n` made visible), and any state variable changes the responses produced.

**Production-device conflict warning.** When you type a host and port that's already used by a device in your project, the panel surfaces a warning above the Send button identifying the device. Many AV devices (Sony BVM, Christie projectors, Crestron 3-Series console) accept only one TCP control session at a time, so testing would kick the live device offline. You can:

- **Pause device** — cleanly disconnect the production driver and suppress auto-reconnect for the duration of the test. The panel offers a **Resume** button to bring it back online. Closing the test tab automatically resumes any devices the panel paused, and as a safety net the server resumes a paused device on its own if the test session disappears without cleaning up (browser crash, lost connection). A paused device shows a Paused badge on the Devices page with its own Resume button.
- **Connect anyway** — proceed without pausing. Use this when you know the device is already gone (e.g. it's been physically disconnected), or when the device tolerates multiple sessions.

The check is TCP-only; UDP, HTTP, and OSC don't have the single-session problem.

**Rate limit.** The Send button is throttled to one call per 2 seconds. When you press it too fast, a brief countdown appears on the button ("Rate limited (1.4s)") and the result row is tagged **Throttled** so you can tell it apart from a device protocol failure.

#### Live YAML preview

Click the **YAML** button in the editor header to open a side pane showing the serialized driver in real time. Read-only — it's exactly what gets saved as the `.avcdriver` file. Useful for double-checking that the form output matches what you'd write by hand.

#### Save, duplicate, export

- **Save** — writes to `driver_repo/`. Available in the Add Device dialog immediately.
- **Duplicate** (copy icon in the driver list) — clones the driver with a unique ID and `(Copy)` appended to the name. Replaces the export/reimport ritual for branching a driver. The verified flag is cleared on the copy since it hasn't been validated.
- **Export .avcdriver** — downloads the driver file to share with others or commit to a git repo.

### Importing and Exporting Drivers

#### Exporting a driver to share it

You can export any driver as an `.avcdriver` file:

- **From the list**: Click the download icon next to a driver in the left panel.
- **From the editor**: Click the **Export** button in the editor header (next to Save).

This downloads an `.avcdriver` file you can share with other OpenAVC users, commit to a git repo, or back up.

#### Importing a driver someone shared with you

Click **Import from File** in the left panel. You have two options:

- **Choose a file**: Click "Choose a .avcdriver file" to pick a driver definition file from your computer.
- **Paste JSON/YAML**: Paste the definition text directly into the text area and click Import.

The driver is validated, saved to `driver_repo/`, and immediately available for use.

#### What about Python drivers?

Python drivers (`.py` files) can also be imported through the UI or installed from the community repository. They are saved to `driver_repo/` and loaded automatically at startup.

### Community Driver Repository

The **Browse Community** tab in the Driver Builder view connects to the [OpenAVC Community Driver Library](https://github.com/open-avc/openavc-drivers) on GitHub. From there you can:

- Search for drivers by manufacturer, model, or device type
- Browse community-contributed drivers (both YAML and Python)
- Filter by category (Projector, Display, Switcher, Audio, Camera, etc.)
- Install with one click

Installed drivers are saved to `driver_repo/` and immediately available in the "Add Device" dialog.

---

## Method 2: Driver Definition File (.avcdriver)

A driver definition is a YAML file with the `.avcdriver` extension. It's what the Driver Builder UI creates under the hood. Writing one by hand is useful for sharing drivers, version-controlling them, or when you want to work in a text editor.

YAML was chosen over JSON because it supports comments (essential for documenting protocol details from manufacturer manuals) and doesn't require double-escaping regex patterns.

### Where to put .avcdriver files

| Directory | Purpose |
|-----------|---------|
| `server/drivers/definitions/` | Built-in drivers (shipped with OpenAVC) |
| `driver_repo/` | Community and user drivers |

Both directories are scanned at startup. Files are loaded, validated, and registered automatically.

You can also import an `.avcdriver` file through the Driver Builder UI (click **Import from File**), which copies it into `driver_repo/` for you.

### Full Example: Extron SIS Switcher

```yaml
# Extron SIS Switcher Driver
# Reference: Extron SIS Command/Response Reference, Section 3
# Protocol: text-based over TCP (port 23) or RS-232 (9600 8N1)

id: extron_sis_switcher
name: Extron SIS Switcher
manufacturer: Extron
category: switcher
version: 1.0.0
author: OpenAVC Community
description: Controls Extron SIS-compatible switchers over TCP or serial.
transport: tcp
delimiter: "\r\n"

help:
  overview: >
    Controls Extron SIS-compatible switchers. Supports input routing,
    volume control, and mute.
  setup: >
    1. Connect the switcher to the network or via RS-232 (9600 8N1).
    2. For TCP, use port 23 (default Extron telnet port).

default_config:
  host: ""
  port: 23
  poll_interval: 15

config_schema:
  host:
    type: string
    required: true
    label: IP Address
  port:
    type: integer
    default: 23
    label: Port
  poll_interval:
    type: integer
    default: 15
    min: 0
    label: Poll Interval (sec)

state_variables:
  input:
    type: integer
    label: Current Input
  volume:
    type: integer
    label: Volume
  mute:
    type: boolean
    label: Mute

commands:
  set_input:
    label: Set Input
    send: "{input}!\r\n"             # e.g., "3!\r\n" to select input 3
    help: Route a specific input to all outputs.
    params:
      input: { type: integer, required: true, help: "Input number (1-based)" }

  set_volume:
    label: Set Volume
    send: "{level}V\r\n"             # e.g., "45V\r\n" to set volume to 45
    help: Set the audio volume level.
    params:
      level: { type: integer, required: true, help: "Volume level 0-100" }

  mute_on:
    label: Mute On
    send: "1Z\r\n"
    help: Mute the audio output.
    params: {}

  mute_off:
    label: Mute Off
    send: "0Z\r\n"
    help: Unmute the audio output.
    params: {}

  query_input:
    label: Query Input
    send: "!\r\n"                    # Response: "In3 All"
    params: {}

  query_volume:
    label: Query Volume
    send: "V\r\n"                    # Response: "Vol45"
    params: {}

responses:
  # "In3 All" -> input = 3
  - match: 'In(\d+) All'
    set: { input: "$1" }

  # "Vol45" -> volume = 45
  - match: 'Vol(\d+)'
    set: { volume: "$1" }

  # "Amt1" -> mute = true, "Amt0" -> mute = false
  - match: 'Amt(\d+)'
    set: { mute: "$1" }

polling:
  # Cadence comes from default_config.poll_interval (15s above), not here.
  queries:
    - "!\r\n"                        # Query current input
    - "V\r\n"                        # Query current volume
```

For **TCP and serial** drivers, each query is the raw protocol string to send (including its line terminator), as above. A query may instead be the **name of a declared command** — on any transport it then runs as that command, so its response is matched and any `command_prefix` / `command_suffix` framing is applied (name a framed command here rather than re-typing its wire string). On **HTTP** a query that isn't a command name is treated as a path. The same applies to `on_connect`.

Notice how much cleaner this is compared to JSON: comments explain the protocol, regex patterns don't need double-escaping, and the structure is easy to scan.

### Definition Reference

A JSON Schema for the `.avcdriver` format is published with the community driver library:

```
https://raw.githubusercontent.com/open-avc/openavc-drivers/main/avcdriver.schema.json
```

Add this line to the top of any `.avcdriver` file and editors with YAML Language Server support (VS Code, Neovim, JetBrains, and others) will validate it live and autocomplete field names as you type:

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/open-avc/openavc-drivers/main/avcdriver.schema.json
```

The tables below document each field in detail.

#### Top-level fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique driver identifier. Lowercase, underscores. |
| `name` | Yes | Human-readable display name. |
| `transport` | Yes | `"tcp"`, `"serial"`, `"http"`, `"udp"`, `"osc"`, or `"bridge"` (an IR device that emits through a bridge; see IR devices below). |
| `manufacturer` | No | Manufacturer name. Default: `"Generic"`. |
| `category` | No | One of: `projector`, `display`, `switcher`, `scaler`, `audio`, `camera`, `lighting`, `relay`, `utility`, `other`. |
| `version` | No | Semantic version. Default: `"1.0.0"`. |
| `author` | No | Who wrote this driver. |
| `description` | No | Brief description. |
| `help` | No | Help text object: `{overview: "...", setup: "..."}`. Shown in the Add Device dialog and available to the AI assistant. |
| `delimiter` | No | Message delimiter. Default: `"\\r"`. Use `"\\r\\n"` for CRLF. |
| `command_prefix` | No | A constant string prepended to every command's `send` string. Set it once for a protocol whose commands all share a fixed lead-in (a "packet header") instead of repeating it on each command. Byte-stream transports only (TCP/serial/UDP). |
| `command_suffix` | No | A constant string appended to every command's `send` string (its terminator). Set it once so you don't type `\r` on every command. Byte-stream transports only. Supports the same escape sequences as `send`. |
| `default_config` | No | Default values for config fields. |
| `config_schema` | No | Describes config fields shown in "Add Device" dialog. |
| `device_settings` | No | Configurable settings that live on the device. See below. |
| `state_variables` | No | State properties this driver exposes. |
| `child_entity_types` | No | Sub-units this device manages (encoders, decoders, zones, presets). See below. |
| `commands` | No | Commands this driver can send. |
| `quick_actions` | No | Command ids promoted to one-click buttons at the top of the device view. See below. |
| `actions` | No | Full-form promoted buttons (icon, confirm, visibility). See below. |
| `responses` | No | Regex patterns for parsing device replies. |
| `auth` | No | Login handshake performed between TCP connect and `on_connect`. See `auth` section below. |
| `on_connect` | No | List of raw commands sent immediately after connecting. Use for enabling verbose/feedback mode or requesting initial state. |
| `polling` | No | Periodic status query configuration. |
| `liveness` | No | Connection watchdog — send a probe on an interval, reconnect after consecutive misses. See `liveness` section below. |
| `push` | No | Device-initiated push notifications — a multicast group the device sends frames to, or an SSE event stream on its HTTP API. Everything feeds the same `responses` rules. See `push` section below. |
| `frame_parser` | No | Advanced: custom receive framing (see below). |
| `send_frame` | No | Advanced: send-side packet framing — wraps every command in a binary header with a computed data length (e.g. eISCP). The send twin of `frame_parser` (see below). Byte-stream transports only. |
| `protocols` | No | Protocol names this driver speaks (e.g., `["pjlink"]`, `["extron_sis"]`). Helps discovery match devices to drivers. |
| `discovery` | No | Discovery declarations — fingerprints and hints. See Discovery below. |

#### `config_schema` entry

```json
"host": {
  "type": "string",
  "required": true,
  "label": "IP Address",
  "default": "",
  "description": "Help text shown below the field"
}
```

Types: `string`, `text`, `integer`, `number`, `float`, `boolean`, `enum`. For `enum`, add a `"values"` array.

`text` renders as a multi-line monospace textarea in the Add Device dialog. Use it for config that doesn't fit in a single line — block lists for DSPs (e.g., Biamp Tesira's per-block declarations), zone definitions for room combiners, channel-name maps, custom command translation tables, anything the integrator pastes from manufacturer software. The raw string is preserved on save (no JSON parsing or number coercion); your driver parses it at `__init__` time.

#### `device_settings` entry

Device settings are configurable values that live **on the device**, readable via polling and writable by the driver. Unlike config (which is stored in the project file), device settings are pushed directly to the device hardware. Examples: NDI source name, device hostname, tally mode, video format.

```yaml
device_settings:
  ndi_name:
    type: string
    label: NDI Source Name
    help: >
      The name other devices use to subscribe to this NDI source
      on the network. Must be unique across all NDI devices.
    state_key: ndi_name
    default: BIRDDOG
    setup: true
    unique: true
    write:
      method: POST
      path: /encodesetup
      body: '{"NDIName": "{value}"}'
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | `string`, `integer`, `number`, `float`, `boolean`, or `enum`. |
| `label` | Yes | Human-readable label shown in the Programmer IDE. |
| `help` | Yes | Inline help text explaining what the setting does. |
| `state_key` | No | Which state variable provides the current value. Defaults to the setting key. |
| `default` | Yes | Default value for new devices. |
| `setup` | No | If `true`, the setting is prompted during add-to-project. Default: `false`. |
| `unique` | No | If `true`, the system generates a non-clashing default (appends device ID). Default: `false`. |
| `values` | No | For `enum` type: array of allowed values. Each entry is a plain wire value or a `{value, label}` pair (the editor shows the label, the wire value is written). Unlike a command picker, a setting write that resolves to nothing in the list is rejected — it is persisted device config, not forgiving free text. |
| `min` / `max` | No | For `integer` / `number` types: value range. |
| `regex` | No | Optional regex for string validation. |
| `write` | No | How to write the setting to the device (YAML drivers only, see below). |

**Write definitions (YAML drivers):**

For HTTP drivers, the `write` section specifies the HTTP request to send:

```yaml
write:
  method: POST
  path: /encodesetup
  body: '{"NDIName": "{value}"}'
```

For TCP/serial drivers, use a `send` string:

```yaml
write:
  send: 'SET HOSTNAME {value}\r'
```

The `{value}` placeholder is replaced with the new setting value at runtime. Config values like `{host}` are also available.

A `boolean` setting arrives as a real true/false, and `integer` / `number` as a real number, so use a Python format spec to shape the wire form:

- **Boolean flag byte** (`1` / `0`): `{value:d}` — for example `send: 'TALLY{value:d}\r'` or `path: /cgi?cmd=TAE{value:d}`. Plain `{value}` on a boolean would send `True` / `False`, which most devices reject.
- **Zero-padded number**: `{value:03d}` sends `63` as `063` for fixed-width fields.

Every device setting needs a `state_key` that polling actually populates. That polled value is the read-back shown in the editor. If a setting can be written but never read, leave it as a command instead, so the UI never shows a stale value.

**Python drivers** override `set_device_setting(key, value)` instead of using `write` definitions:

```python
async def set_device_setting(self, key: str, value: Any) -> Any:
    match key:
        case "ndi_name":
            await self._api_post("encodesetup", {"NDIName": str(value)})
            self.set_state("ndi_name", str(value))
        case _:
            raise ValueError(f"Unknown device setting: {key}")
```

**In the Programmer IDE**, device settings appear in a dedicated "Device Settings" section in the device detail view, separate from commands. Each setting shows its current value (from state polling), label, and help text. Users can click to edit and the new value is pushed directly to the device.

#### `state_variables` entry

```json
"power": {
  "type": "enum",
  "values": ["off", "on", "warming", "cooling"],
  "label": "Power State",
  "help": "Current power state. 'warming' and 'cooling' are transitional."
}
```

```yaml
output_1_fader_db:
  type: number
  label: Output 1 Gain (dB)
  min: -80.0
  max: 10.0
  step: 0.5
  unit: dB
  control: true
```

Types: `string`, `integer`, `number`, `float`, `boolean`, `enum`.

The `label` field is required — it's the human-readable name shown wherever the variable appears (device card, properties panel, simulator), and a state variable without one fails validation so the whole driver won't load. The optional `help` field provides a description shown in the Driver Builder UI and available to the AI assistant.

Numeric variables can declare their real range and resolution:

- `min` / `max` — the value range the device reports. The UI Builder offers to match a bound slider, fader, gauge, or meter to this range, and the simulator UI renders a matching slider.
- `step` — the device's value resolution (e.g. `0.5` for a fader that moves in half-dB increments). Fills the matched control's Step.
- `unit` — the unit as text (e.g. `dB`, `Hz`, `%`). Fills the matched control's Unit. Without it, the UI falls back to parsing a trailing "(dB)" from the label.

Any variable can also declare:

- `control: true` — marks a variable an integrator would bind a panel control to (a fader level, a mute, a source selection), as opposed to a read-out or metadata. The UI Builder's value picker lists flagged variables first. Ordering only — unflagged variables always remain pickable.

#### `child_entity_types` entry

Some devices are really a controller for many sub-units: a video matrix manages hundreds of encoders and decoders, a DSP has dozens of zones, a presentation switcher has video-wall presets. Declaring `child_entity_types` lets the driver register those sub-units as **child entities** so each gets its own state, its own row in the device's Child Entities tab, and addressable state keys (`device.<id>.<type>.<local_id>.<property>`) — without inventing your own key conventions.

Drivers that don't declare `child_entity_types` behave exactly as before: one flat device.

```yaml
child_entity_types:
  encoder:
    label: Encoder
    label_plural: Encoders
    id_format:
      type: integer      # only integer IDs are supported
      min: 1
      max: 762
      pad_width: 3       # render encoder 5 as "005" in state keys
    state_variables:
      name: { type: string }
      ip: { type: string }
      signal_present: { type: boolean, cloud_priority: high }
      edid_block: { type: string, cloud_priority: low }
    summary_fields: [name, ip, signal_present]
    label_field: name
```

- `label` / `label_plural`: Human-readable names shown in the IDE.
- `id_format`: How the controller addresses this sub-unit.
  - `type: integer` (default): numbered sub-units. `min`/`max` bound the valid range; `pad_width` zero-pads the ID when it appears in state keys.
  - `type: string`: sub-units keyed by a device-native **name** instead of a number (a Q-SYS component Code Name, an MQTT topic leaf, a zone name). The name must be `[A-Za-z0-9_-]` only (so it's safe in a state key and in glob subscriptions) and at most `max_length` characters (default 128). Sanitize the device's native name to that charset and keep the original in the child's `label`.
- `state_variables`: Same shape as device `state_variables` (types: `string`, `integer`, `number`, `float`, `boolean`, `enum`), including the optional `min`/`max`/`step`/`unit` numeric metadata and the `control: true` flag — a child variable flagged as a control is what the UI Builder's value picker and the `options_from: child_schema` command cascade list first. The platform always adds a boolean `online` and a string `label` per child, so you don't declare those. Each variable may carry an optional `cloud_priority`:
  - `high` — relayed to the cloud at the fast top-level cadence (for latency-sensitive fields like routing or mute).
  - `low` — relayed at the slow verbose cadence (for chatty per-IO state).
  - omitted — the default per-child cadence.
- `summary_fields`: Which fields show as columns in the Child Entities list (the rest stay in the expanded per-child view).
- `label_field`: Which field carries the controller's own name for the unit. The user's friendly label is separate and lives in the project file.
- `dynamic: true`: Mark a type **dynamic** when each sub-unit's control set is only known at connect time and **differs between sibling units** — e.g. a DSP whose components are user-built (a gain has gain/mute, a custom block has whatever the designer named). Leave `state_variables` empty (or with only the fields every child shares); each child publishes its own schema when you register it (see below). The IDE renders each dynamic child's discovered controls in its expanded row. Dynamic types are a **Python-driver** capability (a YAML `instances:` roster is fixed or config-driven; enumerating sub-units from the device at runtime needs Python).
- `instances`: Makes the type real at runtime for **YAML drivers** — see the next section. Without it, a YAML declaration is types-only and children are created only by a Python driver's `register_child`.

##### Declarative children in YAML (`instances`, `child_set`, `each_child`)

A YAML driver creates children by adding an `instances:` rule to the type declaration. The driver registers them right after connecting (and after any `auth:` handshake), so routed responses always have children to land on. Exactly one roster source:

```yaml
child_entity_types:
  output:
    label: Output
    id_format: { type: integer, min: 1, max: 16, pad_width: 2 }
    state_variables:
      input:  { type: integer, label: Routed Input, cloud_priority: high }
      volume: { type: integer, label: Volume (dB) }
    instances:
      count: 6                 # fixed: registers IDs 1..6
      # count_from: output_count   # or: an integer config field (frame size varies by model)
      # ids_from: zone_ids         # or: a comma-separated config field ("1,2,4" — sparse IDs)
      label: "Output {id}"     # optional initial label; a user's project label always wins
```

Route response captures into child state with `child_set:` on a response entry. `id` is a capture reference (`$1`) or a literal; each state value is a capture reference or a literal, coerced by the child property's declared type:

```yaml
responses:
  # The device echoes which output a value belongs to — capture it as the ID:
  - match: 'Out(\d+) In(\d+)'
    child_set:
      - { type: output, id: $1, state: { input: $2 } }
  # A combined status line reports several children at fixed positions —
  # one entry per child with literal IDs:
  - match: '^x(\d+)AVx1,\s*x(\d+)AVx2$'
    child_set:
      - { type: output, id: 1, state: { input: $1 } }
      - { type: output, id: 2, state: { input: $2 } }
```

A response entry can carry `set:` (flat state) and `child_set:` together; first-match-wins dispatch is unchanged. A routed ID that isn't registered is skipped quietly — devices legitimately answer for ports beyond a configured roster. `child_set` works on regex responses (TCP, serial, UDP, HTTP text); it is not supported on OSC or `json:` responses.

Poll each child with an `each_child:` entry in `polling.queries` (also allowed in `on_connect`). It expands to one query per registered child, substituting `{child_id}` with the unpadded local ID:

```yaml
polling:
  interval: 10
  queries:
    - "PWR?\r"                                  # sent once
    - { each_child: output, send: "?VOUT{child_id}\r" }   # sent once per output
```

Per-child **writes** need nothing new — declare a command with a `child_id` parameter (see `commands`) and the platform validates and substitutes the ID. Per-child values that the device persists (a zone volume, an output mute) are modeled as child state variables plus a `child_id` command, not as `device_settings` — a device setting's `state_key` is flat and can't address a child. The IDE's per-child "Refresh from Device" re-derives the roster from config automatically.

Python drivers declare the same block in `DRIVER_INFO` and register instances at runtime with `self.register_child(type, local_id, initial_state=...)`, update them with `set_child_state` / `set_children_state_batch`, and remove them with `deregister_child`. For a **dynamic** type, pass the discovered control schema when registering:

```python
# Each component discovered over the wire publishes its own controls.
self.register_child(
    "component", "PgmGain",                       # string local_id (sanitized Code Name)
    schema={
        "gain": {"type": "number", "label": "Gain (dB)"},
        "mute": {"type": "boolean", "label": "Mute"},
    },
    initial_state={"gain": -6.0, "label": "Program Gain"},
)
self.set_child_state("component", "PgmGain", "gain", -3.0)   # validated against THIS child's schema
```

Each dynamic child's schema is validated independently, so `PgmGain` (gain/mute) and a sibling `PgmRouter` (select_1…) reject each other's props. To change a child's control set after the device topology changes, `deregister_child` then register again with the new schema. See the BaseDriver child-entity API for details.

#### Exposing a previewable video stream

If a device — or a child entity — offers a video stream a browser can show (a camera, an AV-over-IP encoder's preview feed), publish two state variables and the **Video Panel** plugin lists it automatically as a selectable source in the UI Builder. No manual setup, no plugin-specific code:

| Property | Type | Value |
|----------|------|-------|
| `preview_url` | string | The stream URL, reachable **from the OpenAVC server** (the server proxies it to the panel; the panel never connects to the AV network directly). Set it to `""` when no stream is available right now. |
| `preview_format` | string | `mjpeg` for multipart MJPEG over HTTP (rendered as a live image), or `rtsp` (played through the same WebRTC pipeline as a camera). |

Declare them like any other `state_variables` entry and set them as the device reports. The plugin reuses the device's or child's `label` (or `name`) for the dropdown entry, so there's nothing extra to name. Device-level keys are `device.<id>.preview_url`; child-level keys follow the child-entity convention (`device.<id>.<type>.<padded>.preview_url`). A worked example is the `chazy_control_pro` encoder child, which derives these from its secondary-stream URLs.

#### `commands` entry

```yaml
set_input:
  label: Set Input
  send: "{input}!\r"
  help: Switch the active input source on the switcher.
  params:
    input: { type: integer, required: true, help: "Input number (1-based)" }
```

- `send`: The raw bytes to send. `{param_name}` placeholders are substituted at runtime. Config values like `{set_id}` are also available. Supported escape sequences: `\r`, `\n`, `\t`, `\\`, `\xHH` (hex byte). (The key `string` is accepted as an alias.)
- `raw`: Optional. Set `raw: true` to send this command's `send` string exactly as written, skipping the driver's `command_prefix` / `command_suffix` framing (below). Use it for the odd command that doesn't share the common frame.
- `help`: Optional description of what the command does. Shown in the Programmer IDE command testing panel, macro editor, UI builder, and used by the AI assistant to understand commands.

> **Command framing.** When a text protocol wraps every command in a fixed header and terminator, declare them once at the driver level with `command_prefix` and `command_suffix` and author bare `send` strings. For example, `command_prefix: "!1"` and `command_suffix: "\r"` turn a command whose `send` is `PWR01` into `!1PWR01\r` on the wire. Both are opt-in and byte-stream only (TCP/serial/UDP); an OSC or HTTP command is never framed, and a single command can opt out with `raw: true`. To poll a framed command, list its **name** in `polling.queries` (it runs as that command, so the frame is applied and the response is matched) rather than re-typing the framed string.
- `params`: Parameter definitions. Each key matches a `{placeholder}` in the send string. Each parameter can include an optional `help` field describing what values are expected.

Parameter types are `string`, `integer`, `number`, `boolean`, `enum`, and `child_id`. A `child_id` parameter targets one of the driver's declared `child_entity_types` — set `child_type` to the type name. The command picker offers a dropdown of that device's registered children, and the integer local ID is substituted into the `{placeholder}`:

```yaml
route_decoder:
  label: Route Decoder
  send: "SET DEC {decoder_id} SWITCH {encoder_id}\r"
  params:
    decoder_id: { type: child_id, child_type: decoder, required: true }
    encoder_id: { type: child_id, child_type: encoder, required: true }
```

**Enum labels.** An `enum` parameter's `values` entries may be plain strings or `{value, label}` pairs. The **label** is what the operator picks in the dropdown and reads in a macro; the **value** is what goes on the wire — so you label a code set once instead of defining one command per code. A caller may pass either the label or the wire value (picker, macro `$var`, or the REST/cloud API); the runtime normalizes to the value. Plain-string lists behave exactly as before.

```yaml
set_dsp:
  label: DSP Mode
  send: "LMD{mode}"
  params:
    mode:
      type: enum
      required: true
      values:
        - { value: "00", label: Stereo }
        - { value: "0f", label: Multi Channel Stereo }
        - "ff"   # a plain string is still fine (value == label)
```

**Option pickers.** Wherever the platform already knows a parameter's valid values, turn the field into a dropdown so an operator picks instead of typing a string they can misspell. Beyond `enum` (a static list) and `child_id` (live child entities), a parameter can say where its options come from. These pickers appear on every authoring surface — Send Command, Quick Actions, macro steps, and UI Builder button bindings — and stay forgiving (you can still type a value the device hasn't reported yet):

- `options_state: <key>` — a dropdown sourced from this device's state. The IDE reads `device.<id>.<key>`, whose value is a JSON-encoded list (plain strings or `{value, label}` objects), and offers it. The driver publishes the list as a state variable and keeps it current (snapshot banks, named controls, router inputs, mixer channels — anything it enumerates at runtime).
- `options_source: <key>` — the same, but an absolute state key read as-is. Use `options_state` for per-device lists.
- `options_from: { param: <sibling>, source: child_schema }` — a *cascading* dropdown: the options follow another parameter's choice. With `source: child_schema`, picking a component in a sibling `child_id` parameter populates this one with that component's controls. This is how a "control name" follows the chosen component instead of being free-typed.
- `type_from: { param: <sibling> }` — make a parameter's *input type* follow the control chosen in a sibling cascade. The named sibling is itself an `options_from: { source: child_schema }` parameter; once a control is picked there, this parameter renders as that control's type (a number spinner with its range, a Yes/No for a boolean, etc.) instead of plain text. Until a control is picked, it stays a forgiving text box.

```yaml
recall_snapshot:
  label: Recall Snapshot
  send: "RECALL {bank}\r"
  params:
    bank: { type: string, required: true, label: Snapshot Bank, options_state: snapshot_banks }

set_component_control:
  label: Set Component Control
  params:
    component: { type: child_id, child_type: component, required: true }
    control:   { type: string, required: true, options_from: { param: component, source: child_schema } }
    value:     { type: string, required: true, type_from: { param: control } }   # follows the picked control's type
```

**Forgiving free-text.** For a value that genuinely can't be listed, keep it a text box but constrain its shape so a typo can't silently go on the wire:

- `min` / `max` on an `integer`/`number` parameter bound the value. Out-of-range values are rejected at command time, and the IDE shows an inline error and blocks the send/save.
- `pattern` is a regex the value must fully match — a shape check for an IP, hostname, or fixed-length ID. Same enforcement, runtime and IDE.
- Leading and trailing whitespace is trimmed before the value is sent. A raw passthrough parameter whose edge whitespace is part of the payload (say, a trailing line terminator) can declare `trim: false` to keep it.

These rules apply to every driver format — a Python driver's declared `min`/`max`/`pattern` are enforced at command time exactly like a YAML driver's.

```yaml
connect_host:
  send: "CONNECT {host}\r"
  params:
    host:  { type: string, label: Host, pattern: '^\d{1,3}(\.\d{1,3}){3}$' }   # dotted-quad IPv4
    level: { type: integer, label: Level, min: 0, max: 100 }
```

These constraints are checked by the runtime, so they hold no matter how the command is sent (Send Command, a macro, the API). The IDE mirrors them as you author so you catch a bad value before sending.

#### `quick_actions` and `actions` (Quick Action buttons)

Every command appears in the device view's "Send Command" list. For a device
with many commands, promote the few an operator reaches for to prominent
one-click buttons at the top of the view. The Send Command list still shows
everything — the strip is additive.

`quick_actions` is the simple form: a flat list of command ids. Each becomes a
button labelled by the command, firing it on click (commands with parameters
open an input dialog first).

```yaml
quick_actions: [power_on, power_off, recall_preset_1]
```

`actions` is the full form, with per-button icon, confirmation, and visibility:

```yaml
actions:
  - id: power_on            # required, unique
    kind: command           # promotes a command (the default kind)
    icon: power             # optional lucide icon name
  - id: reboot
    kind: command
    command: reboot_device  # command to send (defaults to the action id)
    icon: rotate-ccw
    confirm: "Reboot now? The device drops offline until it restarts."
  - id: recall_preset
    kind: command
    label: Recall Preset
    params:                 # same shape as command params; opens a dialog
      preset: { type: integer, required: true, min: 1, max: 8 }
```

- `icon`: a [lucide](https://lucide.dev/icons/) icon name in kebab-case (e.g. `power`, `search`, `rotate-ccw`).
- `confirm`: `true` for a generic prompt, or a message string. Use it for anything disruptive.
- `availability`: `online` (default) hides the button while the device is offline; `offline` shows it only while offline; `always` ignores connection state.
- `visible_when`: show the button only when a device state condition holds — `{ key: "device.$id.alarm", operator: truthy }`, or an `{any: [...]}` / `{all: [...]}` group. `$id` resolves to the device's id. Operators: `eq, ne, gt, lt, gte, lte, truthy, falsy`.

Each promoted command id must name a declared command. If an id appears in both `quick_actions` and `actions`, the `actions` entry wins.

#### `responses` entry

The shorthand `set` format is recommended (cleaner, matches community driver conventions):

```yaml
# Shorthand — set state variables directly from capture groups
- match: 'In(\d+) All'
  set: { input: "$1" }

# With a value map — translate raw values to friendly names
- match: 'POWR=(\d)'
  set: { power: "$1" }
  # In the verbose format, value maps are supported:
  # mappings:
  #   - { group: 1, state: power, type: string, map: { "0": "off", "1": "on" } }
```

- `match`: A regular expression. Use capture groups `()` to extract values.
- `set`: Maps capture groups to state variables. `"$1"` refers to the first capture group, `"$2"` to the second, etc. Literal strings without `$` set a static value.

**Verbose format** (used when you need type conversion or value maps):

```yaml
- match: 'POWR=(\d)'
  mappings:
    - { group: 1, state: power, type: string, map: { "0": "off", "1": "on" } }
```

- `mappings[].group`: Which regex capture group (1-based).
- `mappings[].state`: Which state variable to update.
- `mappings[].type`: How to convert the captured text: `string`, `integer`, `float`, `boolean`.
- `mappings[].map` (optional): A lookup table. If the captured value is a key in this object, the mapped value is used instead. The mapped value is then converted with `type` just like an unmapped capture, so the stored state matches its declared type regardless of transport.

Responses are checked in order. The first matching pattern wins.

**Throttling high-rate telemetry.** A response entry may declare `throttle: <seconds>`: after the rule matches and applies, further matches of the same rule are dropped until the window elapses. Use it on continuous telemetry streams — audio level meters, position feedback — where a device sends many frames per second and every dropped frame is superseded by the next one anyway:

```yaml
- match: 'METER (\d+),(\d+)'
  throttle: 0.5            # apply at most every 0.5 s (~2 updates/sec)
  set: { meter_in: "$1", meter_out: "$2" }
```

Don't throttle ordinary command replies or state-change notices — a dropped frame there means stale state until the next poll. Works on regex, `json: true`, and OSC address rules alike.

**Reading many fields from one JSON reply.** A regex response stops at the first match, so it can't fill several state variables from a single JSON body. When a reply is a JSON object (common with HTTP/REST devices), use a `json: true` response instead — it parses the body once and applies every mapping:

```yaml
responses:
  - json: true
    set:
      in_use:      { key: inUse, type: boolean }
      sessions:    { key: sessions, type: integer }
      status_text: { key: status }                  # type taken from the state variable
      mode:        { key: video.mode, map: { "1": Extended, "2": Clone } }
```

Each `set` value is the JSON field to read: a plain key, a dot path (`video.mode`), or a `{ key, type, map }` object. Missing keys are left alone, and a key that lands on a list or object yields its length. A reply wrapped in a single-element array (`[{ ... }]` — some devices wrap every reply that way) is unwrapped to its object first; multi-element arrays are ambiguous and are not parsed. You can add several `json: true` responses — they all run against every reply — and a body that isn't JSON falls through to your regex patterns. In the no-code Commands & Responses editor this is the "has a JSON field" response mode (one field per row).

Response patterns (and the `auth` prompt regexes) are validated when the driver loads: an invalid regex, or one with nested/overlapping quantifiers that can cause catastrophic backtracking against hostile device input (for example `(.+)+`, `(a|a)+`, `(foo|foobar)*`), is rejected and the driver won't load. Anchor and bound your patterns instead.

**Config values in patterns**: You can use config value placeholders like `{level_control}` in response patterns. They are resolved when the driver connects. This is useful for protocols like QSC Q-SYS where responses include user-configured control names.

#### `on_connect` section

Commands sent once immediately after the TCP/serial connection is established, before polling starts. Use this to enable feedback modes, request initial state, or set up the device for real-time notifications.

```yaml
on_connect:
  - "\x1b3CV\r\n"    # Extron: enable verbose mode 3 (push all changes)
```

```yaml
on_connect:
  - "< GET ALL >"    # Shure: request all current state values
```

Many AV devices can push state changes in real-time (volume knob turned, input switched from front panel) but only after the controller enables feedback mode. Without `on_connect`, the driver relies entirely on polling and misses changes between poll cycles.

#### `auth` section

For Telnet-style devices that present `Username:` / `Password:` prompts before accepting commands. The handshake runs after the TCP connection is established and before `on_connect` commands are sent. Add `username` and `password` fields to `default_config` and `config_schema` so the user can enter credentials in the Add Device dialog.

The handshake reads a raw byte stream, so it is only valid on `tcp` and `serial` transports — declaring `auth` on `udp`, `http`, or `osc` is a validation error. Both `username_prompt` and `password_prompt` are required; a handshake declared without them is rejected at load time rather than silently connecting unauthenticated.

```yaml
auth:
  type: telnet_login              # only type supported today
  username_prompt: "login: "      # regex matched against incoming bytes
  password_prompt: "Password: "   # regex matched against incoming bytes
  success_pattern: "GNET> "       # optional regex; if omitted, success is assumed
  failure_pattern: "Login incorrect"  # optional regex; matches => fail fast
  username_field: username        # which config field holds the username (default: "username")
  password_field: password        # which config field holds the password (default: "password")
  skip_if_empty: true             # if true and username is blank, the handshake is skipped (default: true)
  timeout_seconds: 10             # how long to wait for each prompt before failing
  line_ending: "\r\n"             # appended after username and password (default: "\r\n")
```

**How it works:**

1. The transport's frame parser is dropped to raw mode for the duration of the handshake. This lets the driver see partial prompts like `Login: ` that have no trailing newline.
2. The driver waits for `username_prompt` to appear in the incoming bytes, then sends `<username><line_ending>`.
3. It waits for `password_prompt`, then sends `<password><line_ending>`.
4. If `success_pattern` is set, the driver waits for it. Otherwise it briefly drains any post-password noise and assumes success.
5. The original frame parser is restored, then `on_connect` runs.

**Testing against the simulator:** the auto-generated simulator mirrors the handshake — it presents the prompts, honors the declared `line_ending`, and skips authentication in the same cases the driver would (`skip_if_empty` with a blank username). It accepts any credentials except the designated bad credential: a username or password of `invalid` makes the simulator reject the login — emitting `failure_pattern` when declared, otherwise re-prompting for the username — so you can exercise the driver's auth-failure handling without real hardware.

If the device's auth scheme isn't a prompt-and-response Telnet login (for example, a `LOGIN <password>` command-style auth or JSON-RPC `login` method), `auth: type: telnet_login` does not fit and you should use a Python driver.

#### `polling` section

```json
"polling": {
  "queries": ["!\\r\\n", "V\\r\\n"]
}
```

- `queries`: Command strings sent each cycle.

The poll cadence is **not** set in the `polling` block — it comes from `default_config.poll_interval` (in seconds), which device config can override per-instance. Set `poll_interval: 0` to disable polling. A top-level `polling.interval` is inert (the runtime never reads it) and is rejected by the community-catalog build, so don't add one.

#### `liveness` section

Some links can die without the driver ever noticing: UDP is connectionless (queries are fire-and-forget, so a dead host answers nothing and nothing errors), OSC likewise, and a push-style TCP device that vanishes without closing the socket looks connected forever. A `liveness` block arms a watchdog: send a cheap probe every `interval` seconds, expect a reply within `timeout`, and after `max_failures` consecutive misses drop the connection so the platform reconnects and the device card shows *Not responding*.

```yaml
liveness:
  send: "STATUS?\r\n"     # probe payload — raw protocol string, same rules as polling.queries
                           # (escape sequences, {config} substitution, terminator included);
                           # on osc transport this is an OSC address (optional args: list)
  expect: "^STATUS"        # optional regex — only matching replies count; if omitted,
                           # ANY inbound data during the wait counts as alive
  interval: 30             # seconds between probes (default 30)
  timeout: 5               # reply deadline per probe (default 5)
  max_failures: 2          # consecutive misses before dropping the link (default 2)
```

Pick a probe the device always answers — a status query the driver already polls is ideal (the reply also refreshes state through normal response matching). Leave `expect` off unless the device chatters on its own so much that "any data" would mask a dead control channel.

Valid on `tcp`, `serial`, `udp`, and `osc`. HTTP drivers don't need it: every HTTP poll already awaits its response, so missed polls flip the device offline on their own. Use it whenever the device is UDP/OSC-polled, or push-based over TCP with long idle gaps.

For plain TCP request/response devices you can also enable OS-level keepalive instead: set `tcp_keepalive: true` in `default_config` and the socket itself detects a dead peer (roughly 90 seconds, tuned by the platform). The two are complementary — `tcp_keepalive` proves the TCP path is up; `liveness` proves the device is actually answering the protocol.

#### `frame_parser` (advanced)

For protocols that don't use a simple delimiter, you can specify a frame parser:

```json
"frame_parser": {
  "type": "length_prefix",
  "header_size": 2,
  "header_offset": 0,
  "include_header": false
}
```

Types: `length_prefix` (reads a length header then N bytes), `fixed_length` (messages are always N bytes). For anything more complex, use a Python driver.

For `length_prefix`:

- `header_size` — bytes that hold the body length. Must be `1`, `2`, or `4`.
- `header_offset` — added to the length the header decodes to. Use a negative value (e.g. `-2`) when the length field counts the header bytes themselves, so only the body is read. Default `0`.
- `include_header` — `true` keeps the header bytes in the parsed frame; `false` (default) returns just the body.
- `length_offset` — constant bytes **before** the length field, when the length isn't the first thing on the wire. Default `0`. eISCP, for example, puts its 4-byte length at offset 8, behind the `ISCP` magic and a header-size field.
- `header_extra` — constant bytes **after** the length field, before the data (e.g. eISCP's version + reserved = 4). Default `0`. The full fixed header consumed per frame is `length_offset + header_size + header_extra`.
- `length_endian` — `big` (default) or `little`. Byte order of the length field.

For `fixed_length`, set `length` to the byte count of every frame.

#### `send_frame` (advanced)

The send-side twin of `frame_parser`. Use it when a protocol wraps every command in a binary packet header whose data-length is **computed per message** — something a static `command_prefix` can't express, because the length changes with each command (a feedback query like `PWRQSTN` is longer than a set like `PWR01`). The canonical case is **eISCP** (Onkyo / Integra / Pioneer receivers over TCP 60128): a 16-byte header of `ISCP` magic + header-size + a 4-byte data length + version/reserved, wrapping the `!1…<CR>` ISCP command.

```yaml
send_frame:
  type: length_prefix
  header: "ISCP\x00\x00\x00\x10"     # magic + fixed header-size (16), big-endian
  length_size: 4                     # width of the computed data-length field
  length_endian: big                 # big (default) | little
  after_length: "\x01\x00\x00\x00"   # version + 3 reserved bytes, before the data
```

On the wire each command becomes `header + <computed length> + after_length + (command_prefix + send + command_suffix)`. The length is the byte length of the framed command data (e.g. `!1PWR01\r` = 8). `header` and `after_length` are literal-escape byte strings (`\r`, `\n`, `\xHH`). `send_frame` applies to every byte-stream send origin — commands, raw poll/on_connect queries, the liveness probe, and device-setting writes — so a length-framed device answers all of them.

Pair it with a matching `frame_parser` to read the device's replies. For eISCP the reply header is identical, so the parser is `length_prefix` with the 4-byte length at offset 8:

```yaml
frame_parser:
  type: length_prefix
  length_offset: 8    # skip "ISCP" + header-size
  header_size: 4      # 4-byte length field
  header_extra: 4     # skip version + reserved
```

`command_prefix` / `command_suffix` still handle the inner ISCP framing (`!1` and `\r`); `send_frame` adds the outer packet header on top. For a serial protocol that uses the same `!1…\r` command bodies but no packet header, drop `send_frame` and keep just `command_prefix` / `command_suffix`.

#### `push` section

Most devices report state only when polled, and some push updates on the connection the driver already holds — both of those need nothing special. A `push` block is for devices that deliver notifications on a **separate channel the platform must open**. Two types are supported: `multicast` (the device sends state-change frames to a multicast group that OpenAVC joins) and `sse` (the device streams updates over a Server-Sent-Events endpoint on its HTTP API).

```yaml
# UDP multicast — the device sends frames to a group OpenAVC joins:
push:
  type: multicast
  group: "{notify_group}"   # literal address, or a {config_field} the user can change
  port: "{notify_port}"
```

- `type` — `multicast`.
- `group` — the IPv4 multicast group (224.0.0.0 – 239.255.255.255), as a literal or a `{config_field}` template. Use a template with a matching `config_schema` field when the device's notification target is user-configurable, so an installer who changed it on the device can match it in OpenAVC.
- `port` — the UDP port, literal or `{config_field}` template.

```yaml
# SSE — the driver holds a GET open with Accept: text/event-stream and the
# device streams updates back. HTTP transport only:
push:
  type: sse
  path: /v2/configuration/system/status   # one path, or a list of paths
  idle_timeout: 200                        # optional, seconds
```

- `type` — `sse`.
- `path` — the event-stream URL path on the device, or a **list** of paths for devices that stream each resource separately (Barco ClickShare lets you subscribe to every endpoint you can GET). Literal paths start with `/`; `{config_field}` templates are allowed.
- `idle_timeout` — optional. If the stream is silent (keepalives included) for this many seconds, the connection is presumed dead and reopened. Set it above the device's keepalive interval (ClickShare sends one every 90 s); omit it to wait indefinitely.

How it behaves:

- The subscription starts as soon as the device connects — **before** `on_connect` runs, so a device whose notifications must be armed by an `on_connect` command never sends a frame the platform misses. It stops when the device disconnects and re-arms automatically on reconnect.
- Everything that arrives goes through the driver's normal `responses` rules — same `match`/`set` semantics as a polled reply, nothing new to learn. A multicast datagram carrying several frames is split on the driver's `delimiter` first; an SSE event dispatches whole, exactly like an HTTP response body (SSE payloads are typically JSON — pair them with `json: true` response rules).
- Multicast frames are accepted **only from the device's own address**, so two identical devices multicasting to the same group each update their own OpenAVC device. SSE needs no filtering — the stream rides the driver's own HTTP session, with its authentication and TLS settings.
- A dropped SSE stream reconnects on its own with exponential backoff (1 s doubling to 30 s); a device reboot re-establishes the subscription without any user action.
- Push supplements polling; it doesn't replace it. Keep your `polling` block as the baseline resync — if the network filters multicast or an event stream drops (see below), the device still works, just at poll speed.

Many devices ship with notifications disabled and a runtime command to enable them — send it from `on_connect`. If a device offers a continuous meter stream on the same channel, gate it behind a config field (substituted into the arming command) and put a `throttle:` on the meter response rule so panels get smooth readings without flooding the system.

Network requirements (worth repeating in your driver's `help.setup` text): for multicast, the device and OpenAVC must be on the same VLAN (multicast doesn't cross VLANs without a router configured for it), and switches with IGMP snooping need an IGMP querier or the group may never reach the server. SSE has no special requirements — it's an ordinary outbound HTTPS/HTTP connection to the device's existing API port, just held open. When a join fails or a stream can't connect, nothing breaks — the driver logs the gap and polling carries on.

The simulator understands `push` too: a driver with a multicast push block emits its `simulator.notifications` templates to the group instead of the control connection, and a driver with an SSE push block serves its declared event-stream paths and delivers the templates there — so you can watch push updates end-to-end against a simulated device either way. See the [notifications section](https://github.com/open-avc/openavc-drivers/blob/main/docs/writing-simulators.md) of the simulator guide.

### Discovery

The `discovery:` block tells the matcher which network signals identify your device. Two kinds of declarations:

- **Fingerprints** identify the driver alone. One match is enough. Result state: *identified*.
- **Hints** narrow candidates. Several hints together produce a *possible* match with a candidate driver list. Result state: *possible*.

**Choosing a fingerprint type.** Most AV devices announce themselves passively over one of three multicast channels. Pick the one your device actually emits. `mdns:` matches the device's Bonjour / Avahi service-type announcement (e.g. `_pjlink._tcp.local.`) and is the common case for projectors, displays, and networked audio gear. `ssdp:` matches the UPnP device-type URN advertised in SSDP NOTIFY (e.g. `urn:schemas-upnp-org:device:MediaRenderer:1`) and shows up on media renderers, smart displays, and consumer-grade AV devices; when a vendor's whole product family advertises one URN, add a `model:` (or `manufacturer:` / `friendly_name:`) filter to the entry — it matches the device-description XML the scanner fetches, so each model can map to its own driver. `amx_ddp:` matches the AMXB make / model beacon many AV control-system devices emit on multicast `239.255.250.250:9131`; provide a `make` string and an optional `model_pattern` glob. When the device doesn't announce, fall back to `tcp_probe:` or `udp_probe:` to actively send a control-port query and match the response. Reach for `python:` only when the wire format is too dynamic for the declarative blocks (multi-step handshakes, binary parsing, broadcast-then-per-host TCP follow-ups).

A driver with no `discovery:` block at all is invisible to the matcher (still installable manually). The loader logs a warning so you notice.

The matcher is deterministic — there is no scoring. A signal either fires or it does not. Fingerprints always beat hint accumulation when both fire.

**Always declare hints alongside any fingerprint.** A fingerprint-only driver is fragile: the same device shows up via several scanner paths — an SSDP NOTIFY, an mDNS announcement, a banner-grab on the control port, or just an ARP-table sweep that captures the OUI. A driver claiming only one path silently misses the rest, even when the discovery scan already has the device's manufacturer string and hostname in evidence. Hints (`oui`, `hostname`, `port_open`, `manufacturer_alias`) cost nothing to declare and let the driver claim the device regardless of how it was found. Hints never produce *identified* on their own, but they turn an *unknown* into a *possible (candidate: <your driver>)* — strictly better, since the user gets a one-click choice.

**Cross-vendor demotion.** Some fingerprints identify a *protocol class*, not a specific vendor (a multi-vendor projector control protocol, a multi-vendor camera discovery beacon, a control-system family beacon). Drivers hosting those signals declare `cross_vendor: true` on the relevant fingerprint. When a cross-vendor fingerprint matches, the matcher consults peer drivers' hints; a vendor-specific peer matching via `oui`, `hostname`, `manufacturer_alias`, or `port_open` becomes the primary identification and the cross-vendor anchor moves to `alternatives[0]`. The user can switch back via the dropdown on the Discovery card.

If your driver targets a device that also responds to a generic cross-vendor probe, declare every brand string the firmware emits in `manufacturer_alias`. The exact string varies by vendor and model — list every variant you've seen (e.g. `["NEC", "Sharp NEC", "Sharp"]`, `["EPSON", "Seiko Epson"]`). You don't opt into the cross-vendor probe yourself — the anchor driver hosts it. Your `manufacturer_alias` is what makes your driver win the "best fit" pick.

```yaml
discovery:
  # ─── Fingerprints — any one alone identifies this driver ──────────

  mdns: "_pjlink._tcp.local."
  # OR list:
  #   mdns:
  #     - "_pjlink._tcp.local."
  #     - service: "_http._tcp.local."
  #       txt: { manufacturer: "Shure" }   # TXT-record filter

  ssdp: "urn:schemas-upnp-org:device:MediaRenderer:1"
  # OR list; entries can filter on the UPnP device description when a
  # vendor's whole family shares one URN:
  #   ssdp:
  #     - device_type: "urn:schemas-upnp-org:device:ATCUDevice:1"
  #       model: "ATDM-0604a"            # exact match, case-insensitive
  #       # also: manufacturer, friendly_name

  amx_ddp:
    make: "Polycom"
    model_pattern: "SoundStructure*"   # optional, default "*"

  tcp_probe:
    port: 4352
    tls: false                          # optional — TLS-wrap before send/read
                                        # for an HTTPS-only device. Default false.
    send_ascii: "%1POWR ?\r"           # exactly one of: send_ascii, send_hex,
                                        # (omit for connect-only banner read)
    expect: "%1POWR=[01]"               # exactly one of: expect (substring),
                                        # expect_regex, expect_hex
    cross_vendor: false                 # default false
    extract_manufacturer: "PJLink"      # optional — feeds manufacturer_alias path
    extract:                             # optional — free-form metadata
      model:
        regex: "model=(.+)"
        group: 1
    timeout_ms: 3000                    # optional, default 3000, max 10000

  udp_probe:
    port: 6454
    send_hex: "417274..."
    expect_regex: "NovaStar"
    cross_vendor: false
    extract_manufacturer: "NovaStar"
    timeout_ms: 2000                    # optional, default 2000 for UDP
                                        # (tcp_probe defaults to 3000), max 10000

  python:
    file: ./pjlink_class1_discovery.py
    cross_vendor: true
  # Path is relative to the driver YAML. The module must export
  # `async def probe(ctx) -> None`. See OpenAVC-Discovery-Spec.md
  # § Python escape-hatch for the companion API.

  # ─── Hints — combine to narrow candidates ─────────────────────────

  oui: ["00:0e:dd", "d8:34:ee"]              # MAC vendor blocks
  hostname: ["^MXA", "^ANI"]                  # regex patterns
  port_open: [2202]                           # vendor-specific TCP ports
  manufacturer_alias: ["NEC", "Sharp NEC"]   # case-insensitive exact match
  snmp_pen: 17049                             # IANA Private Enterprise Number
```

| Field | Kind | Description |
|-------|------|-------------|
| `mdns` | Fingerprint | mDNS service type the device announces. Bare string or list; list entries can be `{service, txt}` for TXT-record disambiguation when the service type is generic. |
| `ssdp` | Fingerprint | UPnP device-type URN announced in SSDP `ST` / `NT` headers. Bare string or list; list entries can be `{device_type, model, manufacturer, friendly_name}` — the optional fields match the device's UPnP description exactly (case-insensitive), so several drivers can share a family-wide URN. |
| `amx_ddp` | Fingerprint | AMX Device Discovery Protocol beacon match. Provide `make` (required) and optional `model_pattern` glob. |
| `tcp_probe` | Fingerprint | Connect to `port`, optionally send `send_ascii` / `send_hex`, match exactly one of `expect` / `expect_regex` / `expect_hex`. Optional `tls` (TLS-wrap the connection, no cert verification, for an HTTPS-only device), `cross_vendor`, `extract_manufacturer`, `extract` rules, `timeout_ms` (≤ 10000). |
| `udp_probe` | Fingerprint | Broadcast on `port`, match the response. Same sub-fields as `tcp_probe`, except `timeout_ms` defaults to 2000 (vs 3000 for `tcp_probe`). |
| `python` | Fingerprint | Sibling `<driver_id>_discovery.py` with `async def probe(ctx) -> None`. Use when the wire format needs Python (multi-step handshakes, binary parsers, broadcast-then-per-host TCP follow-ups). Sub-fields: `file` (path relative to the driver) and optional `cross_vendor`. |
| `oui` | Hint | MAC OUI prefixes (e.g. `["00:05:a6"]`). Drives the *possible* state and the "Unknown device, vendor: …" display. |
| `hostname` | Hint | Regex patterns matched against reverse-DNS / NetBIOS name. |
| `port_open` | Hint | TCP ports the device leaves open (e.g. `[1710, 4352]`). Generic web/SSH ports (22, 80, 443, 8000, 8080, 8443, 8888) are disallowed. |
| `manufacturer_alias` | Hint | Manufacturer / make strings the device returns when a scan captures one (probe response, AMX DDP `make`, ONVIF Manufacturer field, etc.). Case-insensitive exact match after whitespace strip. List every variant. Multiple drivers may share an alias. |
| `snmp_pen` | Hint | IANA Private Enterprise Number. |

#### Validation rules

Enforced at driver-load time and mirrored at catalog-build time by `openavc-drivers/scripts/build_index.py`:

1. **`port_open` rejects generic web/SSH ports `{22, 80, 443, 8000, 8080, 8443, 8888}`** — they would match every web/SSH device. Other ports are accepted.
2. **`tcp_probe` and `udp_probe` accept exactly one of `send_ascii` / `send_hex`.** Both is an error; omitting both is allowed for TCP connect-only banner reads.
3. **Probes declare exactly one of `expect` / `expect_regex` / `expect_hex`.** Required for both `tcp_probe` and `udp_probe`. Regex patterns are compiled at load time — invalid patterns fail validation.
4. **`timeout_ms` ≤ 10 000.** Hard cap.
5. **`extract_manufacturer:` is sugar for the manufacturer-alias enrichment path.** The probe runner lifts the value into the evidence response so the matcher can pick a vendor-specific peer when this driver carries `cross_vendor: true`.
6. **`manufacturer_alias` is case-insensitive and de-duplicated** at parse time. Multiple drivers may declare the same alias.
7. **Fingerprint collisions raise.** Two drivers cannot claim the same fingerprint (same kind, same source ID, same TXT filter) without explicit cross-vendor framing. The signal index raises at build time.
8. **Template drivers exempt.** Drivers whose ID starts with `generic_` skip discovery validation entirely — they are project starting points, not discoverable devices.

When you bump a driver to use a new schema field your platform target may lack, set `min_platform_version` in `index.json` so older OpenAVC instances grey out the driver instead of trying to parse fields they don't understand.

CI in the community-driver repo enforces the same rules across the whole catalog before any driver enters the index.

#### Protocol Declaration

The top-level `protocols` field is metadata for catalog tagging — it does **not** affect discovery matching. The matcher uses the `discovery:` block above. Declare `protocols` so the catalog can group drivers by protocol family:

```yaml
protocols: ["pjlink"]
```

This lets the discovery system match your driver directly when it identifies the protocol on a device, without relying on the built-in fallback mapping.

**Where to find MAC prefixes:** Look at the MAC address of your device (shown in device network settings or `arp -a`). The first three octets (e.g., `00:05:a6`) identify the manufacturer. You can verify at [IEEE OUI lookup](https://standards-oui.ieee.org/).

### HTTP/REST Drivers (.avcdriver)

For devices controlled via HTTP/REST APIs (Panasonic PTZ cameras, Sony Bravia displays, Crestron DM NVX, Zoom Rooms, etc.), set `transport: http` and use HTTP-specific command fields.

HTTP commands use `method`, `path`, and `body` instead of `string`/`send`:

```yaml
# Panasonic AW-series PTZ Camera (HTTP CGI control)
id: panasonic_aw_ptz
name: Panasonic AW PTZ Camera
manufacturer: Panasonic
category: camera
transport: http

default_config:
  host: ""
  port: 80
  poll_interval: 5

config_schema:
  host:
    type: string
    required: true
    label: IP Address
  port:
    type: integer
    default: 80
    label: Port
  auth_type:
    type: enum
    values: ["none", "basic", "digest"]
    default: "none"
    label: Authentication
  username:
    type: string
    default: "admin"
    label: Username
  password:
    type: string
    default: ""
    label: Password
    secret: true
  verify_ssl:
    type: boolean
    default: false
    label: Verify SSL Certificate

state_variables:
  power:
    type: enum
    values: ["off", "on"]
    label: Power State
  pan:
    type: string
    label: Pan Position
  tilt:
    type: string
    label: Tilt Position

commands:
  power_on:
    label: Power On
    method: GET
    path: "/cgi-bin/aw_ptz?cmd=%23O1&res=1"

  power_off:
    label: Power Off
    method: GET
    path: "/cgi-bin/aw_ptz?cmd=%23O0&res=1"

  recall_preset:
    label: Recall Preset
    method: GET
    path: "/cgi-bin/aw_ptz?cmd=%23R{preset:02d}&res=1"
    params:
      preset:
        type: integer
        required: true
        label: Preset Number
        min: 1
        max: 100

  set_pan_tilt:
    label: Set Pan/Tilt
    method: GET
    path: "/cgi-bin/aw_ptz?cmd=%23APC{pan}{tilt}&res=1"
    params:
      pan:
        type: string
        required: true
        label: Pan (hex, 4 chars)
      tilt:
        type: string
        required: true
        label: Tilt (hex, 4 chars)

responses:
  # Power query response contains "p1" (on) or "p0" (off)
  - match: 'p1'
    set: { power: "on" }
  - match: 'p0'
    set: { power: "off" }

polling:
  # Cadence comes from default_config.poll_interval (5s above), not here.
  queries:
    - "/cgi-bin/aw_ptz?cmd=%23O&res=1"
```

When the device answers with a JSON object instead of plain text like this, use a `json: true` response (see the `responses` entry section above) so one reply fills every state variable, rather than writing a regex per field.

#### HTTP command fields

| Field | Required | Description |
|-------|----------|-------------|
| `method` | No | HTTP method: `GET`, `POST`, `PUT`, `DELETE`. Default: `GET`. |
| `path` | Yes | URL path (appended to `http://host:port`). Supports `{param}` substitution. |
| `body` | No | Request body. Parsed as JSON when possible (Content-Type set to `application/json`); otherwise sent as raw bytes (no Content-Type unless you set one in `headers`). Supports `{param}` substitution. Used with POST/PUT. |
| `query_params` | No | Query parameters as key-value pairs. Supports `{param}` substitution. |
| `headers` | No | Custom request headers as a key-value map. Use this when the device requires a specific `Content-Type` for non-JSON bodies (e.g. `text/xml` for SOAP / Cisco RoomOS xAPI), or any other custom header. Values support `{param}` substitution. |
| `params` | No | Parameter definitions (same as TCP/serial commands). |

Example with custom headers (XML body):

```yaml
commands:
  put_xml:
    method: POST
    path: "/putxml"
    headers: { Content-Type: "text/xml" }
    body: "<Command><Audio><Volume><Set><Level>{level}</Level></Set></Volume></Audio></Command>"
    params:
      level: { type: integer, required: true, default: 50, min: 0, max: 100 }
```

#### TCP config fields

These fields in `config_schema` (or `default_config`) are recognized by the TCP transport:

| Field | Description |
|-------|-------------|
| `host` | Device IP or hostname (required) |
| `port` | TCP port (required) |
| `ssl` | Wrap the connection in TLS (default: false). Use for a device that exposes its control port over TLS. |
| `verify_ssl` | Verify the device's TLS certificate (default: true; set false for a self-signed cert) |
| `timeout` | Connection timeout in seconds (default: 5) |
| `inter_command_delay` | Minimum seconds between sends (default: 0). Use for gear that drops back-to-back commands. |

`ssl` / `verify_ssl` mirror the HTTP transport's fields, so a single `transport: tcp` driver reaches a plaintext or a TLS-wrapped device just by toggling `ssl` in config — no Python `connect()` override needed.

#### HTTP config fields

These fields in `config_schema` are recognized by the HTTP transport:

| Field | Description |
|-------|-------------|
| `host` | Device IP or hostname (required) |
| `port` | Port number (default: 80) |
| `ssl` | Use HTTPS (default: false) |
| `auth_type` | `"none"`, `"basic"`, `"bearer"`, `"api_key"`, `"digest"` |
| `username` | For basic/digest auth |
| `password` | For basic/digest auth |
| `token` | For bearer auth |
| `api_key` | For API key auth |
| `api_key_header` | Header name for API key (default: `X-API-Key`) |
| `verify_ssl` | Verify HTTPS certificates (default: true, set false for self-signed) |
| `timeout` | Request timeout in seconds (default: 10) |

#### HTTP polling

For HTTP drivers, polling queries can be:
- **Command names**: executes that command (e.g., `"get_status"`)
- **URL paths**: sends a GET request to that path (e.g., `"/api/status"`)

Response text from polled endpoints is matched against `responses` patterns, same as TCP/serial.

#### JSON body with parameter substitution

For REST APIs that expect JSON bodies, use the `body` field. Parameter placeholders `{name}` are substituted, and literal JSON braces are preserved:

```yaml
commands:
  set_volume:
    label: Set Volume
    method: POST
    path: "/api/audio"
    body: '{"channel": "program", "level": {level}}'
    params:
      level:
        type: integer
        required: true
        min: 0
        max: 100
```

With `level=75`, this sends `POST /api/audio` with body `{"channel": "program", "level": 75}`.

### Managing Drivers via API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/driver-definitions` | GET | List all driver definitions |
| `/api/driver-definitions/{id}` | GET | Get a single definition |
| `/api/driver-definitions` | POST | Create a new definition |
| `/api/driver-definitions/{id}` | PUT | Update a definition |
| `/api/driver-definitions/{id}` | DELETE | Delete a definition |
| `/api/driver-definitions/{id}/test-command` | POST | Test a command against live hardware |

### OSC (Open Sound Control) Drivers

For devices controlled via OSC over UDP (Behringer X32, QLab, ETC Eos, TouchDesigner, Resolume, etc.), set `transport: osc` and use OSC-specific command and response fields.

OSC commands use `address` and `args` instead of `send`/`string` (TCP/serial) or `method`/`path` (HTTP). OSC responses match by address pattern instead of regex.

#### OSC Command Format

```yaml
commands:
  set_ch1_fader:
    label: Set Channel 1 Fader
    address: "/ch/01/mix/fader"
    args:
      - type: f
        value: "{level}"
    params:
      level:
        type: number
        label: Level (0.0-1.0)

  mute_ch1:
    label: Mute Channel 1
    address: "/ch/01/mix/on"
    args:
      - type: i
        value: "0"

  query_info:
    label: Query Info
    address: "/info"
    # No args — sends address only (query)
```

Argument types: `f` (float32), `i` (int32), `s` (string), `h` (int64), `d` (float64), `T` (true), `F` (false), `N` (nil).

Argument `value` strings support `{param}` substitution from the command's params and device config, including format specs — `value: "{level}"` sends the param as-is, `value: "{level:.2f}"` formats it first. Address paths substitute the same way.

#### OSC Response Format

```yaml
responses:
  - address: "/ch/01/mix/fader"
    mappings:
      - arg: 0
        state: ch1_fader
        type: float

  - address: "/ch/01/mix/on"
    mappings:
      - arg: 0
        state: ch1_mute
        type: boolean
        map:
          "0": "true"
          "1": "false"
```

Responses use `address` instead of `match`/`pattern`, and `arg` (argument index) instead of `group` (regex capture group). Address patterns support `*` wildcards (e.g., `/ch/*/mix/fader`).

#### OSC on_connect and Polling

```yaml
# on_connect: send bare addresses (no args) or dicts with args
on_connect:
  - "/xremote"
  - "/info"

# Polling: command names or bare OSC addresses
# (cadence is default_config.poll_interval, not an interval key here)
polling:
  queries:
    - "renew_subscription"
```

#### Connection Verification

When connecting, the platform sends an OSC `/info` query to verify the device is reachable. If your device doesn't respond to `/info`, add `verify_timeout: 0` to `default_config` to skip the check:

```yaml
default_config:
  host: ""
  port: 8000
  verify_timeout: 0
```

The platform also monitors poll responses. If no data arrives from the device for several consecutive poll cycles, it marks the device as disconnected and starts auto-reconnect. This requires polling to be configured (which it should be for most OSC devices).

#### Listen Port

Most OSC devices reply to the sender's port (set `listen_port: 0`, the default). Some devices send feedback to a separate port. Set `listen_port` in the config if your device documentation specifies one.

#### Common OSC Ports

| Device | Send Port | Listen Port |
|--------|-----------|-------------|
| Behringer X32 / X32 Compact | 10023 | 0 (same socket) |
| Behringer X-Air (XR18, XR16, XR12) | 10024 | 0 (same socket) |
| Midas M32 | 10023 | 0 (same socket) |
| QLab | 53000 | 0 |
| ETC Eos | 3032 | 0 |
| Resolume Arena | 7000 | 0 |
| vMix | 8088 | 0 |

#### Extracting a value from a JSON reply (`json_path`)

Some OSC devices answer with the useful value buried inside JSON. QLab, for
example, replies as `/reply/<the address you sent>` with a single string
argument that holds JSON like `{"status":"ok","data":"Intro Music"}`. Add
`json_path` to a mapping to parse that string and pull out the value before it
is coerced and stored:

```yaml
responses:
  # QLab reply: the string arg is JSON; take its "data" field.
  - address: "/reply*/cue/playhead/displayName"
    mappings:
      - arg: 0
        json_path: data
        state: current_cue_name
        type: string
```

`json_path` is a dot-separated path of object keys and integer list indices:
`data`, `data.name`, `data.0`. If the path lands on an array or object, its
**length** is used — so a `boolean` state becomes "is there anything?" and an
`integer` state becomes the count:

```yaml
  # data is a JSON array of running cues; boolean-coerces to "anything running?"
  - address: "/reply*/runningOrPausedCues"
    mappings:
      - arg: 0
        json_path: data
        state: is_running
        type: boolean
```

If the argument isn't valid JSON, or the path doesn't resolve, the mapping is
skipped (the existing state is left untouched) rather than storing a wrong
value. Omit `json_path` for the normal behavior — the argument is read directly
by its OSC type. `json_path` also works on regex/text responses (TCP/HTTP), where
it is applied to the captured group.

#### OSC over TCP (SLIP framing)

QLab and some other OSC gear accept OSC over **TCP** as well as UDP. TCP is the
reliable path when replies are large (full cue lists) or delivery matters. Add a
`transport_mode` config field; when it is `tcp`, the platform frames OSC with
SLIP (RFC 1055) over a TCP connection and replies arrive on the same socket
(`listen_port` is not used in TCP mode):

```yaml
config_schema:
  transport_mode:
    type: enum
    values: [udp, tcp]
    default: udp
    label: Transport
default_config:
  transport_mode: udp
```

UDP is the default, so OSC drivers that don't declare `transport_mode` are
unaffected.

#### Derived config values (`config_derived`)

`config_derived` computes extra config values from other config fields. The
classic use is an **optional address prefix**: QLab messages are either rootless
(`/go`, the front workspace) or workspace-scoped (`/workspace/<id>/go`). Expose
one friendly `workspace_id` field and derive the prefix:

```yaml
config_derived:
  ws: "/workspace/{workspace_id}"   # "" when workspace_id is blank

commands:
  go:
    label: GO
    address: "{ws}/go"              # "/go" rootless, or "/workspace/<id>/go"
```

Each entry is a template substituted from config. If **any** `{field}` it
references is empty or missing, the whole derived value becomes `""` — so the
optional segment simply disappears. Derived values are computed when the device
connects and are visible to every command address, `on_connect` entry, response
address, and poll query, exactly like a real config field.

---

### Multi-transport drivers and bridges

Some text protocols run identically over the network and over RS-232. If yours does, add a `transports` list so the same driver can connect either way:

```yaml
transport: tcp          # the default medium
transports: [tcp, serial]
```

A device using this driver then shows a connection picker (`Network (IP)`, `Direct serial`, `Through a bridge`) in its Connection settings. Only declare `transports` when the command and response strings are byte-for-byte identical across the listed media. If they differ, ship separate drivers instead.

A **bridge** is a device that other devices connect through, such as a serial-to-Ethernet adapter. A bridge driver declares the typed ports it exposes:

```yaml
bridge:
  ports:
    - id: "serial:1"
      kind: serial            # serial, ir, or relay
      passthrough_port: 4999  # serial: the TCP port that pipes this line
      label: "RS-232 Port 1"
```

A downstream device binds to a bridge from its own Connection settings (choose `Through a bridge`, then pick the bridge and port). For a serial port, the platform routes the downstream over the bridge's pass-through with no extra code. Pushing baud and parity to the hardware needs a Python driver that overrides `prepare_bridge_port` (see Method 3). The Global Cache iTach IP2SL driver in the community library is a complete example.

### IR devices and IR bridges

An **IR device** is controlled by an infrared remote through an IR bridge. It has no address of its own, so it uses `transport: bridge` and sets `ir_codes: true`. Its commands are a code-set: a map of named codes stored as vendor-neutral **Pronto hex** plus a per-command repeat. Each code becomes a device command, so panel buttons and macros bind to it normally.

The built-in **IR Device** (`generic_ir`) is authored per-device in the device page's **IR Codes** editor (learn from a remote, paste Pronto, type a raw code, or search a database). To publish a ready-made code-set as a community driver, use the same shape and ship the codes in `default_config.ir_codes`:

```yaml
id: brandx_tv_ir
name: BrandX TV (IR)
manufacturer: BrandX
category: display
transport: bridge         # no address of its own; emits through a bridge
ir_codes: true            # shows the IR Codes editor; codes become commands
default_config:
  ir_codes:
    power_on: { label: "Power On",    pronto: "0000 006D 0000 0022 ...", repeat: 1 }
    vol_up:   { label: "Volume Up",   pronto: "0000 006D 0000 0022 ...", repeat: 2 }
    hdmi1:    { label: "Input HDMI1", pronto: "0000 006D 0000 0022 ...", repeat: 1 }
```

An **IR bridge** declares `kind: ir` ports. Unlike a serial port, an IR port has no transparent pipe, so it needs a Python driver that emits and (optionally) learns: override `bridge_emit` to convert a Pronto code to the hardware's wire format and send it, and the `bridge_learn_*` methods plus `can_learn` to capture codes from a remote. Use `server.transport.ir_codec` for the Pronto step. The Global Cache iTach IP2IR driver is a complete example.

## Method 3: Python Driver

Python drivers give you full control. Use this method when:

- The device uses a **binary protocol** (bytes, checksums, length headers).
- The device's authentication scheme isn't a Telnet-style `Username:` / `Password:` prompt handshake. (Prompt-driven Telnet auth is supported declaratively via the `.avcdriver` `auth` section — use that first. Python is needed for `LOGIN <password>` command-style auth, JSON-RPC login, OAuth, challenge-response, etc.)
- You need **complex state logic** that can't be expressed as regex patterns.
- The device uses a **non-standard transport** (UDP, HTTP, etc.).
- The device must be **provisioned before it will connect** — e.g. a control interface that ships switched off. A Python driver can declare a setup action (a Quick Action with `kind: "setup"`) and implement `run_setup_action`, a wizard that runs while the device is offline, talks to the device over its own connection, and can rewrite the device config and reconnect when done. See the driver development guide for the `run_setup_action` contract.

### Minimal Example: Simple TCP Device

If your device uses a text protocol over TCP with `\r` delimiters, you can rely on the auto-transport system and only implement `send_command()`:

```python
# server/drivers/my_switcher.py

from server.drivers.base import BaseDriver
from typing import Any


class MySwitcherDriver(BaseDriver):
    """Controls my custom video switcher."""

    DRIVER_INFO = {
        "id": "my_switcher",
        "name": "My Video Switcher",
        "manufacturer": "Custom",
        "category": "switcher",
        "version": "1.0.0",
        "author": "Your Name",
        "description": "Controls my custom switcher via TCP.",
        "transport": "tcp",
        "default_config": {
            "host": "",
            "port": 23,
            "poll_interval": 15,
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 23, "label": "Port"},
            "poll_interval": {"type": "integer", "default": 15, "label": "Poll Interval (sec)"},
        },
        "state_variables": {
            "input": {"type": "integer", "label": "Current Input"},
        },
        "commands": {
            "set_input": {
                "label": "Set Input",
                "params": {
                    "input": {"type": "integer", "required": True},
                },
            },
            "query_input": {"label": "Query Input", "params": {}},
        },
    }

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        match command:
            case "set_input":
                input_num = params.get("input", 1)
                await self.transport.send(f"{input_num}!\r".encode())
            case "query_input":
                await self.transport.send(b"!\r")

    async def on_data_received(self, data: bytes) -> None:
        text = data.decode("ascii", errors="ignore").strip()
        # Parse "In3 All" style responses
        if text.startswith("In") and "All" in text:
            try:
                input_num = int(text[2:].split()[0])
                self.set_state("input", input_num)
            except ValueError:
                pass

    async def poll(self) -> None:
        if self.transport and self.transport.connected:
            await self.transport.send(b"!\r")
```

**What's happening here**:
- `connect()` and `disconnect()` are **not defined**. The base class handles them automatically using `DRIVER_INFO["transport"]` and the device config.
- The default delimiter (`\r`) is used for message framing.
- Polling is started automatically if `poll_interval > 0` in the device config.
- `on_data_received()` is called with complete, delimiter-stripped messages.
- `set_state("input", 3)` writes to `device.<device_id>.input` in the state store.
- **All sent and received data is automatically logged** in the device log. No logging code needed. See "Device Log" below.

### MQTT Drivers (Python)

Some devices are controlled over **MQTT** — they run (or connect to) an MQTT broker, and you control them by publishing to command topics and subscribing to status topics. Examples: TVs with an embedded broker, building-management gateways, IoT bridges.

MQTT is a **Python-only transport** (like SSH). It isn't offered in the Driver Builder or `.avcdriver` files, because topic-based pub/sub doesn't map onto the request/response shape those use. Set `"transport": "mqtt"` in a Python driver's `DRIVER_INFO` and the platform builds the connection for you.

Because MQTT is pub/sub rather than a single byte stream, inbound messages arrive **topic-tagged**: override `on_mqtt_message(topic, payload)` instead of `on_data_received(data)`, and subscribe to the topics you care about in `_post_connect()` (which also runs again after a reconnect).

```python
from server.drivers.base import BaseDriver


class MyMqttDeviceDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "my_mqtt_device",
        "name": "My MQTT Device",
        "manufacturer": "Custom",
        "category": "display",
        "version": "1.0.0",
        "author": "Your Name",
        "description": "Controls a device over MQTT.",
        "transport": "mqtt",
        "default_config": {
            "host": "",
            "port": 1883,
            "username": "",
            "password": "",
            # TLS (optional): ssl + verify_ssl mirror the TCP/HTTP transports.
            # client_cert / client_key are paths for devices that require a
            # client certificate. mqtt_version defaults to "3.1.1" ("5.0" opt-in).
            "ssl": False,
            "verify_ssl": True,
        },
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 1883, "label": "Port"},
        },
        "state_variables": {"power": {"type": "boolean", "label": "Power"}},
        "commands": {"power_on": {"label": "Power On", "params": {}}},
    }

    async def _post_connect(self) -> None:
        # Transport is open here. Subscribe to status topics.
        await self.transport.subscribe("device/+/status")

    async def on_mqtt_message(self, topic: str, payload: bytes) -> None:
        if topic.endswith("/status"):
            self.set_state("power", payload == b"on")

    async def send_command(self, command, params=None):
        if command == "power_on":
            await self.transport.publish("device/cmd/power", "on")
```

The transport exposes `await self.transport.publish(topic, payload)`, `await self.transport.subscribe(topic)`, and `await self.transport.unsubscribe(topic)`. Recognized config keys: `host`, `port`, `username`, `password`, `client_id`, `ssl` (alias `use_tls`), `verify_ssl`, `client_cert`, `client_key`, `ca_cert`, `ciphers`, `keepalive`, and `mqtt_version`. When `verify_ssl` is off, the transport also relaxes the cipher level so devices with old or weak self-signed broker certificates still connect.

To test an MQTT driver without hardware, pair it with a `_sim.py` that subclasses `MQTTSimulator` (a minimal broker) — see the simulator guide.

### Creating Python Drivers in the Code View

The easiest way to create a Python driver is in the Programmer IDE:

1. Click **Code** in the sidebar
2. Under **Python Drivers**, click **+**
3. Fill in the driver ID, name, manufacturer, category, and transport
4. Select a template (TCP, HTTP, Serial, Polling, or Minimal)
5. Click **Create Driver**

The editor opens with a pre-filled template. Edit the code, then click **Save & Reload Driver** (or press Ctrl+Shift+R) to hot-reload the driver without restarting the server. If the code has errors, the old driver stays active and the error is shown in the console.

### A Python driver is a bundle

A Python driver is usually more than one file. Alongside the main `<driver>.py` you may have:

- `<driver>_discovery.py` — an optional discovery companion that helps the network scan identify the device.
- `<driver>_sim.py` — an optional simulator so the device can be tested without hardware.

These companions sit next to the main file in `driver_repo/`. They don't appear in the **Python Drivers** list (only the driver itself does), but they travel with it: exporting, installing, and deleting the driver all act on the whole set.

### Importing and Exporting Python Drivers

In the **Code** view, the **Python Drivers** section header has an **Import** button (the upload icon), and each driver row has an **Export** button (the download icon):

- **Export** downloads the driver and any companions as a single `.zip` bundle, so you can hand the whole driver to someone else in one file.
- **Import** accepts either a single `.py` file or a `.zip` bundle. A bundle is unpacked into `driver_repo/`, the main driver is loaded, and the companions come along with it.

A single `.py` file is enough to control hardware on its own; importing the bundle is what brings the simulator and the discovery companion too.

> A Python driver is executable code. Importing one runs it inside the OpenAVC server, so only import drivers from a source you trust.

### Installing Python Drivers

You can also install Python drivers from other sources:
- **Browse Community** tab: click Install on any Python driver. Companions (`_discovery.py` / `_sim.py`) are pulled in automatically.
- **Manual:** place the `.py` file (and any companions) directly in `driver_repo/`.

OpenAVC scans `driver_repo/` at startup and dynamically loads any Python file that contains a `BaseDriver` subclass with a valid `DRIVER_INFO` dict. Drivers created in the Code view are also saved to `driver_repo/`.

After installation, the driver appears in the "Add Device" dialog.

### Full Example: Binary Protocol (Samsung MDC)

For binary protocols, you override `_create_frame_parser()` and `_resolve_delimiter()` to tell the transport how to split the byte stream into messages. This example is the actual Samsung MDC driver included with OpenAVC.

```python
# server/drivers/samsung_mdc.py

from server.drivers.base import BaseDriver
from server.transport.binary_helpers import checksum_sum
from server.transport.frame_parsers import CallableFrameParser, FrameParser
from typing import Any, Optional

# MDC command constants
CMD_POWER = 0x11
CMD_VOLUME = 0x12

# Frame builder helper
def _build_mdc_frame(cmd: int, display_id: int, data: bytes = b"") -> bytes:
    frame = bytes([cmd, display_id, len(data)]) + data
    cs = checksum_sum(frame)
    return bytes([0xAA]) + frame + bytes([cs])

# Frame parser helper
def _parse_mdc_frame(buffer: bytes) -> tuple[bytes | None, bytes]:
    start = buffer.find(0xAA)
    if start == -1:
        return None, b""
    if start > 0:
        buffer = buffer[start:]
    if len(buffer) < 4:
        return None, buffer
    data_len = buffer[3]
    total_len = 4 + data_len + 1
    if len(buffer) < total_len:
        return None, buffer
    frame = buffer[1 : total_len - 1]
    return frame, buffer[total_len:]


class SamsungMDCDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "samsung_mdc",
        "name": "Samsung MDC Display",
        "manufacturer": "Samsung",
        "category": "display",
        "transport": "tcp",
        "default_config": {"host": "", "port": 1515, "display_id": 1, "poll_interval": 15},
        "config_schema": {
            "host": {"type": "string", "required": True, "label": "IP Address"},
            "port": {"type": "integer", "default": 1515, "label": "Port"},
            "display_id": {"type": "integer", "default": 1, "label": "Display ID"},
        },
        "state_variables": {
            "power": {"type": "enum", "values": ["off", "on"], "label": "Power"},
            "volume": {"type": "integer", "label": "Volume"},
        },
        "commands": {
            "power_on": {"label": "Power On", "params": {}},
            "set_volume": {"label": "Set Volume", "params": {"level": {"type": "integer"}}},
        },
    }

    def _create_frame_parser(self) -> Optional[FrameParser]:
        # Use a callable parser for custom binary framing
        return CallableFrameParser(_parse_mdc_frame)

    def _resolve_delimiter(self) -> Optional[bytes]:
        # Binary protocol -- no delimiter
        return None

    async def send_command(self, command: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        if not self.transport or not self.transport.connected:
            raise ConnectionError(f"[{self.device_id}] Not connected")

        display_id = self.config.get("display_id", 1)

        match command:
            case "power_on":
                await self.transport.send(_build_mdc_frame(CMD_POWER, display_id, bytes([1])))
            case "set_volume":
                level = max(0, min(100, int(params.get("level", 0))))
                await self.transport.send(_build_mdc_frame(CMD_VOLUME, display_id, bytes([level])))

    async def on_data_received(self, data: bytes) -> None:
        if len(data) < 3:
            return
        cmd = data[0]
        payload = data[3:] if len(data) > 3 else b""

        if cmd == CMD_POWER and payload:
            self.set_state("power", "on" if payload[0] else "off")
        elif cmd == CMD_VOLUME and payload:
            self.set_state("volume", payload[0])
```

**Key differences from the text-protocol example**:
- `_create_frame_parser()` returns a `CallableFrameParser` with a custom function that knows how to find message boundaries in the binary stream.
- `_resolve_delimiter()` returns `None` because there's no text delimiter.
- `on_data_received()` gets complete binary frames (header and checksum already stripped by the parser).

### Custom connect(): Authentication Handshake

If a device requires a handshake on connect (like PJLink's greeting), override `connect()`:

```python
async def connect(self) -> None:
    from server.transport.tcp import TCPTransport

    host = self.config.get("host", "")
    port = self.config.get("port", 4352)

    self.transport = await TCPTransport.create(
        host=host,
        port=port,
        on_data=self.on_data_received,
        on_disconnect=self._handle_transport_disconnect,
        delimiter=b"\r",
        name=self.device_id,  # identifies this device in the log
    )

    # Wait for the device's greeting message
    await asyncio.sleep(0.1)

    # Send authentication if needed
    password = self.config.get("password", "")
    if password:
        await self.transport.send(f"AUTH {password}\r".encode())
        await asyncio.sleep(0.1)

    self._connected = True
    self.set_state("connected", True)
    await self.events.emit(f"device.connected.{self.device_id}")

    poll_interval = self.config.get("poll_interval", 15)
    if poll_interval > 0:
        await self.start_polling(poll_interval)
```

**Important**: Always pass `name=self.device_id` when creating a transport manually. This ensures all sent and received data appears in the device log with the correct device name for filtering.

### Custom Transport: UDP / Wake-on-LAN

For devices that don't use persistent connections, override both `connect()` and `disconnect()`:

```python
class WakeOnLANDriver(BaseDriver):
    DRIVER_INFO = {
        "id": "wake_on_lan",
        "transport": "udp",
        # ...
    }

    async def connect(self) -> None:
        # No persistent connection needed
        self._connected = True
        self.set_state("connected", True)
        await self.events.emit(f"device.connected.{self.device_id}")

    async def disconnect(self) -> None:
        self._connected = False
        self.set_state("connected", False)
        await self.events.emit(f"device.disconnected.{self.device_id}")

    async def send_command(self, command: str, params=None) -> Any:
        if command == "wake":
            # Create a temporary UDP socket, send, close
            udp = UDPTransport(name=self.device_id)
            await udp.open(allow_broadcast=True)
            await udp.send(magic_packet, "255.255.255.255", 9)
            udp.close()
```

**Reachability for UDP drivers.** UDP is purely connectionless — there's no `verify()` probe and no socket-level disconnect signal. If your device is meant to be bidirectional (i.e., you poll status, not just fire-and-forget like Wake-on-LAN), declare a positive `poll_interval` in `default_config` and implement `poll()` to send a status query. A successful round-trip keeps `connected: True`; consecutive failures flip it to `False` and start auto-reconnect. Without polling, the platform has no way to know the device went away and `connected` stays `True` against a dead host. The Wake-on-LAN example above is the rare case where omitting polling is correct, because there's nothing to read back.

### Controller drivers: managing many child entities

Some devices are really a controller for many sub-units — a video matrix with hundreds of encoders and decoders, a DSP with dozens of zones, a presentation switcher with video-wall presets. Declare those sub-units as **child entities** (the [`child_entity_types`](#child_entity_types-entry) block in `DRIVER_INFO`) and the platform gives each one its own state, a row in the device's Child Entities tab, and addressable keys (`device.<id>.<type>.<local_id>.<property>`) — you never assemble those key strings yourself.

Only Python drivers can register children at runtime (a YAML driver just declares the types). Every controller driver follows the same pattern, worked end-to-end in `chazy_control_pro.py`:

1. **On connect, enumerate the roster.** Run one cheap "list everything" command, parse it, and register each unit. `register_child` is idempotent, so calling it again on every poll is safe.
2. **Fill in detail** with `poll_children`, which batches the registered IDs (50 at a time) instead of firing one request per unit.
3. **On poll, reconcile** — register units that appeared, deregister ones the controller no longer reports. Keep the roster query on the normal poll interval and run the heavier per-unit detail refresh on a slower cadence (a separate `detail_poll_interval`) so a large controller doesn't flood the network every cycle.
4. **Set `online` to match the unit's nature.** Physical endpoints (encoders/decoders) that come and go derive `online` from their link state and stay registered while offline; virtual objects (groups, walls, presets) are online whenever the controller lists them, and only disappear when deleted on the device.
5. **Support "Refresh from Device"** by overriding `refresh_children`.

```python
class MatrixControllerDriver(BaseDriver):
    DRIVER_INFO = {
        # …identity, connection, default_config (poll_interval, detail_poll_interval)…
        "child_entity_types": {
            "encoder": {
                "label": "Encoder", "label_plural": "Encoders",
                "id_format": {"type": "integer", "min": 1, "max": 256, "pad_width": 3},
                "state_variables": {
                    "name": {"type": "string"},
                    "ip": {"type": "string"},
                    "signal_present": {"type": "boolean"},
                },
                "summary_fields": ["name", "ip", "signal_present"],
                "label_field": "name",
            },
            # decoder, video_wall, …
        },
    }

    async def connect(self) -> None:
        await super().connect()
        await self._reconcile_roster()                      # register from the roster
        await self.poll_children("encoder", self._fetch_encoder_detail)

    async def poll(self) -> None:
        await self._reconcile_roster()                      # add new / drop gone
        if self._detail_due():                              # slower cadence
            await self.poll_children("encoder", self._fetch_encoder_detail)

    async def _reconcile_roster(self) -> None:
        roster = self._parse_status(await self._send("GET STATUS"))
        seen = set()
        for unit in roster:
            seen.add(unit.id)
            self.register_child(
                "encoder", unit.id,
                initial_state={"name": unit.name, "online": unit.linked},
            )
        for stale in set(self.list_children("encoder")) - seen:
            self.deregister_child("encoder", stale)

    async def refresh_children(self) -> dict:               # IDE "Refresh from Device"
        await self._reconcile_roster()
        return {"encoders": len(self.list_children("encoder"))}

    async def _fetch_encoder_detail(self, ids: list[int]) -> dict[int, dict]:
        ...                                                 # return {id: {prop: value}}
```

Commands that act on a specific child take a `child_id` parameter (see the [`commands`](#commands-entry) reference) — the platform substitutes the integer local ID into the wire template, so there's no separate per-child command surface.

### BaseDriver Hooks Reference

These methods can be overridden in your driver subclass:

| Method | Required | Default Behavior |
|--------|----------|-----------------|
| `send_command(command, params)` | **Yes** | (abstract, must implement) |
| `connect()` | No | Auto-creates TCP or serial transport from DRIVER_INFO and config |
| `disconnect()` | No | Stops polling, closes transport, updates state |
| `on_data_received(data)` | No | No-op. Override to parse device responses. |
| `poll()` | No | No-op. Override to send status queries. |
| `_create_frame_parser()` | No | Returns `None` (uses delimiter framing). Override for binary protocols. |
| `_resolve_delimiter()` | No | Checks DRIVER_INFO, then config, then defaults to `b"\r"`. |
| `_handle_transport_disconnect()` | No | Sets connected=False, emits disconnect event. |

### The `poll()` contract

Python drivers that override `poll()` **must propagate transport-level errors**. The polling loop catches `ConnectionError`, `TimeoutError`, `OSError`, and any `httpx.HTTPError` and counts them toward the missed-poll watchdog. After 3 consecutive dry polls, the platform flips `device.<id>.connected` to `False` and emits `device.disconnected.<id>`.

Swallowing transport errors here causes `device.<id>.connected` to lie when the device is unreachable. **Do this:**

```python
async def poll(self) -> None:
    try:
        await self._refresh_state()
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise ConnectionError(f"Device not responding: {exc}") from exc
```

**Not this:**

```python
async def poll(self) -> None:
    try:
        await self._refresh_state()
    except (httpx.ConnectError, httpx.TimeoutException):
        log.warning("Poll failed — device not responding")
        # WRONG: device.<id>.connected will stay True forever
```

Protocol-level errors (unexpected response shape, expected device states like "in standby") may be handled inside `poll()` — those indicate the device is reachable but not in a queryable state, so they should not penalize the watchdog. If you raise `ValueError` or any non-transport exception, the platform logs it via `device.error.<id>` and continues polling.

If your driver doesn't use one of the platform transport classes (`TCPTransport`, `HTTPClientTransport`, `UDPTransport`, `OSCTransport`), use `_verify_reachable(host, port, timeout)` in `connect()` to confirm the device is alive before setting `connected=True`:

```python
async def connect(self) -> None:
    host = self.config.get("host", "")
    port = self.config.get("port", 1400)
    if not await self._verify_reachable(host, port, timeout=3.0):
        raise ConnectionError(f"Device at {host}:{port} not responding")
    # ...rest of setup, then set_state("connected", True)
```

**Declaring the offline reason.** When a device is offline, the platform publishes `device.<id>.offline_reason` (a stable code: `auth_failed`, `connection_refused`, `unreachable`, `no_response`, ...) and `device.<id>.offline_detail` (the sentence shown on the device card). Standard transport failures classify automatically. When your driver detects a failure the transport can't see — a rejected login, a device that accepts the socket but never speaks your protocol — raise a typed fault so the reason is exact:

```python
from server.drivers.base import BaseDriver, ConnectionFaultError

raise ConnectionFaultError(
    f"Login rejected for {host}:{port} — check the username and password.",
    code="auth_failed",
)
```

The code becomes `offline_reason` verbatim and the message becomes `offline_detail`; unknown codes raise immediately so a typo can't silently misclassify. For a failure with no exception to carry it (a keep-alive loop that stopped hearing replies), stash the reason before forcing the disconnect: `self._stash_fault("no_response", "...")` then `self._handle_transport_disconnect()`. Don't re-wrap transport errors that already carry their cause (a refused socket, a DNS failure) — re-raise those unchanged.

### Convenience Methods

These are available on every driver via the `BaseDriver` base class:

| Method | Description |
|--------|-------------|
| `self.set_state("power", "on")` | Sets `device.<device_id>.power` in the state store |
| `self.get_state("power")` | Gets the current value of `device.<device_id>.power` |
| `await self.start_polling(15)` | Starts calling `self.poll()` every 15 seconds |
| `await self.stop_polling()` | Stops the polling loop |
| `await self._verify_reachable(host, port)` | Returns True if a TCP connection opens within the timeout |
| `await self.transport.send(data)` | Send raw bytes to the device |
| `await self.transport.send_and_wait(data, timeout=5)` | Send and wait for the next response |
| `self.device_id` | The device's ID (e.g., `"projector1"`) |
| `self.config` | The device's config dict from project.avc |
| `self.events` | The EventBus instance (for emitting custom events) |

### Device Log

All transport types (TCP, serial, HTTP, UDP) automatically log every send and receive at INFO level, tagged with the device ID. This means:

- **You do not need to add any logging code** for protocol traffic. It's built into the transport layer.
- Every `TX` (sent) and `RX` (received) message appears in the Programmer IDE's device log, filterable by device.
- Text data is shown decoded. Binary data is shown as hex.

**Example log output** (automatic, no code needed):
```
[my_projector] TX: %1POWR 1
[my_projector] RX: %1POWR=OK
[my_projector] TX: %1POWR ?
[my_projector] RX: %1POWR=3
```

**When to add your own logging**: Use `log.info(f"[{self.device_id}] ...")` for semantic events that add meaning beyond the raw protocol. For example, interpreting a power state code into a human-readable value:

```python
from server.utils.logger import get_logger
log = get_logger(__name__)

async def on_data_received(self, data: bytes) -> None:
    # The transport already logged: [my_projector] RX: %1POWR=3
    # Add a semantic log for the interpreted meaning:
    if code == "POWR":
        state = {"0": "off", "1": "on", "2": "cooling", "3": "warming"}[value]
        self.set_state("power", state)
        log.info(f"[{self.device_id}] Power: {state}")
```

**If you override `connect()`**: Always pass `name=self.device_id` when creating a transport manually. If you forget, the log will show the IP address instead of the device name, and device log filtering won't work.

### Available Frame Parsers

Import from `server.transport.frame_parsers`:

| Parser | Use Case |
|--------|----------|
| `DelimiterFrameParser(b"\r")` | Text protocols with a line ending |
| `LengthPrefixFrameParser(header_size=2)` | Protocols with a length byte/word before the payload |
| `FixedLengthFrameParser(length=8)` | Protocols where every message is exactly N bytes |
| `CallableFrameParser(your_function)` | Custom protocols where you write the parsing logic |

All frame parsers accept an optional `max_buffer` parameter (default: 65536 bytes / 64 KB). If the internal buffer exceeds this limit (for example, when a device sends garbage data without proper delimiters), the buffer is automatically cleared to prevent unbounded memory growth.

### Available Binary Helpers

Import from `server.transport.binary_helpers`:

| Function | Description |
|----------|-------------|
| `checksum_xor(data)` | XOR all bytes together |
| `checksum_sum(data)` | Sum all bytes, masked to 0xFF |
| `crc16_ccitt(data)` | CRC-16/CCITT-FALSE |
| `hex_dump(data)` | Format bytes as a hex dump string for logging |
| `escape_bytes(data, escape_char, special)` | Escape special bytes |
| `unescape_bytes(data, escape_char, special)` | Reverse escape |

---

## DRIVER_INFO Reference

Every driver, whether Python, JSON, or Driver Builder, defines the same metadata structure. Here's the complete reference:

```python
DRIVER_INFO = {
    # --- Required ---
    "id": "unique_driver_id",        # Lowercase, underscores only
    "name": "Human-Readable Name",
    "transport": "tcp",              # "tcp", "serial", "http", "udp", or "osc"

    # --- Optional metadata ---
    "manufacturer": "Generic",
    "category": "utility",           # projector, display, switcher, etc.
    "version": "1.0.0",
    "author": "Your Name",
    "description": "What this driver does.",

    # --- Help text (shown in UI and used by AI assistant) ---
    "help": {
        "overview": "Brief description of what this driver controls.",
        "setup": "Step-by-step setup instructions for connecting the device.",
    },

    # --- Connection defaults ---
    "default_config": {
        "host": "",
        "port": 23,
        "poll_interval": 15,
    },

    # --- Config fields shown in "Add Device" dialog ---
    "config_schema": {
        "host": {
            "type": "string",
            "required": True,
            "label": "IP Address",
        },
        "port": {
            "type": "integer",
            "default": 23,
            "label": "Port",
        },
    },

    # --- State properties this driver exposes ---
    "state_variables": {
        "power": {
            "type": "enum",
            "values": ["off", "on"],
            "label": "Power State",
            "help": "Current power state of the device.",
        },
        "volume": {
            "type": "integer",
            "label": "Volume",
            "help": "Current volume level (0-100).",
        },
    },

    # --- Commands this driver accepts ---
    "commands": {
        "power_on": {
            "label": "Power On",
            "params": {},
            "help": "Turn on the device.",
        },
        "set_volume": {
            "label": "Set Volume",
            "params": {
                "level": {
                    "type": "integer",
                    "min": 0,
                    "max": 100,
                    "required": True,
                    "help": "Volume level 0-100.",
                },
            },
            "help": "Set the audio volume level.",
        },
    },

    # --- Protocol declarations (catalog metadata) ---
    "protocols": ["extron_sis"],

    # --- Discovery declarations (optional — fingerprints + hints) ---
    "discovery": {
        "tcp_probe": {
            "port": 23,
            "send_ascii": "\x1b3CV\r\n",
            "expect": "Vrbn",
            "extract_manufacturer": "Extron",
        },
        "oui": ["00:05:a6"],            # hint; produces "possible" state
    },
}
```

---

## Testing Your Driver

### Without hardware (simulation mode)

For serial drivers, use the `SIM:` prefix as the port name (e.g., `SIM:test`). This creates a simulated serial connection that accepts sends without error.

For TCP and HTTP drivers, use the built-in device simulator. Start simulation from the Programmer IDE toolbar or with the REST API:

```bash
# Start the simulator as a standalone process
python -m simulator --config sim_config.json
```

The simulator auto-generates behavior for all YAML drivers. For more realistic simulation, add a `simulator:` section to your driver (see "Adding Simulation Support" above).

### With the server

```bash
python -m server.main
```

Then open the Programmer UI at `http://localhost:8080/programmer`:

1. Go to **Devices** > **Add Device**.
2. Select your new driver from the dropdown.
3. Enter the connection details.
4. Use the **Command Testing** section to send commands and see state updates.

### Writing automated tests

See `tests/test_pjlink_driver.py` or `tests/test_samsung_mdc_driver.py` for examples. The pattern is:

1. Create a simulator fixture that listens on a test port.
2. Create a driver fixture that connects to the simulator.
3. Send commands and assert state changes.

```python
async def test_power_on(my_driver, state):
    await my_driver.send_command("power_on")
    await asyncio.sleep(0.2)  # Wait for response
    assert my_driver.get_state("power") == "on"
```

Run tests with:

```bash
pytest tests/test_my_driver.py -v
```

## Adding Simulation Support

The OpenAVC Simulator (included with OpenAVC at `simulator/`) lets your driver work without real hardware by running a fake protocol server. YAML drivers get basic simulation automatically. Python drivers need a companion `_sim.py` file.

### YAML Drivers: Add a `simulator` Section

All YAML drivers auto-generate basic simulation from their commands and responses. For more realistic behavior, add a `simulator:` section to your `.avcdriver` file:

```yaml
simulator:
  initial_state:
    input: 1
    volume: 50
    mute: false

  delays:
    command_response: 0.03   # seconds before responding

  controls:
    - type: slider
      key: volume
      label: Volume
      min: 0
      max: 100
      step: 1
    - type: toggle
      key: mute
      label: Mute
    - type: select
      key: input
      label: Input
      options: ["1", "2", "3", "4"]

  command_handlers:
    # Simple template: match a pattern, set state, respond
    - receive: '(\d+)!'
      set_state: { input: "{1}" }
      respond: "In{1} All\r\n"

    # Script handler: inline Python for complex logic
    - match: '(\d+)V'
      handler: |
        level = int(match.group(1))
        level = max(0, min(100, level))
        state["volume"] = level
        respond(f"Vol{level}\r\n")

  # Enable push: state changes are pushed to connected drivers
  push_state: true

  # Optional: override push format for specific variables
  notifications:
    mute:
      'true': 'Amt1'
      'false': 'Amt0'

  error_modes:
    communication_timeout:
      behavior: no_response
      description: "Device stops responding to all commands"
```

**Key sections:**

| Section | Purpose |
|---------|---------|
| `initial_state` | Default values when simulation starts |
| `delays` | Response timing (makes simulation feel realistic) |
| `controls` | UI controls in the Simulator dashboard (sliders, toggles, buttons) |
| `push_state` | Enable push behavior (state changes sent to connected drivers) |
| `notifications` | Optional overrides for the push format on state changes |
| `command_handlers` | How the simulator responds to commands |
| `error_modes` | Failure scenarios for testing error handling |

**Control types:** `power`, `slider`, `toggle`, `select`, `matrix`, `indicator`, `meters`, `presets`, `group`.

**Push behavior** is opt-in. Set `push_state: true` to enable it. When enabled, state changes from the simulator UI are pushed to connected drivers using the driver's existing response format. Only set this for devices that actually send unsolicited updates (e.g., Extron verbose mode, Shure subscriptions). Without it, the simulator is poll-only. **Notifications** are optional overrides for the push format. If your device uses a different format for unsolicited messages (for example, value-specific strings like `Amt1`/`Amt0` instead of `Amt{value}`), add a `notifications` section to override the format. Use `'*'` as the key to match any value with `{value}` as a placeholder, or use specific values like `'true'`/`'false'` for exact matches.

For the complete simulator guide with all control types, state machines, and Python simulators, see the Writing Simulators documentation in the driver repository.

---

## Troubleshooting

### State variables not updating

**Symptom:** Commands send successfully but state variables stay at their default values.

**Common causes:**

1. **Wrong delimiter.** If the delimiter doesn't match what the device sends, messages are never split correctly and response patterns never see a complete line. Check your device's protocol manual. Try `\r\n`, `\r`, and `\n`. Use the Test tab to see raw responses.

2. **Response pattern doesn't match.** Open the device log in the Programmer IDE and look at the `RX` lines. Copy the exact text and test your regex pattern against it. Common mistakes:
   - Forgetting to escape special regex characters (`.`, `(`, `)`, `+`, `*`)
   - Pattern expects `"Vol45"` but device sends `"Vol 45"` (extra space)
   - Pattern is case-sensitive but device sends mixed case

3. **Wrong capture group number.** `$1` refers to the first set of parentheses in the pattern. If your pattern is `Vol(\d+)\s+(\w+)` and you want the second group, use `$2`.

4. **Type coercion mismatch.** If the type is `integer` but the captured value is `"abc"`, it falls back to storing the raw string and logs a warning. Check that the captured text is actually numeric.

5. **Config placeholders in patterns.** If your response pattern uses config values like `{level_control}`, make sure the config field exists and has a value. These are substituted before the regex is compiled.

### Commands not sending or silently failing

**Symptom:** Clicking a command in the UI does nothing, or the device doesn't respond.

**Common causes:**

1. **Device not connected.** Check the connection status in the Devices view. If it shows disconnected, verify the IP address and port.

2. **Missing parameter.** If a command uses `{input}` but the parameter isn't provided, the command may fail silently. Check that all required parameters are filled in.

3. **Wrong escape sequences.** The command string `{input}!\r` needs the `\r` to be inside quotes in YAML. In the Driver Builder UI, escape sequences are handled automatically.

4. **Inter-command delay too high.** If you set a long delay (e.g., 5 seconds), commands queue and appear unresponsive.

### Driver doesn't appear in the Add Device dialog

**Symptom:** You saved a driver but it doesn't show up when adding a device.

**Common causes:**

1. **Validation error on save.** Check the error message in the Driver Builder. Common issues: missing `id` or `name`, invalid transport type, malformed regex in a response pattern.

2. **Duplicate driver ID.** If another driver already has the same ID, the save will fail with a 409 Conflict error. Driver IDs must be unique across all sources (built-in, community, user-created).

3. **Python driver missing DRIVER_INFO.** For Python drivers, the file must contain a class that inherits from `BaseDriver` and has a `DRIVER_INFO` dict with at least `id`, `name`, and `transport`.

4. **File not in the right directory.** Drivers must be in `driver_repo/` (or `server/drivers/definitions/` for built-in drivers). The directory is scanned at startup.

### Device reconnects constantly

**Symptom:** The device connects, then immediately disconnects and reconnects in a loop.

**Common causes:**

1. **Authentication failing.** Some devices close the connection if authentication fails. Check if the device requires a password or API key.

2. **Wrong protocol.** If you connect a text-protocol driver to a binary-protocol device (or vice versa), the device may reject the malformed data and close the connection.

3. **Firewall or network issue.** The device may be accepting the TCP connection but dropping it due to a firewall rule or access list.

---

## Using AI Assistants

If you use an AI coding assistant (Claude, ChatGPT, Copilot, etc.), you can point it to the `AGENTS.md` file in the [community driver repository](https://github.com/open-avc/openavc-drivers/blob/main/AGENTS.md). It contains the complete YAML schema, Python driver API, naming conventions, and examples in a format optimized for LLM agents. The repository also includes `scripts/build_index.py` (run with `--check` to validate without writing) that your assistant can use to verify its work — it validates the schema, regex patterns, and catalog consistency.
