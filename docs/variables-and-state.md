# Variables and State

Manage user variables, monitor device state, and track system activity.

The **State** tab (sidebar) is your central hub for variables, device states, and system activity. It has three sub-tabs:

## Variables

Manages user-defined variables and shows where each is used. Variables are the glue between UI elements, macros, triggers, and scripts.

**Creating Variables:** Click **New Variable** in the header:
- **Name**: descriptive identifier (e.g., `room_active`, `current_source`, `volume_level`)
- **Type**: string, number, or boolean
- **Default value**: initial value on system start
- **Description** (optional): freeform text explaining the variable's purpose. Shows in tooltips and the Variable Key Picker throughout the IDE.

State key format: `var.<name>` (e.g., `var.room_active`, `var.current_source`)

**Persistence:** By default, variables reset to their default value when the server restarts. Enable **Persist Across Restarts** in the variable's detail panel to save the current value to disk. Persisted values survive reboots and power outages, so the system comes back in the same state it was in. Useful for room mode, last selected source, and similar stateful values. Persisted values are stored in `state.json` alongside the project file. Changes are saved to disk with a 1-second debounce to avoid excessive disk writes during rapid state changes, and writes are atomic (using a temporary file and rename) to prevent corruption if the server loses power mid-write. Persisted values are loaded before any scripts or triggers run at startup, so your automation always sees the correct state from the start.

Keep in mind that device states are always re-polled from hardware when devices reconnect, so they are always current. A persisted variable, however, reflects whatever value it had when the system last ran. If the real world changed while the system was off (for example, someone manually switched an input on a matrix switcher), a persisted variable tracking that input could be stale. For variables that need to stay in sync with hardware, use a **Source Binding** (below) or a **Startup trigger** to re-read the device state and update the variable when the system comes back online. See [Macros and Triggers](macros-and-triggers.md) for details on startup triggers.

**Validation Rules:** In a variable's detail panel, you can set optional validation constraints. For number variables, set a min and/or max value. For string variables, define a list of allowed values (enum). When a value violates its validation rule (set via macro, script, or UI), a warning appears in the Activity log. Validation warns but does not block the set, so automation continues running.

**Renaming Variables:** Click the rename icon next to a variable's ID to rename it. The IDE previews every reference that will be updated (macros, triggers, UI bindings, scripts) before applying the change. All references are updated automatically.

**Usage Cross-Reference:** Each variable shows a count and list of everywhere it is referenced: macros, UI elements, triggers, and scripts. Use the **Delete Unused** button in the header to bulk-remove variables with zero references (with confirmation showing which variables will be deleted).

**Source Binding:** Variables can optionally be *bound* to a device state key. Choose "Bound to state key" in the Source section of a variable's detail panel, select the device state to mirror, and optionally add a value map to translate hardware values into friendly text (e.g., `on` → `Ready`, `warming` → `Warming Up`). This eliminates the need for scripts for simple device-to-variable mirroring.

**Create variables without leaving the editor:** Anywhere you pick a state key -- a Set Variable action, a UI control's **Value** binding, a macro step, a trigger condition -- the picker has a **Create New Variable** option inline. Give it a name, type, and default, and the variable is created and selected in one step, so you can author a binding and the variable it needs together. This is the "pick, don't type" approach used throughout the IDE: you choose keys from a searchable list that shows each one's live value, instead of typing `var.x` by hand and hoping it matches.

**Two-way controls (read and write a variable):** A UI control can both *show* a variable and *change* it. In the UI Builder, set the control's **Shows > Value** to a `var.*` key and check **Two-way (this control can change it)**. Now a slider writes the variable as you drag it, a select writes it when you pick an option, a text field writes it as you type -- and the control still reflects the variable if a macro or script changes it elsewhere. Two-way is available **only for writable `var.*` keys**. You cannot make a control two-way to a `device.*` key: device state is a read-only mirror of what the hardware last reported, and writing it would simply be overwritten on the next poll. To make a control drive a device, read the device value and add a command under **Does** that uses `$value` (see [UI Builder](ui-builder.md)). This is the binding model's one firm rule: never write `device.*` state directly; drive a device with a command.

**Common Variable Patterns:**

