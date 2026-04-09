# Scripting Guide

Python scripting API for OpenAVC automation.

> Scripts provide advanced logic beyond what macros can express. If you are new to OpenAVC, start with [Macros and Triggers](macros-and-triggers.md). Macros handle most use cases without code.

## Overview

OpenAVC scripts are Python files that react to events and state changes. They run inside the OpenAVC process with full access to devices, state, and the event bus.

Scripts live in `projects/<name>/scripts/` and are registered in the project's `.avc` file. Use the Monaco editor in the Programmer IDE to write and test scripts.

## The openavc Module

Every script imports from the `openavc` module, which is injected by the Script Engine at runtime:

```python
from openavc import (
    on_event, on_state_change,                        # Decorators
    devices, state, events, macros, log, isc,         # Proxy objects
    delay, after, every, cancel_timer, cancel_all_timers,  # Timer functions
)
```

You do not need to install anything. The `openavc` module is provided automatically when the Script Engine loads your script.

## Decorators

### @on_event(pattern)

Register a function to run when an event fires. Two handler signatures are supported:

**Single-parameter (recommended):** Receives an `Event` object with attribute access to payload fields.

```python
@on_event("ui.press.btn_system_on")
async def handle_system_on(event):
    log.info(f"Event: {event.name}")        # "ui.press.btn_system_on"
    log.info(f"Element: {event.element_id}") # "btn_system_on"
    await devices.send("projector_main", "power_on")
```

**Two-parameter (legacy):** Receives `(event_name_str, payload_dict)`. Still fully supported for backward compatibility.

```python
@on_event("ui.press.btn_system_on")
async def handle_system_on(event, payload):
    await devices.send("projector_main", "power_on")
```

The engine detects the handler's parameter count automatically. No configuration needed.

Supports glob wildcards:

```python
@on_event("ui.press.*")
async def handle_any_press(event):
    log.info(f"Button pressed: {event.name}")
```

#### Event Object

The `Event` object (available via `from openavc import Event`) wraps the event name and payload:

| Property | Description |
|----------|-------------|
| `event.name` | Full event name string (e.g., `"ui.press.btn1"`) |
| `event.payload` | Copy of the payload dict |
| `event.get(key, default)` | Safe access to payload fields |
| `event.<key>` | Attribute access to payload fields (raises `AttributeError` if missing) |

### @on_state_change(pattern)

Register a function to run when a state key changes.

```python
@on_state_change("device.projector_main.power")
async def projector_power_changed(key, old_value, new_value):
    if new_value == "warming":
        state.set("var.projector_status_text", "Warming up...")
    elif new_value == "on":
        state.set("var.projector_status_text", "Ready")
    elif new_value == "off":
        state.set("var.projector_status_text", "Off")
```

Supports glob wildcards:

```python
@on_state_change("device.*.power")
async def any_device_power_changed(key, old_value, new_value):
    log.info(f"{key}: {old_value} -> {new_value}")
```

## Proxy Objects

### devices (Device Control)

```python
await devices.send(device_id, command, params=None)
devices.list()
```

The `params` argument is a dictionary. Pass `None` (or omit it) for commands that take no parameters.

```python
# No parameters
await devices.send("projector_main", "power_on")

# With parameters (pass a dict)
await devices.send("projector_main", "set_input", {"input": "hdmi1"})
await devices.send("switcher_main", "route", {"input": 3, "output": 1})
await devices.send("dsp1", "set_fader", {"channel": "program", "level": -12.0})

# List all devices
all_devices = devices.list()
```

### state (State Store)

```python
state.get(key, default=None)        # Read a value
state.set(key, value, source="script")  # Write a value
state.delete(key)                    # Remove a key entirely (unlike set(key, None) which keeps the key)
state.get_namespace(prefix)          # Read all keys under a prefix
```

```python
power = state.get("device.projector_main.power")
is_active = state.get("var.room_active", False)

state.set("var.room_active", True)
state.set("var.current_source_name", "Laptop")

# Get all device keys for projector_main
proj_state = state.get_namespace("device.projector_main.")

# Clean up a temporary state key
state.delete("var.temp_countdown")
```

All state values are flat primitives: `str`, `int`, `float`, `bool`, or `None`. No nested objects.

State key namespaces: `device.<id>.*` (device state), `var.*` (user variables), `ui.*` (UI state), `system.*` (system info), `plugin.<id>.*` (plugin state), `isc.*` (remote instances). Scripts can read any key and write to `var.*`.

#### Controlling UI Elements from Scripts

