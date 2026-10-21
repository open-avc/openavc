# OpenAVC v0.37.1

Fixes for 0.37.0, update buttons where installed drivers and plugins are listed, and Discovery scans that finish complete on busy networks.

## UI Builder

* Preview shows the dashed edge the panel draws on a button or camera preset whose press cannot reach a device that is up. Editing a macro or device group that a button runs updates that mark on every open panel.
* A button action that sets another control's Visible now offers Yes or No. A button saved with typed text keeps it until the choice is made again.
* Smaller fixes: keypad warnings name the keypad's own settings, Send without Enter and Send Delay.

## Programmer

* An **Update to** button with the new version number appears on each driver in Devices > Drivers > Installed that has a newer version in the community library, with a count at the top, and on the driver's detail page and the page of each device that uses it. The Plugins list and a plugin's page have the same. A version that needs a newer OpenAVC shows **Needs OpenAVC** and the version instead, there and on the Browse Community, Browse Plugins and Discovery cards.
* Refresh in Browse Community, Browse Plugins and the Plugins view loads the library's current list. An install just after a driver or plugin changes in the library checks the library again before refusing, and if it still cannot install, says to try again in five minutes.
* Saving is no longer refused with "Project was modified by another session" after renaming a child entity, uninstalling a plugin the project uses, saving a preset in a plugin's own screen, or queuing settings for a device that is not connected yet. Those queued settings show as pending straight away.
* The Device Log on a device page keeps that device's last 100 lines however busy the other devices are, and a failed connection's log line gives the same reason as the device card.
* Send Command shows the device's own reason when it refuses a command, and names the device and the command when a driver fails.
* The trigger editor's Evaluated now preview decides as the trigger does: text without regard to case, on, yes and 1 as Yes, and a number written as text as that number.
* Smaller fixes: turning off Monitored on a monitor that has a name, a normal range or a delay asks first; macro and trigger warnings name a field by its label; a reopened AI assistant conversation shows each tool call where it came in the reply; the Child Entities Label column shows the names the device reports without a reload; Device Audit writes whole numbers without ".0"; the log shows a username as typed outside its own device's traffic.

## Panel

* A panel shown inside another page, such as a signage player or a dashboard's web card, works again on a system with a Programmer password. Since 0.37.0 it showed System Offline.

## Device control and drivers

* With Settings > Network > Reach devices through set to an adapter that has only a self-assigned 169.254 address (a network with no DHCP server), Wake-on-LAN and sACN go out from that address.
* A device that stopped answering keeps "The device stopped answering" on its card while it reconnects.
* A wake command on a device with no MAC address says so: a panel or a macro says the device cannot be turned on until its setup is finished, and Send Command names the MAC Address setting.
* Pause Polling: a reconnect to a unit in standby no longer sends a connection check that would wake it.
* Starter projects: the Advanced AV Suite occupancy sensor reports, and the switcher, DSP and camera in the Advanced AV Suite and Conference Room starters take their commands, in simulation too. A system that already opened a starter keeps its copy.
* For Python driver authors: `WakeAddressError` and `CommandRefusedError` in `openavc.drivers.base` give a missing MAC address or a device's refusal in the driver's own words.
* Smaller fixes: setup action progress goes to the Programmer only; a product with OpenAVC built in no longer reports a finished command as failed when it restarts right after it.

## Discovery

* A scan on a busy network finishes complete, and keeps every device a driver's check has already recognized.
* A driver installed from Browse Community runs its own check on the next scan, without a restart.
* Install & Add asks for a driver's required settings, such as an API key, before adding the device.
* Smaller fixes: when two of a manufacturer's drivers match a device equally, the one for the model or kind of equipment it reported comes first; a device is no longer named after another device it lists in its own queries; a driver's check can name a device from its answer to a second question.

## Simulator

* Stop Simulation clears what the simulator reported, so a device's values stay blank until the real equipment reports its own.
* A Generic device's command with a value, such as `VOL {level}`, changes the simulated value and replies.

## Updating

* aiohttp is updated to 3.14.4 for published security advisories.
* Project format stays at 0.14.0.
