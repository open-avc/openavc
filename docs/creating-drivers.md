# Creating Device Drivers for OpenAVC

OpenAVC supports three ways to create device drivers, from easiest to most powerful:

1. **Driver Builder UI.** Visual wizard in the Programmer IDE. No code required.
2. **Driver Definition File (.avcdriver).** Write a YAML file by hand. No code required.
3. **Python Driver.** Full Python class for advanced protocols.

All three methods produce drivers that work identically at runtime. Choose the simplest method that covers your device's protocol.

| Method | Skill Level | Best For |
|--------|-------------|----------|
| Driver Builder UI | Beginner | Text-based protocols (Extron SIS, Kramer, generic RS-232) |
| .avcdriver File | Intermediate | Text-based protocols, sharing drivers as files |
| Python Driver | Advanced | Binary protocols, authentication handshakes, complex state |

---

## Quick Decision Guide

**Can the device be controlled with text commands over TCP or serial?**
(e.g., sending `"POWR ON\r"` and getting back `"POWR=ON\r"`)

- **Yes:** Use the **Driver Builder UI** or a **.avcdriver definition**.
- **No, it uses HTTP/REST:** Use a **.avcdriver definition** with `transport: http`. HTTP commands use `method`, `path`, and `body` fields instead of raw command strings. See the HTTP section below.
- **No, it uses a binary protocol:** Use a **Python driver**.
- **No, it uses UDP broadcast:** Use a **Python driver** (see the Wake-on-LAN driver as an example).

---

## Method 1: Driver Builder UI

The Driver Builder is a visual tool inside the Programmer IDE. Open it by clicking the **Driver Library** icon (hard drive) in the sidebar.

### Step-by-step Walkthrough

#### 1. Create a new driver

Click **Create New Driver** in the left panel. The editor opens with seven tabs.

#### 2. General tab: Name your driver

| Field | Example | Notes |
|-------|---------|-------|
| Driver ID | `extron_sw4` | Lowercase, no spaces. Cannot be changed later. |
| Driver Name | `Extron SW4 HD 4K` | Human-readable. Shown in the "Add Device" dialog. |
| Manufacturer | `Extron` | |
| Category | `Switcher` | Pick from the dropdown. |
| Version | `1.0.0` | |
| Author | `Your Name` | |
| Description | `Controls Extron SW4 HD 4K HDMI switcher via RS-232 or TCP.` | |

#### 3. Transport tab: How to connect

- **Transport Type**: TCP (network) or Serial (RS-232/RS-485).
- **Message Delimiter**: The character(s) that mark the end of every message. Check your device's protocol guide. Common values:
  - `\r` for most AV devices (Extron, Kramer, PJLink)
  - `\r\n` for some network devices
  - `\n` is rare
- **Default Port** (TCP) or **Default Baud Rate** (Serial): Pre-filled when adding this device.

#### 4. State Variables tab: What to track

Define the properties you want to read from the device. Each state variable becomes visible in the Devices view and available for macros, scripts, and UI bindings.

| Variable ID | Label | Help Text | Type |
|-------------|-------|-----------|------|
| `input` | Current Input | Active input number | Integer |
| `volume` | Volume | Volume level 0-100 | Integer |
| `mute` | Mute | Audio mute state | Boolean |

Types: `string`, `integer`, `boolean`, `enum`.

The **Help Text** column is optional but recommended. It's shown in the Driver Builder and used by the AI assistant to understand what each variable represents.

#### 5. Commands tab: What to send

Click **Add Command**, then fill in:

| Field | Example | Notes |
|-------|---------|-------|
| Command ID | `set_input` | Used in macros and scripts. |
| Display Label | `Set Input` | Shown in the UI. |
| Help Text | `Switch the active input source.` | Shown when selecting this command in the IDE and used by the AI assistant. |
| Command String | `{input}!\r` | The raw bytes to send. Use `{param_name}` for parameter placeholders. |
| Parameters | `input` (Integer) | Defines what the user fills in when using this command. |

**Parameter placeholders**: The string `{input}!\r` with parameter `input=3` becomes `3!\r` when sent.

**Escape sequences**: The following escape sequences are supported in command strings and delimiters: `\r` (carriage return), `\n` (newline), `\t` (tab), `\\` (literal backslash), `\xHH` (hex byte, e.g. `\x1B` for ESC).

**Example commands for an Extron switcher**:

| Command ID | Label | String | Parameters |
|------------|-------|--------|------------|
| `set_input` | Set Input | `{input}!\r` | `input` (Integer) |
| `set_volume` | Set Volume | `{level}V\r` | `level` (Integer) |
| `mute_on` | Mute On | `1Z\r` | (none) |
| `mute_off` | Mute Off | `0Z\r` | (none) |
| `query_input` | Query Input | `!\r` | (none) |

