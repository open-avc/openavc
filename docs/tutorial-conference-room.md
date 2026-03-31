# Tutorial: Build a Conference Room

This hands-on tutorial walks you through building a complete conference room control system using OpenAVC. You will add devices, create macros, design a touch panel, and set up automated shutdown, all without writing a single line of code.

If you have programmed rooms in Crestron SIMPL or Extron Global Configurator, the concepts will feel familiar. The workflow is different (browser-based, no compilation, no proprietary hardware), but the logic is the same: devices, commands, feedback, and automation.

**What you will learn:**

- Adding and testing devices
- Creating variables to track room state
- Building macros for system on/off and source selection
- Designing a multi-page touch panel with live feedback
- Scheduling automatic shutdown with guard conditions

**Estimated time:** 30-45 minutes.

## What You'll Build

The finished system controls a conference room with:

- A PJLink projector (connected to the built-in simulator)
- An Extron video switcher (configured but offline, no real hardware needed)
- A motorized projection screen (macro-controlled)

The touch panel has three pages:

- **Main**: Power on/off, source selection, projector status
- **Audio**: Volume fader and mute toggle
- **Advanced**: Reserved for future technical controls

System On and System Off macros handle the full startup and shutdown sequence automatically. A scheduled trigger shuts the room down at 10 PM on weekdays if someone forgets.

## Prerequisites

- OpenAVC installed and running. See [Getting Started](getting-started.md) if you need to set up.
- Start OpenAVC with `python -m server.main`.
- Open the Programmer IDE at http://localhost:8080/programmer.

No real AV hardware is required. The PJLink simulator responds to power and input commands just like a real projector.

## Step 1: Create a New Project

1. Click **Program** in the sidebar.
2. Click **New** in the Project Library at the bottom.
3. Enter the project name: `Conference Room 201`.
4. Click **Create**.

OpenAVC creates a `.avc` project file and a `scripts/` folder. The project file stores everything (devices, UI layout, macros, variables, triggers) in a single JSON file. Think of it as the equivalent of a Crestron .smw or Extron .gcf, but human-readable and version-controllable.

## Step 2: Add Devices

### Add the projector

1. Click **Devices** in the sidebar.
2. Click **Add Device**.
3. Select **PJLink Class 1 Projector** from the driver dropdown.
4. Set the Device ID to `projector_main`.
5. Set the Display Name to `Main Projector`.
6. Set the Host to `localhost` and Port to `4352` (the simulator address).
7. Click **Add**.

The device should connect immediately. You will see a green indicator next to it in the device list, meaning the simulator is responding.

### Add the video switcher

1. Click **Add Device** again.
2. Select **Extron SIS Switcher** from the driver dropdown.
3. Set the Device ID to `switcher_main`.
4. Set the Display Name to `Video Switcher`.
5. Leave the Host as the default. Since there is no real switcher on the network, this device will show a red indicator (disconnected). That is expected and fine for this tutorial.
6. Click **Add**.

### A note on Device IDs

Device IDs are permanent identifiers used in macros, scripts, and UI bindings. Pick a naming convention and stick with it. Common patterns:

- `projector_main`, `switcher_main`, `dsp_main` (by function)
- `projector_1`, `switcher_1` (by number)
- `cr201_projector`, `cr201_switcher` (by room and function)

The Display Name is what appears in the UI and can be changed anytime.

## Step 3: Test a Device Command

1. Click **projector_main** in the device list to open the detail panel.
2. In the command testing section, select **power_on** from the command dropdown.
3. Click **Send**.

Watch the live state section update. The `power` property should change from `off` to `warming`, then after a few seconds to `on`. This confirms the simulator is responding and the driver is parsing responses correctly.

Try sending **power_off** next. The state should cycle through `cooling` and back to `off`.

This is the equivalent of testing signals in the Crestron debugger. Always confirm your commands work at the device level before building macros on top of them.

## Step 4: Create Variables

Variables track room-level state that no single device reports. Click **State** in the sidebar to open the Variables view.

### Create room_active

1. Click **New Variable**.
2. Set the Name to `room_active`.
3. Set the Type to **boolean**.
4. Set the Default Value to `false`.
5. Click **Create**.

This variable tracks whether the room is in active use. Macros will set it, and triggers will check it.

### Create current_source

1. Click **New Variable**.
2. Set the Name to `current_source`.
3. Set the Type to **string**.
4. Set the Default Value to empty (leave blank).
5. Click **Create**.

This tracks which input source is currently selected (laptop, blu-ray, etc.). Source buttons will use it for feedback highlighting.

### Create projector_status

