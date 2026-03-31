# Tutorial: Build a Classroom with Scripts

This tutorial walks you through building a classroom control system using Python scripts. You will control a projector, display, and DSP, and use scripting to add logic that macros cannot handle.

**Estimated time:** 30-45 minutes.

If you completed the conference room tutorial, you already know how to create projects, add devices, and build macros. This tutorial picks up where that one left off, introducing scripts for situations where macros are not enough.

## What You'll Build

A classroom with three devices:

- **Projector** (PJLink), the main display for the instructor
- **Display** (Samsung MDC), a confidence monitor or secondary screen
- **DSP** (Biamp Tesira), audio processing for room microphones and speakers

The system needs logic that macros cannot express:

- **Volume mapping**: convert a UI slider (0-100) to the DSP's decibel range (-100 to 0 dB)
- **State-reactive UI**: show different status text as the projector warms up, runs, and cools down
- **Error handling**: show a meaningful message when a device is offline
- **Timed monitoring**: check room occupancy every 5 minutes and log a warning if the system is running in an empty room

## Prerequisites

- Completed the conference room tutorial (or equivalent experience with the Programmer IDE)
- OpenAVC running (`python -m server.main`)
- A browser open to the Programmer IDE at `http://localhost:8080/programmer`

## When Scripts Beat Macros

| Use a Macro When | Use a Script When |
|-----------------|-------------------|
| Simple sequence of commands | Need if/else conditional logic |
| Fixed delays between steps | Need to check state mid-sequence |
| No error handling needed | Need try/except error handling |
| Quick one-off actions | Need loops or complex timing |
| Non-programmers will maintain it | Need to call external APIs or do math |

If your automation needs any of the items in the right column, reach for a script.

## Step 1: Set Up the Project

Create a new project and add the three devices. You have done this before, so here is the quick version:

1. Click **Projects** in the sidebar, then **New Project**
2. Name it `classroom_101`
3. Click **Devices** in the sidebar, then **Add Device**
4. Add a PJLink projector. Set the ID to `projector_main`, enter the IP address, and use the PJLink driver.
5. Add a second device for the display. Set the ID to `display_confidence`, use the Samsung MDC driver.
6. Add a third device for the DSP. Set the ID to `dsp1`, use the Biamp Tesira driver.

If you do not have real hardware, the devices will show as disconnected. That is fine. Scripts still run and you can test the logic using variables and the state panel.

## Step 2: Your First Script

Now create a script file:

1. Click **Scripts** in the sidebar
2. Click **New Script**
3. Name it `room_control`
4. The Monaco code editor opens with a blank file

Type the following code:

```python
from openavc import on_event, devices, state, log

@on_event("ui.press.btn_system_on")
async def system_on(event):
    log.info("System ON triggered")
    state.set("var.room_active", True)
    await devices.send("projector_main", "power_on")
```

Before anything else, here is what each part means:

- `from openavc import ...` loads the tools you need. You do not install anything. OpenAVC provides these automatically.
- `@on_event("ui.press.btn_system_on")` tells OpenAVC to run this function when a button called `btn_system_on` is pressed on the panel.
- `async def system_on(event):` defines the function. The `async` keyword is required on every handler. Just include it and OpenAVC handles the rest.
- `log.info(...)` prints a message to the console in the Script Editor.
- `state.set(...)` saves a value that the UI and other scripts can read.
- `await devices.send(...)` sends a command to a device. The `await` keyword means "wait for this to finish before continuing."

Click **Run** at the top of the editor. This hot-reloads the script without restarting the server. You should see "Script loaded" in the console panel.

## Step 3: Add a Delay and Sequence

Projectors need time to warm up before they accept input commands. Expand the handler to wait 15 seconds, then switch to HDMI 1:

```python
from openavc import on_event, devices, state, log, delay

@on_event("ui.press.btn_system_on")
async def system_on(event):
    log.info("System ON triggered")
    state.set("var.room_active", True)
    await devices.send("projector_main", "power_on")
    await devices.send("display_confidence", "power_on")
    await delay(15)
    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    log.info("System ON complete")
```

Key details:

- `await delay(15)` pauses this handler for 15 seconds. Other handlers keep running normally during the wait.
- Never use `time.sleep()`. It freezes the entire system. Always use `await delay()`.
- Device command parameters are passed as a dictionary: `{"input": "hdmi1"}`. Do not use keyword arguments.

