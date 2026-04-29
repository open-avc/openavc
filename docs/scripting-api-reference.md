# Scripting API Reference

Quick reference for the `openavc` module available in all OpenAVC scripts.

> For tutorials and patterns, see the [Scripting Guide](scripting-guide.md). This page is a lookup reference for every function, object, and property.

## Import

```python
from openavc import (
    on_event, on_state_change,                        # Decorators
    devices, state, events, macros, log, isc,         # Proxy objects
    delay, after, every, cancel_timer, cancel_all_timers,  # Timer functions
    Event,                                            # Event class
)
```

You do not need to install anything. The `openavc` module is injected automatically by the Script Engine.

---

## Decorators

### @on_event(pattern)

Register an async function to run when an event matches `pattern`. Supports glob wildcards (`*`).

```python
@on_event("ui.press.btn_power")
async def handle(event):
    ...
```

**Handler signatures:**
- `async def handler(event)` receives an `Event` object (recommended)
- `async def handler(event_name, payload)` receives string + dict (legacy)

The engine detects the parameter count automatically.

### @on_state_change(pattern)

Register an async function to run when a state key matching `pattern` changes. Supports glob wildcards.

```python
@on_state_change("device.projector_main.power")
async def handle(key, old_value, new_value):
    ...
```

**Handler signature:** `async def handler(key: str, old_value, new_value)`

**Timing:** the handler runs *after* the change is applied — the new value is already in the store. Handlers cannot intercept or pre-empt a change. Sibling handlers run concurrently with no defined order. See the scripting guide for details.

---

## Event Object

| Property / Method | Returns | Description |
|-------------------|---------|-------------|
| `event.name` | `str` | Full event name (e.g., `"ui.press.btn1"`) |
| `event.payload` | `dict` | Copy of the payload dictionary |
| `event.get(key, default)` | `Any` | Safe access to a payload field |
| `event.<key>` | `Any` | Attribute access to payload fields (raises `AttributeError` if missing) |

---

## devices

Control connected AV equipment.

| Method | Returns | Description |
|--------|---------|-------------|
| `await devices.send(device_id, command, params=None)` | `Any` | Send a command to a device. `params` is a dict or `None`. |
| `devices.list()` | `list[dict]` | List all devices with connection status. |

```python
await devices.send("projector_main", "power_on")
await devices.send("projector_main", "set_input", {"input": "hdmi1"})
await devices.send("dsp1", "set_fader", {"channel": "program", "level": -12.0})
all_devices = devices.list()
```

**Note:** `params` must be a dict, not keyword arguments. Write `{"input": "hdmi1"}`, not `input="hdmi1"`.

---

## state

Read and write the reactive state store. All values are flat primitives (`str`, `int`, `float`, `bool`, `None`).

| Method | Returns | Description |
|--------|---------|-------------|
| `state.get(key, default=None)` | `Any` | Read a state value. |
| `state.set(key, value, source="script")` | `None` | Write a state value. Triggers change notifications. |
| `state.delete(key)` | `None` | Remove a key entirely (unlike `set(key, None)` which keeps the key). |
| `state.get_namespace(prefix)` | `dict` | Read all keys under a prefix (e.g., `"device.projector_main."`). |

### State Key Namespaces

| Prefix | Example | Who Writes |
|--------|---------|------------|
| `device.<id>.*` | `device.projector_main.power` | Drivers (read-only for scripts) |
| `var.*` | `var.room_active` | Scripts, macros, UI |
| `ui.<id>.*` | `ui.btn_power.label` | Scripts (UI element overrides) |
| `system.*` | `system.uptime` | System (read-only) |
| `plugin.<id>.*` | `plugin.mqtt.connected` | Plugins |
| `isc.<peer_id>.*` | `isc.lobby.device.proj1.power` | ISC (remote state) |

### UI Element Overrides

Scripts can directly control UI element appearance by setting `ui.*` keys:

| Key Pattern | Type | Description |
|-------------|------|-------------|
| `ui.<element_id>.label` | `str` | Override the element's display text |
| `ui.<element_id>.bg_color` | `str` | Override background color (hex) |
| `ui.<element_id>.text_color` | `str` | Override text color (hex) |
| `ui.<element_id>.visible` | `bool` | Show or hide the element |
| `ui.<element_id>.opacity` | `float` | Set opacity (0.0 to 1.0) |

Set a key to `None` to clear the override and revert to the feedback binding or default.

---

## events

Emit custom events on the event bus.

| Method | Returns | Description |
|--------|---------|-------------|
| `await events.emit(event_name, payload=None)` | `None` | Fire an event. `payload` is a dict or `None`. |

