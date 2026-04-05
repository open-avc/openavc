# Macros and Triggers

Automate your AV space with macros (command sequences) and triggers (automatic conditions).

## Macros

Macros are named sequences of actions and the easiest way to automate without writing code. If you have used Crestron macros or Extron presets, this works the same way but with more flexibility.

Use the search box at the top of the macro list to filter by name.

## Creating a Macro

1. Click **Macros** in the sidebar
2. Click **New Macro**
3. Give it a descriptive name (e.g., `system_on`, `select_laptop`, `shutdown_all`)
4. Add steps using the **+** button:

| Step Type | Description | Example |
|-----------|-------------|---------|
| **Device Command** | Send a command to a device | `projector_main` -> `power_on` |
| **Group Command** | Send a command to all devices in a group at once | `projectors` -> `power_on` |
| **Delay** | Wait N seconds between steps | Wait 15 seconds for projector warmup |
| **Set Variable** | Set a user variable | `var.room_active` = `true` |
| **Emit Event** | Fire a custom event on the event bus | `room.shutdown_complete` |
| **Run Macro** | Execute another macro as a sub-routine | Run `select_hdmi1` |
| **Conditional** | If/else branching based on state | If projector is already on, skip power-on |

The **Device Command** step uses smart dropdowns: after selecting a device, the command dropdown only shows commands defined by that device's driver, with parameter fields that match the driver's command definition. No guessing at command syntax.

The **Group Command** step works the same way but targets a device group instead of a single device. All devices in the group receive the command concurrently. Only commands shared by every device in the group are shown. Offline devices are skipped automatically. Create and manage device groups from the **Groups** tab in the Devices view.

Reorder steps by dragging the grip handle on the left side of each step. Toggle **Stop on Error** in the macro header to halt execution if any step fails (by default, macros continue through errors).

**Step Templates:** Click the **Templates** dropdown in the step toolbar to insert pre-built multi-step patterns: Volume Ramp, Source Switch, and Power Sequence. Templates add several coordinated steps at once and are a fast way to build common sequences.

**Copy and Paste Steps:** Right-click a step (or use the step menu) to copy it. Paste into the same macro or a different one. This works across macros, so you can reuse steps without rebuilding them.

## A Typical System-On Macro

Here is a real-world example of a `system_on` macro for a conference room:

| Step | Type | Details |
|------|------|---------|
| 1 | Set Variable | `var.room_active` = `true` |
| 2 | Device Command | `screen_1` -> `lower` |
| 3 | Device Command | `projector_main` -> `power_on` |
| 4 | Delay | 20 seconds (projector warmup) |
| 5 | Device Command | `projector_main` -> `set_input`, input: `hdmi1` |
| 6 | Device Command | `switcher_1` -> `set_route`, input: 1, output: 1 |
| 7 | Device Command | `dsp_1` -> `set_level`, channel: `program`, level: -20 |
| 8 | Device Command | `dsp_1` -> `unmute`, channel: `program` |

## Conditional Steps

Use the **Conditional** step type to add if/else logic to a macro without writing a script.

A conditional step checks a state value and runs one set of steps if the condition is true, and optionally a different set if false. For example: "If the projector is already on, skip the power-on and warmup delay."

1. Add a **Conditional** step
2. Set the **If** condition: pick a state key, operator (equals, not equals, greater than, less than, truthy, falsy), and value
3. Add steps to the **Then** block (runs when condition is true)
4. Optionally add steps to the **Else** block (runs when condition is false)

Conditionals can be nested (a conditional inside a conditional) up to 5 levels deep. For most rooms, one level is enough.

## Skip If Guards

Every step has an optional **Skip this step if...** guard in the Guards section at the bottom of the step editor. When enabled, the step is silently skipped if the condition is true.

This is simpler than a full conditional block when you just want to skip one step. For example: skip the "power on" command if `device.projector.power` already equals `"on"`.

Device Command steps also have a **Skip if device is offline** checkbox. When checked, the step is silently skipped instead of failing if the device is disconnected. This is useful for macros that control optional equipment that may not always be present.

## Dynamic Parameters

Macro step parameters are normally static values set at design time. Dynamic parameters let a step use whatever value a variable holds at runtime.

Click the **$** button next to any parameter field to switch between static and dynamic mode. In dynamic mode, you pick a state key (like `var.target_volume`), and the macro reads the current value when it runs.

For example, a volume slider on the touch panel writes to `var.volume_level`. A macro step can use `$var.volume_level` as the level parameter for a DSP set_volume command. The same macro works for any volume, determined by the slider position.

Dynamic references also work on the **Set Variable** step's value field. This lets you copy one variable to another at runtime.

## Cancel Groups

When two macros should not run at the same time (like System On and System Off), assign them to the same **cancel group**. When a macro with a cancel group starts, any other running macro in the same group is cancelled first.

Set the cancel group in the macro header, next to the macro ID. Give both macros the same group name (e.g., `system_power`). Now if someone presses System Off while System On is still running, the system-on macro stops immediately and system-off takes over.

You can also cancel a running macro manually by clicking the **Cancel** button in the macro header while it's running.

## Progress in Panel

When a macro is running, the panel provides two forms of feedback:

**Button busy state (automatic).** If a button triggers a macro and that macro is currently running, the button automatically shows a pulsing animation and blocks re-presses. This prevents users from accidentally triggering the same macro twice. No configuration needed.

**Progress label (opt-in).** You can bind a label's text to "Macro Progress" in the Text Binding editor. Select the macro to track and set the idle text (what shows when the macro is not running). While the macro runs, the label shows the current step's description. Add descriptions to each macro step in the step editor to control what users see (e.g., "Powering on projector", "Waiting for warmup"). If no description is set, the system generates one from the action type.

