# Variables and State

Manage user variables, monitor device state, and track system activity.

The **State** tab (sidebar) is your central hub for variables, device states, and system activity. It has three sub-tabs:

## Variables

Manages user-defined variables and shows where each is used. Variables are the glue between UI elements, macros, triggers, and scripts.

**Creating Variables:** Click **New Variable** in the header:
- **Name**: descriptive identifier (e.g., `room_active`, `current_source`, `volume_level`)
- **Type**: string, number, or boolean
- **Default value**: initial value on system start

State key format: `var.<name>` (e.g., `var.room_active`, `var.current_source`)

**Usage Cross-Reference:** Each variable shows a list of everywhere it is referenced: macros, UI elements, triggers, and scripts. Invaluable for debugging.

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

A live feed of recent state changes across the entire system. Each entry shows the timestamp, key, old and new values, and the source of the change (device, macro, script, UI, API). Use the filter buttons to narrow by namespace.

## See Also

- [Programmer IDE Overview](programmer-overview.md). IDE layout, state concepts, and typical workflow.
- [Macros and Triggers](macros-and-triggers.md). Command sequences and automation conditions.
- [Scripting Guide](scripting-guide.md). Complete Python scripting API.