#### 6. Responses tab: How to parse replies

Each response pattern is a regular expression (regex) that matches a line the device sends back. When a match is found, capture groups are mapped to state variables.

**Example**: The Extron switcher responds `In3 All` when input 3 is selected.

| Regex Pattern | Group | State Variable | Type |
|---------------|-------|----------------|------|
| `In(\d+) All` | 1 | `input` | Integer |

- **Group 1** means the first `(\d+)` capture group, the number.
- **Type** tells the system how to convert the captured string: `integer` parses it as a number, `boolean` treats `1`/`true`/`on` as true, `string` keeps it as-is.

**Value maps**: For devices that return codes instead of readable values, you can add a value map. For example, if a projector returns `POWR=0` for off and `POWR=1` for on:

| Regex Pattern | Group | State Variable | Type | Map |
|---------------|-------|----------------|------|-----|
| `POWR=(\d)` | 1 | `power` | String | `{"0": "off", "1": "on"}` |

Value maps are configured in the JSON definition (see Method 2). The UI shows a type dropdown.

#### 7. Polling tab: Automatic status queries

If the device doesn't push status changes on its own, you can poll it periodically.

- **Poll Interval**: How often to send queries (seconds). Set to 0 to disable. Typical: 10--30 seconds.
- **Poll Queries**: The command strings to send each cycle. For example, `!\r` to query the current input.

#### 8. Live Test tab: Try it out

Enter a device's IP address and port, type a command string, and hit Send. You'll see the raw response from the device. Use this to verify your command strings and response patterns before saving.

#### 9. Save

Click **Save**. The driver is immediately available in the "Add Device" dialog. The definition file is saved to the `driver_repo/` directory.

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
| `transport` | Yes | `"tcp"`, `"serial"`, or `"http"`. |
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

Types: `string`, `integer`, `number`, `enum`, `object`. For `enum`, add a `"values"` array.

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
| `type` | Yes | `string`, `integer`, `number`, `boolean`, or `enum`. |
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

Types: `string`, `integer`, `boolean`, `enum`.

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

- `match`: A regular expression. Use capture groups `()` to extract values. (The key `pattern` is accepted as an alias.)
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
| `body` | No | Request body (JSON string). Supports `{param}` substitution. Used with POST/PUT. |
| `query_params` | No | Query parameters as key-value pairs. Supports `{param}` substitution. |
| `params` | No | Parameter definitions (same as TCP/serial commands). |

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

---

## Method 3: Python Driver

Python drivers give you full control. Use this method when:

- The device uses a **binary protocol** (bytes, checksums, length headers).
- The device requires an **authentication handshake** on connect.
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

### Installing a Python Driver

Place your `.py` driver file in the `driver_repo/` directory. OpenAVC scans this directory at startup and dynamically loads any Python file that contains a `BaseDriver` subclass with a valid `DRIVER_INFO` dict.

You can also install Python drivers through the Programmer IDE:
- **Browse Community** tab: click Install on any Python driver
- **Import from File:** upload a `.py` file from your computer

After installation or restart, the driver appears in the "Add Device" dialog.

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
| `command_handlers` | How the simulator responds to commands |
| `error_modes` | Failure scenarios for testing error handling |

**Control types:** `power`, `slider`, `toggle`, `select`, `matrix`, `indicator`, `meters`, `presets`, `group`.

For the complete simulator guide with all control types, state machines, and Python simulators, see the Writing Simulators documentation in the driver repository.

---

## Troubleshooting

### State variables not updating

**Symptom:** Commands send successfully but state variables stay at their default values.

**Common causes:**

1. **Wrong delimiter.** If the delimiter doesn't match what the device sends, messages are never split correctly and response patterns never see a complete line. Check your device's protocol manual. Try `\r\n`, `\r`, and `\n`. Use the Live Test tab to see raw responses.

2. **Response pattern doesn't match.** Open the device log in the Programmer IDE and look at the `RX` lines. Copy the exact text and test your regex pattern against it. Common mistakes:
   - Forgetting to escape special regex characters (`.`, `(`, `)`, `+`, `*`)
   - Pattern expects `"Vol45"` but device sends `"Vol 45"` (extra space)
   - Pattern is case-sensitive but device sends mixed case

3. **Wrong capture group number.** `$1` refers to the first set of parentheses in the pattern. If your pattern is `Vol(\d+)\s+(\w+)` and you want the second group, use `$2`.

4. **Type coercion mismatch.** If the type is `integer` but the captured value is `"abc"`, it silently becomes `0`. Check that the captured text is actually numeric.

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
