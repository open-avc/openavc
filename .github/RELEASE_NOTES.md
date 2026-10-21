# OpenAVC v0.37.0

A big release: new settings for which networks can reach OpenAVC and which network it uses to reach devices, offline detection for devices that stop responding, YAML driver scaling, settings filled in from the device and paused polling, macro control of every panel element, and a locked-down data folder on Windows.

## Programmer

* New setting under Settings > Network: **Accept connections from**, with Any network, one network adapter, or This computer only. It replaces Bind address and can be changed on Windows, macOS, Linux and Raspberry Pi installs; on Docker, set it in the compose file. The default is Any network.
* The chosen adapter is followed through DHCP address changes, and panel addresses, the QR code and the Panel app's list use the chosen network. The computer's own screen, the tray and menu bar apps and OpenAVC Cloud remote access work with every option.
* If a change would cut off the browser you are using, the Programmer shows the address to open next. If nobody opens it there within 10 minutes, the setting goes back to the previous choice.
* **Control interface** is renamed **Reach devices through** and is picked by adapter name. It now covers every connection to a device, including Python drivers' own connections, SSH, MQTT and Wake-on-LAN. Plugins can use it; Stream Deck and Video Panel do.
* With Reach devices through set to Every connected adapter, Wake-on-LAN, sACN and Inter-System discovery go out on every adapter, which fixes them on Windows computers with WSL or Hyper-V. Pick one adapter to keep sACN on the lighting network.
* Windows virtual machines on Hyper-V or Azure now list their network adapter in Settings > Network, and Discovery scans it.
* True / false values show as Yes / No throughout the Programmer. Saved values do not change.
* Add Device and Edit Device do not save with a required setting (red asterisk) left blank. Changing a device's driver keeps its address and login.
* Test Connection tests the address and port the device connects to.

## Panel and UI Builder

* Macros and scripts can set the color, opacity, visibility and label of every panel element, not just buttons. A new **Clear** option on the Set Variable or Control step puts the element back the way it was designed.
* Sliders, dropdowns, text boxes, status LEDs and camera presets show the unavailable mark when their device is offline.
* Fixed: an element kept its Appearance color after the condition stopped being true, so an input picker could show several inputs lit at once.
* Panel text has the same line height on every device, and keypads, dropdowns and text boxes use the theme font. Text on Windows panels is about 10% tighter where no Line Height is set.
* Keypads: new **Mask Display** option for PIN entry. "Send after all digits entered" is renamed **Send without Enter**, with a warning when Send Delay is under 1500 ms. On submit can save the digits to a variable.

## Device control and drivers

* A device that stops responding is marked offline (Not responding) after three polls in a row get no reply, over TCP, UDP and OSC. Commands still go out while the connection is open, so a display that stops responding in standby can still be turned on.
* When a device's replies do not match its driver, such as a device on the wrong port, a warning with the last reply is written to its log.
* When a device rejects its username or password, it is marked offline with Authentication failed, and automatic reconnects stop until the login is changed or Reconnect is pressed.
* YAML drivers: **scale** and **offset** convert a device's raw numbers, for example gain sent as 000 to 060 shown as -18 to +42 dB.
* YAML device settings can send the device's own word for a value, such as ON and OFF.
* **Filled In From**: a setting such as the model, channel count or MAC address is filled in from the device when it connects.
* **Pause Polling**: polling, a single poll query or a start-up step can wait while the device is in a state such as standby.
* A response rule can read one reply that lists every output's route or mute, and update all outputs from it.
* YAML drivers send start-up and poll commands at least 50 ms apart. HTTP drivers now wait the Inter-Command Delay between requests.
* Discovery: a device added from a scan gets the MAC address from the scan. When more than one driver matches a device, all of them are offered, the manufacturer's driver first.
* New driver in Add Device: **OpenAVC built-in product**, for a manufacturer's product that has OpenAVC built in.

## Macros and triggers

* New step: **Clear Help Request**. It sets `system.help_state` back to `idle` and clears the request, so a panel stops showing it. The request stays open in OpenAVC Cloud until it is closed there.

## Simulator

* **Device stops responding** on every simulated device, and **Capacity** on TCP, serial, UDP and OSC devices to limit how fast the device reads.

## IT and security

* Windows: the data folder is restricted to the OpenAVC service and administrators, and updates keep the OpenAVC firewall rule as configured.
* Settings held by the server's environment show as locked, with the variable name.
* Settings > Access > Programmer login requires at least 8 characters.
* `X-Forwarded-For` is only trusted from a proxy on the same computer or one listed in the new `network.trusted_proxies` setting. See Updating.
* New docs page: Hardened Deployment Profile, for managed networks.

## Updating

* After updating, update your installed drivers in Devices > Drivers > Browse Community. Installed drivers do not update with OpenAVC, and many new driver versions need 0.37.0.
* Behind a reverse proxy on another computer, or any proxy in front of Docker with published ports: set `network.trust_forwarded_for` to `true` and list the proxy in `network.trusted_proxies` (or `OPENAVC_TRUSTED_PROXIES`). Otherwise every client behind the proxy shares one rate limit.
* Rolling back to 0.36.0 keeps the system reachable from every network (on a Mac, from this computer only if that was selected).
* Project format stays at 0.14.0.
