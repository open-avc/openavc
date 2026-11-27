# Macros and Triggers

Automate your AV space with macros (command sequences) and triggers (automatic conditions).

## Macros

Macros are named sequences of actions and the easiest way to automate without writing code. If you have used Crestron macros or Extron presets, this works the same way but with more flexibility.

Use the search box at the top of the macro list to filter by name.

## Creating a Macro

1. Click **Macros** in the sidebar
2. Click **+** at the top of the macro list
3. Give it a descriptive name (e.g., `system_on`, `select_laptop`, `shutdown_all`)
4. Add steps with **Add Step**:

| Step Type | Description | Example |
|-----------|-------------|---------|
| **Device Command** | Send a command to a device | `projector_main` -> `power_on` |
| **Group Command** | Send a command to all devices in a group at once | `projectors` -> `power_on` |
| **Delay** | Wait N seconds between steps | Wait 15 seconds for projector warmup |
| **Wait Until** | Pause until a state value matches a condition | Wait until the projector reports it is warm |
| **Set Variable or Control** | Set a user variable, or change a control on the panel | `var.room_active` = Yes, or `ui.btn_power.label` = `Turn Off` |
| **Emit Event** | Fire a custom event on the event bus | `room.shutdown_complete` |
| **Run Macro** | Execute another macro as a sub-routine | Run `select_hdmi1` |
| **Conditional** | If/else branching based on state | If projector is already on, skip power-on |
| **Navigate Panel** | Send every panel to a specific page or overlay | Return all panels to `home` at end of meeting |
| **Ask for Help** | Raise a help request to whoever supports this space | A Help button, or a trigger after three failed power-ons |
| **Clear Help Request** | Put the help state back to idle | The last step of `system_off` |

The **Device Command** step uses smart dropdowns: after selecting a device, the command dropdown only shows commands defined by that device's driver, with parameter fields that match the driver's command definition. No guessing at command syntax.

**A stream key, password or PIN in a step is saved on this system.** When a command's parameter is one its driver marks as a secret, its box works like a device password: type the value and click **Save**, and from then on it shows **Saved on this system** with **Change**. The value is kept on this system, not in the project, so a backup, an export, a template or the cloud's copy of the project does not contain it, and no touch panel receives it. The step sends it as usual. A copied or duplicated step keeps it, and changing it in one copy leaves the other as it was. On a system that does not have it, such as one the project was copied to without its logins, the box says "Not saved on this system. Enter it here.", the step is marked like an unfinished one, and it sends nothing until it is entered. A value the step reads from a variable with **$** is not saved this way.

The **Group Command** step works the same way but targets a device group instead of a single device. All devices in the group receive the command concurrently. Only commands shared by every device in the group are shown, and its parameters use the same smart inputs as a single-device command: a Yes/No selector for boolean parameters, a dropdown for enumerated values, and the **$** toggle for dynamic values. You pick from the driver's choices instead of typing raw values. Offline devices are skipped automatically, except one whose connection is still open and one whose command works while the device is offline, like a Power On sent by Wake-on-LAN (see **Skip if device is offline** below). Create and manage device groups from the **Groups** tab in the Devices view.

The **Emit Event** step can attach optional payload fields that travel with the event. Each field has a name and a typed value (text, number, or a Yes / No boolean), and a value starting with `$` is resolved when the step runs, so `$var.current_source` travels as that variable's value rather than as the text. Scripts read a field with `event.get("<field>")` inside an `@on_event` handler, and a macro triggered by the event can read each field with `$trigger.<field>`. The same step is available directly on a button or other control, in the UI Builder's **Does** bucket.

**Name your own events under `custom.`**, such as `custom.room_occupied`. Names starting with `system.` are OpenAVC's own (`system.started`, `system.stopping` and `system.project.reloaded`): a trigger can listen for them, but an Emit Event step that names one is refused, and the macro editor marks it with the `custom.` name to use. A project from an earlier version that sent its own `system.` events, such as the editor's former Room occupied, Room vacant and System panic suggestions, has them renamed to `custom.` when it opens, along with the Event triggers listening for them, and the Programmer lists each change. Anything outside the project listening for an old name, such as a Node-RED flow, needs the new one.

