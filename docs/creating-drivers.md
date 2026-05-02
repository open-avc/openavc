# Creating Device Drivers for OpenAVC

OpenAVC supports three ways to create device drivers, from easiest to most powerful:

1. **Driver Builder UI.** Visual wizard in the Programmer IDE. No code required.
2. **Driver Definition File (.avcdriver).** Write a YAML file by hand. No code required.
3. **Python Driver.** Full Python class for advanced protocols.

All three methods produce drivers that work identically at runtime. Choose the simplest method that covers your device's protocol.

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
| **Connection** | Transport (TCP/serial/UDP/OSC/HTTP), Authentication, Connect Sequence (`on_connect`), Frame Parser, Configuration Fields (`config_schema`) |
| **Behavior** | State Variables, Commands, Responses, Polling, Device Settings |
| **Discovery** | Discovery hints (mDNS, SSDP, OUI, ports, protocols) |
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

Below the identity block, the **Help & Setup** section takes two markdown fields:

- **Overview** — what the device is, who uses it.
- **Setup Instructions** — step-by-step the integrator follows to get the device talking (IP setup, pairing, physical buttons).

Both appear in the Add Device dialog when someone picks this driver.

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

**State Variables** — the read-only properties the driver reports. Each entry has a type (string, integer, number, boolean, enum), an optional label and help text, and for numerics optional min/max/step (used by the simulator and panel UI to auto-generate sliders).

| Variable ID | Label | Type | Notes |
|-------------|-------|------|-------|
| `input` | Current Input | Integer | |
| `volume` | Volume | Integer | min 0, max 100 |
| `mute` | Mute | Boolean | |

**Commands** — actions the driver can perform. Each command's shape depends on the transport:

- **TCP / serial / UDP**: a single **Send** field. `{param_name}` placeholders substitute parameter values; `{config_key}` placeholders substitute device config (e.g., `{set_id}`).
- **HTTP**: method, path, body, headers, query params. Every field supports `{placeholders}`.
- **OSC**: address + a typed argument list (`f`/`i`/`s`/`h`/`d`/`T`/`F`/`N`).

**Parameters** for each command let users fill in what to send. Each parameter has a type, optional required flag, label, help, default, and (numeric) min/max bounds or (enum) allowed values.

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

**Polling** — periodic queries that keep state fresh on devices that don't push updates. Set the interval (seconds) and list the command names (or raw query strings) to send each cycle.

**Device Settings** — writable values stored on the device hardware (labels, IDs, lock codes). Pending writes queue while the device is offline and replay on reconnect. Less common than state variables — most drivers don't need this.

#### 5. Discovery tab (optional)

Hints the discovery engine uses to match found devices to this driver: ports, MAC OUI prefixes, protocol identifiers, mDNS service names, hostname patterns, SSDP/UPnP device-type URN substrings.

Skip this if the device isn't auto-discoverable.

#### 6. Simulation tab (optional)