1. Click **New Variable**.
2. Set the Name to `projector_status`.
3. Set the Type to **string**.
4. Set the Default Value to `Off`.
5. In the Source section, select **Bound to state key**.
6. Choose `device.projector_main.power` as the source key.
7. Add a value map:
   - `on` -> `Ready`
   - `off` -> `Off`
   - `warming` -> `Warming Up`
   - `cooling` -> `Cooling Down`
8. Click **Create**.

This variable automatically mirrors the projector's power state but translates the raw values into friendly text. When the projector reports `warming`, the variable reads `Warming Up`. No code, no polling. The binding handles it reactively. This is similar to how you would use an analog-to-serial join in Crestron, but without the signal routing.

## Step 5: Build the System On Macro

Click **Macros** in the sidebar, then click **New Macro**. Name it `system_on`.

Add the following steps using the **+** button:

| Step | Type | Details |
|------|------|---------|
| 1 | Set Variable | `var.room_active` = `true` |
| 2 | Device Command | `projector_main` -> `power_on` |
| 3 | Delay | 20 seconds |
| 4 | Device Command | `projector_main` -> `set_input`, input: `hdmi1` |
| 5 | Set Variable | `var.current_source` = `laptop` |

The 20-second delay gives the projector time to warm up before switching inputs. Most projectors ignore input commands during warmup, so this delay is critical. In Crestron, you would handle this with a timer or wait symbol. In OpenAVC, it is just a delay step.

Click **Test** to run the macro. Watch the device state panel. The projector should power on, warm up, and switch to HDMI 1. The `var.room_active` indicator in the State view should show `true`.

## Step 6: Build the System Off Macro

Click **New Macro** and name it `system_off`.

Add these steps:

| Step | Type | Details |
|------|------|---------|
| 1 | Device Command | `projector_main` -> `power_off` |
| 2 | Set Variable | `var.room_active` = `false` |
| 3 | Set Variable | `var.current_source` = `""` (empty string) |

Click **Test** to verify. The projector should power off and the variables should reset.

## Step 7: Build Source Select Macros

Create two more macros for source switching.

### select_laptop

Click **New Macro**, name it `select_laptop`, and add:

| Step | Type | Details |
|------|------|---------|
| 1 | Device Command | `projector_main` -> `set_input`, input: `hdmi1` |
| 2 | Set Variable | `var.current_source` = `laptop` |

### select_bluray

Click **New Macro**, name it `select_bluray`, and add:

| Step | Type | Details |
|------|------|---------|
| 1 | Device Command | `projector_main` -> `set_input`, input: `hdmi2` |
| 2 | Set Variable | `var.current_source` = `bluray` |

In a real system you would also send routing commands to the video switcher here (e.g., `switcher_main` -> `set_input`, input: `1`). Since our switcher is offline, we will skip that, but the structure would be identical. Just add more Device Command steps.

## Step 8: Design the Main Page

Click **UI Builder** in the sidebar. You will see an empty canvas with a grid overlay. The left panel shows the Element Palette, and the right panel shows Properties for whatever is selected.

### Add System On button

1. Drag a **Button** from the Element Palette onto the canvas.
2. In the Properties panel, set the Label to `System On`.
3. Under **Press Binding**, select **Run Macro** and choose `system_on`.
4. Under **Style**, set the background color to a green shade (e.g., `#4CAF50`).

### Add System Off button

1. Drag another **Button** onto the canvas, below or beside the System On button.
2. Set the Label to `System Off`.
3. Under **Press Binding**, select **Run Macro** and choose `system_off`.
4. Set the background color to a red shade (e.g., `#F44336`).

### Add source select buttons

1. Drag a **Button** onto the canvas. Set the Label to `Laptop`.
2. Under **Press Binding**, select **Run Macro** and choose `select_laptop`.
3. Under **Feedback Binding**, set the Source to `var.current_source`.
4. Set the Condition so the button is active when the value equals `laptop`.
5. Set the Active appearance: background color to your theme's accent color (e.g., `#2196F3`).
6. Set the Inactive appearance: background color to a dimmer tone (e.g., `#424242`).

Repeat for the Blu-Ray button:

1. Drag another **Button**. Set the Label to `Blu-Ray`.
2. Press Binding: **Run Macro** -> `select_bluray`.
3. Feedback Binding: Source = `var.current_source`, active when equals `bluray`.
4. Same active/inactive colors as the Laptop button.

Now when you press Laptop, the Laptop button highlights and Blu-Ray dims. Press Blu-Ray and the highlighting swaps. This is the equivalent of feedback joins in Crestron. The button state is driven by the variable, not by the press itself.

### Add a status label

