# OpenAVC v0.36.0

This release adds an approval setting for panels, a Device Audit for testing a network device and its driver, better device identification in Discovery, and an optional confirmation on driver commands that erase or reset a device.

## Programmer

* Drivers > Installed updates as soon as a driver is installed, from any page or browser.
* The Driver Builder's Test tab lists replies that no response rule matched, and shows a warning when the test targets a device already in the project.
* An unset yes/no command parameter shows "(none)" if it is optional and "Select..." if it is required, in Send Command, macro steps and panel bindings.
* Fixed: saving from a device page while the UI Builder had unsaved work showed "Project modified externally" and could clear undo history.
* Fixed: Add Device saved a driver's default table rows, such as a DSP's block list, as an empty list.
* Smaller fixes: the Driver Builder no longer crashes when opening a command with no parameters, dropdowns stay open when something else on the screen scrolls, network adapters use their Windows names, exports and backups no longer include the contents of linked folders, a rollback after a failed update restores a linked project folder as a link, and DOMPurify is updated to 3.4.16.
* Project format stays at 0.14.0. Projects saved in this version open in v0.35.0.

## Panel

* New setting under Settings > Access > Panel access: Approved panels only, or Anyone on the network.
* With Approved panels only, a tablet or browser that opens the panel shows a waiting screen with a code until it is approved from the Dashboard's Panels card or the notice in the Programmer. Panels can also be denied, renamed and revoked there. An approved panel stays approved through restarts.
* New installs default to Approved panels only. Systems updated from an earlier version are set to Anyone on the network, so existing panels keep working with no changes.
* The OpenAVC host's own display, panels opened through OpenAVC Cloud and connections using an API key never need approval. Node-RED and other integrations need an API key when Approved panels only is set, unless they run on the OpenAVC host.
* New setting under Settings > Network: Advertise on the network, which controls whether the Panel app can find the system automatically.
* Smaller fixes: the lock screen and waiting screen fit a phone held upright, the pair page links to Google Play or the App Store, plugin files are served only from the plugin's own folder, and links inside a custom UI or plugin folder are no longer served. If you kept a linked file in `ui/` or a plugin's `panel/` folder, copy the file in instead.

## Device Audit

* New: Devices > Drivers > **Audit a Device**, also on a Discovery result or a device page. Tests one network device and its driver. Project devices at that address are paused during the audit.
* A network check of about a minute shows the best-matching driver and the evidence for it. The driver test then sends each command and records the response, writes each device setting and restores it, and measures reconnect time after a power cycle and a cable pull.
* The report is a zip with a summary page, `report.json` and a timeline, with passwords masked, and a link to a pre-filled driver test report. See Device Audit in the docs.

## Discovery

* Device announcements are collected for the full listen window, so more devices are found. Scans take up to 5 s (quick), 15 s (standard) or 30 s (thorough) longer.
* Single-address scans work, including devices that don't respond to ping, and probe only that address.
* With no control interface pinned, scans cover every network adapter with a link. With one pinned in Settings > Network, all scan traffic uses that adapter, which fixes scans on laptops with a VPN.
* Devices are now also identified by their mDNS name, Telnet or SSH banner and SSDP server name.
* MAC address lookup recognizes manufacturers registered with medium and small IEEE blocks, which fixes devices showing the wrong manufacturer.
* Smaller fixes: identified devices appear under their driver's category in the filter, and on Windows the ping sweep no longer counts empty addresses as live hosts.

## Device control and the driver contract

* Driver commands that erase or reset a device can require confirmation before they are sent manually, from Send Command, a Quick Action button, the Driver Builder's Live Test or the Device Audit. Macros, triggers and panel buttons send without a confirmation. Set it with **Ask before sending** on the Driver Builder's command card. Drivers that use it require v0.36.0.
* A command's Control picker can be limited to the controls that command applies to, for example only on/off controls for a toggle.
* Fixed: a device that dropped its connection while its driver was starting up after a reconnect stayed offline until OpenAVC restarted.
* Smaller fixes: `restarts_device_for` works in YAML drivers, simulation works with a control interface pinned, and pre-shared keys, PINs and access keys are masked in the server log.

## Docs

A new Your First Space tutorial replaces the conference room tutorial, and there are new pages for the Device Audit and the OpenAVC Academy.