## Step 4: Volume Mapping

This is where scripts earn their keep. A UI slider sends values from 0 to 100, but the DSP expects decibels from -100.0 to 0.0. That math conversion is impossible in a macro.

First, you will create the slider in Step 8. For now, write the handler:

```python
@on_event("ui.change.vol_slider")
async def volume_changed(event):
    # UI slider: 0-100
    # DSP expects: -100.0 to 0.0 dB
    db = (event.value / 100.0) * 100.0 - 100.0
    await devices.send("dsp1", "set_fader", {"channel": "program", "level": db})
    log.info(f"Volume: {event.value}% = {db:.1f} dB")
```

When the slider is at 0, the math produces -100.0 dB (silence). At 100, it produces 0.0 dB (full). At 50, it produces -50.0 dB. The `f"..."` syntax is a Python formatted string that inserts variable values into text.

## Step 5: State-Reactive Logic

PJLink projectors report their power state as they cycle through warming, on, cooling, and off. You can react to those changes and update a status label on the panel automatically.

Add this to your script:

```python
from openavc import on_state_change

@on_state_change("device.projector_main.power")
async def projector_state_changed(key, old_value, new_value):
    status_map = {
        "warming": "Warming up...",
        "on": "Ready",
        "cooling": "Cooling down...",
        "off": "Off"
    }
    state.set("var.projector_status_text", status_map.get(new_value, "Unknown"))
    log.info(f"Projector: {old_value} -> {new_value}")
```

The `@on_state_change` decorator fires whenever the specified state key changes. The handler receives three arguments: the key that changed, its previous value, and its new value.

`status_map` is a Python dictionary that maps device values to human-readable text. The `.get()` method returns "Unknown" if the projector reports a value not in the map.

> **Tip:** For this simple case, you could also use a variable with source binding instead of a script. In the State tab, edit a variable and set its Source to "Bound to state key" with a value map. Scripts are the right choice when you need more complex transformations or when one state change should update multiple things.

## Step 6: Error Handling

When a device is offline or unreachable, `devices.send()` raises an exception. Without error handling, the rest of your handler stops running. Wrap device commands in `try`/`except` to keep things working:

```python
@on_event("ui.press.btn_system_on")
async def system_on(event):
    state.set("var.room_active", True)

    try:
        await devices.send("projector_main", "power_on")
    except Exception as e:
        log.error(f"Failed to turn on projector: {e}")
        state.set("var.projector_status_text", "Error - check connection")
        return

    await devices.send("display_confidence", "power_on")
    await delay(15)
    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    log.info("System ON complete")
```

The `try` block attempts the command. If it fails, Python jumps to the `except` block, where you log the error and update the status label. The `return` statement exits the handler early so it does not continue sending commands to a projector that is not responding.

Even without `try`/`except`, a script error will never crash the server. OpenAVC logs the error and keeps running. But error handling lets you show useful feedback to the person operating the panel.

## Step 7: Timers

Use `every()` to run a function on a repeating schedule. This example checks every 5 minutes whether the room is still occupied:

```python
from openavc import on_event, every, cancel_timer, state, log

_poll_timer = None

@on_event("system.started")
async def start_monitoring(event):
    global _poll_timer

    async def check_room():
        if not state.get("var.room_active"):
            return
        occupied = state.get("device.sensor1.occupied", False)
        if not occupied:
            log.warning("Room active but unoccupied -- consider auto-shutdown")

    _poll_timer = every(300, check_room)

@on_event("system.stopping")
async def stop_monitoring(event):
    global _poll_timer
    if _poll_timer:
        cancel_timer(_poll_timer)
```

How this works:

- `every(300, check_room)` calls `check_room` every 300 seconds (5 minutes) and returns a timer ID.
- `global _poll_timer` lets both handlers access the same variable. Without it, each function would have its own separate `_poll_timer`.
- `cancel_timer(_poll_timer)` stops the recurring timer when the system shuts down.
- The `system.started` event fires once when OpenAVC finishes starting up. The `system.stopping` event fires when it shuts down.

If you do not have an occupancy sensor, you can still test this pattern by checking other state values, like whether the projector has been on for more than 4 hours.

## Step 8: Build the UI

Now create the panel page that connects to your script handlers.

1. Click **UI Builder** in the sidebar
2. Click **Add Page** and name it `Main`

