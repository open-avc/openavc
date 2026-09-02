# OpenAVC v0.32.0

Scans now find everything on the network the first time, and show what they have found while they are still running. An update that cannot reach the internet tries again instead of giving up. SSH connects to older AV gear, and dialog and hint text in the Programmer is easier to read on the light theme.

## Device discovery

* **A scan finds all the equipment on the subnet the first time.** On a large subnet a first scan could miss about half of it, and you only saw the rest on a second scan.
* **The device count climbs while the scan runs.** It used to stay at zero until the scan finished, then jump to the full number.
* **The progress bar keeps moving when nothing is being found.** A scan on an empty network no longer looks dead.
* **Devices the scan has removed disappear from the list.** Equipment that had left the network stayed on screen until you reloaded the page.
* **Discovery can tell apart models that share a command set.** Where a smaller model answers everything a larger one does, the larger unit was identified as the smaller one. A driver hint can now ask a question only the larger model answers.
* **New discovery hints no longer stop a driver installing on an older system.**

## Updates

* **An update that cannot reach the internet tries again.** It used to be discarded after one failure, leaving a system on its old version indefinitely and reporting itself healthy. It now retries on the next few restarts, up to three attempts.
* **The Updates page shows when an update did not finish**, naming the version and what stopped it.
* **Installing an update by hand on a system with no internet takes longer**, because the next two restarts each try it again. The offline instructions say so.

## Connecting to devices

* **SSH connects to older AV gear.** Equipment that still ships older algorithms refused the connection outright, even though its documentation says SSH works. OpenAVC now offers those algorithms alongside the modern ones, and a per-device setting turns that off.
* **A mismatched Connection and Port setting says so.** Changing one and not the other used to time out and report a network problem, pointing you at cabling and gateways.
* **A command that did part of its work says what happened.** A power cycle that cut power and could not restore it used to report that nothing happened at all.

## Drivers

* **A response rule can be gated on the device's mode.** Some devices answer a query whose result only applies in one of their modes, and that value reached the panel, alerts and scripts regardless. A response entry can now carry a condition and is skipped while that condition is false. The Driver Builder edits it.
* **The AVPro Edge AC-MX matrix driver v2.2.0 installs from this release.** Its extracted-audio readout now only appears when the frame is switching audio separately.
* **A Python driver's details open in the Programmer.** An installed and connected Python driver reported "not found".

## Touch panels

* **A new matrix keeps audio with video.** Ticking "move the audio with it" added an audio readout to every destination and there was no way to remove it again, so outputs you had never touched showed a source their video was not on. New matrices no longer tick it, and unticking now removes the readout. Existing matrices are unchanged.

## The Programmer

* **Dialog and hint text meets the contrast standard on the light theme.** Twenty-nine colors were also missing their definitions, which left some dialogs drawing sage green on white and some small text at the wrong size.
* **Pressing Enter right after opening a picker keeps the current value.** It used to clear the field, and in the device pickers it discarded the command and its parameters too.
* **The Programmer warns when a script handles an event nothing sends.** The handler sat there and never ran, with nothing on screen to say why.

## Plugins

* **A plugin that needs a firewall port gets one on Linux and Raspberry Pi.** The port never opened before.

## Other fixes

* **Turning on HTTPS no longer locks you out of the system's own screen.** A browser on the machine is served the app instead of being sent to a certificate it cannot accept. This matters on a panel appliance, where there is no other way in.
* Security updates to aiohttp, cryptography, python-multipart, and the code editor's HTML sanitizer.
* The Cloud Connection page pointed at the wrong place for a pairing token. They are on the portal's Systems page.
* Several screens asked for a laptop, phone or tablet where what you actually need is a browser on the same network.

## Before you update

* **Updating from v0.30.0 or older upgrades your project file.** A control's Call script function action now calls the function and passes it values. It used to send an event named after the function. A function that handles an event is not called this way.
* **OpenAVC Cloud support sessions need the system on v0.27.0 or newer.** A system on v0.26.0 or older stops at its own sign-in screen.
