# OpenAVC v0.37.2

Driver commands that send a file to a device or bring one back, simulation that stays on through a server restart, and panels that show a plain sentence instead of a driver's own error text.

## UI Builder

* A fader with no unit shows its value with no unit, as sliders and gauges do, and the Unit field hint reads "e.g. dB, %". Opening an existing project gives each fader with no unit the "%" it was showing, so panels look the same.
* A new custom control or plugin control no longer has a label it never draws, so the page review stops warning about it. Opening an existing project removes that label from custom controls, plugin controls and clocks. Nothing on a panel changes.

## Programmer

* Send Command on a device page starts a choice on the driver's default, else its first option, and a yes/no on its default, else No. A required choice with no default starts empty, and Send waits until one is picked.
* A device command inside a script that gets no answer shows in the Scripts view as that script's own error, at the line that sent it. Only a script that runs past its 30 seconds reads "timed out after".
* A monitor added for a channel's reading right after the device connects is named the way the device names the channel, as the Label column is. Monitors saved before keep their names.
* A plugin can check its settings before they are saved. When the Programmer or the AI assistant saves settings the plugin could not use, nothing is saved and the message names the part and what is wrong with it. A problem already in saved settings, from an older project for example, does not stop an edit being saved; the Programmer names it once. For plugin authors: define `validate_config(config)` on the plugin class.
* Smaller fixes: Change Setting names the device and the setting when the driver fails, with the details in the Device Log; a Quick Action whose command did part of its work shows the driver's explanation, as Send Command does; the AI assistant no longer keeps a driver's, the operating system's or a library's error text in its chat history.

## Panel

* When a driver fails for a reason of its own, a panel button, a macro started from a panel and a custom control's command show a sentence such as "Lobby Display couldn't run Power On. Contact support." instead of the driver's error text. The Programmer's run history, Send Command, OpenAVC Cloud, another OpenAVC system and the AI assistant say "Lobby Display couldn't run Power On. The Device Log on its page has the details." A refusal the device words for the reader still shows as written.
* A control that calls a script shows the same sentence when a device command in the script fails, and "This control couldn't finish. Contact support." when the script itself fails, with the details in the Scripts view.
* A custom control that listens for device errors gets "Lobby Display reported an error. Contact support." Panels no longer receive script errors.

## Device control and drivers

* **Commands that take a file or hand one back.** A driver command can take a file, such as a firmware image or a certificate, or return one, such as a configuration backup. Send Command on the device page shows a Choose file button, says before sending when a file is too large or the wrong kind, and shows the upload and the device taking it, with Stop. A file brought back gets a Download button. These commands run from the device page and from a device audit; a macro, trigger, panel button, Quick Action, script, plugin, OpenAVC Cloud or the AI assistant is told the command runs from the device page. OpenAVC keeps no copy of the file, the Device Log and an audit report show only its name and size, and large files work through Cloud Remote Access.
* A command that restarts the device, such as a restart, a firmware update or a new certificate, no longer reads in the Device Log as a connection that keeps dropping, and a device still restarting when its expected time runs out is not retried less often.
* A UDP device whose network has gone, such as an sACN or Art-Net stream with its cable pulled, logs the first error and one line when datagrams go out again with how many failed, instead of a warning for every datagram.
* For Python driver authors: a command parameter of type `file` (with `accept` and `max_bytes`) and `returns_file`, described in Creating Drivers. A driver that declares them installs on OpenAVC 0.37.2 and later.
* Smaller fixes: on Windows, a device audit no longer counts a value the driver set from its own settings as reported by the device when the device answered within a few milliseconds.

## Discovery

* A driver can identify a device that answers a discovery question by broadcasting to a port of its own instead of replying to the computer that asked. The Driver Builder's UDP probe has a Reply port field (`reply_port` in a driver file), and OpenAVC before 0.37.2 skips the discovery checks of a driver that uses it. When another program on the computer holds that port, the scan says so in its warnings and runs the rest as usual.
* A Discovery card is headed by the name the device will be added under, and names the matched driver inside the card. With AV only ticked during a scan, the list says no devices match the filters yet and that the scan is still identifying devices, instead of showing nothing.

## Simulator

* **Simulation comes back after a restart.** A simulation running when the server stops (a restart, an update, a power cut) starts again when the server starts, on every install type, and the devices go straight to their simulators. It stays on until Stop Simulation. If it cannot come back, the devices connect to the real equipment and the Programmer says why, with Start Simulation and Turn Off Simulation.
* While simulating, a bar across the top of every Programmer view says so with a Stop Simulation button, panels show a Simulated tag in the bottom-left corner, and the system's page in OpenAVC Cloud shows Simulating beside its name.
* Open Web UI opens the real device's page at the address in the project, simulating or not. A device that finds its own web page gets its button once simulation stops.
* Smaller fixes: a simulated device with Pushes state changes on sends only a value's own notice when one is written for it, as the device does; on Windows, a simulated device that also takes UDP commands no longer fails to start now and then.

## Updating

* A simulation running when you update to this version stops with the update. Start it once afterwards and it stays on from then on.
* A script that raised an ordinary error to put text on a panel now shows "This control couldn't finish. Contact support." Raise `PanelMessage` with the text instead (`from openavc import PanelMessage`).
* DOMPurify is updated to 3.4.17 for published security advisories.
* Project format moves to 0.16.0. A project saved here still opens on v0.37.1.