```python
await events.emit("custom.room_ready", {"zone": "auditorium"})
```

---

## macros

Execute named macros.

| Method | Returns | Description |
|--------|---------|-------------|
| `await macros.execute(macro_id)` | `None` | Run a macro by ID. The macro must exist in the project. |

---

## log

Write messages to the system log. Messages appear in the Programmer IDE Log View and script console.

| Method | Description |
|--------|-------------|
| `log.info(message)` | Informational message |
| `log.warning(message)` | Warning message |
| `log.error(message)` | Error message |
| `log.debug(message)` | Debug message (hidden unless debug level is enabled) |

---

## isc

Communicate with other OpenAVC instances on the network. ISC must be enabled in the project configuration.

| Method | Returns | Description |
|--------|---------|-------------|
| `await isc.send_to(instance_id, event, payload=None)` | `None` | Send an event to a specific remote instance. |
| `await isc.broadcast(event, payload=None)` | `None` | Send an event to all connected instances. |
| `await isc.send_command(instance_id, device_id, command, params=None)` | `Any` | Send a device command to a remote instance's equipment. |
| `isc.get_instances()` | `list[dict]` | List all discovered/connected peer instances. |

Remote state from peers is available in the state store under `isc.<peer_id>.<key>`.

---

## Timer Functions

### delay(seconds)

Async sleep. Must be awaited. Pauses the current handler without blocking the system.

```python
await delay(15)  # Wait 15 seconds
```

### after(seconds, callback, *args) -> str

Schedule a function to run once after a delay. Returns a timer ID for cancellation. Non-blocking. Extra positional arguments are passed to the callback.

```python
timer_id = after(15, my_function)
timer_id = after(5, set_input, "hdmi1")  # passes "hdmi1" to set_input()
```

### every(seconds, callback, *args) -> str

Schedule a function to run repeatedly at an interval. Returns a timer ID. Non-blocking. Extra positional arguments are passed to the callback.

```python
timer_id = every(60, check_status)
timer_id = every(30, poll_device, "projector_main")  # passes "projector_main" to poll_device()
```

### cancel_timer(timer_id) -> bool

Cancel a timer created by `after()` or `every()`. Returns `True` if cancelled, `False` if not found.

```python
cancelled = cancel_timer(timer_id)
```

### cancel_all_timers() -> int

Cancel all active timers at once. Returns the number of timers cancelled.

```python
count = cancel_all_timers()
```

---

## Event Types

Events fired by the system that scripts can listen for with `@on_event`.

| Pattern | Payload Fields | Description |
|---------|---------------|-------------|
| `ui.press.<element_id>` | `element_id` | Button pressed |
| `ui.release.<element_id>` | `element_id` | Button released |
| `ui.hold.<element_id>` | `element_id` | Button held past threshold |
| `ui.toggle_off.<element_id>` | `element_id` | Toggle button turned off |
| `ui.change.<element_id>` | `element_id`, `value` | Slider or select value changed |
| `ui.route.<element_id>` | `element_id`, `input`, `output` | Matrix route changed |
| `ui.submit.<element_id>` | `element_id`, `value` | Text input or keypad submitted |
| `ui.page.<page_id>` | (none) | Page navigation |
| `device.connected.<device_id>` | (none) | Device connected |
| `device.disconnected.<device_id>` | (none) | Device disconnected |
| `device.error.<device_id>` | `device_id`, `error` | Device communication error |
| `macro.started.<macro_id>` | `macro_id`, `name`, `total_steps` | Macro began executing |
| `macro.completed.<macro_id>` | `macro_id`, `name` | Macro finished executing |
| `macro.cancelled.<macro_id>` | `macro_id`, `name` | Macro was cancelled |
| `macro.error.<macro_id>` | `macro_id`, `name`, `error` | Macro failed |
| `system.started` | (none) | System startup complete |
| `system.stopping` | (none) | System shutting down |
| `system.project.reloaded` | (none) | Project reloaded |
| `isc.*.<event>` | `source_instance`, ... | Event from a remote instance |
| `custom.<anything>` | (user-defined) | User-defined events |

> **Note on schedules:** Scheduled actions are handled by triggers that directly execute macros, not by events. To run a script on a schedule, create a macro with an "Emit Event" step that fires a custom event, handle that event in your script with `@on_event`, and add a schedule trigger to the macro.

---

## See Also

- [Scripting Guide](scripting-guide.md). Tutorials, patterns, and complete examples.
- [Macros and Triggers](macros-and-triggers.md). When macros are enough and you don't need scripts.
- [Variables and State](variables-and-state.md). Managing state without code.
