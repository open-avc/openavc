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
];
