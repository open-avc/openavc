# Tutorial: Your First Space

In about twenty minutes you take a starter project, see it run against a simulated projector, and add three things to it: a status readout in plain words, source buttons that light up to show which input is live, and a shutdown that runs itself every weeknight. No scripts, and no AV hardware.

**What you will learn:**

- Opening a starter project and running it in the Simulator
- Testing a command and reading a device's live state
- A variable that mirrors a device value and translates it
- Wiring a control: what it does when touched, and what it shows
- A scheduled trigger with a condition
- Trying the panel as an operator, and saving your work

## What You'll Build

The Simple Projector starter has one PJLink projector, a System On macro and a System Off macro, and a Main page with the two buttons and a status LED. You add:

- A label that reads Warming up, Ready, Cooling down or Off
- Laptop and Wireless buttons that switch the projector's input and light up to match it
- A trigger that runs System Off at 10 PM on weekdays, but only if the projector is still on

## Prerequisites

- OpenAVC installed and the Programmer open in your browser. See [Getting Started](getting-started.md) if you have not done that yet, including the first-run sign-in.

## Step 1: Open the Starter Project

1. Click **Program** in the sidebar.
2. Under **Project Library**, click **Simple Projector**.
3. The Open Project dialog says the running project will be replaced and backed up. Click **Open**.

The project name at the top of the Program view changes to Simple Projector. The starter's driver installs with it, so the projector is ready to use.

## Step 2: Start the Simulator

The projector in this project points at an address that does not exist on your network. The Simulator stands in for it.

1. Click **Simulate Devices** (the play button at the bottom of the sidebar).
2. Click **Start Simulation**.

The Simulator opens in a new browser tab. It shows a card for the projector with its own power control, an Input row, lamp hours and a log of every message it exchanges with OpenAVC. This is the equipment side of the conversation, and you can change things here as if you were standing at the projector.

Back in the Programmer, click **Devices**. The projector now shows as Online.

## Step 3: Test a Command

1. Click **Projector** in the device list.
2. Click **Power On** at the top of the device page.

Watch **Live State** below it: `power` changes to `warming`, then a few seconds later to `on`. In the Simulator tab, the projector card now reads ON.

Every device page works this way. The row of buttons at the top runs the driver's common commands with one click, **Send Command** runs any command with the parameters it needs, and **Device Log** shows the exact bytes sent and received. Confirm a device answers here before you build anything on top of it.

## Step 4: A Status Readout in Plain Words

The projector reports `warming`, `on`, `cooling` and `off`. An operator should read Warming up or Ready. A variable can mirror the device value and translate it, with no script.

1. Click **State** in the sidebar, then **New Variable**.
2. Set the ID to `projector_status`, leave the Type as String, set the Label to `Projector Status` and the Default to `Off`. Click **Create**.
3. The new variable opens on the right. Under **Source**, choose **Bound to state key**.
4. Click **Select state key to bind...** and pick `device.projector1.power` under Device: Projector.
5. Under **Value Map**, click **Add mapping** four times and fill in the rows:

| Source value | Variable value |
|---|---|
| `on` | `Ready` |
| `off` | `Off` |
| `warming` | `Warming up` |
| `cooling` | `Cooling down` |

The Current Value at the top of the panel reads Ready while the projector is on. Change the projector's power from the Simulator tab and it follows.

## Step 5: Show It on the Panel

1. Click **UI Builder** in the sidebar. The Main page shows the title, the two system buttons and the status LED.
2. In the **Elements** list on the left, click **Label**. A new label lands on the canvas. Drag it to a clear spot below the buttons.
3. With the label selected, scroll the Properties panel on the right to **Bindings**. Every control has two buckets there: **Shows** (what it reflects from live state) and **Does** (what happens when it is touched). A label only shows.
4. Under **Shows > Text**, choose **State Variable**, click **Select variable...** and pick **Projector Status**.

