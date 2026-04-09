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

## Activity

A live feed of recent state changes across the entire system (up to 500 entries). Each entry shows the timestamp, key, old and new values, and the source of the change (device, macro, script, UI, API). Use the filter buttons to narrow by namespace, or type a specific variable or device key in the search box to filter to just that key.

## See Also

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow.
- [Macros and Triggers](macros-and-triggers.md). Command sequences and automation conditions.
- [Scripting Guide](scripting-guide.md). Complete Python scripting API.
