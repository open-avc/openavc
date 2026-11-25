// GENERATED FILE - DO NOT EDIT.
// Rendered from the scripting API (openavc/__init__.py, openavc/core/script_api.py),
// its words (openavc/core/script_api_help.py) and the events a project hears
// (openavc/core/event_bus.py EVENTS).
// Regenerate with:  python -m openavc.core.script_api_gen
// A test compares this file against a fresh render, so hand edits fail CI.

/** One name a script can use, or one member of one of its objects. */
export interface ScriptApiName {
  name: string;
  kind: "function" | "class" | "object";
  /** A function's parameters as Python writes them, or "". */
  signature: string;
  /** What a function returns, or "". */
  returns: string;
  /** Called with `await`. */
  awaited: boolean;
  /** What choosing it inserts; `${n:...}` marks each place to fill in. */
  insertText: string;
  /** What typing `@` and choosing it inserts, for a decorator; else "". */
  decorator: string;
  summary: string;
  example: string;
}

/** One family of events: `<...>` where the sender puts an id. */
export interface ScriptEvent {
  name: string;
  /** The pattern that hears the whole family. */
  pattern: string;
  description: string;
}

/** What `from openavc import` offers, in `__all__`'s order. */
export const SCRIPT_API: ScriptApiName[] = [
  {
    "name": "Event",
    "kind": "class",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "Event",
    "decorator": "",
    "summary": "The event a handler is given: its `name`, its `payload` as a dict, and each payload field by name. `event.get(key, default)` reads a field that may be missing.",
    "example": "@on_event(\"ui.change.vol_slider\")\nasync def on_volume(event):\n    log.info(f\"{event.name}: {event.value}\")"
  },
  {
    "name": "PanelMessage",
    "kind": "class",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "PanelMessage",
    "decorator": "",
    "summary": "Raise from a function a control calls to show this sentence on the panel that was pressed. Anything else it raises shows that the control could not finish.",
    "example": "def start_film():\n    if state.get(\"var.lamp_hours\", 0) > 2000:\n        raise PanelMessage(\"Projector lamp needs replacing\")"
  },
  {
    "name": "after",
    "kind": "function",
    "signature": "(seconds: float, callback: Callable, *args: Any)",
    "returns": "str",
    "awaited": false,
    "insertText": "after(${1:seconds}, ${2:callback})",
    "decorator": "",
    "summary": "Run a function once, after a number of seconds. Extra arguments are passed to it. Returns the timer's id, for cancel_timer.",
    "example": "timer_id = after(5, macros.start, \"all_off\")"
  },
  {
    "name": "cancel_all_timers",
    "kind": "function",
    "signature": "()",
    "returns": "int",
    "awaited": false,
    "insertText": "cancel_all_timers()",
    "decorator": "",
    "summary": "Cancel every timer this script started with after() or every(). Other scripts' timers keep running. Returns how many were cancelled.",
    "example": "count = cancel_all_timers()"
  },
  {
    "name": "cancel_timer",
    "kind": "function",
    "signature": "(timer_id: str)",
    "returns": "bool",
    "awaited": false,
    "insertText": "cancel_timer(\"${1:timer_id}\")",
    "decorator": "",
    "summary": "Cancel a timer started by after() or every(). Returns False when no running timer has that id.",
    "example": "cancel_timer(timer_id)"
  },
  {
    "name": "compare",
    "kind": "function",
    "signature": "(actual: Any, operator: str, target: Any = None)",
    "returns": "bool",
    "awaited": false,
    "insertText": "compare(${1:actual}, \"${2:operator}\")",
    "decorator": "",
    "summary": "Compare two values the way macro and trigger conditions do: `eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `truthy`, `falsy`, and their aliases, with a number in text compared as a number. A mismatch is False, never an error.",
    "example": "if compare(state.get(\"var.volume\"), \"gte\", 50):\n    await devices.send(\"amp_main\", \"duck\")"
  },
  {
    "name": "delay",
    "kind": "function",
    "signature": "(seconds: float)",
    "returns": "None",
    "awaited": true,
    "insertText": "delay(${1:seconds})",
    "decorator": "",
    "summary": "Wait a number of seconds inside an async handler, without holding up anything else.",
    "example": "await delay(2)"
  },
  {
    "name": "devices",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "devices",
    "decorator": "",
    "summary": "Send commands to the project's devices, and list them.",
    "example": ""
  },
  {
    "name": "events",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "events",
    "decorator": "",
    "summary": "Send events that scripts, triggers and macros listen for.",
    "example": ""
  },
  {
    "name": "every",
    "kind": "function",
    "signature": "(seconds: float, callback: Callable, *args: Any)",
    "returns": "str",
    "awaited": false,
    "insertText": "every(${1:seconds}, ${2:callback})",
    "decorator": "",
    "summary": "Run a function every so many seconds. Extra arguments are passed to it. Returns the timer's id, for cancel_timer. Start it at the top of the script, so it starts again each time the script loads.",
    "example": "timer_id = every(60, check_status)"
  },
  {
    "name": "isc",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "isc",
    "decorator": "",
    "summary": "Send events and commands to other OpenAVC instances.",
    "example": ""
  },
  {
    "name": "log",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "log",
    "decorator": "",
    "summary": "Write to the Log view.",
    "example": ""
  },
  {
    "name": "macros",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "macros",
    "decorator": "",
    "summary": "Run the project's macros.",
    "example": ""
  },
  {
    "name": "on_event",
    "kind": "function",
    "signature": "(pattern: str)",
    "returns": "Callable",
    "awaited": false,
    "insertText": "on_event(\"${1:pattern}\")",
    "decorator": "on_event(\"${1:event_pattern}\")\nasync def ${2:handler}(event):\n    ${0}",
    "summary": "Run the function below when an event matching the pattern fires. `*` matches any part of a name. The function takes one argument, the Event.",
    "example": "@on_event(\"ui.press.btn_power\")\nasync def handle(event):\n    log.info(\"Power pressed\")"
  },
  {
    "name": "on_state_change",
    "kind": "function",
    "signature": "(pattern: str)",
    "returns": "Callable",
    "awaited": false,
    "insertText": "on_state_change(\"${1:pattern}\")",
    "decorator": "on_state_change(\"${1:key_pattern}\")\ndef ${2:handler}(key, old_value, new_value):\n    ${0}",
    "summary": "Run the function below when a state key matching the pattern changes. `*` matches any part of a key. The function takes the key, the old value and the new value.",
    "example": "@on_state_change(\"var.volume\")\ndef on_vol(key, old_value, new_value):\n    log.info(f\"Volume: {new_value}\")"
  },
  {
    "name": "plugins",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "plugins",
    "decorator": "",
    "summary": "Call the methods a running plugin offers to scripts.",
    "example": ""
  },
  {
    "name": "state",
    "kind": "object",
    "signature": "",
    "returns": "",
    "awaited": false,
    "insertText": "state",
    "decorator": "",
    "summary": "Read and write state: variables, device values and panel overrides.",
    "example": ""
  }
];