Adds simulator support so the driver can be exercised without real hardware. Most drivers can rely on auto-generated simulation; the editor here lets you customize initial state, push behavior, command handlers, error modes, and (for richer simulators) state machines and controls. See [Writing Simulators](https://docs.openavc.com/writing-simulators/) for the full guide.

#### 7. Test tab: run it against a device

The test panel runs commands through the real `ConfigurableDriver` runtime — auth handshake and connect sequence run first, parameter substitution and response patterns work the same as production. Anything that works here will work when the driver is wired into a project device.

For each test:

- **Host / Port** — defaults to the driver's `default_config.port`. Override per test.
- **Driver Config** — fields declared in `config_schema` (credentials, instance tags) appear here so you can fill them in without saving them to defaults.
- **Command** — pick a defined command from the dropdown. The form below shows its parameters with typed inputs, and a live wire-format preview shows the substituted string that will go on the wire.
- **Raw probe** — also available in the dropdown. Sends arbitrary bytes without auth or on_connect. Useful for one-off "what does this device say" checks.

Each result shows what was sent, every received chunk (with `\r` and `\n` made visible), and any state variable changes the responses produced.

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
  interval: 15
  queries:
    - "!\r\n"                        # Query current input
    - "V\r\n"                        # Query current volume
```

Notice how much cleaner this is compared to JSON: comments explain the protocol, regex patterns don't need double-escaping, and the structure is easy to scan.

### Definition Reference

#### Top-level fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique driver identifier. Lowercase, underscores. |
| `name` | Yes | Human-readable display name. |
| `transport` | Yes | `"tcp"`, `"serial"`, `"http"`, `"udp"`, or `"osc"`. |
| `manufacturer` | No | Manufacturer name. Default: `"Generic"`. |
| `category` | No | One of: `projector`, `display`, `switcher`, `scaler`, `audio`, `camera`, `lighting`, `relay`, `utility`, `other`. |
| `version` | No | Semantic version. Default: `"1.0.0"`. |
| `author` | No | Who wrote this driver. |
| `description` | No | Brief description. |
| `help` | No | Help text object: `{overview: "...", setup: "..."}`. Shown in the Add Device dialog and available to the AI assistant. |
| `delimiter` | No | Message delimiter. Default: `"\\r"`. Use `"\\r\\n"` for CRLF. |
| `default_config` | No | Default values for config fields. |
| `config_schema` | No | Describes config fields shown in "Add Device" dialog. |
| `device_settings` | No | Configurable settings that live on the device. See below. |
| `state_variables` | No | State properties this driver exposes. |
| `commands` | No | Commands this driver can send. |
| `responses` | No | Regex patterns for parsing device replies. |
| `auth` | No | Login handshake performed between TCP connect and `on_connect`. See `auth` section below. |
| `on_connect` | No | List of raw commands sent immediately after connecting. Use for enabling verbose/feedback mode or requesting initial state. |
| `polling` | No | Periodic status query configuration. |
| `frame_parser` | No | Advanced: custom framing (see below). |
| `protocols` | No | Protocol names this driver speaks (e.g., `["pjlink"]`, `["extron_sis"]`). Helps discovery match devices to drivers. |
| `discovery` | No | Discovery hints for network scanning. See Discovery Hints below. |

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

Types: `string`, `integer`, `number`, `float`, `boolean`, `enum`. For `enum`, add a `"values"` array.

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
| `values` | No | For `enum` type: array of allowed values. |
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

Types: `string`, `integer`, `number`, `float`, `boolean`, `enum`.

The optional `help` field provides a description shown in the Driver Builder UI and available to the AI assistant.

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
- `help`: Optional description of what the command does. Shown in the Programmer IDE command testing panel, macro editor, UI builder, and used by the AI assistant to understand commands.
- `params`: Parameter definitions. Each key matches a `{placeholder}` in the send string. Each parameter can include an optional `help` field describing what values are expected.

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
- `mappings[].map` (optional): A lookup table. If the captured value is a key in this object, the mapped value is used instead.

Responses are checked in order. The first matching pattern wins.

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

If the device's auth scheme isn't a prompt-and-response Telnet login (for example, a `LOGIN <password>` command-style auth or JSON-RPC `login` method), `auth: type: telnet_login` does not fit and you should use a Python driver.

#### `polling` section

```json
"polling": {
  "interval": 15,
  "queries": ["!\\r\\n", "V\\r\\n"]
}
```

- `interval`: Seconds between poll cycles. Also set via `poll_interval` in device config.
- `queries`: Command strings sent each cycle.

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

### Discovery Hints

The `discovery` section helps OpenAVC's network discovery system identify devices on the network and match them to your driver. When a user runs a discovery scan, these hints improve how accurately your driver is suggested for detected devices.

```yaml
discovery:
  ports: [23]
  mac_prefixes: ["00:05:a6"]
```

| Field | Type | Description |
|-------|------|-------------|
| `ports` | list[int] | TCP ports this device typically listens on. Used to match against open ports found during scanning. |
| `mac_prefixes` | list[str] | IEEE OUI prefixes (first 3 bytes of MAC address) for this manufacturer's devices. Format: `"00:05:a6"`. |
| `mdns_services` | list[str] | mDNS/Bonjour service types the device advertises (e.g., `"_pjlink._tcp.local."`). |
| `upnp_types` | list[str] | UPnP device type URNs the device advertises. |
| `hostname_patterns` | list[str] | Regex patterns to match against the device's hostname (e.g., `"^DTP-.*"`). |

All fields are optional. Even without a `discovery` section, the driver's `manufacturer`, `category`, and `default_config.port` are used as basic hints. Adding explicit discovery hints makes matching more accurate.

#### Protocol Declaration

If your device speaks a known protocol that OpenAVC's discovery probes can identify (PJLink, Extron SIS, Biamp Tesira, QSC Q-SYS, Kramer P3000, Samsung MDC, VISCA, etc.), declare it with the top-level `protocols` field:

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
  interval: 5
  queries:
    - "/cgi-bin/aw_ptz?cmd=%23O&res=1"
```

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

Argument types: `f` (float), `i` (integer), `s` (string), `T` (true), `F` (false).

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
polling:
  interval: 9
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

---

## Method 3: Python Driver

Python drivers give you full control. Use this method when:

- The device uses a **binary protocol** (bytes, checksums, length headers).
- The device's authentication scheme isn't a Telnet-style `Username:` / `Password:` prompt handshake. (Prompt-driven Telnet auth is supported declaratively via the `.avcdriver` `auth` section — use that first. Python is needed for `LOGIN <password>` command-style auth, JSON-RPC login, OAuth, challenge-response, etc.)
- You need **complex state logic** that can't be expressed as regex patterns.
- The device uses a **non-standard transport** (UDP, HTTP, etc.).

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

### Creating Python Drivers in the Code View

The easiest way to create a Python driver is in the Programmer IDE:

1. Click **Code** in the sidebar
2. Under **Python Drivers**, click **+**
3. Fill in the driver ID, name, manufacturer, category, and transport
4. Select a template (TCP, HTTP, Serial, Polling, or Minimal)
5. Click **Create Driver**

The editor opens with a pre-filled template. Edit the code, then click **Save & Reload Driver** (or press Ctrl+Shift+R) to hot-reload the driver without restarting the server. If the code has errors, the old driver stays active and the error is shown in the console.

### Installing Python Drivers

You can also install Python drivers from other sources:
- **Browse Community** tab: click Install on any Python driver
- **Import from File:** upload a `.py` file from your computer
- **Manual:** place the `.py` file directly in `driver_repo/`

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

### Convenience Methods

These are available on every driver via the `BaseDriver` base class:

| Method | Description |
|--------|-------------|
| `self.set_state("power", "on")` | Sets `device.<device_id>.power` in the state store |
| `self.get_state("power")` | Gets the current value of `device.<device_id>.power` |
| `await self.start_polling(15)` | Starts calling `self.poll()` every 15 seconds |
| `await self.stop_polling()` | Stops the polling loop |
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
    "transport": "tcp",              # "tcp", "serial", "http", or "udp"

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

    # --- Protocol declarations (optional, improves discovery matching) ---
    "protocols": ["extron_sis"],

    # --- Discovery hints (optional, improves network scanning) ---
    "discovery": {
        "ports": [23],
        "mac_prefixes": ["00:05:a6"],
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

If you use an AI coding assistant (Claude, ChatGPT, Copilot, etc.), you can point it to the `AGENTS.md` file in the [community driver repository](https://github.com/open-avc/openavc-drivers/blob/main/AGENTS.md). It contains the complete YAML schema, Python driver API, naming conventions, and examples in a format optimized for LLM agents. The repository also includes a `validate.py` script that your assistant can use to check its work.