You can directly change UI element appearance by setting `ui.*` state keys. These override feedback bindings and take effect immediately on the panel.

```python
# Change a button's label and color
state.set("ui.btn_power.label", "WARMING...")
state.set("ui.btn_power.bg_color", "#FFC107")
state.set("ui.btn_power.text_color", "#000000")

# Hide or dim an element
state.set("ui.btn_advanced.visible", False)
state.set("ui.btn_locked.opacity", 0.3)

# Clear an override (reverts to feedback binding or default)
state.set("ui.btn_power.label", None)
```

Available override keys for any element: `label`, `bg_color`, `text_color`, `opacity`, `visible`. The element ID is the same ID shown in the UI Builder properties panel.

### events (Event Bus)

```python
await events.emit(event_name, payload=None)
```

```python
await events.emit("custom.room_ready", {"room": "auditorium"})
```

### macros (Macro Engine)

```python
await macros.execute(macro_id)
```

Runs a macro by its ID. The macro must exist in the project definition.

### log (Logger)

```python
log.info(message)
log.warning(message)
log.error(message)
log.debug(message)
```

Log messages appear in the Programmer IDE's Log View and in the script console.

### isc (Inter-System Communication)

```python
await isc.send_to(instance_id, event, payload=None)
await isc.broadcast(event, payload=None)
await isc.send_command(instance_id, device_id, command, params=None)
isc.get_instances()
```

Communicate with other OpenAVC instances on the network. ISC must be enabled in the project's `isc` configuration.

```python
from openavc import isc, on_event, log

# Send an event to a specific instance
await isc.send_to("lobby-instance-id", "custom.all_off", {"zone": "building"})

# Broadcast to all connected instances
await isc.broadcast("custom.fire_alarm", {"zone": "all"})

# Send a device command to a remote instance's equipment
result = await isc.send_command("lobby-instance-id", "display1", "power_off")

# List all discovered peers
peers = isc.get_instances()
for p in peers:
    log.info(f"Peer: {p['name']} connected={p['connected']}")

# React to events from remote instances
@on_event("isc.*.custom.panic_button")
async def handle_remote_panic(event):
    log.warning(f"Panic from {event.source_instance}")
    await devices.send("display_main", "show_alert")
```

Remote state from peers is available in the state store under `isc.<peer_id>.<key>`:

```python
# Read state from a remote instance
remote_power = state.get("isc.lobby-id.device.projector1.power")
```

## Timer Functions

### delay(seconds)

Async sleep. Pauses the current handler. Must be awaited.

```python
await devices.send("projector_main", "power_on")
await delay(15)  # Wait for warmup
await devices.send("projector_main", "set_input", {"input": "hdmi1"})
```

### after(seconds, callback)

Non-blocking: schedules a function to run once after a delay. Returns a timer ID.

```python
async def set_input():
    await devices.send("projector_main", "set_input", {"input": "hdmi1"})

timer_id = after(15, set_input)
```

### every(seconds, callback)

Recurring timer. Returns a timer ID.

```python
async def check_status():
    power = state.get("device.projector_main.power")
    log.info(f"Projector power: {power}")

timer_id = every(60, check_status)
```

### cancel_timer(timer_id)

Cancel a timer created by `after()` or `every()`. Returns `True` if cancelled, `False` if the timer was not found.

```python
timer_id = every(60, check_status)
# Later...
cancel_timer(timer_id)
```

### cancel_all_timers()

Cancel all active timers at once. Returns the number of timers cancelled. Useful for cleanup when stopping a script or switching modes.

```python
count = cancel_all_timers()
log.info(f"Cancelled {count} timers")
```

## State Key Conventions

| Pattern | Example | Description |
|---------|---------|-------------|
| `device.<id>.<property>` | `device.projector_main.power` | Device state |
| `var.<name>` | `var.room_active` | User-defined variable |
| `ui.<id>.<property>` | `ui.vol_slider.value` | UI element state |
| `system.<property>` | `system.uptime` | System state |

## Event Types

| Pattern | Description |
|---------|-------------|
| `ui.press.<element_id>` | Button pressed |
| `ui.release.<element_id>` | Button released |
| `ui.hold.<element_id>` | Button held past threshold |
| `ui.change.<element_id>` | Slider or select value changed (payload includes `value`) |
| `ui.submit.<element_id>` | Text input or keypad submitted (payload includes `value`) |
| `ui.page.<page_id>` | Page navigation |
| `device.connected.<device_id>` | Device connected |
| `device.disconnected.<device_id>` | Device disconnected |
| `device.error.<device_id>` | Device communication error (payload includes `error`) |
| `macro.completed.<macro_id>` | Macro finished executing |
| `system.started` | System startup complete |
| `system.stopping` | System shutting down |
| `system.project.reloaded` | Project reloaded (after save, import, or cloud push) |
| `isc.*.<event>` | Event from a remote OpenAVC instance |
| `custom.<anything>` | User-defined events |

