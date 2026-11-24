# OpenAVC v0.41.0

If OpenAVC fails to start, the start is now retried automatically, then a **Start again** button is shown. Updates and rollbacks now shut the system down the same way a restart does. If a save can't be applied, the reason is shown and the running project is left as it was. Stream keys, passwords and PINs entered in macro steps and control actions are now stored on the system, like device passwords. HTTP commands that get an error response from the device now show as failed.

## UI Builder

* Validate now checks toggle Off Actions, Long Press Actions, matrix destination routes, macro steps that go to a deleted page or device group, and icon names that aren't in the panel's icon set. The Validate button shows the number of issues.
* Changing a control's ID now updates the macro triggers on that control and `$ui.<id>.` references. Deleting a page now removes the actions and macro steps that go to it and removes it from page groups.
* Panel Settings: when Accent Color or Font Family is blank, the theme's accent and font are shown in the field. **Theme font** is the first Font Family option.
* New limits on number fields: lock PIN up to 6 digits, dialog Width and Height 5 to 100%, Shown decimals 0 to 20, idle timeout up to 24 hours.
* Fixed: Ctrl+Shift+Z redoes; an edit right after Undo, Redo or a canvas move gets its own undo step; Ctrl+P and Ctrl+E work with Caps Lock on; Escape in the ID field cancels a rename; page background pictures set to Stretch fill the page; dialogs stay open in Preview; leaving the UI Builder right after an edit no longer shows a "Leave without saving?" prompt.

## Programmer

* New: unsaved changes are kept through a page reload, with the choice to restore or discard them. Logins typed into device settings are not kept, and the devices that need them entered again are listed.
* If a save can't be applied (for example, the disk is full), "Not applied:" and the reason are shown, and the running project and project file stay unchanged. If a save doesn't reach the server, a banner across the top of every view shows "Not saved yet. Trying again in 2 seconds." while it retries, then "Save failed" with a Retry button.
* Saving a driver in the Driver Builder or Code view no longer overwrites a copy someone else saved in the meantime. Save then has three choices: Reload Theirs, Save Mine Over It, or Keep Editing.
* Project events now use the `custom.` prefix. The `system.` prefix is reserved for OpenAVC's own events. Emit Event steps with a `system.` name are refused and are highlighted in the Macro editor, Validate and the script editor with the name to use.
* If opening a project from an earlier release changes anything in it, each change is listed on the Dashboard with an **Open Validate** button.
* New **Actions** section on each plugin's page in the Plugins view, with a button for each plugin action.
* Plugin routing matrix: routed crosspoints are lit, rows and columns use the plugin's own names, and clicking a routed crosspoint unroutes it.
* Scripts: the script editor now autocompletes every name and event available to scripts. Script errors, such as a timer callback that throws, are shown on the script's row in the Scripts list. Scripts and Set Variable steps can no longer write `system.` or `isc.` keys. Script templates and Convert to Script now produce scripts that load.
* Fixed: driver search matches every word typed, in any order; Send Command sends numbers and Yes/No values the same way macros do; child dropdowns update when the device reports new names; an open device page goes back to Select a device when that device is deleted somewhere else; a setup wizard reopens after a restart signs you out; Stop Simulation pressed while simulation is starting now stops it; uptime is correct on computers that set their clock from the network.

## Panel

* Saving the project or a panel reconnecting no longer closes open dialogs and sidebars or replays the page transition. Rotating a tablet keeps them open too.
* Macro buttons show as busy, and progress labels show the current step, after going back to the page or opening a dialog while the macro is running.
* Value comparisons on the panel now work the same as macro conditions: text matches regardless of case, and `true` matches `on`, `yes` and any number other than 0. For example, an Appearance or Status LED color set for `on` now lights when the device reports `ON`.
* Sliders and faders with an output range no longer jump after the device reports back. Gauges, sliders, faders and level meters show `--` when the device reports a value that isn't a number.
* Icons no longer disappear from a panel left open through a restart or update. Icons saved under an old icon name now display.
* Fixed: a quick double tap on a Page Nav with a page transition no longer lands on the wrong page; a tapped list row highlights immediately; dimmed controls stay dimmed with entry animations on; keypads and dropdowns on a closed dialog no longer send; an internal error on a press shows "This control couldn't finish. Contact support."; meeting timers end on 00:00.

## Devices and drivers

* HTTP devices: a command or setting write that gets an error response (400 or higher) is now reported as failed. Send Command, panel presses and macro steps show that the equipment could not accept the command, and the HTTP status is in the device log. Live Test in the Driver Builder shows the status.
* Driver value maps now match the same way as the rest of OpenAVC: exact match first, then ignoring case, then as true/false or a number. For example, a reply of `on` now matches a map entry for `"ON"`.
* If a driver can't set up a device, the device stays in the device list with the reason on its Offline banner, and the rest of the space starts normally. A driver file that can't be read is skipped without affecting other drivers.
* Connection settings that can never work, such as a serial parity no port supports, are shown on the device card, and reconnect attempts stop until the device is edited. Serial settings typed the way manuals write them (`Even`, `None`, `RTS/CTS`) now work. Add Device now has Mark and Space parity, XON/XOFF and 230400 baud.
* USB-to-serial adapters: a device no longer connects to a different adapter that took its port name while its own adapter was unplugged. Two adapters with the same serial number are kept apart by port. An adapter that is slow to open no longer holds up other devices.
* If a login is refused after a Live Test or Device Audit, automatic reconnects stop until the login is changed.
* If a driver install, update or import fails, the existing driver is kept and the reason is shown. A driver in use by devices can't be deleted.
* Discovery: identified devices are listed under their driver's name and category. Ports in use by a project device are shown as "in use by" that device. Projectors and cameras found through PJLink or another shared protocol now also list the manufacturer's driver. Install & Add no longer adds a device without its driver.
* Driver Builder: Live Test raw probes use the driver's serial settings, HTTPS and TLS. Renaming a command updates everything that uses it.
* Driver authors: problems that used to appear only once the device connected, such as a response rule that writes an undeclared state variable or a reply map key that isn't quoted, are now caught when the driver is saved, for Python drivers as well as YAML. Installed drivers with these problems still load, and the Installed list shows what to fix. Simulated devices now use the driver's line ending, framing and device settings.
* Driver authors: new `self.spawn_task()` for background work that is stopped when the connection closes. Setup actions can now take a file, such as a project export. The BACnet/IP and KNX protocol libraries are now included with OpenAVC.
* Fixed: Generic device commands typed with their own line ending no longer get a second one; serial devices connect when Reach devices through is set to an adapter with no address; editing a device during a firmware upload waits for the upload to finish; settings queued for an offline device are kept until they're applied; offline reasons no longer include the device password.

