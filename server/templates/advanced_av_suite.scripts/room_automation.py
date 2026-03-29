"""
Advanced AV Suite — Room Automation Scripts

Demonstrates the full scripting API:
- Source routing with matrix switcher control
- Volume mapping (UI 0-100 -> DSP dB)
- Mic mute toggle via custom events
- Display status tracking via state change handlers
- Occupancy-based auto-shutdown with warning timer
- Presentation mode presets (adjusts DSP and routing per mode)
- Recurring status polling
"""

from openavc import (
    on_event, on_state_change,
    devices, state, events, log,
    after, every, cancel_timer,
)

# --- Source Routing ---
# UI buttons emit custom.select_source with a payload. This script
# handles the routing logic: set the matrix switcher, update state.

SOURCE_ROUTES = {
    "laptop":   {"input": 1, "output": 1},
    "wireless": {"input": 2, "output": 1},
    "bluray":   {"input": 3, "output": 1},
}

@on_event("custom.select_source")
async def handle_source_select(event):
    source = event.get("source", "")
    route = SOURCE_ROUTES.get(source)
    if not route:
        log.warning(f"Unknown source: {source}")
        return

    if state.get("var.system_power") != "on":
        log.info(f"System is off, ignoring source select: {source}")
        return

    log.info(f"Switching to source: {source}")
    await devices.send("switcher1", "set_route", route)
    # Route to confidence monitor too (always follows main)
    await devices.send("switcher1", "set_route", {"input": route["input"], "output": 2})
    state.set("var.current_source", source)


# --- Volume Mapping ---
# UI slider: 0-100 -> DSP: -60 dB to 0 dB

@on_event("ui.change.sld_volume")
async def volume_changed(event):
    ui_value = event.get("value", 40)
    db = (ui_value / 100.0) * 60.0 - 60.0
    await devices.send("dsp1", "set_fader", {"channel": "program", "level": round(db, 1)})


# --- Mic Mute Toggle ---

@on_event("custom.toggle_mic_mute")
async def toggle_mic_mute(event):
    current = state.get("var.mic_mute", True)
    new_mute = not current
    state.set("var.mic_mute", new_mute)
    await devices.send("dsp1", "mute", {"channel": "mics", "muted": new_mute})
    log.info(f"Mic mute: {new_mute}")


# --- Display Status Tracking ---

@on_state_change("device.display1.power")
async def display1_status(key, old_value, new_value):
    status_map = {
        "on": "Display Ready",
        "warming": "Warming up...",
        "cooling": "Cooling down...",
        "off": "Display Off",
    }
    state.set("var.display_status", status_map.get(str(new_value), "Unknown"))


# --- Occupancy-Based Auto-Shutdown ---
# When the room empties while the system is on, show a warning for 5 minutes.
# If no one returns, shut the system down. If someone comes back, cancel.

_shutdown_timer = None

@on_state_change("var.room_occupied")
async def occupancy_changed(key, old_value, new_value):
    global _shutdown_timer

    system_on = state.get("var.system_power") == "on"

    if not new_value and system_on:
        # Room just emptied while system is on — start countdown
        log.info("Room empty — starting 5-minute auto-shutdown countdown")
        state.set("var.auto_shutdown_warning", True)
        _shutdown_timer = after(300, _auto_shutdown)

    elif new_value and _shutdown_timer:
        # Someone came back — cancel shutdown
        log.info("Room occupied again — cancelling auto-shutdown")
        cancel_timer(_shutdown_timer)
        _shutdown_timer = None
        state.set("var.auto_shutdown_warning", False)


async def _auto_shutdown():
    global _shutdown_timer
    _shutdown_timer = None
    state.set("var.auto_shutdown_warning", False)

    if state.get("var.system_power") == "on" and not state.get("var.room_occupied"):
        log.info("Auto-shutdown: room still empty after 5 minutes, powering off")
        await events.emit("macro.execute", {"macro_id": "system_off"})


# --- Presentation Mode Presets ---
# When the user selects a mode from the dropdown, adjust DSP and routing.

@on_event("custom.mode_change")
async def mode_changed(event):
    mode = state.get("var.mode", "standard")
    log.info(f"Switching to mode: {mode}")

    if mode == "standard":
        await devices.send("dsp1", "set_fader", {"channel": "mics", "level": -12.0})
        await devices.send("dsp1", "mute", {"channel": "mics", "muted": False})

    elif mode == "video":
        # Video playback: louder program, mute mics
        await devices.send("dsp1", "set_fader", {"channel": "program", "level": -6.0})
        await devices.send("dsp1", "mute", {"channel": "mics", "muted": True})
        state.set("var.mic_mute", True)

    elif mode == "teleconference":
        # Teleconference: mics hot, moderate program, camera to wide shot
        await devices.send("dsp1", "set_fader", {"channel": "mics", "level": -6.0})
        await devices.send("dsp1", "mute", {"channel": "mics", "muted": False})
        state.set("var.mic_mute", False)
        await devices.send("camera1", "recall_preset", {"preset": 1})


# --- Startup: System Ready Event ---
# When the system finishes powering on, apply defaults.

@on_event("custom.system_ready")
async def on_system_ready(event):
    log.info("System ready — applying defaults")
    # Set default source
    await handle_source_select(type("E", (), {"get": lambda self, k, d=None: {"source": "laptop"}.get(k, d)})())
    # Unmute program audio at default level
    await devices.send("dsp1", "mute", {"channel": "program", "muted": False})
    # Mute mics by default
    await devices.send("dsp1", "mute", {"channel": "mics", "muted": True})
    state.set("var.mic_mute", True)


# --- Recurring Status Poll ---
# Poll the occupancy sensor every 60 seconds.

_poll_timer = None

@on_event("system.started")
async def start_polling(event, payload):
    global _poll_timer

    async def poll():
        try:
            result = await devices.send("sensor1", "get_status")
            if result and isinstance(result, dict):
                occupied = result.get("occupied", False)
                state.set("var.room_occupied", bool(occupied))
        except Exception:
            pass  # Sensor offline — don't crash

    _poll_timer = every(60, poll)
    log.info("Occupancy polling started (60s interval)")


@on_event("system.stopping")
async def stop_polling(event, payload):
    global _poll_timer
    if _poll_timer:
        cancel_timer(_poll_timer)
        log.info("Occupancy polling stopped")
