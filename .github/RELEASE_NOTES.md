# OpenAVC v0.38.0

This release adds Report a problem to device pages, a Programmer password reset for every install type, and a message when a Remote Access session ends. Discovery scans no longer connect to ports in use by connected project devices.

## Programmer

* New **Report a problem** link on device pages: under the device name, on the Offline banner, and after a failed command. It sends the OpenAVC team the device's settings (passwords hidden), address, recent messages, log lines and driver file. The dialog shows the report's contents before sending, and **Download** saves a copy instead. No cloud account or pairing needed.
* New **Send to OpenAVC** button on the Device Audit's last step, next to Download report.
* New **Forgot your password?** link on the sign-in screen, with the steps to reset the Programmer login: **Reset Programmer Password** in the Windows tray or Mac menu bar, a file named `openavc-reset-password` on a Raspberry Pi's SD card boot partition, or a file named `reset-programmer-password` in the data folder on Linux and Docker. The reset clears the username, password and API key, and new ones are set at the next sign-in.
* Windows: the OpenAVC Programmer and OpenAVC Panel shortcuts now open on the port set in Settings > Network. New **OpenAVC Tray** Start menu entry restarts the tray icon after Quit.
* Driver Builder: importing a YAML file with the ID of an installed driver now shows **Update Driver**, **Import a Copy** and **Cancel**. Update Driver replaces the installed driver and reconnects its devices with their settings kept.
* Smaller fixes: clicking a device tile on the Dashboard opens that device; Browse Community shows a connection error with Retry when the driver library can't be reached; Set Variable on a Stream Deck key, dial or touch zone lists only variables; removed the Panel Access card's instruction to close a Remote Programming session before opening Remote Panel.

## Device control and drivers

* A command parameter left out of a script, a REST call or an AI assistant command is now sent with the driver's default value. A required parameter left out is still refused.
* A device that sends a login prompt while its Password setting is blank now shows offline with "The device asks for a sign-in, and the Password setting is blank." Devices whose driver skips the login when no password is set can take up to 2.5 seconds longer to connect over TCP.
* Fixed: a standby command sent during polling could wake the device again.
* Driver authors: new `then_query:` on response rules, a list of queries sent as soon as the rule matches. Use it for a device that reports that something changed but not the new value. In the Driver Builder it is the Then Query list on each text rule. Drivers using it still install and run on earlier releases.
* Driver authors: a `liveness` probe is now paused while polling is paused by the device's state. Earlier releases send it in every state, so a driver with a state-paused poll needs `min_platform_version` 0.38.0 to add one. Python drivers override `_liveness_held()`.
* Driver authors: a `tcp_probe` is sent to a port with a login prompt only when its `expect` matches the prompt. New `ctx.hold_port` for discovery companions.
* Driver authors: new **Connections** section on each simulated TCP device, for simulating a device that accepts one control connection at a time. Drivers can set it with `simulator.single_client`, also on the Driver Builder's Simulation tab. `python -m openavc.simulator.validate` no longer reports a prompt after a reply as an error.

## Discovery

* Scans no longer connect to ports in use by connected project devices. Those devices are identified by their project driver, and displays and projectors that accept one control connection at a time stay connected during a scan.
* Probes are sent to each port one at a time. A port with a login prompt is sent only the probes written for that prompt.
* New **Probes** line on each Discovery card: for each port, whether it was in use, had a login prompt, and how many probes connected or read its greeting.
* Test Connection on a connected device reports the existing connection instead of opening a second one. The Device Audit's network check uses the same rules as a scan.
* Fixed: a device advertising a 169.254 address next to its real one showed as two cards.

## Cloud

* When a Remote Access session is closed in the portal or times out, the Programmer and panel now show "This remote session has ended. Open it again from the portal."
* Fixed: files over 10 MB, such as large asset library images or a project export, failed to load through Remote Access.
* The AI assistant's connection test and the cloud port diagnostics show a port held by a connected device as in use by that device. The AI's raw command test is refused on that port.

## Plugins

* When a plugin fails to start, the AI assistant shows the plugin's error only if the plugin raised `PluginRefusal` (new in 0.38.0). For any other start-up error, the reply refers to the Programmer's Log.

## IT and security

* **Send to OpenAVC** uploads the report to `https://cloud.openavc.com/api/v1/reports`, from paired and unpaired systems. Nothing is sent without pressing it. If the OpenAVC server can't reach that address, the browser uploads the same file. Added to the Network & Security Cut Sheet.

## Updating

* Project format stays at 0.16.0, so a project saved on this version opens on v0.37.2.
