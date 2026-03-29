"""
Conference Room Scripts — volume mapping and projector status.

Demonstrates two common scripting patterns:
1. Volume mapping: UI slider (0-100) -> DSP dB range (-60 to 0)
2. State-reactive labels: projector power state -> human-readable text
"""

from openavc import on_event, on_state_change, devices, state, log


# --- Volume Mapping ---
# The UI slider sends 0-100, but the DSP expects decibels (-60 to 0).
# This script translates between the two.

@on_event("ui.change.sld_volume")
async def volume_changed(event):
    ui_value = event.get("value", 50)
    # Map 0-100 -> -60dB to 0dB
    db = (ui_value / 100.0) * 60.0 - 60.0
    log.info(f"Volume: {ui_value}% -> {db:.1f} dB")
    await devices.send("dsp1", "set_fader", {"channel": "program", "level": db})


# --- Projector Status ---
# Watch the projector's power state and update a user-friendly status variable.
# This variable drives the "Projector: Ready" label on the UI.

@on_state_change("device.projector1.power")
async def projector_status_changed(key, old_value, new_value):
    status_map = {
        "on": "Ready",
        "warming": "Warming up...",
        "cooling": "Cooling down...",
        "off": "Off",
    }
    text = status_map.get(str(new_value), "Unknown")
    state.set("var.projector_status", text)
    log.info(f"Projector status: {new_value} -> {text}")