## Starting, stopping and updating

* New: if OpenAVC fails to start, the start page shows the reason and a countdown, and the start is retried automatically up to three times. After that, the page shows the reason and a **Start again** button. Panels showing a space switch to this page (Android needs OpenAVC Panel 0.2.1 or newer). **Restart now** in Settings also shows the reason. If an updated version fails to start, the update is rolled back automatically.
* If a non-essential part fails to start, such as the cloud connection or a plugin, the space still starts and the Programmer shows a notice listing what didn't start.
* Updates and rollbacks now shut the system down the same way a restart does: System Stopping triggers run, persisted variables are saved, plugins stop and devices disconnect. Shutdown takes at most about 10 seconds, and every install type allows 20.
* The Windows tray icon and Mac menu bar show Starting, Stopping or Could not start, with the reason. `/api/health` returns 503 while starting or stopping and after a failed start.
* The Install button now shows the version it will install. While an update or rollback is installing, another update, a rollback or a restart is blocked, with a message showing the version being installed.
* On appliance hardware paired to OpenAVC Cloud, panel software now updates from the cloud after the panel has been idle for ten minutes. Pre-update backups there now include the project.
* Fixed: systems on a release candidate are offered the final release; a damaged update history no longer turns off updates; on Windows, Rollback goes back to the release you updated from instead of its release candidate; OpenAVC raises its own open-files limit, so projects with hundreds of network devices aren't stopped by the operating system's default; restarting on Linux while simulating no longer connects devices to real equipment during shutdown; old update downloads are deleted.

## Cloud

* If OpenAVC Cloud needs a newer version of OpenAVC, a notice on the Dashboard and the Cloud Connection page shows the version needed. Click it to open Updates.
* Fleet updates now install only a newer version. Systems already on that version or newer are left unchanged.
* Remote Access sessions now end on the system as soon as its cloud connection drops, and an open remote panel or Programmer page shows right away that the session ended.
* Deploying a template with "Update template content only" no longer overwrites changes saved on the system while the deployment was being prepared.
* Fixed: the ping diagnostic works on systems without a ping program, such as appliance hardware.

## IT and security

* Stream keys, passwords and PINs entered in macro steps and control actions are now stored on the system. Backups, exports, templates, cloud copies and panels no longer include them, and the editors show them as **Saved on this system**.
* Command parameters marked secret, or named `password`, are masked in every form and never shown in error messages, the log or the device traffic view.
* Revoking a panel, or switching Panel access to approved panels only, now blocks it from plugin panels (such as camera streams) right away.
* Logins in project backups made by earlier versions are encrypted at the next start. On Linux and macOS, backups that contain logins are readable only by the service account.
* Fixed: simulation no longer writes device logins to a temp file; the HTTP-to-HTTPS redirect no longer follows a malformed Host header; Device Audit reports mask passwords in driver files with any text encoding.

## Updating

* The project format is now 0.18.0. When an older project is opened, values outside the new limits are set to the nearest limit, project events are renamed from `system.` to `custom.`, and each change is listed on the Dashboard. Scripts and Node-RED flows that use an old `system.` event name need the new name.
* Behavior changes: HTTP error responses now fail the command; scripts and Set Variable steps can't write `system.` or `isc.` keys; `cancel_all_timers()` cancels only the calling script's timers; a slider or fader with only Output Min or only Output Max set now uses it; serial ports named `SIM:` are refused (use Start Simulation); a device whose USB adapter was replaced shows the old adapter as not plugged in until the new one is picked in Edit Device; macros set to Queue skip a held run after five minutes; `system.startup_errors` is removed.
* Node-RED: panel connections, and Node-RED connections without an API key, no longer receive trigger and plugin messages. Connect with an API key to get them. Macro nodes without an API key now get the same failure message the panel shows.
* Plugins with an ID that isn't lowercase letters, digits and underscores no longer load. Drivers that passed the Driver Builder's Test tab before may now fail it.
* Panels opened through Remote Access take a few seconds longer to load the first time than on 0.40.0. A fix is planned for the next update.
* Linux and Pi installs restart once more about 90 seconds after this update and download the new BACnet/IP, KNX and ZIP packages. Docker: if your compose file was downloaded before this release, add `stop_grace_period: 20s` under the service.
* Mac: after updating from 0.40.0 in the app, the menu bar shows its old wording until OpenAVC.app is quit and reopened.
