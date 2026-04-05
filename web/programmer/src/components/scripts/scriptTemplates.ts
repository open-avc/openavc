export interface ScriptTemplate {
  name: string;
  description: string;
  code: string;
}

export const SCRIPT_TEMPLATES: ScriptTemplate[] = [
  {
    name: "Button Handler",
    description: "Respond to a UI button press",
    code: `"""Button handler — respond to UI button presses."""
from openavc import on_event, devices, state, log


@on_event("ui.press.*")
async def handle_button(event, payload):
    element_id = payload.get("element_id", "")
    log.info(f"Button pressed: {element_id}")
`,
  },
  {
    name: "State Change Handler",
    description: "React when a state value changes",
    code: `"""State change handler — react to variable changes."""
from openavc import on_state_change, state, log


@on_state_change("var.*")
def handle_state(key, old_value, new_value):
    log.info(f"{key}: {old_value} -> {new_value}")
`,
  },
  {
    name: "Device Control",
    description: "Send commands to devices on an event",
    code: `"""Device control — send commands to a device."""
from openavc import on_event, devices, state, log


@on_event("custom.my_trigger")
async def control_device(event, payload):
    await devices.send("projector1", "power_on")
    state.set("var.room_active", True)
    log.info("System powered on")
`,
  },
  {
    name: "Periodic Timer",
    description: "Run code on a repeating interval",
    code: `"""Periodic timer — check status every 60 seconds."""
from openavc import on_event, every, state, log


@on_event("system.started")
def start_timer(event, payload):
    every(60, check_status)


def check_status():
    log.info("Periodic check running")
`,
  },
  {
    name: "System On/Off",
    description: "Power on/off all room equipment",
    code: `"""System On/Off — control all room equipment."""
from openavc import on_event, devices, state, log
import asyncio


@on_event("ui.press.btn_system_on")
async def system_on(event, payload):
    log.info("System powering on...")
    state.set("var.projector_status_text", "Starting...")
    await devices.send("projector1", "power_on")
    state.set("var.room_active", True)
    await asyncio.sleep(3)
    await devices.send("projector1", "set_input", {"input": "hdmi1"})
    state.set("var.current_source", "HDMI 1")
    log.info("System on complete")


@on_event("ui.press.btn_system_off")
async def system_off(event, payload):
    log.info("System powering off...")
    await devices.send("projector1", "power_off")
    state.set("var.room_active", False)
    state.set("var.current_source", "None")
    log.info("System off complete")
`,
  },
  {
    name: "Scheduled Task",
    description: "Run code on a repeating schedule",
    code: `"""Scheduled task — runs on a cron schedule via a trigger.
Create a Schedule trigger on a macro, or use the every() timer here."""
from openavc import on_event, every, cancel_timer, state, devices, log


timer_id = None


@on_event("system.started")
def start_schedule(event, payload):
    global timer_id
    # Run every 5 minutes (300 seconds)
    timer_id = every(300, scheduled_check)
    log.info("Scheduled task started (every 5 minutes)")


def scheduled_check():
    # Add your periodic logic here
    log.info("Running scheduled check...")
    # Example: check a device, update a variable, send an alert
`,
  },
  {
    name: "Device Monitor",
    description: "Watch device connections and react to status changes",
    code: `"""Device monitor — react to device connect/disconnect events."""
from openavc import on_event, state, log


@on_event("device.*.connected")
async def on_connected(event, payload):
    device_id = event.split(".")[1]
    log.info(f"Device connected: {device_id}")
    state.set(f"var.{device_id}_online", True)


@on_event("device.*.disconnected")
async def on_disconnected(event, payload):
    device_id = event.split(".")[1]
    log.warning(f"Device disconnected: {device_id}")
    state.set(f"var.{device_id}_online", False)


@on_event("device.*.error")
async def on_error(event, payload):
    device_id = event.split(".")[1]
    error = payload.get("error", "Unknown error")
    log.error(f"Device error on {device_id}: {error}")
`,
  },
  {
    name: "Custom Event Handler",
    description: "Listen for and respond to custom events",
    code: `"""Custom event handler — create your own event-driven logic."""
from openavc import on_event, events, state, log


@on_event("custom.room_mode_changed")
async def on_mode_change(event, payload):
    mode = payload.get("mode", "unknown")
    log.info(f"Room mode changed to: {mode}")

    if mode == "presentation":
        state.set("var.room_mode", "presentation")
        # Emit follow-up events for other scripts to handle
        await events.emit("custom.lights_preset", {"preset": "dim"})
    elif mode == "meeting":
        state.set("var.room_mode", "meeting")
        await events.emit("custom.lights_preset", {"preset": "bright"})


# To trigger this from a macro or another script:
# await events.emit("custom.room_mode_changed", {"mode": "presentation"})
`,
  },
  {
    name: "Variable Watcher",
    description: "Monitor variable changes and enforce rules",
    code: `"""Variable watcher — monitor changes and enforce rules."""
from openavc import on_state_change, state, log


@on_state_change("var.volume")
def watch_volume(key, old_value, new_value):
    # Clamp volume to safe range
    if isinstance(new_value, (int, float)):
        if new_value > 80:
            log.warning(f"Volume {new_value} exceeds safe limit, clamping to 80")
            state.set("var.volume", 80)
        elif new_value < 0:
            state.set("var.volume", 0)


@on_state_change("var.room_active")
def watch_room(key, old_value, new_value):
    if old_value and not new_value:
        log.info("Room deactivated — resetting variables")
        state.set("var.current_source", "None")
        state.set("var.volume", 30)
`,
  },
];