Add these elements by dragging from the Element Palette:

**System On button:**
- Drag a **Button** onto the canvas
- Set ID to `btn_system_on`
- Set Label to "System On"
- This button triggers the `system_on` handler you wrote in Step 2

**System Off button:**
- Drag another **Button** onto the canvas
- Set ID to `btn_system_off`
- Set Label to "System Off"

**Volume slider:**
- Drag a **Slider** onto the canvas
- Set ID to `vol_slider`
- Set Min to 0, Max to 100
- This slider triggers the `volume_changed` handler from Step 4

**Status label:**
- Drag a **Label** onto the canvas
- Set ID to `lbl_projector_status`
- In the Properties panel, under Bindings, set the label text to bind to `var.projector_status_text`
- This label updates automatically when the `projector_state_changed` handler runs

Switch to **Preview Mode** (toggle at the top of the canvas) to test. Press the System On button and watch the console for log messages. Move the volume slider and confirm the dB conversion appears in the console.

## Step 9: Test Everything

1. Click **Run** in the Script Editor to hot-reload your script
2. Open the Panel UI in another tab: `http://localhost:8080/panel`
3. Press **System On** and watch the Script Console for log output
4. Move the volume slider and verify the dB calculation in the logs
5. Check the status label updates as the projector state changes
6. Check the State panel in the Programmer IDE to see `var.room_active`, `var.projector_status_text`, and other values

If something is not working, check the Script Console for error messages. Script errors include the line number and a description of what went wrong.

## The Complete Script

Here is the full `room_control.py` with all the pieces together:

```python
from openavc import (
    on_event, on_state_change,
    devices, state, log,
    delay, every, cancel_timer
)

_poll_timer = None

# --- System Power ---

@on_event("ui.press.btn_system_on")
async def system_on(event):
    state.set("var.room_active", True)
    try:
        await devices.send("projector_main", "power_on")
    except Exception as e:
        log.error(f"Failed to turn on projector: {e}")
        state.set("var.projector_status_text", "Error - check connection")
        return
    await devices.send("display_confidence", "power_on")
    await delay(15)
    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    log.info("System ON complete")

@on_event("ui.press.btn_system_off")
async def system_off(event):
    await devices.send("projector_main", "power_off")
    await devices.send("display_confidence", "power_off")
    await devices.send("dsp1", "mute", {"channel": "program", "muted": True})
    state.set("var.room_active", False)
    log.info("System OFF complete")

# --- Volume ---

@on_event("ui.change.vol_slider")
async def volume_changed(event):
    db = (event.value / 100.0) * 100.0 - 100.0
    await devices.send("dsp1", "set_fader", {"channel": "program", "level": db})

# --- Projector Status ---

@on_state_change("device.projector_main.power")
async def projector_state_changed(key, old_value, new_value):
    status_map = {
        "warming": "Warming up...",
        "on": "Ready",
        "cooling": "Cooling down...",
        "off": "Off"
    }
    state.set("var.projector_status_text", status_map.get(new_value, "Unknown"))

# --- Occupancy Monitoring ---

@on_event("system.started")
async def start_monitoring(event):
    global _poll_timer
    async def check_room():
        if not state.get("var.room_active"):
            return
        occupied = state.get("device.sensor1.occupied", False)
        if not occupied:
            log.warning("Room active but unoccupied")
    _poll_timer = every(300, check_room)

@on_event("system.stopping")
async def stop_monitoring(event):
    global _poll_timer
    if _poll_timer:
        cancel_timer(_poll_timer)
```

## Tips

- **All handlers must be `async`**. Add the keyword to every handler function.
- **Use `await delay()`, never `time.sleep()`**. `time.sleep()` freezes the entire system.
- **Parameters are dicts**. Write `{"input": "hdmi1"}`, not `input="hdmi1"`.
- **Script errors are safe**. A broken handler logs an error but does not crash the server.
- **Click Run to hot-reload**. No need to restart the server when editing scripts.
- **Use the console**. `log.info()`, `log.warning()`, and `log.error()` all appear in the Script Console.

## What's Next

- [Scripting Guide](scripting-guide.md). Full API reference for all functions, decorators, and patterns.
- [Creating Drivers](creating-drivers.md). Build custom drivers for devices not in the community library.
- [Plugins](plugins.md). Install and configure system plugins.
