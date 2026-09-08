# OpenAVC v0.33.0

Panel idle dimming, panel display settings stored in the project file, Node-RED support and an events API. A large part of the rest is controls, macros and triggers showing the actual result instead of showing success.

## Panel display

* Panels dim after five minutes with no touch, to 20%, on hardware where OpenAVC drives the screen. On by default, in Settings > Panel Display.
* Panel brightness is set in Settings > Panel Display, with a minimum of 10%.
* The dim level is a percentage of the panel's brightness, not a fixed value.
* A dim level of 0 blacks the screen out. A touch restores it. The screen is never slept, because a slept panel does not wake on touch.
* **Stay bright while** holds full brightness against a state key, such as a system-on variable or a display's power state.
* The waking touch does not press the control underneath. There is a setting for the other behaviour.
* Brightness changes apply immediately while the panel is dimmed.

## Panel controls

* **Control text starts at 28px instead of 14px, on every control type**, and text inside a control follows the control's Font Size. Existing panels take the new size on next open. A tight layout may need more room, and **Validate** names any control that is now too small.
* A reading the device has not sent draws `--` with no handle. This is separate from the v0.32.0 unreachable mark.
* Readings from an unreachable device are dimmed and marked **last heard** on the device page. Panel controls still blank them.
* Failed presses are shown: a deleted macro, a run that failed while a schedule ran the same macro, a matrix preset or lock failure. Refusals nobody in the room can act on, such as a rate limit, are not marked.
* A condition wait that times out gives the reason on the panel.
* Toggle buttons show whether the thing they control is on, and crosspoints mark dead rows.

## Devices

* Sub-units of an unreachable device read as unavailable with a reason, and come back with the device. Bindings to them are kept.
* Declared sub-units appear when the device is added, not after its first connect.
* A driver can declare a position as a slot rather than a channel. Empty slots draw with a grey ring and are not counted as down. Driver contract change, `min_platform_version` 0.33.0; `at_atdm_0604a` 2.3.0 is the first driver using it.
* Per-channel readings can be monitored from the **Child Entities** table, with the type, unit and range the driver declares.
* `Device 'x' not found` is returned only when the device is absent. A driver's own failure is reported as one, writing an undeclared setting names the setting, and retrying a device that is not an orphan returns 409.
* A device group names a member that is not in the project, and deleting a device clears it from every group.
* `DELETE /api/devices/{id}` returns a `references` object naming the groups, macro steps, triggers, bound controls, master elements and scripts that still name the device. The delete confirmation lists the same.
* **Update** in Browse Community rebuilds the devices on that driver, with no restart.
* A paused device stays paused across a project save, a driver hot-reload or a simulator redirect.
* Project saves return before the fleet connects, and the devices come up behind the save.

## Macros and triggers

* Macro runs return `completed`, `failed`, `cancelled` or `skipped`, and the IDE's Test button shows which. **A clean run is `completed` where it was `executed`.**
* Trigger cards show **Ran OK** or **Last run failed** with the reason. `GET /api/triggers` returns `last_outcome` and `last_error`, and a `trigger.completed` WebSocket message covers each fire. **`POST /api/triggers/{id}/test` returns an outcome instead of `fired`.**
* State change triggers saved without an operator fire again. A room whose automation was dead will start running it after the update.
* A blank value for a required parameter returns a 400 naming the parameter, instead of a no-op reported as success. Free text is exempt. `NaN` and `Infinity` are refused on numbers.
* A sub-macro failure fails the calling step, so the parent's error policy applies, and cancelling stops the whole chain.
* Test saves pending macro edits before running.
* Runs from the IDE or the AI return "running" after 30 seconds instead of a failure. Triggers, scripts, plugins and panel presses still wait for the full run.
* `macro.skipped` covers an overlap or cooldown refusal, and `macro.cancel` over the WebSocket stops a run.
* Collapsed Run Macro steps name the macro, and a step calling a deleted macro is marked missing.
* A script whose top-level code runs past the load timeout is stopped, listed and counted in `system.abandoned_script_loads`.

## Programmer

* UI Builder undo history survives a device, a discovery, a cloud push or a reconnect touching the project. It is cleared only on a real collision, with a message.
* A Builder tab left open across a restart no longer overwrites later edits, and the 409 names the restart.
* Save As can replace a project saved under the same ID.
* Release Action can be set in any button mode, not only Tap.
* Server refusals are shown as a sentence rather than raw API error text, and a refused project import or save names the fields at fault.
* The Appearance card lists a state key's declared values before the device has reported.
* Sliders and faders keep their handles in the design canvas and the styling previews. Live Preview still shows missing readings as missing.
* Theme Studio rounding presets are 8px and 16px, and a saved preset is recognised.
* Smaller fixes: the canvas warning badge can be hovered to read it, renaming a control moves its placement, a plugin that fails to start is listed in the sidebar, discovery drops hosts a finished scan lost, Monitor controls fit on one line, a tab left open across an update reloads itself, a failed update check no longer reads as up to date, Save enables when a panel setting is the only change, Validate checks macro steps the same way as controls, and simulator cards size to their own contents.

## Projects

* Panel display settings and the device retry interval are stored in the project file, so they travel with a deployment or a cloud template. Project format 0.13.0.
* Network address and ports, credentials, cloud pairing, certificates and the update channel stay with the individual system.
* Custom panel themes travel with library saves, exports, imports, starter bundles and backups.
* Project bundles include drivers' simulator and discovery companion files.
* The Conference Room and Classroom starters send `set_input` to the PJLink projector, so source selection works.
* A system whose live project came from a starter no longer boots with orphaned devices after a bundle refresh.

## Integrations and API

* Node-RED support: `@open-avc/node-red-openavc` on npm, with `docs/node-red.md`. This is the release the event nodes need; state, commands, macros and variables work against any server. A flow announces itself with `?name=<name>`, which holds `system.integration.<name>.connected` while the socket is open.
* Events over the API in both directions. Subscribe with `?events=custom.*,ui.press.*` or an `event.subscribe` message; emit with `event.emit` or `POST /api/events`, restricted to `custom.*` for outside clients.
* A script handler for an event nothing in the project emits is badged "with no emitter".
* Alerts and resolves are stamped when this system saw the fault, so service report response times exclude transit delay.
* A resolved alert includes why it ended, so a removed or disabled rule is not recorded as a fault somebody fixed.
* Notify only is sent to the cloud, so the portal can show which rooms are opted out. The cloud side needs its own deploy.
* A Programmer password or username containing a non-ASCII character works over HTTP Basic and through `OPENAVC_PROGRAMMER_PASSWORD` and `OPENAVC_API_KEY`.
* A custom control or plugin panel file saved twice within one second is no longer served stale.

## Updates

* **Notify only** stops a system installing an update on its own, including one the cloud scheduled for a maintenance window. Installing by hand is unaffected.
* The "Auto-backup before update" toggle is gone. The backup is taken on every update.

## Upgrading

* Projects are migrated to format 0.13.0 on first open. Nothing needs re-authoring.
* Two API results changed: a clean macro run returns `completed` instead of `executed`, and `POST /api/triggers/{id}/test` returns an outcome instead of `fired`.
* `offline_reason` and `offline_detail` on a sub-unit are unset rather than an empty string, matching a device. A trigger comparing one to an empty string should compare it to nothing set.
