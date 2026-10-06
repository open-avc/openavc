# Tutorial: Build a Classroom with Scripts

This tutorial builds a classroom control system with a projector, a confidence display and a DSP, and uses a Python script for the logic a macro cannot express: a decision that reads two devices at once, a volume ceiling that depends on the state of the room, and a startup that copes with a projector that does not answer.

**Estimated time:** 45 minutes. Everything runs against the Simulator, so no equipment is needed.

If you built [your first space](tutorial-first-space.md), you already know how to add devices, create variables and wire a control. This tutorial picks up there and introduces scripts.

## What You'll Build

Three devices:

- **Projector** (PJLink), the main display for the instructor
- **Confidence display** (Samsung MDC), the small screen facing the instructor
- **DSP** (Biamp Tesira), program audio for the room

And one script, `room_control`, that provides:

- **Startup that follows the projector.** The input is switched the moment the projector reports it is on, and a projector that does not answer produces a clear message instead of a stuck sequence.
- **A status line from two devices.** "Ready" only when both the projector and the confidence display are on.
- **A volume ceiling.** When the presenter's microphone is live, program audio is capped.
- **A check that repeats** every five minutes.

## When a Script Earns Its Place

Macros do more than they used to, so be honest about which tool you need:

| A macro or a control setting handles it | A script earns its place |
|---|---|
| A sequence of commands with delays | A decision that combines several state values |
| Waiting for a device to report a state (**Wait Until**) | Arithmetic beyond scaling a range |
| One if/else on a state value (**Conditional**, **Skip if**) | `try`/`except` with a fallback the operator can see |
| Stopping when a command fails (**Stop on Error**) | Something that repeats on a timer |
| Scaling or tapering a slider's value (its **Output Min/Max** and **Response**) | Calling a service outside OpenAVC |

If a macro can express it, use the macro: the next programmer can read it without opening a code editor.

## Prerequisites

- OpenAVC running and the Programmer open in your browser.
- [Your first space](tutorial-first-space.md) completed, or equivalent time in the Programmer.

## Step 1: Set Up the Project

### Create the project and install the drivers

1. Click **Program** in the sidebar, then **New**. Name the project `Classroom 101` and click **Create**.
2. Click **Devices**, then the **Drivers** tab, then **Browse Community**. Search for and **Install** each of these: **PJLink Class 1 Projector**, **Samsung MDC Display** and **Biamp Tesira TTP**.

### Add the three devices

Back on the **Devices** tab, click **Add Device** three times. Pick the driver by searching, set the ID and name, and give each an IP address (any address will do; the Simulator takes over in a moment). Leave the port at its default.

| Driver | Device ID | Display Name | IP Address |
|---|---|---|---|
| PJLink Class 1 Projector | `projector_main` | `Projector` | `192.168.1.100` |
| Samsung MDC Display | `display_confidence` | `Confidence Display` | `192.168.1.101` |
| Biamp Tesira TTP | `dsp1` | `Room DSP` | `192.168.1.102` |

### Tell the DSP which blocks it has

A Tesira exposes whatever the designer built in its configuration, so the driver needs to know the blocks you want to control. Click **Room DSP** in the device list and find **DSP Block List** on its page. Make sure it has these two rows, adding them with **Add block** if the list is empty, then click **Save**:

| Instance Tag | Block Type | Channels |
|---|---|---|
| `PgmLvl` | Level / Fader | `1` |
| `PgmMute` | Mute | `1` |

On a real Tesira the tags come from the design file (right-click a block in Tesira software and choose Properties). The Simulator's DSP already has these two.

### Start the Simulator

Click **Simulate Devices** (the play button at the bottom of the sidebar), then **Start Simulation**. All three devices show Online on the Devices tab, and the Simulator tab shows a card for each.

## Step 2: Declare the Variables

A script can write any `var.` key, but the Builder's pickers only list variables the project declares. Click **State**, then **New Variable**, and create four:

| ID | Type | Label |
|---|---|---|
| `room_active` | Boolean | `Room Active` |
| `projector_status_text` | String | `Projector Status` |
| `mic_live` | Boolean | `Mic Live` |
| `program_level` | Number | `Program Level` |

## Step 3: Your First Script

1. Click **Code** in the sidebar.
2. Click **+** next to Scripts, enter the Script ID `room_control` and click **Create**.

The editor opens with a two-line start. Replace it with:

```python
from openavc import devices, state, log


async def system_on():
    state.set("var.room_active", True)
    await devices.send("projector_main", "power_on")
    await devices.send("display_confidence", "power_on", {"display": 1})
    log.info("System on")
```

What each part means:

