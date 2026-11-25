"""What the script editor says about each name a script can use.

``openavc/__init__.py``'s ``__all__`` is what a script may import, and
``core/script_api.py`` is what each name is: its parameters, what it returns,
whether it is awaited. Both are read by ``core/script_api_gen.py``, which
renders the editor's completions. The words are the one thing the code cannot
say for itself, so they are kept here, one entry per name and per member of
each object (``devices.send``), and ``tests/test_script_api_generated.py``
fails a name with no entry and an entry with no name. The docstrings in
``script_api`` are written for whoever maintains it; these are written for
whoever writes a script.
"""

from __future__ import annotations

from typing import NamedTuple


class Help(NamedTuple):
    """One name, as the editor shows it."""

    summary: str
    example: str = ""
    #: What typing ``@`` and choosing a decorator inserts: the decorator and
    #: a handler in the form the script engine loads.
    snippet: str = ""


HELP: dict[str, Help] = {
    # --- What a script imports ---
    "Event": Help(
        "The event a handler is given: its `name`, its `payload` as a dict, and "
        "each payload field by name. `event.get(key, default)` reads a field "
        "that may be missing.",
        '@on_event("ui.change.vol_slider")\n'
        "async def on_volume(event):\n"
        '    log.info(f"{event.name}: {event.value}")',
    ),
    "PanelMessage": Help(
        "Raise from a function a control calls to show this sentence on the panel "
        "that was pressed. Anything else it raises shows that the control could "
        "not finish.",
        "def start_film():\n"
        '    if state.get("var.lamp_hours", 0) > 2000:\n'
        '        raise PanelMessage("Projector lamp needs replacing")',
    ),
    "after": Help(
        "Run a function once, after a number of seconds. Extra arguments are passed "
        "to it. Returns the timer's id, for cancel_timer.",
        'timer_id = after(5, macros.start, "all_off")',
    ),
    "cancel_all_timers": Help(
        "Cancel every timer this script started with after() or every(). Other "
        "scripts' timers keep running. Returns how many were cancelled.",
        "count = cancel_all_timers()",
    ),
    "cancel_timer": Help(
        "Cancel a timer started by after() or every(). Returns False when no "
        "running timer has that id.",
        "cancel_timer(timer_id)",
    ),
    "compare": Help(
        "Compare two values the way macro and trigger conditions do: `eq`, `ne`, "
        "`gt`, `lt`, `gte`, `lte`, `truthy`, `falsy`, and their aliases, with a "
        'number in text compared as a number. A mismatch is False, never an error.',
        'if compare(state.get("var.volume"), "gte", 50):\n'
        '    await devices.send("amp_main", "duck")',
    ),
    "delay": Help(
        "Wait a number of seconds inside an async handler, without holding up "
        "anything else.",
        "await delay(2)",
    ),
    "devices": Help("Send commands to the project's devices, and list them."),
    "events": Help("Send events that scripts, triggers and macros listen for."),
    "every": Help(
        "Run a function every so many seconds. Extra arguments are passed to it. "
        "Returns the timer's id, for cancel_timer. Start it at the top of the "
        "script, so it starts again each time the script loads.",
        "timer_id = every(60, check_status)",
    ),
    "isc": Help("Send events and commands to other OpenAVC instances."),
    "log": Help("Write to the Log view."),
    "macros": Help("Run the project's macros."),
    "on_event": Help(
        "Run the function below when an event matching the pattern fires. `*` "
        "matches any part of a name. The function takes one argument, the Event.",
        '@on_event("ui.press.btn_power")\n'
        "async def handle(event):\n"
        '    log.info("Power pressed")',
        snippet='on_event("${1:event_pattern}")\nasync def ${2:handler}(event):\n    ${0}',
    ),
    "on_state_change": Help(
        "Run the function below when a state key matching the pattern changes. "
        "`*` matches any part of a key. The function takes the key, the old value "
        "and the new value.",
        '@on_state_change("var.volume")\n'
        "def on_vol(key, old_value, new_value):\n"
        '    log.info(f"Volume: {new_value}")',
        snippet=(
            'on_state_change("${1:key_pattern}")\n'
            "def ${2:handler}(key, old_value, new_value):\n    ${0}"
        ),
    ),
    "plugins": Help("Call the methods a running plugin offers to scripts."),
    "state": Help("Read and write state: variables, device values and panel overrides."),
    # --- devices ---
    "devices.send": Help(
        "Send a command to a device and wait for it to run. `params` is a dict. A "
        "command that does not run raises, in the words Send Command shows for it.",
        'await devices.send("projector1", "power_on")\n'
        'await devices.send("switcher", "set_input", {"input": "hdmi1"})',
    ),
    "devices.list": Help(
        "List the project's devices, each as a dict with its `id`, `name`, "
        "`driver` and `connected`.",
        "for device in devices.list():\n"
        "    log.info(f\"{device['name']}: {device['connected']}\")",
    ),
    # --- state ---
    "state.get": Help(
        "Read a state value. Returns `default` (None unless you give one) when the "
        "key is not set.",
        'volume = state.get("var.volume", 50)\n'
        'is_on = state.get("device.projector1.power")',
    ),
    "state.set": Help(
        "Set a state value: text, a number, True or False, or None. Panels, macros "
        "and other scripts see it at once. A `system.` or `isc.` key belongs to "
        "OpenAVC and a list is not one value, so either is refused and shown on "
        "the script's row. A `device.` value lasts until the device's next report.",
        'state.set("var.room_active", True)\n'
        'state.set("var.current_source", "HDMI 1")',
    ),
    "state.delete": Help(
        "Remove a key, so it reads as not set. `state.set(key, None)` keeps the key "
        "with no value. A `system.` or `isc.` key cannot be removed.",
        'state.delete("var.last_error")',
    ),
    "state.get_namespace": Help(
        "Read every key under a prefix, as a dict.",
        'projector = state.get_namespace("device.projector1.")',
    ),
    # --- events ---
    "events.emit": Help(
        "Send an event. Scripts, triggers and macros listening for it run. A name "
        "starting with `system.` is OpenAVC's own and is refused.",
        'await events.emit("custom.source_changed", {"source": "hdmi1"})',
    ),
    # --- macros ---
    "macros.execute": Help(
        "Run a macro by its id, wait for its last step, and get back how it ended: "
        "\"completed\", \"failed\", \"cancelled\" or \"skipped\". The wait counts "
        "toward the 30 second limit on a handler or timer, so use start for a long "
        "macro.",
        'if await macros.execute("system_on") != "completed":\n'
        '    log.warning("System On did not finish cleanly")',
    ),
    "macros.start": Help(
        "Start a macro by its id and carry on at once, without waiting. It runs to "
        "its last step however long it takes. Call it from a handler or a timer, "
        "not from the top of a script.",
        'macros.start("system_off")',
    ),
    # --- log ---
    "log.info": Help("Write an informational line to the Log view.", 'log.info("Room is ready")'),
    "log.warning": Help("Write a warning to the Log view.", 'log.warning("Projector lamp is low")'),
    "log.error": Help("Write an error to the Log view.", 'log.error("Display did not answer")'),
    "log.debug": Help(
        "Write a debug line, shown only when the log level is Debug.",
        'log.debug(f"Volume is {state.get(\'var.volume\')}")',
    ),
    # --- isc ---
    "isc.send_to": Help(
        "Send an event to one other instance.",
        'await isc.send_to("lobby-instance-id", "custom.all_off", {"zone": "building"})',
    ),
    "isc.broadcast": Help(
        "Send an event to every connected instance.",
        'await isc.broadcast("custom.fire_alarm", {"zone": "all"})',
    ),
    "isc.send_command": Help(
        "Run a device command on another instance's equipment and wait for its "
        "answer. That instance has to allow the command in its remote-command "
        "allowlist. Raises isc.RemoteError when it refuses, ConnectionError when it "
        "cannot be reached and TimeoutError when it does not answer.",
        'await isc.send_command("lobby-instance-id", "display1", "power_off")',
    ),
    "isc.get_instances": Help(
        "List the other instances this one has found, each as a dict with its "
        "`instance_id`, `name` and whether it is `connected`.",
        "for peer in isc.get_instances():\n"
        "    log.info(f\"{peer['name']} connected={peer['connected']}\")",
    ),
    "isc.RemoteError": Help(
        "What send_command raises when the other instance answered and refused: its "
        "allowlist denied the command, it is already running as many remote "
        "commands as it accepts, or its device reported a fault. The fix is on that "
        "instance.",
        "try:\n"
        '    await isc.send_command("lobby-instance-id", "display1", "power_off")\n'
        "except isc.RemoteError as e:\n"
        '    log.warning(f"The lobby refused it: {e}")',
    ),
}