1. Drag a **Label** onto the canvas.
2. Under **Text Binding**, select **State Variable** and choose `var.projector_status`.

This label will automatically show "Off", "Warming Up", "Ready", or "Cooling Down" based on the projector's live state, using the value map you defined in Step 4.

### Add a status LED

1. Drag a **Status LED** onto the canvas near the status label.
2. Under **Color Binding**, set the Source to `device.projector_main.power`.
3. Add a color map:
   - `on` -> green (`#4CAF50`)
   - `warming` -> amber (`#FF9800`)
   - `cooling` -> amber (`#FF9800`)
   - `off` -> gray (`#9E9E9E`)

The LED gives an instant visual indicator of projector state without reading any text.

## Step 9: Add an Audio Page

### Create the page

Click the **+** tab at the top of the canvas to add a new page. Name it `Audio`.

### Add a volume fader

1. Drag a **Fader** from the Element Palette onto the Audio page.
2. Under **Change Binding**, you would normally bind this to a DSP command (e.g., `dsp_main` -> `set_level` with `$value` as the level parameter). Since we do not have a DSP device in this tutorial, you can skip the binding or set it to update a variable for demonstration.

### Add a mute toggle button

1. Drag a **Button** onto the Audio page.
2. Set the Label to `Mute`.
3. Set the **Mode** to **Toggle**.
4. In a real system, you would set the toggle state key to `device.dsp_main.mute`, the On Action to a mute command, and the Off Action to an unmute command. For this tutorial, just set the label so you can see the layout.

### Add navigation from Main to Audio

Switch back to the Main page by clicking its tab. Then:

1. Drag a **Page Nav** element from the Element Palette onto the Main page.
2. Set the Label to `Audio`.
3. Set the Target Page to `Audio`.

Now users can tap "Audio" on the Main page to navigate to the Audio page. To get back, add another Page Nav on the Audio page with the Target Page set to `Main`.

## Step 10: Add a Trigger for Auto-Shutdown

Go back to the `system_off` macro by clicking **Macros** in the sidebar and selecting it.

1. Click the **Triggers** tab in the macro editor.
2. Click **Add Trigger**.
3. Set the Type to **Schedule**.
4. Use the visual cron builder to select:
   - **Time:** 10:00 PM
   - **Days:** Monday through Friday
   - This generates the cron expression `0 22 * * 1-5`.
5. Under **Guard Conditions**, click **Add Condition**.
6. Set the Key to `var.room_active`, operator to **equals**, value to `true`.

The guard condition means the shutdown macro only fires if the room is actually in use. If someone already shut the room down manually, the trigger skips. This prevents unnecessary power-off commands being sent to equipment that is already off.

In Crestron, you would need a SIMPL program with a scheduler symbol, an AND gate checking the room state, and a trigger to fire the shutdown. In OpenAVC, it is a trigger with a guard condition. No code, no signal routing.

## Step 11: Test in Preview Mode

Click the **Preview** toggle at the top of the UI Builder canvas. The grid overlay disappears and the panel becomes interactive with live device state.

1. Press **System On**. Watch the status LED turn amber and the status label show "Warming Up". After the delay, the LED turns green and the label shows "Ready".
2. Press **Laptop**. The Laptop button highlights. Press **Blu-Ray**. The highlight swaps.
3. Navigate to the Audio page using the Page Nav button. Navigate back.
4. Press **System Off**. The LED turns amber briefly (cooling), then gray. The status label shows "Off". Source buttons dim.

You can also open the Panel UI at http://localhost:8080/panel in a separate browser tab to see exactly what an end user would see on a wall-mounted touchscreen.

Toggle Preview off to return to the design view and make any adjustments.

## What's Next

You have built a working conference room control system with devices, macros, variables, UI bindings, feedback, and scheduled automation, all without writing a single script.

Here are your next steps:

- **[Scripting Guide](scripting-guide.md)**: When macros are not enough (conditional logic, loops, error handling, external APIs), Python scripts give you full control. Most rooms do not need scripts, but complex spaces benefit from them.
- **[Creating Drivers](creating-drivers.md)**: If your AV equipment does not have a driver yet, build one using the visual Driver Builder, a YAML definition file, or a Python class.
- **[Devices and Drivers](devices-and-drivers.md)**: Browse and install community drivers, run network discovery scans to find devices automatically.
- **[Macros and Triggers](macros-and-triggers.md)**: Deeper reference on trigger types, debounce, cooldown, overlap control, and converting macros to scripts.
- **[UI Builder](ui-builder.md)**: Full reference for all 18 element types, themes, overlays, master elements, and animations.