/** Each object's members, by the object's name. */
export const SCRIPT_API_MEMBERS: Record<string, ScriptApiName[]> = {
  "devices": [
    {
      "name": "list",
      "kind": "function",
      "signature": "()",
      "returns": "list[dict[str, Any]]",
      "awaited": false,
      "insertText": "list()",
      "decorator": "",
      "summary": "List the project's devices, each as a dict with its `id`, `name`, `driver` and `connected`.",
      "example": "for device in devices.list():\n    log.info(f\"{device['name']}: {device['connected']}\")"
    },
    {
      "name": "send",
      "kind": "function",
      "signature": "(device_id: str, command: str, params: dict[str, Any] | None = None)",
      "returns": "Any",
      "awaited": true,
      "insertText": "send(\"${1:device_id}\", \"${2:command}\")",
      "decorator": "",
      "summary": "Send a command to a device and wait for it to run. `params` is a dict. A command that does not run raises, in the words Send Command shows for it.",
      "example": "await devices.send(\"projector1\", \"power_on\")\nawait devices.send(\"switcher\", \"set_input\", {\"input\": \"hdmi1\"})"
    }
  ],
  "events": [
    {
      "name": "emit",
      "kind": "function",
      "signature": "(event: str, payload: dict[str, Any] | None = None)",
      "returns": "None",
      "awaited": true,
      "insertText": "emit(\"${1:event}\")",
      "decorator": "",
      "summary": "Send an event. Scripts, triggers and macros listening for it run. A name starting with `system.` is OpenAVC's own and is refused.",
      "example": "await events.emit(\"custom.source_changed\", {\"source\": \"hdmi1\"})"
    }
  ],
  "isc": [
    {
      "name": "RemoteError",
      "kind": "class",
      "signature": "",
      "returns": "",
      "awaited": false,
      "insertText": "RemoteError",
      "decorator": "",
      "summary": "What send_command raises when the other instance answered and refused: its allowlist denied the command, it is already running as many remote commands as it accepts, or its device reported a fault. The fix is on that instance.",
      "example": "try:\n    await isc.send_command(\"lobby-instance-id\", \"display1\", \"power_off\")\nexcept isc.RemoteError as e:\n    log.warning(f\"The lobby refused it: {e}\")"
    },
    {
      "name": "broadcast",
      "kind": "function",
      "signature": "(event: str, payload: dict[str, Any] | None = None)",
      "returns": "None",
      "awaited": true,
      "insertText": "broadcast(\"${1:event}\")",
      "decorator": "",
      "summary": "Send an event to every connected instance.",
      "example": "await isc.broadcast(\"custom.fire_alarm\", {\"zone\": \"all\"})"
    },
    {
      "name": "get_instances",
      "kind": "function",
      "signature": "()",
      "returns": "list[dict[str, Any]]",
      "awaited": false,
      "insertText": "get_instances()",
      "decorator": "",
      "summary": "List the other instances this one has found, each as a dict with its `instance_id`, `name` and whether it is `connected`.",
      "example": "for peer in isc.get_instances():\n    log.info(f\"{peer['name']} connected={peer['connected']}\")"
    },
    {
      "name": "send_command",
      "kind": "function",
      "signature": "(instance_id: str, device_id: str, command: str, params: dict[str, Any] | None = None)",
      "returns": "Any",
      "awaited": true,
      "insertText": "send_command(\"${1:instance_id}\", \"${2:device_id}\", \"${3:command}\")",
      "decorator": "",
      "summary": "Run a device command on another instance's equipment and wait for its answer. That instance has to allow the command in its remote-command allowlist. Raises isc.RemoteError when it refuses, ConnectionError when it cannot be reached and TimeoutError when it does not answer.",
      "example": "await isc.send_command(\"lobby-instance-id\", \"display1\", \"power_off\")"
    },
    {
      "name": "send_to",
      "kind": "function",
      "signature": "(instance_id: str, event: str, payload: dict[str, Any] | None = None)",
      "returns": "None",
      "awaited": true,
      "insertText": "send_to(\"${1:instance_id}\", \"${2:event}\")",
      "decorator": "",
      "summary": "Send an event to one other instance.",
      "example": "await isc.send_to(\"lobby-instance-id\", \"custom.all_off\", {\"zone\": \"building\"})"
    }
  ],
  "log": [
    {
      "name": "debug",
      "kind": "function",
      "signature": "(msg: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "debug(\"${1:msg}\")",
      "decorator": "",
      "summary": "Write a debug line, shown only when the log level is Debug.",
      "example": "log.debug(f\"Volume is {state.get('var.volume')}\")"
    },
    {
      "name": "error",
      "kind": "function",
      "signature": "(msg: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "error(\"${1:msg}\")",
      "decorator": "",
      "summary": "Write an error to the Log view.",
      "example": "log.error(\"Display did not answer\")"
    },
    {
      "name": "info",
      "kind": "function",
      "signature": "(msg: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "info(\"${1:msg}\")",
      "decorator": "",
      "summary": "Write an informational line to the Log view.",
      "example": "log.info(\"Room is ready\")"
    },
    {
      "name": "warning",
      "kind": "function",
      "signature": "(msg: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "warning(\"${1:msg}\")",
      "decorator": "",
      "summary": "Write a warning to the Log view.",
      "example": "log.warning(\"Projector lamp is low\")"
    }
  ],
  "macros": [
    {
      "name": "execute",
      "kind": "function",
      "signature": "(macro_id: str)",
      "returns": "str",
      "awaited": true,
      "insertText": "execute(\"${1:macro_id}\")",
      "decorator": "",
      "summary": "Run a macro by its id, wait for its last step, and get back how it ended: \"completed\", \"failed\", \"cancelled\" or \"skipped\". The wait counts toward the 30 second limit on a handler or timer, so use start for a long macro.",
      "example": "if await macros.execute(\"system_on\") != \"completed\":\n    log.warning(\"System On did not finish cleanly\")"
    },
    {
      "name": "start",
      "kind": "function",
      "signature": "(macro_id: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "start(\"${1:macro_id}\")",
      "decorator": "",
      "summary": "Start a macro by its id and carry on at once, without waiting. It runs to its last step however long it takes. Call it from a handler or a timer, not from the top of a script.",
      "example": "macros.start(\"system_off\")"
    }
  ],
  "state": [
    {
      "name": "delete",
      "kind": "function",
      "signature": "(key: str)",
      "returns": "None",
      "awaited": false,
      "insertText": "delete(\"${1:key}\")",
      "decorator": "",
      "summary": "Remove a key, so it reads as not set. `state.set(key, None)` keeps the key with no value. A `system.` or `isc.` key cannot be removed.",
      "example": "state.delete(\"var.last_error\")"
    },
    {
      "name": "get",
      "kind": "function",
      "signature": "(key: str, default: Any = None)",
      "returns": "Any",
      "awaited": false,
      "insertText": "get(\"${1:key}\")",
      "decorator": "",
      "summary": "Read a state value. Returns `default` (None unless you give one) when the key is not set.",
      "example": "volume = state.get(\"var.volume\", 50)\nis_on = state.get(\"device.projector1.power\")"
    },
    {
      "name": "get_namespace",
      "kind": "function",
      "signature": "(prefix: str)",
      "returns": "dict[str, Any]",
      "awaited": false,
      "insertText": "get_namespace(\"${1:prefix}\")",
      "decorator": "",
      "summary": "Read every key under a prefix, as a dict.",
      "example": "projector = state.get_namespace(\"device.projector1.\")"
    },
    {
      "name": "set",
      "kind": "function",
      "signature": "(key: str, value: Any, source: str = 'script')",
      "returns": "None",
      "awaited": false,
      "insertText": "set(\"${1:key}\", ${2:value})",
      "decorator": "",
      "summary": "Set a state value: text, a number, True or False, or None. Panels, macros and other scripts see it at once. A `system.` or `isc.` key belongs to OpenAVC and a list is not one value, so either is refused and shown on the script's row. A `device.` value lasts until the device's next report.",
      "example": "state.set(\"var.room_active\", True)\nstate.set(\"var.current_source\", \"HDMI 1\")"
    }
  ]
};

/** The events a project hears, in the order the docs list them. */
export const SCRIPT_EVENTS: ScriptEvent[] = [
  {
    "name": "ui.press.<element_id>",
    "pattern": "ui.press.*",
    "description": "A button was pressed"
  },
  {
    "name": "ui.release.<element_id>",
    "pattern": "ui.release.*",
    "description": "A button was released"
  },
  {
    "name": "ui.hold.<element_id>",
    "pattern": "ui.hold.*",
    "description": "A button was held past its hold time"
  },
  {
    "name": "ui.toggle_off.<element_id>",
    "pattern": "ui.toggle_off.*",
    "description": "A toggle button was turned off"
  },
  {
    "name": "ui.change.<element_id>",
    "pattern": "ui.change.*",
    "description": "A slider or select changed value"
  },
  {
    "name": "ui.select.<element_id>",
    "pattern": "ui.select.*",
    "description": "A list row was selected"
  },
  {
    "name": "ui.route.<element_id>",
    "pattern": "ui.route.*",
    "description": "A matrix route was made"
  },
  {
    "name": "ui.audio_route.<element_id>",
    "pattern": "ui.audio_route.*",
    "description": "An audio-breakaway route was made"
  },
  {
    "name": "ui.mute_route.<element_id>",
    "pattern": "ui.mute_route.*",
    "description": "A matrix output was muted or unmuted"
  },
  {
    "name": "ui.audio_mute_route.<element_id>",
    "pattern": "ui.audio_mute_route.*",
    "description": "An audio-breakaway output was muted or unmuted"
  },
  {
    "name": "ui.submit.<element_id>",
    "pattern": "ui.submit.*",
    "description": "A text input or keypad was submitted"
  },
  {
    "name": "ui.page.<page_id>",
    "pattern": "ui.page.*",
    "description": "A panel went to this page"
  },
  {
    "name": "device.connected.<device_id>",
    "pattern": "device.connected.*",
    "description": "The device connected"
  },
  {
    "name": "device.disconnected.<device_id>",
    "pattern": "device.disconnected.*",
    "description": "The device's connection was lost"
  },
  {
    "name": "device.error.<device_id>",
    "pattern": "device.error.*",
    "description": "A command or poll failed while the device stayed connected"
  },
  {
    "name": "macro.started.<macro_id>",
    "pattern": "macro.started.*",
    "description": "The macro started"
  },
  {
    "name": "macro.completed.<macro_id>",
    "pattern": "macro.completed.*",
    "description": "The macro finished"
  },
  {
    "name": "macro.cancelled.<macro_id>",
    "pattern": "macro.cancelled.*",
    "description": "The macro was cancelled"
  },
  {
    "name": "macro.step_error.<macro_id>",
    "pattern": "macro.step_error.*",
    "description": "A step of the macro failed, whether or not the macro stopped there"
  },
  {
    "name": "macro.error.<macro_id>",
    "pattern": "macro.error.*",
    "description": "The macro stopped because a step failed (Stop on Error)"
  },
  {
    "name": "macro.skipped.<macro_id>",
    "pattern": "macro.skipped.*",
    "description": "The macro did not start: its Overlap or Cooldown setting refused the run"
  },
  {
    "name": "system.started",
    "pattern": "system.started",
    "description": "The system started. Comes once per server start"
  },
  {
    "name": "system.stopping",
    "pattern": "system.stopping",
    "description": "The system is shutting down"
  },
  {
    "name": "system.project.reloaded",
    "pattern": "system.project.reloaded",
    "description": "The project was saved or reloaded"
  },
  {
    "name": "help.requested",
    "pattern": "help.requested",
    "description": "A macro's Ask for Help step asked for help"
  },
  {
    "name": "help.acknowledged",
    "pattern": "help.acknowledged",
    "description": "Someone acknowledged the help request in the cloud"
  },
  {
    "name": "isc.peer_connected",
    "pattern": "isc.peer_connected",
    "description": "Another OpenAVC instance connected"
  },
  {
    "name": "isc.peer_disconnected",
    "pattern": "isc.peer_disconnected",
    "description": "Another OpenAVC instance disconnected"
  },
  {
    "name": "isc.<instance_id>.<event>",
    "pattern": "isc.*.*",
    "description": "An event sent by another OpenAVC instance"
  },
  {
    "name": "plugin.<plugin_id>.<event>",
    "pattern": "plugin.*.*",
    "description": "An event sent by a plugin"
  },
  {
    "name": "custom.<anything>",
    "pattern": "custom.*",
    "description": "Your own event, sent by an Emit Event step or events.emit()"
  }
];
