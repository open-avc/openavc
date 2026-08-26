# OpenAVC v0.31.0

Long dropdown lists in the Programmer now have a search box, so picking a command on a device that has two hundred of them no longer means scrolling. The Video Panel plugin, which shows live video from a camera or a switcher on a touch panel, gets a setup screen of its own, accepts more kinds of video address, and plays on panels in the room as well as over the cloud connection.

## Search boxes in the Programmer

Anywhere the Programmer asks you to pick from a list, that list has a search box at the top: the UI Builder, macro steps, plugin settings, the Driver Builder. Only the device page had one before.

* Type to narrow the list, arrow keys to move through it, Enter to choose. Escape closes the list without closing the dialog behind it.
* A command shows its friendly name with the command id underneath, on every screen.
* A command from a driver you have since removed is shown rather than leaving the field blank.

## Video on a touch panel

The Video Panel plugin shows live video on a panel page, from an IP camera, a video switcher output, an encoder, or anything else that publishes a stream. Install it from **Plugins** in the Programmer.

* **Video sources are set up on a Video Streams page**, which appears in the Programmer's left sidebar below Settings once the plugin is installed. Sources your devices already publish are listed for you under Found on your devices, so you do not have to track down an address. Anything else you add by hand.
* **The source list says why a source is unavailable.** Picking a source for a Video Stream element names the device setting that is missing and gives you the field to fill in. Sources are grouped by device, and a device that is powered off can still be picked, so you can build a page before the room is live.
* **An address can be RTSP, SRT, RTMP or HLS.** The OpenAVC server connects to it, not the tablet, so it has to be reachable from the server. Pulling a stream needs nothing opened on your firewall.
* **Video plays on panels in the room and over the cloud connection.** On the local network it arrives over WebRTC, and OpenAVC opens the UDP port the plugin needs for it. Everyone watching from outside the building shares one allowance, so there is a limit on how many can watch remotely at once. Panels in the room do not use it.

## Devices

* **A driver can mark settings as Advanced**, and Add Device and Edit Device put those in a collapsed group with their defaults filled in, so the fields you need to get a device talking are not buried among them. The vMix driver is the first to use it, for its four SRT ports.
* **A driver can say why a video preview is not showing**, naming the setting that is missing and where to find it.

## Plugins

* **Updating a plugin reaches panels that are already open**, without anyone reloading the tablet by hand.
* If you write plugins: a plugin can declare the network ports it needs and which of its addresses carry video. Both are in the plugin developer guide.

## Before you update

* Linux and Raspberry Pi: a firewall port that a plugin asks for opens at the next restart of the OpenAVC service, not the moment you enable the plugin.
* OpenAVC Cloud support sessions, where you give OpenAVC staff temporary access to one system from the portal, need that system on v0.27.0 or newer. A system on v0.26.0 or older stops at its own sign-in screen.
