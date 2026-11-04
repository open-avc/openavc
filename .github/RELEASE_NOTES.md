# OpenAVC v0.39.0

This release adds per-state colors and pictures for Containers and Images, Page Navs that light up like tabs, and a choice of which devices to simulate. SSH-controlled devices now connect on every install type, updates and driver installs work on networks that inspect encrypted traffic, and a device put to sleep on purpose reads Asleep instead of offline.

## UI Builder

* New: a Container's color or background picture can change with a state, set in its Appearance card. The controls inside are not dimmed by the Container's state.
* New: an Image can show a different picture for each state, set in its Appearance card, so a script can change a panel's background for a holiday or an event. A state with no picture, and a device that can't be reached, show the Image's own picture. A Camera Preset's Appearance card also gains the Image field.
* New: a Page Nav lights up while its page is showing, so a row of them works as tabs, and it takes Appearance like a button. **Light up while its page is showing** is on for Page Navs placed from now on; tick it on older ones.
* Fixed: a button or Container with a faded background picture and an Appearance binding no longer moves when its state changes.

## Programmer

* New: choose which devices to simulate while the others stay on real equipment. The choice survives a restart, and editing a device or disabling and re-enabling it keeps it.
* New: `macros.start(id)` in a script starts a macro without waiting for it, so a long macro run from a script timer runs to its last step instead of being stopped at the 30-second script limit. A handler or timer that runs past the limit now shows "timed out after 30s" in the log.
* Device Audit: Continue on the Commands step first names any suggested command not tried yet and any command sent with no answer to "Did it happen?". The network check follows a redirect on the same address and reads the page it leads to. Power cycle and cable pull results now count values a driver reads once at connect, and replies a driver read with a JSON rule are no longer counted as unread.
* Fixed: schedules run in the site's time zone on appliance hardware and in Docker, and the schedule editor shows the system's time zone. A schedule set inside the hour the clocks go back no longer fires twice.
* Fixed: after a restart or update, a Programmer left open shows each device online as it connects, and its first change saves instead of failing with a conflict. On a system with a Programmer password, the tab first asks you to sign in again.
* Smaller fixes: after an update, the Programmer and Simulator open the new version instead of a copy the browser kept; when the OpenAVC server stops answering, the Programmer says so instead of showing "Failed to fetch"; a Python driver imported under another file name, such as a second browser download, opens in Code; a device controlled over HTTP no longer fills the log and the Dashboard's Recent Activity; the Simulator's Protocol Log shows what a device sent before the window opened; a variable's Allowed Values box takes a new line on Enter.

## Panel

* A slider or fader now sends while it is dragged: the first movement at once, then one command per Rate interval, and the value it stops on last. When a device answers more slowly than the rate, the values in between are skipped, so the device follows the finger and stops where it was let go. On release only is unchanged.
* Fixed: a panel left open across a restart or update shows each device online as it connects.
* Fixed: a macro's progress on a panel no longer reads "Step 1 of 0" while the macro waits.

## Device control and drivers

* New: a device put to sleep on purpose reads "Asleep. Power On wakes it." on its page and on the panel, is listed as asleep on the Dashboard, and raises no alert. Power On stays usable while its other controls are greyed out. If the device does not come back after Power On, the reason it is offline is shown.
* SSH-controlled devices now connect on every install type. The Docker image and the Linux installer include the SSH client, and appliance hardware installs it after this update once it can reach the internet. When the client is missing, the device page says how to add it.
* New: OpenAVC makes its own SSH key and shows it under **OpenAVC SSH key** on the page of an SSH device that can sign in with a key. Add it to the device's user account and choose key sign-in. A blank private key path now means this key.
* New: an **SSH options** box in Edit Device, under Advanced, one OpenSSH connection setting per line. A device whose SSH server offers only older encryption settings now shows what it offers and the line to add there, instead of retrying forever.
* Fixed: SSH devices connect from Windows 10 and other computers with an SSH client older than OpenSSH 8.5. On Windows, a device password containing &, |, <, > or ^ is now sent exactly as typed (part of such a password could run as a command on the OpenAVC computer).
* Fixed: Skip if device is offline and group commands now send a command that works while the device is offline, such as a Wake-on-LAN Power On.
* Fixed: a device dropped for missed keep-alive probes comes back online as soon as it answers. A reconnect that never answers is now retried after max_failures times the probe interval (60 seconds at the defaults) instead of about 40.
* Fixed: on Linux with ufw or firewalld on, a device that sends its own updates to OpenAVC now gets them through. OpenAVC opens those ports in the host firewall when its service starts.
* Smaller fixes: a device whose IP address, port or login name was saved with a space before or after it now connects (passwords keep their spaces); changing Connection between SSH and telnet changes a Port left on 22 or 23 to match; a JSON reply that repeats a name keeps the values under every copy.
* Driver authors: new `read_back:` on a command, queries sent right after it so the new value shows at once instead of at the next poll. The Driver Builder edits it as **Reads its value back** on each command. Drivers using it still install on earlier releases, which read the value at the next poll.
* Driver authors: new `sleeps_device` on the command that takes a device off the network, naming the command that wakes it.

## Cloud

* Fixed: Unpair in the Programmer now removes the system from the cloud portal as well, so it no longer shows offline there or raises alerts. If the cloud can't be reached, the system unpairs anyway and the Programmer says to remove it in the portal.
* Fixed: the cloud assistant no longer sees a plugin's passwords and keys. It reads them as `***` and leaves them as they are when it changes a plugin's settings.

## IT and security

* Updates, Browse Drivers and driver and plugin installs now work on networks that inspect encrypted traffic. OpenAVC's own internet connections trust the computer's certificate store, so an authority installed by Group Policy, MDM or `update-ca-certificates` needs no setting. A proxy set on the service with `HTTPS_PROXY` is used for those connections, and equipment at a local address is always reached directly. The Network & Security Cut Sheet has the steps for each install type.
* New: on Windows, an update runs its installer only if the installer is signed by OpenAVC LLC. Anything else is refused before it runs, and the Updates view says why.
* Fixed: driver and plugin installs now take files only from the official catalogs' main branch, and a download that redirects is checked again at each step.
* Fixed: a driver's sign-in password and any token it saves are masked in the in-app log.

## Updating

* Project format stays at 0.16.0, so a project saved on this version opens on v0.38.0 and v0.37.2.
* Docker: to run schedules in the site's time zone, add the `/etc/localtime` line or a `TZ` setting to an existing compose file (Deployment guide, Time zone). The image now includes the SSH client and time zone data.
* Linux: the installer now works on a computer whose only Python is 3.14, such as Fedora 43. A Linux, Pi or Docker update downloads one new package, `truststore`.