The label now reads Ready. The UI Builder saves as you go; the toolbar says Saved when it has.

## Step 6: Source Buttons That Light Up

A source button needs both buckets: it sends the input command, and it lights up when the projector reports that input.

### The Laptop button

1. In **Elements**, click **Button**. Under **Basic**, set **Text** to `Laptop`.
2. Under **Bindings > Does**, click **Press Action**. Set the Action Type to **Device Command**, the Device to **Projector** and the Command to **Set Input**. For the `input` parameter, type `hdmi1`.
3. Click **Test** under the parameters. The projector switches to HDMI 1; the Simulator's log shows the command and the reply.
4. Under **Bindings > Shows > Appearance**, set the Source to **Projector** and the State Key to **input**. Each key in that list shows its current value in brackets, and `input` reads `digital1`: that is how a PJLink projector names HDMI 1.
5. For **Active when value equals**, pick `digital1`. The list offers the values the projector has reported, which is why you tested the command first.
6. Under **When active**, set **Background** to a colour, for example `#4a7d5c`. Leave the inactive look as it is.

### The Wireless button

Add a second Button the same way with the Text `Wireless`, the `input` parameter `hdmi2`, and after clicking **Test**, the condition `digital2`. Give it the same active colour.

Drag the two buttons side by side. The one whose input is live is lit. Click **HDMI 1** on the projector card in the Simulator tab and watch the highlight move: the button's look follows the projector, not the press.

## Step 7: Shut Down on a Schedule

1. Click **Macros** in the sidebar, then **System Off**.
2. Under **Triggers**, click **Add Trigger** and choose **Schedule**.
3. Set the Preset to **Weekdays at...** and the Time to `22` : `00`. The trigger's summary reads Weekdays at 22:00.
4. Under **Conditions**, click **Add Condition**. Set the Key to `device.projector1.power` (under Device: Projector), leave the operator as **Equals**, and set the Value to `on`.

The editor evaluates the condition as you type: with the projector on it reads "ALL TRUE, trigger would fire". At 10 PM on a weeknight, System Off runs only if the projector is still on. A projector someone already shut down is left alone.

## Step 8: Try It as an Operator

1. Back in **UI Builder**, click **Preview** in the toolbar. The canvas becomes the live panel and the button reads Stop.
2. Press **System Off**. The label reads Cooling down, then Off, the LED changes, and both source buttons go dark.
3. Press **System On**, wait for Ready, then press **Wireless**.
4. Click **Stop** to go back to editing.

To see exactly what an operator sees, open **http://localhost:8080/panel** in another browser tab. On a tablet or phone, use the address the Dashboard's **Panel Access** card shows and approve the device once when it asks; the packaged installs accept network connections out of the box, and [Getting Started](getting-started.md#7-open-the-panel-ui) covers opening up an install from source.

## Step 9: Keep It

- Click **Program**, then **Save As**. Give the project a new ID such as `first_space` and a name of your own, so it is saved beside the starter rather than replacing it.
- **Export** downloads the project as a `.zip` bundle with everything it needs, which is how you move a project to another machine or hand it over.

## What's Next

You have used the four things every OpenAVC space is made of: devices and their commands, state and variables, macros with triggers, and a panel whose controls do and show. The rest is depth.

- **[OpenAVC Academy](academy.md)**: the Associate course builds a complete presentation and meeting space from a blank project, one decision at a time, and ends in a certification. Foundations is the shorter introduction.
- **[Tutorial: Build a Classroom with Scripts](tutorial-classroom.md)**: when a macro cannot express the logic, a short Python script can.
- **[Devices and Drivers](devices-and-drivers.md)**: installing drivers from the community library, adding real equipment, and finding devices on the network.
- **[UI Builder](ui-builder.md)**: every element type, themes, pages and navigation, master elements.
- **[Macros and Triggers](macros-and-triggers.md)**: conditional steps, waiting for a device state, and every trigger type.