- `from openavc import ...` loads the tools you need. Nothing to install.
- `async def system_on():` is an ordinary function. A control on the panel can call it directly, which is how you will run it in the next step.
- `await devices.send(...)` sends a command to a device and waits for it to finish. The `async` and `await` keywords go together: use them on anything that talks to equipment.
- Command parameters go in a dictionary, `{"display": 1}`. The Samsung driver addresses each display on its chain by its Set ID, so its power commands need one.
- `log.info(...)` writes to the Console at the bottom of the Code view.

Click **Save & Reload Script**. The Console reports `Script 'room_control' reloaded, 0 handler(s) registered`. Zero is right: a handler is a function that reacts to an event, and this one waits to be called.

## Step 4: Wire a Button to It

1. Click **UI Builder**. In the **Elements** list, click **Button** and set its **Text** to `System On`.
2. Under **Bindings > Does**, click **Press Action**. Set the Action Type to **Script Function** and the Function to **system_on**.
3. Click **Preview**, press the button, and watch the Simulator tab: the projector and the display both turn on. Click **Stop**.

Every control with a **Does** bucket can call a function this way. The Builder reads the function's parameters from the script itself, so the names always match.

## Step 5: Follow the Projector, and Cope When It Does Not Answer

Two things are missing from the startup. It never switches the projector's input, and if the projector is unplugged the operator learns nothing.

Switching the input needs a wait: a projector ignores input commands while it warms up, and a real one takes 30 to 60 seconds. A fixed delay is a guess, and a function called from a control is stopped after 30 seconds anyway. The better shape is to react to the projector's own report. Add a handler that runs whenever the projector's power state changes, and extend `system_on` with error handling:

```python
from openavc import devices, state, log, on_state_change


async def system_on():
    state.set("var.room_active", True)
    try:
        await devices.send("projector_main", "power_on")
    except Exception as e:
        log.error(f"Projector did not respond: {e}")
        state.set("var.projector_status_text", "Projector not responding")
        return
    await devices.send("display_confidence", "power_on", {"display": 1})
    log.info("System on")


@on_state_change("device.projector_main.power")
async def projector_changed(key, old_value, new_value):
    display_on = state.get("device.display_confidence.display.001.power") == "on"
    if new_value == "on":
        text = "Ready" if display_on else "Ready, confidence display off"
        if state.get("var.room_active"):
            await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    elif new_value == "warming":
        text = "Warming up"
    elif new_value == "cooling":
        text = "Cooling down"
    else:
        text = "Off"
    state.set("var.projector_status_text", text)
```

How it works:

- `try` attempts the command. If the projector is unreachable, `devices.send` raises, Python jumps to `except`, and the function logs the error, writes a message the panel can show, and `return`s rather than carrying on with a projector that is not there.
- `@on_state_change("device.projector_main.power")` makes `projector_changed` a handler: OpenAVC calls it with the key, the previous value and the new value every time that key changes. The projector reports `warming`, then `on`; the input command goes out the moment `on` arrives, however long the warm-up took.
- The status text reads two devices. A variable with a value map can translate one key; only a script can say "Ready" when both are on. The display is a child entity of the Samsung device, so its power key carries the display's Set ID: check the exact key under **State > Device States** rather than typing it from memory.

Click **Save & Reload Script**. The Console now reports one handler.

## Step 6: Show the Status

1. In **UI Builder**, add a **Label**. Under **Bindings > Shows > Text**, choose **State Variable** and pick **Projector Status**.
2. Click **Preview**, press **System On**, and watch the label: Warming up, then Ready. Click **Stop**.

## Step 7: Volume With a Ceiling

A slider can scale and taper its own value: its **Output Min** and **Output Max** map the range, and **Response** offers a logarithmic curve for audio. What a slider cannot do is decide. Here the ceiling depends on whether the microphone is live. Add to the script:

```python
MIC_LIVE_CEILING_DB = -20.0


async def set_volume(level):
    db = -60 + 0.6 * float(level)
    if state.get("var.mic_live") and db > MIC_LIVE_CEILING_DB:
        db = MIC_LIVE_CEILING_DB
    await devices.send("dsp1", "set_control", {"block": "PgmLvl", "control": "level_1", "value": db})
    state.set("var.program_level", db)
    log.info(f"Volume {level}% -> {db} dB")
```

`set_volume` takes one argument, `level`. The Tesira driver's `set_control` command addresses a declared block by its tag and the control on it, so the program level is `PgmLvl` / `level_1`. Click **Save & Reload Script**.

Now wire a slider to it:

1. In **UI Builder**, add a **Slider**. Under **Bindings > Does > On change**, set the Action Type to **Script Function** and the Function to **set_volume**.
2. For the `level` parameter, click **$** and choose **value** under This control. The slider now hands the function its own position.
3. Click **Preview** and drag the slider. The Console logs each level, and the Simulator's log shows the DSP receiving it as `PgmLvl set level 1 -24.0`. Click **Stop**.