## Variables in Macros

Click the variable icon in any step to create or select a user variable. The Variable Picker shows all available variables with their current values. Variables let macros share state. For example, the `system_on` macro sets `var.room_active` to `true`, and UI buttons use that variable for feedback.

## Macro Dependencies

The macro detail panel shows a dependency tree listing which macros call this macro and which macros this macro calls (via "Run Macro" steps). If you create a circular dependency (macro A calls B, B calls A), the IDE warns you at save time.

## Testing Macros

Click **Test** to execute the macro immediately. A progress indicator shows which step is running, with live status updates. Conditional steps show whether the condition evaluated to true or false, and group command steps show per-device success/fail icons. A **Last Run** summary shows the timestamp, duration, step results, and any errors from the most recent execution.

## Convert to Script

Click **Convert to Script** to generate a Python script from the macro. A preview shows the generated code in a diff-like view before committing. This is useful when you need loops, error handling with retries, external API calls, or complex data processing that macros cannot express. The generated script is fully functional and includes all the same steps with proper `await` calls.

## Triggers

Triggers automatically execute macros based on conditions. Click the **Triggers** tab in the macro editor to add triggers to any macro.

| Trigger Type | Fires When | Example |
|-------------|------------|---------|
| **Schedule** | Cron expression matches | `0 22 * * 1-5` (10 PM weekdays) |
| **State Change** | A state key matches a condition | `device.projector.power` equals `"on"` |
| **Event** | An event fires on the bus | `ui.press.btn_panic` |
| **Startup** | System starts | Run initialization macro |

The schedule trigger includes a visual cron builder with two modes: a **field-by-field editor** (separate inputs for minute, hour, day, month, weekday with labels) and a raw expression editor. A dropdown of common examples ("Every weekday at 8am", "First Monday of month", "Every 15 minutes during business hours") lets you start from a template.

**Fire Now:** Click the **Fire Now** button on any trigger to manually execute it immediately, without waiting for the condition to be met. Useful for testing.

**Condition Preview:** State change triggers show a live preview: "If this condition were evaluated now, it would be: TRUE/FALSE" with the current state values displayed.

**Event Autocomplete:** The event pattern field in event triggers suggests events from your configured devices, macros, and scripts as you type.

Trigger safety features prevent runaway automation:

- **Debounce.** Prevent rapid re-firing (configurable milliseconds).
- **Delay + re-check.** Wait, then verify the condition is still true before executing.
- **Cooldown.** Minimum interval between executions.
- **Guard conditions.** Additional state conditions that must all be true (supports operator aliases like `"equals"`, `"=="`, `">="` in addition to the standard `"eq"`, `"ne"`, `"gt"`, etc.).
- **Overlap prevention.** Will not start a new execution if the previous one is still running.
- **Stop on error.** Set `stop_on_error: true` on a macro to halt execution if any step fails (default is to continue).

Example: A "projector auto-off" trigger watches `device.projector_main.power` for `"on"`, with a guard condition that `var.room_active` equals `false`. This shuts down a projector that someone turned on manually without using the panel, but only if the room is not in active use.

## Scripts

For logic that macros cannot express, write Python scripts using the built-in Monaco code editor. Scripts are stored in `projects/<name>/scripts/` as standard `.py` files.

Use the search box at the top of the script list to filter by file name.

## The Script Editor

- File tree on the left showing project scripts (files with load errors show a red icon)
- Monaco editor with Python syntax highlighting and autocomplete
- Autocomplete for the OpenAVC API with return types, parameter types, and one-line examples
- **Save** to write changes, **Run** to hot-reload without restarting (**Ctrl+Shift+R** for keyboard shortcut)
- Console panel showing script output and full error tracebacks with clickable line numbers

## Quick Example

```python
from openavc import on_event, devices, state, log, delay

@on_event("ui.press.btn_system_on")
async def system_on(event, payload):
    log.info("System ON triggered")
    state.set("var.room_active", True)

    await devices.send("projector_main", "power_on")
    await devices.send("screen_1", "lower")

    # Wait for projector warmup
    await delay(20)

    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    await devices.send("switcher_1", "set_route", {"input": 1, "output": 1})
    await devices.send("dsp_1", "set_level", {"channel": "program", "level": -20})

    log.info("System ON complete")
```

## When to Use Scripts Instead of Macros

| Use a Macro When | Use a Script When |
|-----------------|-------------------|
| Sequences of commands with delays | Need loops (repeat N times, while condition) |
| Simple if/else branching (conditional steps) | Need complex multi-condition logic trees |
| Skipping steps based on state | Need try/except error handling with retries |
| Quick one-off actions | Need to call external APIs or do math |
| Non-programmers will maintain it | Need string manipulation or data parsing |

Most rooms can be built entirely with macros, conditionals, and bindings. Scripts are there when you need them.

## Script Templates

Click **Templates** to insert boilerplate for common patterns:
- Button handler (event listener with device command)
- State change handler (react to a device state change)
- Device control (send commands with error handling)
- Periodic timer (recurring status checks)
- System on/off (full room startup/shutdown sequence)
- Scheduled task (cron-driven operations)
- Device monitor (watch device state and react)
- Custom event handler (listen for custom events)
- Variable watcher (react to variable changes)

See the [Scripting Guide](scripting-guide.md) for the complete API reference including all available functions, decorators, and patterns.

## See Also

- [Scripting Guide](scripting-guide.md). Complete Python scripting API
- [Scheduling Guide](scheduling-guide.md). Cron schedules, trigger-schedules, timers
- [Variables and State](variables-and-state.md). User variables, device states, and activity monitoring