| Variable | Type | Purpose |
|----------|------|---------|
| `room_active` | boolean | Track whether the room is in use |
| `current_source` | string | Track selected input ("laptop", "bluray", "wireless") |
| `projector_status_text` | string | Human-readable status (bound to `device.projector.power` with value map) |
| `volume_level` | number | Track volume for UI feedback |
| `presentation_mode` | string | Current mode ("standard", "video", "teleconference") |

## Device States

Browse all devices and their live state properties. Each property shows:
- The full state key (e.g., `device.projector.power`). Click to copy.
- The current live value
- Driver metadata (type, possible values) when available
- Where the property is referenced (macros, UI bindings, scripts)

Use this view to discover available state keys when building macros or UI bindings.

## Monitor a reading

Some readings tell you whether the room is all right: projector lamp hours, a DSP's temperature, whether the amp is faulted, whether the room is occupied. Tag one with **Monitor** and it appears on the Dashboard with its live value, and on the system's health card in OpenAVC Cloud if this system is paired.

You can tag a reading from either place it already appears:

- **State > Variables**, on the variable you want to watch.
- **State > Device States**, on any property you have selected.
- **Devices**, on any row of a device's Live State list.

They all write to the same list, so it does not matter which you use.

### Setting what normal looks like

Tagging alone is enough. The reading shows up with its value and label, and nothing is claimed about whether it is good or bad.

If you want to be told when it goes wrong, open the monitor and fill in two things:

- **Normal is.** For a number, the range it should stay inside. For anything else, the values that mean everything is fine. Anything you tick is normal; anything else is not.
- **Tell me if it stays wrong for.** How many minutes it has to be wrong before an alert is raised. Leave it blank to be told straight away.

The delay matters more than it looks. A projector that is off is perfectly normal at 3am and a problem ten minutes into a lecture, and a mute that has been on for four seconds is somebody pressing a button.

The label, unit, type and range are filled in from what the driver or the variable already declares, so most readings need nothing typed. All of it stays editable: a driver's 0–10000 hour lamp range is the lamp's range, not the point at which you want telling.

### Words instead of raw values

A reading with a handful of possible values can carry a word for each one, so a panel or a card reads "Occupied" rather than `true`, or "No Signal" rather than `none`. Naming a value does not say anything about whether it is healthy. That is the tick beside it.

### Where the alert goes

A monitor with limits raises an alert through OpenAVC Cloud, using whatever notification routes you have already set up. The limit lives in the project, so it travels with the room and works before the system is ever paired. Fleet-wide rules ("tell me when any system's disk goes above 90%") still belong in the cloud portal's Alert Rules page, which is a different job.

That page lists what each room's project monitors under **Set in the project**, so an alert from a limit you set here can be traced back to it. They are shown there, not edited there: the project owns them, and the next time the project loads it would overwrite anything the portal had changed.

## `$` references

A `$`-prefixed value is a live reference that resolves to the current value when it runs, instead of a fixed value you type in:

- `$var.<name>` reads a project variable (e.g. `$var.target_volume`).
- `$device.<id>.<property>` reads a device's live state (e.g. `$device.dsp_1.output_level`).
- `$system.<property>` reads a system value.

These references work the same way in macro steps, triggers, and UI Builder bindings. Anywhere you can set a command parameter or a value, you can use one. You pick them from the `$` picker (the picker lists every variable and state key with its current value) rather than typing the key by hand, so there is nothing to misspell.

A reference is normally the **whole** value: `$var.target_volume` is a number on its way to a device, and half a number means nothing. The one place references are filled in *inside* a sentence is the message on an [Ask for Help](macros-and-triggers.md#ask-for-help) step, because that one is prose on its way to a person.

## Activity

A live feed of recent state changes across the entire system (up to 500 entries). Each entry shows the timestamp, key, old and new values, and the source of the change (device, macro, script, UI, API). Use the filter buttons to narrow by namespace, or type a specific variable or device key in the search box to filter to just that key.

## See Also

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow.
- [Macros and Triggers](macros-and-triggers.md). Command sequences and automation conditions.
- [Scripting Guide](scripting-guide.md). Complete Python scripting API.
