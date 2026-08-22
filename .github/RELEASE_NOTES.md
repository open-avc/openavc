# OpenAVC v0.29.0

OpenAVC v0.29.0 shows why a child entity is offline instead of only that it is, shows an error on the panel when a command fails, and stores the admin password as a hash. Driver version requirements are now checked wherever you install a driver.

## Child entities

Child entities show why they are offline:

* **Not responding.** The device lists the endpoint and it is not answering.
* **Service fault.** The endpoint answers, but the function it performs is not running.

* Offline endpoints sort to the top of the list and can be filtered to.
* The type tab shows how many are down, and the device page lists them.
* A child's own values, such as a port's input and volume, are shown on the device page.
* Each list is titled with the driver's name for it, such as Displays or Outputs.
* YAML drivers can set `online` on a child.

## Panels

* When a command fails, the panel shows why instead of doing nothing.
* Repeated presses on the same control leave one message, and it does not cover the control that was pressed.
* Messages can be turned off per room.

## The admin password

* The admin password is stored as a scrypt hash instead of plain text. Anything able to read `system.json` previously had a working credential, including scripts and plugins.
* Existing installs convert on first start. Your password does not change.
* The password can no longer be read back from `system.json`. To replace one nobody knows, clear the field and run first-run setup again.
* Linux and Raspberry Pi: the privileged helper is now in the release archive and updates with the system, instead of shipping only in the Pi image.

## Drivers and updates

* Driver version requirements are checked when you install from the catalog, upload a file, import a `.zip` bundle, or save in the Driver Builder. Only catalog installs checked before.
* An empty release list from GitHub now fails the update check instead of reporting the system up to date.
* A device's last error clears when it stops reporting one.

## Programmer

* Dashboard tiles open the view they count. The Cloud tile shows Not set up and opens Cloud Connection. Triggers is now Macros.
* Macro steps and triggers that will not run are marked in the editor and in the macro list. They still save.
* Scanned devices show their banner as text instead of raw bytes.
* Portrait pages are checked against a portrait screen.

## Also in this release

* Assets in subfolders are kept in backups, exports, duplicates and imports.
* `isc.` state keys are read-only. Use a `var.` key and the Shared State Pattern to send a value to peers. Reading and binding are unchanged.
* The Simulator UI responds while a control is being dragged.
* Control minimums went up slightly for the fader, slider, list, level meter, keypad, select and text input.

## Before you update

* Staff support needs v0.27.0 or newer. Systems on v0.26.0 or older will stop at the system's sign-in screen.
* The admin password converts to a hash on first start. Rolling back below v0.29.0 means setting it again.
* Community drivers that report child faults need this version.
* No project format change.
