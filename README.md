<p align="center">
  <img src="https://openavc.com/logo-wide.png" alt="OpenAVC" width="400">
</p>

<h3 align="center">AV Control for Every Space</h3>

<p align="center">
  Open-source platform that replaces Crestron, Extron, and AMX.<br>
  Visual programming. Web-based touch panels. Community device drivers.<br>
  Free software. Your hardware.
</p>

<p align="center">
  <a href="https://openavc.com">Website</a> &bull;
  <a href="https://docs.openavc.com">Documentation</a> &bull;
  <a href="https://github.com/open-avc/openavc-drivers">Community Drivers</a> &bull;
  <a href="https://cloud.openavc.com">Cloud Platform</a>
</p>

---

## What is OpenAVC?

OpenAVC is a software-based AV control platform for professional audiovisual installations. It does what a Crestron processor, Extron controller, or AMX NetLinx system does, but it runs on hardware you already own and the software is completely free.

Program a conference room, lecture hall, worship space, or auditorium. Control projectors, displays, switchers, DSPs, cameras, and lighting from a web-based touch panel that runs on any tablet, phone, or wall-mounted display.

<!-- TODO: Screenshot of Panel UI and Programmer IDE side by side -->

## Why OpenAVC?

| Legacy AV Control | OpenAVC |
|---|---|
| Dedicated control processor per space | Deploy on a mini PC, rack server, Raspberry Pi, or Docker |
| Proprietary touch panels on every wall | Any iPad, Android tablet, kiosk display, or Stream Deck |
| SIMPL, NetLinx, or vendor-specific languages | Visual drag-and-drop builder, or Python when you need it |
| Wait for manufacturers to write device drivers | Community-maintained drivers, or build your own in YAML |
| Closed protocols that IT can't monitor or integrate | REST API, WebSocket, Python scripting. IT-friendly from day one |
| Annual licensing fees and dealer certifications | Free, open-source, MIT licensed. No per-space fees |

## Features

**Visual Programming**
Build macros, triggers, and automation with a drag-and-drop interface. No code required for most spaces. When you need more, a full Python scripting engine is built in.

**Touch Panel Designer**
Design touch panels visually with 18 element types, grid layouts, multiple pages, themes, and live preview. Panels are web-based and work on any device with a browser.

**Community Drivers**
Browse and install device drivers from a shared community library, right from the programming interface. Projectors, displays, switchers, DSPs, cameras, and more. Or write your own in YAML (for text protocols) or Python (for anything else).

**Run on Anything**
Raspberry Pi, Docker container, Windows PC, Linux server, VM, mini PC. OpenAVC is software, not a box. Deploy it on the hardware that makes sense for your installation.

**Cloud Management (Optional)**
Monitor and manage all your spaces from a single dashboard. Remote access, alerts, fleet operations, and system health. Free tier included. Cloud is entirely optional.

**Device Discovery**
Scan your network and OpenAVC finds your equipment automatically. Port scanning, protocol probes, mDNS, SSDP, SNMP, and intelligent driver matching.

**Scheduling and Automation**
Time-based schedules, condition-based triggers with debounce and cooldown, and macro sequences. Automate power-on routines, after-hours shutdowns, or anything else your space needs.

**Inter-System Communication**
Coordinate multiple OpenAVC instances across spaces, buildings, and campuses. Auto-discovery, shared state, and cross-system macro execution.

## Install

### Windows
Download the installer from the [Releases](https://github.com/open-avc/openavc/releases) page. Installs as a Windows service with a system tray app. One click to open the Programmer IDE.

### Raspberry Pi
Flash the pre-built image to an SD card with [Raspberry Pi Imager](https://www.raspberrypi.com/software/). Power on and access the Programmer at `http://openavc.local:8080`. Connect an HDMI touch display for a built-in touch panel.

### Docker
```bash
docker run -d --name openavc -p 8080:8080 -v openavc-data:/data ghcr.io/open-avc/openavc:latest
```

### Linux
```bash
curl -fsSL https://get.openavc.com | bash
```

After installation, open a browser:

| Interface | URL |
|-----------|-----|
| Programmer IDE | http://localhost:8080/programmer |
| Touch Panel | http://localhost:8080/panel |

## Documentation

Full documentation is available at [docs.openavc.com](https://docs.openavc.com).

- [Getting Started](https://docs.openavc.com/getting-started) - Install and build your first project
- [Programmer Overview](https://docs.openavc.com/programmer-overview) - IDE walkthrough and core concepts
- [Devices and Drivers](https://docs.openavc.com/devices-and-drivers) - Adding equipment and installing drivers
- [UI Builder](https://docs.openavc.com/ui-builder) - Designing touch panels
- [Macros and Triggers](https://docs.openavc.com/macros-and-triggers) - Automation and event-driven control
- [Scripting Guide](https://docs.openavc.com/scripting-guide) - Python scripting API
- [Creating Drivers](https://docs.openavc.com/creating-drivers) - Build drivers in YAML or Python
- [Deployment](https://docs.openavc.com/deployment) - Production deployment and configuration

## Community

- [Discord](https://discord.gg/FHcuxG5aTa) - Chat, support, and discussion
- [Community Drivers](https://github.com/open-avc/openavc-drivers) - Device drivers for projectors, displays, switchers, DSPs, cameras, and more
- [Community Plugins](https://github.com/open-avc/openavc-plugins) - Extensions like Stream Deck integration, MQTT bridges, and custom control surfaces
- [Cloud Platform](https://cloud.openavc.com) - Remote monitoring, alerts, and fleet management

## About

OpenAVC is maintained by an AV system designer with 20 years in the industry, working with Crestron, Extron, and Q-SYS control systems. The project grew out of frustration with the tools available and a belief that there should be an open alternative.

This is currently a solo-maintained project. Contributions, feedback, and driver submissions are all welcome.

## License

MIT License. See [LICENSE](LICENSE) for details.

Free to use, modify, and distribute. No per-space fees. No dealer certifications. No annual renewals.

## Trademark

"OpenAVC" and the OpenAVC logo are trademarks of OpenAVC LLC. The MIT License covers the source code but does not grant rights to these trademarks. See [TRADEMARK.md](TRADEMARK.md) for details.