To see the ceiling, click **State**, select **Mic Live**, and under **Current Value** choose **Yes**. Drag the slider to the top: the level stops at -20 dB.

## Step 8: System Off

Add the shutdown and wire a second button to it, the same way as System On:

```python
async def system_off():
    await devices.send("projector_main", "power_off")
    await devices.send("display_confidence", "power_off", {"display": 1})
    await devices.send("dsp1", "set_control", {"block": "PgmMute", "control": "mute_1", "value": True})
    state.set("var.room_active", False)
    log.info("System off")
```

## Step 9: A Check That Repeats

`every()` runs a function on a schedule. This one logs a line every five minutes while the room is running; in a real space it might warn about a projector left on with nobody in the room.

```python
from openavc import every


def check_room():
    if state.get("var.room_active") and state.get("device.projector_main.power") == "on":
        log.info("Room check: the system is running")


every(300, check_room)
```

The `every(...)` call sits at the top level of the script, so it is armed each time the script loads and cleared each time it reloads. Code at the top level runs once, when the script loads, and must never loop: work that repeats belongs in `every()`.

Click **Save & Reload Script**.

## Step 10: Try to Break It

1. Click the play button at the bottom of the sidebar, which now reads Stop Simulation. The projector goes offline.
2. In **UI Builder**, click **Preview** and press **System On**. The status label reads Projector not responding, and the Console shows the error you logged. Nothing else stops working.
3. Start the simulation again and press **System On**: the sequence recovers on its own.

A handler that raises an error you did not catch is also safe. OpenAVC logs it, the script's row in the Code view says how many times it has failed, and the server keeps running.

## The Complete Script

```python
from openavc import devices, state, log, on_state_change, every

MIC_LIVE_CEILING_DB = -20.0


# --- System power ---

async def system_on():
    state.set("var.room_active", True)
    try:
        await devices.send("projector_main", "power_on")
    except Exception as e:
        log.error(f"Projector did not respond: {e}")
        state.set("var.projector_status_text", "Projector not responding")
        return
    await devices.send("display_confidence", "power_on", {"display": 1})
    log.info("System on")


async def system_off():
    await devices.send("projector_main", "power_off")
    await devices.send("display_confidence", "power_off", {"display": 1})
    await devices.send("dsp1", "set_control", {"block": "PgmMute", "control": "mute_1", "value": True})
    state.set("var.room_active", False)
    log.info("System off")


# --- Volume ---

async def set_volume(level):
    db = -60 + 0.6 * float(level)
    if state.get("var.mic_live") and db > MIC_LIVE_CEILING_DB:
        db = MIC_LIVE_CEILING_DB
    await devices.send("dsp1", "set_control", {"block": "PgmLvl", "control": "level_1", "value": db})
    state.set("var.program_level", db)
    log.info(f"Volume {level}% -> {db} dB")


# --- Projector status and input ---

@on_state_change("device.projector_main.power")
async def projector_changed(key, old_value, new_value):
    display_on = state.get("device.display_confidence.display.001.power") == "on"
    if new_value == "on":
        text = "Ready" if display_on else "Ready, confidence display off"
        if state.get("var.room_active"):
            await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    elif new_value == "warming":
        text = "Warming up"
    elif new_value == "cooling":
        text = "Cooling down"
    else:
        text = "Off"
    state.set("var.projector_status_text", text)


# --- Periodic check ---

def check_room():
    if state.get("var.room_active") and state.get("device.projector_main.power") == "on":
        log.info("Room check: the system is running")


every(300, check_room)
```

## Tips

- **`async def` for anything that talks to a device or waits**, with `await` on the command or the delay. A plain `def` runs inline and must stay quick.
- **Use `await delay()`, never `time.sleep()`**. A blocking sleep freezes the whole system.
- **Parameters are dictionaries.** Write `{"input": "hdmi1"}`, not `input="hdmi1"`.
- **A function called from a control has 30 seconds.** Anything that waits longer belongs in a handler that reacts to the state you are waiting for.
- **Click Save & Reload Script** after every change. The server keeps running.
- **Read the Console.** `log.info()`, `log.warning()` and `log.error()` all appear there, with the line number when something raises.

## What's Next

- [Scripting Guide](scripting-guide.md). Every decorator, the `devices`, `state` and `events` objects, timers, and the patterns behind this tutorial.
- [Scripting API Reference](scripting-api-reference.md). A lookup for every function and property.
- [Macros and Triggers](macros-and-triggers.md). Conditional steps, Wait Until and Stop on Error, for the logic that does not need a script.
- [OpenAVC Academy](academy.md). Scripts are not yet a course; Associate covers everything a space needs without them.
