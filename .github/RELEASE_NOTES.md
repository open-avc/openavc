# OpenAVC v0.34.0

Panel text can be edited in place on the design canvas, master elements can be dragged and resized, and duplicating a container copies what is inside it. On the device side: SNMP control for rack and infrastructure equipment, Wake-on-LAN and datagram commands, and a restart window so a display that reboots when you power it on is not reported as a fault.

## UI Builder

* **Double-click text on the canvas to edit it in place.** What opens is the text you wrote, not the text on screen: a label drawing "Amp draw: 0.076 A" opens as `Amp draw: {value} A`. On controls with a caption, only the caption is editable, so a reading or a scale is not disturbed. Master elements and page navigation buttons are editable too.
* **A master element can be dragged and resized**, with the same handles, snapping and guides as any other control. Nudging one while a portrait arrangement is on screen moves it in that arrangement.
* **Duplicate, Ctrl+D and Duplicate All copy a container's contents.** References between copied controls follow the copy, and the whole duplicate is one undo step.
* Deleting an uploaded file or a custom control file that a panel still shows is refused, and the pages and controls using it are named. Listings state what uses each file. There is no undo for either.
* A part-typed number in the Layout fields is no longer applied to the next control you select.
* A monitored reading can state how many decimal places to show. Rounding is display only: the value that fires an alert is the value the device sent.
* Smaller fixes: a monitor tile's state key wraps instead of printing across the value, multi-state appearance names can be edited, an imported theme is selected after the import, and an image keeps its selection while its dimensions load.

## Programmer

* A controller that only accepts connections from itself says so on the startup banner and the setup screen, and shows the address that works. Machine names already ending in `.local` are no longer shown with it twice, and the self-signed certificate covers that name, so installing the certificate authority removes the browser warning.
* The sign-in screen no longer fills in the username `admin`, a wrong password says so, and a session that has ended is reported on the sign-in screen with unsaved edits noted as still open in the tab.
* **A boot that had to recover the project says so.** The Dashboard names the backup and the time it was taken, so work saved after that point is accounted for. The notice survives restarts until dismissed.
* **A script that fails while running is reported.** The Scripts list marks it and says how many times, and the editor marks the line that raised and names the exception type.
* A backup can be restored straight after it is created. The row shows the restore in progress and the message names the backup that landed.
* Pairing failures name the cloud that answered, and a self-hosted cloud's URL typed into the wrong field is refused instead of sending the token to cloud.openavc.com. Error banners across the IDE show the sentence the server sent rather than raw API error text.
* The AI Assistant pane states why the assistant cannot be used instead of offering a prompt box that fails.
* Panel Access points to Remote Panel in the cloud portal when you are working on a system remotely, instead of showing addresses that only reach the space's own network.
* Panels paired by QR code or a typed address remember the system they trust, so an HTTPS system that changes IP address does not have to be paired again. The panel app lists a system by its project name, spaces included.
* `auth.panel_lock_code`, `isc.auth_key` and `isc.discovery_enabled` are removed from `system.json`; nothing read them, and they are dropped from an existing file when it loads. A Programmer password or API key of only spaces is refused rather than stored, and both are trimmed.
* The Inter-System page names which of the two switches is holding the mesh down. A configuration change ends an authentication backoff, and a refused remote command keeps the reason the other system gave.
* Project format stays at 0.13.0, so a project saved here opens on v0.33.0.

## Panel

* **The panel lock PIN stays on the server.** A panel is unauthenticated, so everything it receives is readable by anything on the network. Panels now receive only whether a lock is set, and the attempt is checked by the server.
* A project's theme override reaches the page behind the controls, not just the controls. Preview and the design canvas show the same colour the panel does.
* A page with nothing on it says so instead of drawing the connection badge on black. A space built entirely from custom pages now reaches an appliance or Pi display.
* Panel errors identify disabled equipment by name and tell the occupant to contact support. Technical device IDs stay in the Programmer logs.
* A control that only sends a command takes the same dashed edge as any other unavailable control, and only when nothing it can reach is up.
* Smaller fixes: a panel opens the home page chosen in the project, a slider keeps its reading current after you let go, lock and idle settings apply in Preview, screen-reader button names match the state label, a paused device's dot is distinguishable from a working one, and a reading bound to a sub-unit's status no longer blanks when that sub-unit has something to report.

## Device control

* **SNMP v2c control**: GET, GETNEXT, SET and a walk, with retries. Python drivers only. The community string is sent in cleartext, and write permission is enforced on the device by the community it accepts. A device locked to v3, or set to a community you did not configure, reads as unreachable.
* **A command can send one UDP datagram** beside the device's main connection: a payload to a given host and port, or a Wake-on-LAN packet built from a MAC address in a config field. It needs no connection, so with `available_offline` set it wakes a display that has closed its network port. YAML drivers declare it in the Driver Builder's **Send over UDP** section; Python drivers get `send_udp` and `wake_on_lan`.
* **A command can declare how long it takes the device away for.** For that window the device reads as restarting with a counting-down message, rather than red with a network fault. `offline_reason` is left empty, so alert rules and automation conditions stay quiet about a display somebody just switched on.
* Equipment that holds its connection open but stops responding now goes offline and reconnects on its own, on TCP, serial and SSH.
* A serial device reached through an IP-to-serial bridge names the bridge when the bridge is down, and its line settings are sent again when the bridge reconnects.
* A device setting stays queued until the device reports the value back. If it reports a different value the setting stays queued and the device shows an error naming it. **Reconnect** flushes the queue.

## Driver contract

Drivers built on any of the following declare a minimum platform version of 0.34.0, so they become installable once a system is updated.

* An SSE event stream can be a subscribed session: the driver says where the session id comes from, subscribes it when the stream names it and again on every reopen, substitutes it into later commands, and ends it on disconnect.
* A JSON reply can route values into child entities, so a device that answers with one body holding every sub-unit is modelled with children rather than flat per-zone state.
* A JSON mapping can record whether a list holds a value, turning an array of flag names into one boolean per flag that a trigger can watch.
* A regex response rule can stay eligible after the JSON rules have read a body, for a device that reports an error outside any JSON key it publishes.
* A device's setup action, such as Test Connection, shows what it found. A check that ran but found the wrong answer ends red with the reason instead of a green tick.
* `DRIVER_INFO` written with a type annotation is read like any other.