> **Note on schedules:** Scheduled actions are handled via triggers, not events. A schedule trigger directly executes its macro when the cron expression matches. To run a script on a schedule, create a macro that calls a script function and attach a schedule trigger to it.

## Complete Examples

### Room On / Off

```python
from openavc import on_event, devices, state, log, delay

@on_event("ui.press.btn_system_on")
async def system_on(event):
    log.info("System ON triggered")
    state.set("var.room_active", True)

    await devices.send("projector_main", "power_on")
    await devices.send("screen_relay", "close", {"channel": 1})
    await devices.send("display_lobby", "power_on")

    await delay(15)
    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    await devices.send("switcher_main", "route", {"input": 3, "output": 1})
    await devices.send("dsp1", "set_fader", {"channel": "room_mic", "level": -12.0})

@on_event("ui.press.btn_system_off")
async def system_off(event):
    await devices.send("screen_relay", "open", {"channel": 1})
    await delay(5)
    await devices.send("projector_main", "power_off")
    await devices.send("dsp1", "mute", {"channel": "room_mic", "muted": True})
    await devices.send("display_lobby", "power_off")
    state.set("var.room_active", False)
```

### Volume Mapping

```python
from openavc import on_event, devices

@on_event("ui.change.vol_slider")
async def volume_changed(event):
    # UI slider: 0-100, DSP expects: -100.0 to 0.0 dB
    # event.value contains the slider value
    db = (event.value / 100.0) * 100.0 - 100.0
    await devices.send("dsp1", "set_fader", {"channel": "program", "level": db})
```

### State-Reactive Logic

> **Tip:** For simple device-to-variable mirroring like the example below, you can now use **variable source binding** instead of a script. In the State tab, edit a variable and set its Source to "Bound to state key" with a value map. Scripts are still the right choice for complex transformations, conditional logic, or when you need to update multiple variables from one state change.

```python
from openavc import on_state_change, state

@on_state_change("device.projector_main.power")
async def projector_state_changed(key, old_value, new_value):
    status_map = {
        "warming": "Warming up...",
        "on": "Ready",
        "cooling": "Cooling down...",
        "off": "Off"
    }
    state.set("var.projector_status_text", status_map.get(new_value, "Unknown"))
```

### Recurring Status Check with Timer

```python
from openavc import on_event, state, log, every, cancel_all_timers

@on_event("system.started")
async def start_polling(event):
    async def poll_occupancy():
        occupied = state.get("device.sensor1.occupied", False)
        if not occupied and state.get("var.room_active"):
            log.warning("Room active but unoccupied -- consider auto-shutdown")

    every(300, poll_occupancy)

@on_event("system.stopping")
async def stop_polling(event):
    cancel_all_timers()
```

## Tips

- **All handler functions must be `async`**. Use `await` for device commands and delays.
- **Script errors**: if a handler throws an unhandled exception, the error is logged and a `script.error` event is broadcast to all WebSocket clients with `script_id`, `handler`, `error`, and `traceback` fields. The system continues running. One broken handler does not take down the server.
- **Error handling**: wrap device commands in `try`/`except` if the device might be offline.
- **Hot reload**: click Run in the Script Editor to reload a script without restarting the server.
- **No sandbox**: scripts run in the server process with full Python access. This is intentional. The programmer IS the system administrator (same trust model as Crestron SIMPL# or Q-SYS Lua).
- **Do not block the event loop**: use `await delay()` instead of `time.sleep()`. A blocking call freezes the entire system.
- **Params are dicts**: `devices.send()` takes an optional dictionary as its third argument, not keyword arguments. Write `devices.send("proj", "set_input", {"input": "hdmi1"})`, not `devices.send("proj", "set_input", input="hdmi1")`.

## See Also

- [Scripting API Reference](scripting-api-reference.md). Quick lookup for every function, object, and property.
- [Macros and Triggers](macros-and-triggers.md). Automation without code.
- [Programmer Overview](programmer-overview.md). IDE walkthrough.
- [Scheduling Guide](scheduling-guide.md). Cron schedules, trigger-schedules, and timers.
- [Creating Drivers](creating-drivers.md). Driver development guide.