The **Set Variable or Control** step stores values with the variable's declared type: a string variable keeps text like `0` or `true` exactly as typed. When the target is a device state key instead of a variable, a type selector chooses whether the value is stored as text, a number, or a Yes / No boolean, and the device's next report replaces it. A `system.` or `isc.` key belongs to OpenAVC and cannot be set: the macro list marks the step, and when it runs the macro reports it and carries on with the next step. It also reaches the panel directly: see [Changing a Control from a Macro](#changing-a-control-from-a-macro) below.

A step that will not run as built is marked while you edit it, both inline and on the macro's row in the list. That includes a step that has not been finished (a delay with no seconds, a command step with no device chosen) and a command whose parameters are not filled in: some commands take a parameter they cannot run without, marked **required** in red beside the field, and leaving one empty means the device refuses the step every time with a message like **Set Fader: Channel is required.** The mark names the field the way the step shows it: **Channel is required.**, **Device is required.** A step aimed at a device whose driver is not installed yet is left alone, so a macro written ahead of the equipment stays quiet. Nothing here blocks a save: a half-built macro is a normal thing to leave overnight.

Reorder steps by dragging the grip handle on the left side of each step. Toggle **Stop on Error** in the macro header to halt execution if any step fails (by default, macros continue through errors).

**Step Templates:** Click the **Templates** dropdown in the step toolbar to insert pre-built multi-step patterns: Volume Ramp, Source Switch, and Power Sequence. Templates add several coordinated steps at once and are a fast way to build common sequences.

**Copy and Paste Steps:** Right-click a step (or use the step menu) to copy it. Paste into the same macro or a different one. This works across macros, so you can reuse steps without rebuilding them.

**Plugin Actions:** Installed plugins can add their own step types. They appear at the bottom of the Add Step menu under **Plugin Actions**, grouped by plugin name. Examples: an Audio Player plugin contributes `Play Sound`; an MQTT bridge plugin contributes `Publish Message`. Each plugin action shows the plugin's parameter form, supports dynamic `$var.foo` values just like built-in steps, and can be reordered, copied, and used inside conditionals like any other step. If you remove a plugin that a macro is using, the affected steps show a "Missing plugin" warning until you reinstall it or delete the steps.

## A Typical System-On Macro

Here is a real-world example of a `system_on` macro for a conference room:

| Step | Type | Details |
|------|------|---------|
| 1 | Set Variable or Control | `var.room_active` = Yes |
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
2. Set the **If** condition: pick a state key, operator, and value. Available operators:

| Operator | Aliases | Description |
|----------|---------|-------------|
| `eq` | `equals`, `==` | Equal to |
| `ne` | `not_equals`, `!=` | Not equal to |
| `gt` | `greater_than`, `>` | Greater than |
| `lt` | `less_than`, `<` | Less than |
| `gte` | `greater_or_equal`, `>=` | Greater than or equal to |
| `lte` | `less_or_equal`, `<=` | Less than or equal to |
| `truthy` | | Value is truthy (non-empty, non-zero, non-null) |
| `falsy` | | Value is falsy (empty, zero, null, false) |
3. Add steps to the **Then** block (runs when condition is true)
4. Optionally add steps to the **Else** block (runs when condition is false)

The value field adapts to the key you chose: pick a boolean variable and you get a **Yes / No** dropdown instead of a text box, so there is no guessing at how to spell the value. This same condition editor is used by **Skip If** guards, **Wait Until** steps, and trigger guard conditions, so the Yes / No dropdown appears in all of them.

Comparisons automatically handle type differences between device state and your condition value. A device that reports volume as `"-12.5"` (text) will correctly compare against a numeric threshold like `-20`. Similarly, `"true"`, `"on"`, `"yes"` and `"enabled"` (text) match `true` (boolean), and so does any number but 0; `"false"`, `"off"`, `"no"`, `"disabled"` and 0 match `false`. Text equality also ignores case, so a condition value of `On` matches a device that reports `on`. You don't need to worry about the internal type or exact casing — just enter the value you expect. A condition never holds, either way, for a device that has not reported the value yet. The touch panel compares the same way, so a button's look, a toggle and a Visible when… condition agree with your macros.

`eq`/`ne` against a value that has not been reported yet (a device that has not polled, or a key that does not exist) never match: `power != "on"` will not fire while `power` is still unknown, so an automation cannot act on state a device has not sent. To branch on a missing or empty value, use `falsy`.

To compare with another key instead of a fixed value, start the value with `$`. `device.projector.input == $var.wanted_input` holds when the projector's input matches whatever `var.wanted_input` holds at that moment, and a **Wait Until** on it finishes when either key changes to match. A key with no value yet makes no decision, as an unreported value does. In a **Conditional** or **Skip If**, `$trigger.<field>` reads what ran the macro (see [Reading what ran the macro](#reading-what-ran-the-macro)). The condition editor shows the key's current value beside it, and **Validate** names a `$var.` reference to a variable the project does not define.

Conditionals can be nested (a conditional inside a conditional) up to 5 levels deep. Macros that call other macros via "Run Macro" steps can nest up to 10 levels deep. For most rooms, one level of each is enough.

## Wait Until

A **Wait Until** step pauses the macro until a state value matches a condition, then continues. This replaces the common pattern of guessing a fixed **Delay** for a projector warmup or a display input change. The macro resumes the instant the device reports ready, and has a timeout so a stuck device does not strand the macro forever.

Fields:

- **Wait until** — a condition (same picker as conditional steps): state key, operator, value.
- **Timeout** — seconds to wait before giving up. Check **Never time out** to wait indefinitely.
- **If timeout** — when a timeout fires, either **Fail the macro** (default) or **Continue anyway**. Failing is the safer default for hardware waits, since the next step is usually something that depends on the device being ready.

Give the step a description that makes sense to the person using the panel, such
as “Waiting for the display to turn on.” If the wait fails, the panel's failure
message includes that description. Without one, it says the requested status was
not reached in time. A wait timeout does not by itself prove a network failure;
the condition and timing details remain in the Programmer's diagnostics.

Example: a projector warmup that no longer guesses a delay.

| Step | Type | Details |
|------|------|---------|
| 1 | Device Command | `projector_main` -> `power_on` |
| 2 | **Wait Until** | `device.projector_main.power_state` equals `"on"`, timeout 60s, fail on timeout |
| 3 | Device Command | `projector_main` -> `set_input`, input: `hdmi1` |

Other common uses: wait for a display to confirm an input change before routing audio (short timeout, continue anyway so audio always follows), wait for a device to come back online after a power cycle, or wait for a user to press a confirm button on the panel. For the last case, set a user variable from the button binding and use **Never time out** so the macro waits as long as the user needs. A waiting macro still respects cancel groups and explicit cancellation, so "Never time out" is always breakable from outside.

## Navigate Panel

A **Navigate Panel** step tells every connected panel to switch to a specific page. The target can be:

- A regular page ID, like `home` or `meeting_controls`
- An overlay or sidebar page ID — the panel pushes it onto the overlay stack, unless it is already open
- `$back` — phone-style back: closes the topmost overlay if one is open, otherwise returns to the previously-visited page. No-op if there's nothing to go back to. Page history resets on idle timeout.
- `$dismiss` — closes the topmost overlay only. No fallback to page history.

Every panel showing this project follows. Common uses:

- A "End meeting" macro that resets the system and sends panels back to `home`
- A recovery macro triggered by a fault that opens an `error_overlay`
- A schedule-driven macro that opens a `welcome` page in the morning

For switching **modes** within a single page (lecture vs. presentation, video conferencing vs. local source), this is usually the wrong tool. Prefer setting a variable with **Set Variable** and binding element visibility to that variable with the **Shows > Visible when…** card in the UI Builder. That pattern doesn't push every panel to a new page, survives panel reconnects, and keeps the mode state in one place that scripts and other macros can read.

## Ask for Help

An **Ask for Help** step raises a help request to whoever supports this space: the integrator who looks after it, or OpenAVC directly if the account has no integrator above it. It arrives as an alert, so it follows the notification routes already set up for the account and can be acknowledged and looked back on later.

There is no built-in help button. This is a step you wire up, which means you decide whether a space has one at all, what it is called, and where it sits. Three ways to raise it:

- **A panel button.** Point a button's press at a macro whose only step is Ask for Help. Put it behind a confirm step if the panel is somewhere a passer-by could lean on it.
- **A trigger, with nobody in the room.** This is the one worth more than the button. Three failed power-on attempts, a device that went offline as a class was starting, a temperature climbing: the space asks before anyone in it thinks to.
- **A script**, for anything the first two cannot express.

### The message

Optional, and where most of the value is. `$var.` and `$trigger.` references are filled in **inside** the sentence, so:

```
$var.room_name projector failed to power on $var.attempts times
```

arrives as "Lecture Hall 2 projector failed to power on 3 times" rather than as "someone pressed help". Leave it empty for a plain call for a person.

### Send at most every

A request is sent at most once in this many seconds (300 unless you change it), so a trigger that fires on every poll does not send one each time. A step reached sooner sends nothing and the rest of the macro runs. The macro editor shows that step as skipped, with the reason. Set it to 0 if the space should be able to ask twice in a row.

### Telling the room what happened

The step sets state you can bind to a label, so a panel can answer the person who pressed it:

| Key | What it holds |
|-----|---------------|
| `system.help_state` | `idle`, `requested`, `acknowledged`, or `unreachable` |
| `system.help_message` | The message that was sent |
| `system.help_requested_at` | When it was raised |
| `system.help_acknowledged_at` | When someone picked it up |
| `system.help_acknowledged_by` | Who picked it up |

Bind a label to these and the room can say "Help requested 9:02. Ben acknowledged 9:04." A button that does nothing visible gets pressed four more times, so it is worth showing something back even if it is only "requested".

**If the request does not reach the cloud**, `system.help_state` becomes `unreachable` rather than `requested`. That happens when the system is not connected to the cloud, and when messages to the cloud are paused for a short time after too many were sent at once (a burst of alerts, for example). Nothing is held and sent later: a help request that arrives forty minutes on is a message about a class that already ended. Show the difference on the panel so nobody stands waiting for help that never left the building.

### Putting the space back to idle

The keys keep showing the last request until a **Clear Help Request** step runs. Put one in the macro that ends a session, in a macro on an end-of-day schedule, or behind a Dismiss button on the page that shows the request. It sets `system.help_state` back to `idle` and empties the message, the times and who acknowledged it. The next Ask for Help sends at once, even inside its cooldown.

Clearing changes only what this system shows. The request stays open in OpenAVC Cloud until someone there closes it, and an acknowledgement that arrives after the clear does not put it back on the panel.

### Sending at most every N seconds

The step sends at most one request per cooldown, five minutes by default. This is not about people pressing too often, which is yours to gate on the page. It is about a trigger that fires every poll: "device offline" through a two-hour lecture would otherwise send a hundred identical messages to whoever is meant to answer them. Set it to 0 if a space should be able to ask twice in a row.

## Skip If Guards

Every step has an optional **Skip this step if...** guard in the Guards section at the bottom of the step editor. When enabled, the step is silently skipped if the condition is true.

This is simpler than a full conditional block when you just want to skip one step. For example: skip the "power on" command if `device.projector.power` already equals `"on"`.

Device Command steps also have a **Skip if device is offline** checkbox. When checked, the step is silently skipped instead of failing if the device is disconnected. This is useful for macros that control optional equipment that may not always be present. A device shown offline only because it stopped answering its polls, with its connection still open, still gets the command, so a display that goes quiet in standby can still be switched on. A command that works while the device is offline, like a Power On sent by Wake-on-LAN, is never skipped either. A device that was put to sleep on purpose (its card reads "Asleep") is offline, so its other commands are skipped and its wake command still runs.

A trigger on `device.<id>.connected` fires whenever the device goes off the network, including when it was put to sleep or told to restart. To act only on a fault, watch `device.<id>.offline_reason` instead: it stays empty while a device is asleep or restarting on purpose. `device.<id>.asleep` (`true` while asleep) is a trigger source of its own, and `device.<id>.presence` names the whole state in one word (`online`, `asleep`, `restarting`, `paused`, `offline` and the rest, listed in [Variables and State](variables-and-state.md#is-the-device-there)).

## Dynamic Parameters

Macro step parameters are normally static values set at design time. Dynamic parameters let a step use whatever value a variable holds at runtime.

Click the **$** button next to any parameter field to switch between static and dynamic mode. In dynamic mode, you pick a state key (like `var.target_volume`), and the macro reads the current value when it runs.

For example, a volume slider on the touch panel writes to `var.volume_level`. A macro step can use `$var.volume_level` as the level parameter for a DSP set_volume command. The same macro works for any volume, determined by the slider position.

Dynamic references also work on the **Set Variable** step's value field. This lets you copy one variable to another at runtime.

The same `$` picker now appears on UI Builder bindings, so a touch panel button or slider can read a variable or device state the same way a macro step does. See [UI Builder](ui-builder.md) for the binding-side picker.

### Reading what ran the macro

A macro can read what ran it as `$trigger.<field>`: in a step's value, through the **What ran this macro** group of the `$` picker, or as the key of a **Conditional** or **Skip this step if...** condition (`trigger.new_value`). A trigger's own guard conditions read the same fields.

| What ran the macro | What the macro can read |
|--------------------|-------------------------|
| A state change trigger | `$trigger.key` (the state key that changed), `$trigger.old_value`, `$trigger.new_value` |
| An event trigger | `$trigger.event` (the event's name), and every field of the event |
| A schedule trigger | `$trigger.trigger_type` (`schedule`), `$trigger.cron` |
| A startup trigger | `$trigger.trigger_type` (`startup`) |
| A control that runs the macro | `$trigger.tag`, `$trigger.element` (the control's ID), `$trigger.value`, `$trigger.input`, `$trigger.output`, `$trigger.mute` |

An event's fields keep the names its sender gave them. An **Emit Event** step with the payload field `source` gives the macro `$trigger.source`, and a device error (`device.error.<device_id>`) gives `$trigger.device_id` and `$trigger.error`. To use one, type it into the picker's search box: `trigger.source`.

**Fire now** in the trigger list gives the macro what the trigger can supply with no real change or event, and its message says what it gave. A startup or schedule trigger gives what it gives when it fires. A state change trigger gives `$trigger.key`, and as `$trigger.new_value` the key's current value when that value would fire the trigger; otherwise, for **Equals**, the value it fires on; otherwise nothing. `$trigger.old_value` is always empty. An event trigger, and a state change trigger on a pattern, give nothing.

A macro run by a **Run Macro** step reads what ran the macro that called it. Run any other way (the **Test** button, a script, the API), `$trigger.*` reads as empty.

### One macro for a row of buttons

A control gives the macro it runs its **Tag** as `$trigger.tag`, so nine buttons tagged `1` through `9` can share one macro:

```json
{"action": "device.command", "device": "switcher", "command": "select_input",
 "params": {"input": "$trigger.tag"}}
```

Set the Tag in the properties panel of the UI Builder. It is free text, so `1`, `hdmi2` and `row_3` all work. A control with no tag still gives its ID as `$trigger.element`.

## Cancel Groups

When two macros should not run at the same time (like System On and System Off), assign them to the same **cancel group**. When a macro with a cancel group starts, any other running macro in the same group is cancelled first.

Set the cancel group in the macro header, next to the macro ID. Give both macros the same group name (e.g., `system_power`). Now if someone presses System Off while System On is still running, the system-on macro stops immediately and system-off takes over.

You can also cancel a running macro manually by clicking the **Cancel** button in the macro header while it's running.

**Side effects of a cancelled macro.** Cancellation is not transactional. Any steps the cancelled macro already executed stay in effect: a **Set Variable** write is still in the state store, a **Device Command** or **Group Command** is already on the wire to the equipment, and an **Emit Event** payload has already fired on the event bus. None of those are rolled back. The two steps that unwind cleanly are **Delay** and **Wait Until**: if cancellation hits during either, the wait is abandoned with no residual change. Sub-macros invoked with **Run Macro** are cancelled at their current step, and cancellation stops the entire calling chain. No later steps in either macro run. Partial effects from inside the sub-macro persist the same way. Plan paired macros (System On / System Off, for example) so that being interrupted partway through is still recoverable by the macro that takes over. A common pattern is to set a "target state" variable as the very first step of each, so whichever one finishes last decides the final system state.

## Overlap and Cooldown

A macro can guard against being run too often, or being run again while it's still busy, right from its header, next to the cancel group.

- **Overlap** decides what happens when the macro is fired again while a previous run is still going. **Allow** (the default) lets them run at the same time. **Skip** ignores the new run until the current one finishes. **Queue** holds the new run and starts it once the current one is done. If the current run is still going after five minutes, the held run is dropped and reported as skipped.
- **Cooldown** sets a minimum number of seconds between starts. A start that arrives inside the window is dropped. Leave it at `0` for no cooldown.

The important part is *where* these apply. A trigger has its own overlap and cooldown settings, but those only cover fires from that one trigger. The macro's own overlap and cooldown are enforced on the macro itself, so they hold no matter what starts it: a schedule, a panel button, a script loop, an automation, the AI assistant, or another macro. That closes the gap where a macro you carefully protected on its trigger could still be hammered from a script or a button.

If both a trigger and its macro carry a guard, both apply: the trigger's settings decide whether it fires, then the macro's decide whether the run starts. The trigger's Cooldown counts from the last run it started, so a fire the macro turns away starts no Cooldown on the trigger, and the trigger card says why. A trigger's Cooldown carries on when you save the project and ends when you delete the trigger, so a trigger you add later starts without one. (Because it protects against unthrottled re-runs, a macro's overlap defaults to **Allow** to preserve existing behavior, while a trigger's defaults to **Skip**.)

## Progress in Panel

When a macro is running, the panel provides two forms of feedback:

**Button busy state (automatic).** If a button triggers a macro and that macro is currently running, the button automatically shows a pulsing animation and blocks re-presses. This prevents users from accidentally triggering the same macro twice. No configuration needed.

**Progress label (opt-in).** You can bind a label's text to "Macro Progress" in its **Shows > Text** card. Select the macro to track and set the idle text (what shows when the macro is not running). While the macro runs, the label shows the current step's description. Add descriptions to each macro step in the step editor to control what users see (e.g., "Powering on projector", "Waiting for warmup"). If no description is set, the system generates one from the action type.

## When a Step Fails

By default a macro runs every step even if one of them fails, so a projector
that is not on the network does not stop the screen coming down. Turn on
**Stop on Error** in the macro header to halt the run at the first failure
instead.

Either way, the panel the macro was started from shows what went wrong: a band
naming the device and the reason, the same message a button pressed directly
would give. One run shows one message, the first, since a macro that cannot
reach a device on one step usually cannot reach it on the next. A macro started
by a schedule or a trigger shows nothing on any panel.

A **Group Command** step is a fan-out, so one unreachable device out of eight
is not treated as a failed step. The **Test** view lists every member with a
success or fail icon. It counts as a failed step only when the command reached
no device at all.

### How the run reports itself

However a run ends, the answer says which of these it was:

| | What it means |
|---|---|
| **Completed** | Every step ran. |
| **Failed** | At least one step did not. The macro may still have run to the end — with Stop on Error off, that is what it is meant to do — but it did not do everything it was asked. |
| **Cancelled** | Something stopped it part-way, so its later steps never ran: somebody cancelled it, or another macro in the same Cancel Group preempted it. |
| **Skipped** | It never started, so nothing was sent to any device. The answer says why: its Cooldown had not passed, it was already running with Overlap set to Skip, a run queued behind another one waited more than five minutes, or the system was stopping. |
| **Running** | It is still going after 30 seconds — usually a **Delay** or a **Wait Until**. Not a failure; watch the live progress for how it ends. |

A macro that calls another one reports **Failed** when the macro it called did,
even where the called macro was set to continue past its own failures and
finished normally. Continue on Error decides whether the steps after a failure
still run; it does not make the failure stop being one.

## Variables in Macros

The **Set Variable or Control** step picks its target from the Variable Picker (a searchable dropdown that shows every variable with its current value, and a **Create New Variable** option inline) rather than a free-text key, so there is nothing to misspell. Its value field then matches the variable's type: a boolean variable gets a **Yes / No** dropdown, and the **$** toggle lets you copy another variable, device state, or system value in. Variables let macros share state. For example, the `system_on` macro sets `var.room_active` to Yes, and UI buttons use that variable for feedback.

## Changing a Control from a Macro

A macro can change a control on the panel while the program runs, without a
script. Add a **Set Variable or Control** step and pick the control from the
**UI** groups in the picker, then set the value. Every panel showing that
control updates immediately.

| Property | Value | Effect |
|----------|-------|--------|
| **Label** | text | The words on the control, or its caption on a slider, dropdown or meter, replacing the ones it was given. An image, a clock or a control with no caption has none to replace |
| **Visible** | Yes / No | Show or hide the control |
| **Background color** | a color, e.g. `#e67e22` | Fills the control |
| **Text color** | a color, e.g. `#ffffff` | Colors its words |
| **Opacity** | 0 to 1 | Below 1 fades it |

The key behind each one is `ui.<control id>.<property>`, so a script writes the
same values with `openavc.state.set("ui.btn_power.label", "Turn Off")`.

An override stands until you clear it. In the step, choose **Clear** where the
value's type is picked (Text, Number, Boolean, Clear) and the control returns to
the way it was authored, so a macro that changes a label for a meeting should
have a counterpart that clears it afterwards. A script clears it with
`openavc.state.set("ui.btn_power.label", None)`. An empty text value is not a
clear: it leaves the control with no words.

Two things it does not reach: a control's *value* (a slider's position, a
toggle's on/off) and anything not in the table above. For those, bind the
control to a variable and set the variable instead, which is also what makes
the state survive a panel reconnect.

## Macro Dependencies

The macro detail panel shows a dependency tree listing which macros call this macro and which macros this macro calls (via "Run Macro" steps). If you create a circular dependency (macro A calls B, B calls A), the IDE warns you at save time.

## Steps That Won't Run

A step is not finished until every field it needs is filled in. A **Delay** with no number of seconds, a **Device Command** with no command chosen, or a schedule trigger with no time set will save without complaint and then do nothing when the macro runs.

The IDE marks those for you. An amber note at the top of the macro lists what is unfinished, the step or trigger itself carries a warning icon, and the **macro list marks the macro**, so you can see there is something to fix without opening it.

Nothing is blocked. A half-finished step is normal while you are building, so the macro still saves either way, and each mark clears as soon as you fill the missing field in.

## Testing Macros

Click **Test** to save pending project edits and execute the macro. If saving fails or another session has changed the project, Test shows the save error and does not run the previous saved version. Resolve the error before testing again. A progress indicator shows which step is running, with live status updates. Conditional steps show whether the condition evaluated to true or false, and group command steps show per-device success/fail icons. A **Last Run** summary shows the timestamp, duration, step results, and any errors from the most recent execution. When the run did not go cleanly, Test says so on the button as well: a run whose steps failed, one that was cancelled, and one that never started each get their own message rather than finishing quietly, and a run that never started says why. A start the macro refuses from anywhere (Test, a panel button, a trigger, a script) shows under its steps as **Skipped:** with the reason, until the macro next starts.

## Convert to Script

Click **Convert to Script** to generate a Python script from the macro. A preview shows the generated code in a diff-like view before committing. This is useful when you need loops, error handling with retries, external API calls, or complex data processing that macros cannot express. The generated script runs the same steps with proper `await` calls. Navigate Panel, Ask for Help and Clear Help Request have no script equivalent and come out as comments: keep them in a macro and call it with `await macros.execute("<macro id>")`. A Schedule trigger also comes out as a comment, since scripts have no Schedule trigger. To keep the schedule, leave the trigger on the macro, replace the macro's steps with the one Emit Event step the comment names, and uncomment the handler below it. A Startup trigger becomes a `system.started` handler, which first runs the next time the server starts. A step that reads what ran the macro (`$trigger.new_value`, or **Skip this step if...** on `trigger.new_value`) reads it from `trigger`, which each handler fills in with what its trigger gives the macro. In `run()`, which no trigger runs, those values are empty. A macro the script runs with `macros.execute` gets none of them either, so the preview names any Run Macro step whose macro reads them. Each handler waits out its trigger's Delay and then checks the trigger's guard conditions, as the trigger does. A script has no Debounce, Cooldown or Overlap, so the preview names any that the macro or its triggers set.

## Triggers

Triggers automatically execute macros based on conditions. The **Triggers** section sits above the steps in the macro editor; click **Add Trigger** to add one to any macro.

| Trigger Type | Fires When | Example |
|-------------|------------|---------|
| **Schedule** | Cron expression matches | `0 22 * * 1-5` (10 PM weekdays) |
| **State Change** | A state key matches a condition | `device.projector.power` equals `"on"` |
| **Event** | An event fires on the bus | `ui.press.btn_panic` |
| **Startup** | System starts | Run initialization macro |

A **State Change** trigger does not have to match a value. Leave the operator on **Changes (any value)** and it fires every time the key changes, whatever it changed to, which is what you want for "something moved, go and look" automation. Pick an operator to narrow it to a value instead.

The schedule trigger includes a visual cron builder with two modes: a **field-by-field editor** (separate inputs for minute, hour, day, month, weekday with labels) and a raw expression editor. A dropdown of common examples ("Every weekday at 8am", "First Monday of month", "Every 15 minutes during business hours") lets you start from a template. Times are in the system's time zone, shown under the schedule (see [Time Zone](scheduling-guide.md#time-zone)).

**Fire Now:** Click the **Fire Now** button on any trigger to manually execute it immediately, without waiting for the condition to be met. Useful for testing. The macro reads what the trigger can supply (see [Reading what ran the macro](#reading-what-ran-the-macro)), and the message says which values it was given.

**Condition Preview:** under a trigger's **Conditions**, a live line reads "Evaluated now: ALL TRUE, trigger would fire" or "FALSE, trigger would not fire", with each key's current value beside it. A condition on a field of what fired the trigger (`trigger.new_value`) shows "known when it fires", and while the rest hold the line reads "TRUE so far".

**Event Autocomplete:** The event pattern field in event triggers suggests events from your configured devices, macros, and scripts as you type.

An **Event** trigger on a `custom.*` name can also be fired from outside the system: a [Node-RED](node-red.md) flow's **emit event** node, or a `POST /api/events` from anything that can make an HTTP request. The macro reads whatever came with the event as `$trigger.<field>`, exactly as it would from an Emit Event step.

Trigger safety features prevent runaway automation:

- **Debounce.** Prevent rapid re-firing (configurable seconds).
- **Delay + re-check.** Wait, then verify the condition is still true before executing.
- **Cooldown.** Minimum interval between executions. Debounce, Delay and Cooldown each take 0 or more seconds; 0 turns that one off.
- **Guard conditions.** Additional conditions that must all be true. Supports the same operators as conditional steps (`eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `truthy`, `falsy`) and their aliases, and a value starting with `$` compares with that key's current value. A guard on `trigger.<field>` reads what fired the trigger (see [Reading what ran the macro](#reading-what-ran-the-macro)), so an event trigger can check a field of its event, such as `trigger.source` equals `hdmi1`. After a delay or a queue wait, it still reads the values from the moment the trigger fired. A condition whose operator the system does not know, which only a hand-edited or imported project can hold, never lets the trigger fire, and the trigger card says which operator it is.
- **Overlap policy.** Choose what happens when a trigger fires while its macro is still running: `skip` (ignore the new fire, the default), `queue` (run it once the current one finishes, or drop the fire if that takes more than five minutes), or `allow` (run them concurrently).
- **Stop on error.** Set `stop_on_error: true` on a macro to halt execution if a step fails (default is to continue). If a sub-macro stops on an error, its **Run Macro** step fails in the caller. The caller then follows its own Stop on Error setting. Enable it on both macros when later steps depend on the sub-macro succeeding. A sub-macro set to Continue on Error completes its remaining steps and returns normally, and the caller carries on — but the caller's own run still reports as failed, because a step it asked for did not do what it asked.

A fire waiting in a trigger's delay, debounce or queue keeps waiting when you save the project, and runs the macro as it is saved when the wait ends. If you change or delete that trigger while a fire waits, the fire is dropped, and the card of a trigger you changed says why.

Example: A "projector auto-off" trigger watches `device.projector_main.power` for `"on"`, with a guard condition that `var.room_active` equals No. This shuts down a projector that someone turned on manually without using the panel, but only if the room is not in active use.

### How a trigger reports itself

A trigger card shows how its last fire ended, using the same words as [a macro
run](#how-the-run-reports-itself): **Ran OK** with how long ago, or **Last run
failed** in red with the reason on hover. A trigger that has never fired says
nothing, because that is not the same as one that fired and worked.

When a fire does not go ahead, the card says **Skipped:** with the reason, for
example that its Cooldown had not passed, its conditions were not met, or its
macro was still running. The line stays until the trigger next fires. A fire
its macro turned away, a waiting fire dropped because its trigger was
changed or deleted, and a fire stopped by a condition whose operator the
system does not know are kept as the trigger's last run, so the card still
says why after the Programmer is reopened; any other reason is shown only
while the Programmer is open and connected.

This matters most for the automation nobody watches. A nightly shutdown whose
projector command has been failing for a month still fires on time, so a
trigger with no report at all looked exactly like one doing its job.

**Fire Now** tells you the same thing straight away: whether the macro
completed, failed, was cancelled, or never started, and if it never started,
why. It does not overwrite the card's record, so you can test a
trigger without losing the failure you opened it to look at.

## Scripts

For logic that macros cannot express, write Python scripts using the **Code** view in the Programmer IDE. Scripts are stored in the active project's `scripts/` folder (`projects/default/scripts/`) as standard `.py` files.

Use the search box at the top of the script list to filter by file name.

## The Script Editor

- File tree on the left showing project scripts (files with load errors show a red icon)
- Monaco editor with Python syntax highlighting and autocomplete
- Autocomplete for the OpenAVC API with return types, parameter types, and one-line examples
- **Save** to write changes, **Save & Reload Script** to hot-reload without restarting (**Ctrl+Shift+R** for keyboard shortcut)
- Console panel showing script output and full error tracebacks with clickable line numbers

## Quick Example

```python
from openavc import on_event, devices, state, log, delay

@on_event("ui.press.btn_system_on")
async def system_on(event):
    log.info("System ON triggered")
    state.set("var.room_active", True)

    await devices.send("projector_main", "power_on")
    await devices.send("screen_1", "lower")

    # Wait for projector warmup
    await delay(20)

    await devices.send("projector_main", "set_input", {"input": "hdmi1"})
    await devices.send("switcher_1", "set_route", {"input": 1, "output": 1})
    await devices.send("dsp_1", "set_control", {"block": "PgmLvl", "control": "level_1", "value": -20})

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
- Device control (send commands to a device when an event fires)
- Periodic timer (recurring status checks)
- System on/off (full room startup/shutdown sequence)
- Scheduled task (a repeating check on a timer that can be cancelled)
- Device monitor (watch device state and react)
- Custom event handler (listen for custom events)
- Variable watcher (react to variable changes)

See the [Scripting Guide](scripting-guide.md) for the complete API reference including all available functions, decorators, and patterns.

## See Also

- [Scripting Guide](scripting-guide.md). Complete Python scripting API
- [Scheduling Guide](scheduling-guide.md). Cron schedules, trigger-schedules, timers
- [Variables and State](variables-and-state.md). User variables, device states, and activity monitoring
- [Node-RED](node-red.md). Run a space's logic as a flow, with macros doing the sequences
