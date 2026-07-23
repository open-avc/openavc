# OpenAVC Network & Security Cut Sheet

**Audience:** IT administrators, network engineers, and security teams.

**Scope:** Network requirements, traffic profiles, security posture, and firewall rules for OpenAVC deployments. This is not an installation guide. It is a technical reference for evaluating and approving OpenAVC on a managed network.

---

## What Is OpenAVC?

OpenAVC is an open-source AV room control platform that runs on standard hardware (Windows or Linux PCs, VMs, Docker, Raspberry Pi). It controls projectors, displays, audio DSPs, video switchers, cameras, and lighting over standard IP protocols (TCP, UDP, HTTP, serial).

OpenAVC is a single application with a built-in web server. There is no external database, no application server, and no background services beyond the application itself. Configuration, automation, and device state are stored in local files on the host machine.

---

## Deployment Scenarios

### Existing PC in the room

The most common deployment. Most AV spaces already have a PC at the lectern, behind the display, or in the rack for digital signage, lecture capture, or room scheduling. OpenAVC runs on that existing PC alongside whatever else it does. It is a lightweight application, not a full operating system, and coexists with other software without conflict.

The same PC that presents content can also control the projector, switch inputs, and adjust audio. The touch panel interface can run right in a browser on that PC's display, or on a separate tablet or touch screen in the room. If nobody needs a touch panel at all (e.g., everything is triggered automatically by schedules or sensors), OpenAVC runs quietly in the background with no visible UI.

**Network impact:** Minimal. The PC is already on the network. OpenAVC adds only targeted TCP connections to the specific AV devices in the room. No new hardware, no new network drops.

### Dedicated controller (mini PC or Raspberry Pi)

For spaces where there is no existing PC, or where a dedicated appliance is preferred, OpenAVC runs on a small-form-factor PC (Intel NUC, Lenovo Tiny, HP Mini, etc.) or a Raspberry Pi. The Pi image can optionally drive a touch panel display directly via HDMI.

**Network impact:** One new device on the room's network segment. Communicates only with AV devices on its local subnet and serves a web interface to touch panels or browsers in that room. No traffic leaves the room unless the optional cloud service is enabled.

### Server or VM on the building network

OpenAVC runs on an existing server, VM, or Docker host. It controls AV equipment across multiple rooms from a central location.

**Network impact:** The server needs IP reachability to every AV device it controls, and devices presenting the touch panel UI need HTTP access to the server. If rooms are on isolated VLANs, firewall rules or routing must allow the server to reach those VLANs on the specific ports listed below.

---

## Network Requirements

### Listening ports (inbound to the OpenAVC host)

| Port | Protocol | Purpose | Required? |
|------|----------|---------|-----------|
| **8080** | TCP (HTTP) | Web interface and REST API | Yes |
| **8443** | TCP (HTTPS) | Web interface and REST API over TLS | Only when HTTPS is enabled in Settings > Security |
| **80** | TCP (HTTP) | Convenience redirect so typed URLs can omit the port (`http://<server>/panel`). Pure redirect to the real HTTP/HTTPS port; serves no content. | No — off by default; enable in Settings > Network |
| **19500** | TCP (HTTP) | Device Simulator UI (development/testing only) | No |
| **19872** | UDP | ISC auto-discovery (multi-instance setups only) | No |
| **5353** | UDP | mDNS advertising — lets OpenAVC Panel apps auto-discover this instance on the LAN | No — on by default; disable with `discovery.advertise: false` |
| **8189** | UDP | WebRTC media for the Video Panel plugin (live camera/RTSP streams on the panel) | Only when the Video Panel plugin is installed and a panel is viewing a stream |
| **8190** | UDP | WebRTC media for the Present plugin (wireless presentation) | Only when the Present plugin is installed and someone is presenting or a display is connected |
| **8554** | TCP (RTSP) | Present plugin stream-display output — hardware decoders pull the presentation stream | Only when the Present plugin is installed and a stream display uses RTSP |
| **8899** | UDP (SRT) | Present plugin stream-display output — same stream over SRT | Only when the Present plugin is installed and a stream display uses SRT |
| **22** | TCP (SSH) | Remote console login — Raspberry Pi appliance image only | No — ships disabled; operator-enabled in Settings > Security |
| *Device-specific* | UDP (multicast) | Device push notifications — some drivers join a multicast group the device sends state-change frames to (for example, an Audio-Technica SmartMixer notifying on 239.0.0.100:17000) | Only while such a device is connected; the group and port come from the driver and are shown in its setup notes |
| *Device-specific* | TCP | Device push notifications (dial-back) — some drivers open a listener port that the device connects back to with state-change frames (for example, a Panasonic PTZ camera dialing the port its driver registered, 31004 by default) | Only while such a device is connected; the port comes from the driver's device settings and is shown in its setup notes |
| *(rides 8080/8443)* | TCP (HTTP) | Device push notifications (HTTP callbacks) — some devices post state changes to `/api/push/` on the existing web port; see "Device push notifications (HTTP callbacks)" below | No new port — the web port already listed; active only while such a device is connected |

**Port 8080** is the only port that must be accessible for a standard single-room deployment when HTTPS is off (the default). This is configurable via the `OPENAVC_PORT` environment variable or `system.json`.

**Port 8443** is opened when HTTPS is enabled. When both listeners are running, the HTTP listener on 8080 returns a temporary (302/307) redirect to the HTTPS URL — so existing bookmarks and panel devices keep working without reconfiguration, and nothing is cached that would break access if HTTPS is later disabled. If you disable the redirect listener (Settings > Security), only port 8443 is open. Port 8443 is configurable via `OPENAVC_TLS_PORT` or `tls.port` in `system.json`.

**Port 19500** is used by the device simulator during development and testing. It is only active when the simulator is running. It does not need to be accessible from other machines.

**Port 19872** is used only when multiple OpenAVC instances need to discover each other on the same LAN (inter-system communication). It can be disabled entirely.

**Port 8189** is opened only when the optional Video Panel plugin is installed. That plugin displays live IP camera and RTSP streams on the touch panel, and it delivers video to the browser over WebRTC. The browser sends its media to UDP 8189 on the OpenAVC host. For a panel on the same subnet as the host this often works without any change, because consumer firewalls tend to allow same-subnet UDP. If panels are on a different VLAN or subnet, or video tiles stay black while the rest of the panel works, allow inbound UDP 8189 to the OpenAVC host from the panel devices' subnet. OpenAVC does not add this firewall rule for you, so on a managed network you should add it explicitly. The port is only active while the plugin is running. If the Video Panel plugin is not installed, this port is never opened.

The Video Panel plugin can also show the built-in preview stream of an AV-over-IP encoder (for example a TurtleAV Chazy encoder), which appears on the panel automatically once its controller is connected. These preview streams usually live on the AV/video network, which is commonly a separate VLAN from the control network. The OpenAVC **host** fetches the preview and passes it to the panel over the host's normal web port, so no extra inbound port is needed on the panel side. What is required is that the OpenAVC host can route to the encoder's video network. A host with a second network interface on the AV fabric is the typical arrangement. The panel browser never connects to the video network directly.

**Port 8190** is opened only when the optional Present plugin (wireless presentation) is installed. Present carries video over WebRTC in both directions: a presenter's laptop sends its screen-share media to UDP 8190 on the OpenAVC host, and the devices driving the space's displays receive video from the same port. As with the Video Panel port, same-subnet traffic usually works without changes; if presenters or display devices are on a different VLAN or subnet — presenters on a guest wireless network is the common case — allow inbound UDP 8190 to the OpenAVC host from those networks. The signaling and the join pages ride the normal web ports (8080/8443). The port is only active while the plugin is running, and it is never opened if the plugin is not installed.

**Ports 8554 and 8899** also belong to the Present plugin and serve its *stream displays*: a hardware IP decoder at a display pulls a continuous presentation stream from the OpenAVC host over RTSP (TCP 8554, TCP-interleaved — no UDP RTP port range) or SRT (UDP 8899). Only decoders need to reach these ports, and only the space's own output streams can be pulled from them: each stream URL embeds a per-display secret key, reads are limited to those output paths, and nothing can be published to the host through these ports without internal credentials that never leave the machine. If a decoder sits on an AV VLAN, allow it inbound TCP 8554 or UDP 8899 to the OpenAVC host (pick the protocol the decoder uses). Like the WebRTC port, these listeners exist only while the plugin runs. A space that uses only browser displays never has a decoder pulling from them, and they can stay blocked.

**Device push notifications (multicast).** A few AV devices report state changes by multicasting small UDP frames to a group address instead of answering only when polled. When a driver for such a device is connected, OpenAVC joins that group (standard IGMP membership) and listens on the device's notification port — receive-only, filtered to frames from that device's own IP address, active only while the device is connected. Two network notes: multicast does not cross VLANs, so the device and the OpenAVC host must share one (or your routers must be configured for multicast routing); and on switches with **IGMP snooping** enabled, an **IGMP querier** must exist on the VLAN or the switch may stop forwarding the group and the notifications silently disappear. Nothing breaks if the frames are filtered — OpenAVC keeps polling the device and changes simply appear at poll speed instead of instantly.

**Device push notifications (TCP dial-back).** A few devices — Panasonic PTZ cameras are the common case — push state changes by connecting **to** the OpenAVC host on a TCP port the driver registers with the device. While such a device is connected, OpenAVC listens on that port (fixed per device in the driver's settings, 31004 by default for the Panasonic cameras; several devices of the same kind can share one port). The listener is receive-only: it accepts connections only from the addresses of the devices registered to it, closes anything else immediately, and carries no control surface — inbound frames can only update that device's displayed state, never command anything. Like UDP device control, these frames are unauthenticated by the device's own protocol design, so the trust model is the AV/control VLAN they live on. Firewall note: allow the listener port inbound to the OpenAVC host **from the device's address or the AV VLAN only**. If the port is blocked, nothing breaks — polling covers the device and changes appear at poll speed.

**Device push notifications (event streams).** Some HTTP-controlled devices (Barco ClickShare, Philips Hue bridges) stream state changes over Server-Sent Events instead. For these, OpenAVC simply holds one or more ordinary **outbound** HTTP(S) requests open to the device's existing API port — the same port the driver already polls, with the same authentication and TLS. No inbound port opens on the OpenAVC host, nothing crosses VLAN boundaries beyond the device connection you already allow, and no firewall change is needed. The only infrastructure note: middleboxes that aggressively time out long-lived idle HTTP connections (some proxies and stateful firewalls between VLANs) can silently kill an event stream; the driver notices, reconnects automatically, and polling covers any gap.

**Device push notifications (HTTP callbacks).** A third group of devices (Cisco video codecs, Sonos speakers) delivers notifications the other way around: the **device connects to OpenAVC** and posts small HTTP requests to a callback URL the driver registers on it. These callbacks arrive on the web port the host already listens on (8080 by default — no new port opens), on paths under `/api/push/`. Three things worth knowing about this surface. First, it is receive-only and narrowly scoped: a posted body is handed to the one connected device's driver for state parsing, only requests from that device's own IP address are accepted, and the path answers 404 whenever no matching device is connected — it exposes no data and no control operations. Second, the callbacks are **unauthenticated plain HTTP** — AV devices cannot carry credentials for this, and most cannot deliver to a server whose certificate they don't trust — so treat it with the same posture as UDP device control: keep AV devices and the OpenAVC host on a trusted AV VLAN, where source-address trust is acceptable. (When HTTPS is enabled with the HTTP redirect listener left on, callbacks still work — the redirect listener serves `/api/push/` directly rather than redirecting, because devices don't follow redirects. In an HTTPS-only configuration the callback URL becomes https, and the device must be set to accept the server's certificate — the driver's setup notes say if and how.) Third, the device must be able to **reach** the host: a one-way firewall rule that only allows OpenAVC → device will silently eat the notifications. As with every push channel, nothing breaks if callbacks are blocked — polling carries on and changes appear at poll speed.

Apart from the mDNS advertiser noted above (standard multicast DNS, receive-only, no control surface), the application does not listen on any other ports by default: no SNMP agent, no embedded database port, and no proprietary discovery protocol that accepts inbound connections. The Raspberry Pi appliance image can optionally run an SSH server (port 22) for remote console access, but it ships **disabled** — an operator turns it on in Settings > Security when it is needed (see [Raspberry Pi appliance](#raspberry-pi-appliance-login-and-ssh) below).

### Who can access the web interface

OpenAVC separates two surfaces with different access rules:

- **The room panel** (`/panel`) — the end-user touch interface — is **always open**. Wall tablets and shared room displays reach it without a login, as an AV panel should.
- **The Programmer (configuration interface) and the control/admin API** require an **admin credential**.

**Wireless presentation guest pages (Present plugin).** When the optional Present plugin is installed, it adds two login-free pages on the standard web ports: the guest connect page (`/present`), where a presenter enters the rotating join code shown on the space's displays, and the display pages that drive each screen, each gated by a long per-display key carried in its URL. Neither grants any access to the configuration interface or the API. Wrong join codes are rate-limited, the code rotates between presentation sessions, and a display's key can be regenerated to revoke its link. Screen sharing additionally requires HTTPS to be enabled on the instance, because browsers only permit screen capture on a secure page. These pages exist only while the plugin is installed and running.

**Secure by default.** Packaged deployments (Windows installer, Linux `install.sh`, Docker, Raspberry Pi image) listen on all network interfaces so panels can reach them, but they ship with **no credential and refuse admin access until one is set**. The first time someone opens the Programmer, OpenAVC presents a one-time "create admin password" screen. Until that is done, the configuration interface and control API return HTTP 401; only the panel and health/status endpoints respond. There is no default password and no open admin surface on a shipped box.

**Code-writing endpoints are never open.** The endpoints that create or edit Python drivers and scripts (which execute code on the host) always require the admin credential, even on an instance that is otherwise configured for open access.

**Binding.** Packaged deployments bind to `0.0.0.0` (all interfaces). A bare manual run (`python -m server.main` from source) binds to `127.0.0.1` (localhost only). To force localhost-only on a packaged deployment, set `OPENAVC_BIND=127.0.0.1` (e.g. `sudo systemctl edit openavc` on Linux, or the `network.bind_address` field in `system.json`).

**Credentials.** The admin password set during first-run setup is stored in `system.json` on the host. It can be changed later in **Settings > Security**. For unattended provisioning, it can also be supplied up front via `OPENAVC_PROGRAMMER_PASSWORD` (and optionally `OPENAVC_PROGRAMMER_USERNAME`) or an `OPENAVC_API_KEY` for programmatic clients — an instance configured this way is already "claimed" and goes straight to the login screen.

### Raspberry Pi appliance: login and SSH

The Raspberry Pi appliance image hardens the operating-system login as well as the web interface:

- **No shipped OS password.** The `openavc` Linux account is **locked** in the image — there is no `openavc/openavc` (or any other) default login. The kiosk display still starts automatically; auto-login does not use a password.
- **One credential.** The admin password set during first-run setup becomes the OS console/SSH login for the `openavc` user too, so there is a single password to manage. Changing it in **Settings > Security** re-syncs the OS login.
- **SSH off by default.** No SSH server runs until you enable it with the **Enable SSH** toggle in **Settings > Security**. Once on, log in as `openavc` with the admin password over port 22. Turn it back off from the same toggle.

This is Pi-image-specific. On a generic Linux `install.sh` host, OpenAVC does not touch the operating-system account or `sshd` — the server runs as an unprivileged service user and you manage OS login and SSH yourself.

**Fronting OpenAVC with your own auth** (an SSO reverse proxy, for example): set `OPENAVC_ALLOW_ANONYMOUS=true` to opt back into open admin access, and restrict reachability at the proxy. If you do this behind a trusted proxy that sets `X-Forwarded-For`, also set `network.trust_forwarded_for: true` in `system.json` so per-client rate limiting sees the real client IP.

---

## Outbound Traffic (OpenAVC host to the network)

### AV device control

OpenAVC initiates outbound TCP and UDP connections to AV equipment. The specific ports depend entirely on which devices are configured in the project. OpenAVC only communicates with devices explicitly defined in the project configuration, using the ports those devices expect. It does not scan or probe the network during normal operation.

The table below lists common AV control ports. This is not exhaustive. AV manufacturers use a wide range of proprietary and standard ports, and new drivers may use ports not listed here.

| Port | Protocol | Device type | Example |
|------|----------|-------------|---------|
| 22 | TCP (SSH) | Embedded devices, AV processors | SSH-managed gear |
| 23 | TCP (Telnet) | Switchers, DSPs, amplifiers | Extron, Biamp, QSC, Kramer, Shure, LG |
| 80 | TCP (HTTP) | Devices with web APIs | Panasonic cameras, REST-based devices |
| 443 | TCP (HTTPS) | Devices with secure web APIs | Newer AV-over-IP devices |
| 445 | TCP (SMB) | Windows-based AV servers, NAS | File access |
| 1400 | TCP | Audio | Sonos UPnP |
| 1515 | TCP | Samsung commercial displays | Samsung MDC protocol |
| 1688 | TCP | Crestron-compatible devices | Crestron CIP |
| 1710 | TCP | Audio DSPs | Q-SYS QRC (JSON-RPC) |
| 3088 | TCP | Crestron-compatible devices | Crestron XIO |
| 4352 | TCP | Projectors | PJLink standard |
| 5000 | TCP | Switchers, DSPs | Kramer Protocol 3000 / Q-SYS QRC alt |
| 5900 | TCP | Remote desktop/preview | VNC |
| 7142 | TCP | AMX-compatible devices | AMX ICSP |
| 8080 | TCP | Devices with web APIs | Alternate HTTP management |
| 9090 | TCP | Devices with web APIs | Alternate HTTP management |
| 10500 | TCP | PTZ cameras | Sony VISCA over IP |
| 41794 | TCP | Crestron-compatible devices | Crestron CTP |
| 49152 | TCP | Audio DSPs | Biamp Tesira |
| 49280 | TCP | Audio mixers | Yamaha RCP (CL/QL/TF/Rivage/DM3) |
| 52000 | TCP | Audio DSPs | QSC Q-SYS |
| 61000 | TCP | Wireless microphones | Shure DCS |
| 161 | UDP | SNMP-managed devices | Read-only status query (discovery only) |
| 5343 | TCP | Network-attached control surfaces | Elgato Network Dock, Stream Deck Studio (Stream Deck plugin) |

An OpenAVC instance controlling only PJLink projectors and a Biamp DSP, for example, will only generate traffic on ports 4352 and 49152. If your network policy requires explicit allow-listing, the exact ports in use for a given deployment can be determined from the project's device configuration.

**Network-attached control surfaces (port 5343):** when the Stream Deck plugin is configured with a network-attached deck, the server keeps an outbound TCP connection to the unit on port 5343 and may send mDNS queries (UDP multicast 224.0.0.251:5353) to find it. This protocol has no authentication or encryption — any host on the segment can drive the unit — so place these devices on the control VLAN with the AV equipment. mDNS discovery does not cross VLANs, NAT, or Docker bridge networks; the surface is added by IP address there (a static IP on the unit is recommended).

### Diagnosing offline devices

When a device can't connect, its card in the Programmer shows an "Offline" banner with a specific reason. Use it to tell a network problem from a credentials or device problem before escalating:

| Banner reason | What it means | What to check |
|---------------|---------------|---------------|
| Authentication failed | The device answered, but rejected the username/password or key. | The credentials in the device's configuration. For SSH gear, that the OpenAVC key is installed, or that password auth is enabled. Not a network problem. |
| Connection refused | OpenAVC reached the device's IP, but nothing is listening on that port. | That the control service is enabled on the device (SSH/Telnet/HTTP/API server), and that the configured port matches. Not usually a firewall problem (a firewall drop normally shows as "unreachable"). |
| Can't reach the device | No route to the host, a DNS failure, or a connection timeout. | The device IP, that the host is powered and on the network, VLAN/routing between the OpenAVC host and the device, and any firewall blocking the control port. |
| SSH host key changed | The device's SSH host key no longer matches the one OpenAVC trusted. | Whether the device was replaced or re-imaged (expected), or whether this is unexpected (possible man-in-the-middle). Re-accept the key only once verified. |
| Device didn't respond as expected | The connection opened, but the device didn't speak the expected protocol. | That the right driver and transport (e.g. SSH vs Telnet, the right port) are selected for this device. |
| Required client not found | A client OpenAVC shells out to is missing on the host (e.g. the OpenSSH `ssh` client). | That the client is installed and on the system PATH on the OpenAVC host. |

For a network reason (connection refused, can't reach the device, no response), OpenAVC retries automatically with exponential backoff for about an hour before giving up, and the banner shows the current attempt. Reasons that retrying can't fix — authentication failed, SSH host key changed, an untrusted TLS certificate, invalid connection settings, or a missing client — stop the retry loop early instead, and the banner says so rather than showing a climbing attempt count. Fix the cause and press Reconnect. The same reason is also published as the `device.<id>.offline_reason` state key for automation and monitoring.

### Device discovery (on-demand only)

OpenAVC includes a network discovery feature to help AV integrators find devices during initial setup. Discovery is never automatic. It runs only when a scan is explicitly started — by an integrator in the Programmer interface, or by an authenticated request to the discovery API (including from the cloud console). Default scan budgets are 60 s (Quick), 120 s (Standard), and 180 s (Thorough).

During a discovery scan, OpenAVC will:

1. **Ping sweep** the local subnet(s) using ICMP echo requests. The server prefers an unprivileged ICMP datagram socket where the OS allows it, uses a raw ICMP socket where one is available (the packaged Linux service and Docker image are granted the `CAP_NET_RAW` capability for this), and falls back to the system `ping` command otherwise. Firewalls on the AV VLAN must permit ICMP echo request/reply for discovery to see hosts; a host that drops echo can still be found by the mDNS / SSDP listeners or driver probes, but will not be port-scanned.
2. **TCP port scan** responding hosts on the AV ports listed above
3. **SNMP query** (v2c, community string `public`, read-only) on port 161
4. **mDNS / DNS-SD query** on multicast group 224.0.0.251:5353
5. **SSDP M-SEARCH** on multicast group 239.255.255.250:1900
6. **AMX DDP listen** on multicast group 239.255.250.250:9131 (passive — receive only)
7. **NetBIOS name query** on UDP 137 to live hosts (Standard / Thorough scan only)
8. **Driver-declared TCP probes** on open AV ports. Each installed driver may declare its own probe; common probes for AV equipment include identification queries against switchers, DSPs, displays, mixers, and cameras (Extron, Biamp Tesira, QSC Q-SYS, Samsung MDC, Yamaha RCP, Sony VISCA, etc.). The exact wire format and port depend on which drivers are installed; the Programmer IDE's Driver Builder shows them per driver.
9. **Driver-declared UDP probes** if any installed driver declares one. These are sent to each scanned subnet's directed-broadcast address. Examples include PJLink Class 2 SRCH (UDP 4352) when the `pjlink_class1` driver is installed and Crestron CIP discovery (UDP 41794) when the `utility/crestron_cip` driver is installed, along with other vendor-specific broadcasts shipped by individual drivers.

All discovery traffic is confined to the local subnet(s) detected on the host's network interfaces. It does not scan remote subnets, public IP ranges, or addresses outside the host's directly-connected networks. Virtual and VPN adapters are excluded automatically.

Driver-declared UDP probes are rate-limited to 10 per second across the whole scan; TCP probes are spread with a short per-probe stagger instead. All probes bind to the configured control adapter and are one-shot per scan, with no retries.

### Internet access (optional)

OpenAVC does not require internet access for normal operation. All device control, automation, and UI serving works entirely offline.

The following features require outbound internet access if enabled:

| Destination | Port | Protocol | Purpose | Can be disabled? |
|-------------|------|----------|---------|-----------------|
| `api.github.com` | 443 | HTTPS | Check for software updates | Yes (`updates.check_enabled: false`) |
| `github.com` | 443 | HTTPS | Download updates and community drivers | Yes (manual install alternative) |
| `cdn.jsdelivr.net` | 443 | HTTPS | IR code database search (only while searching in the IR Codes editor) | Yes (feature is only used on demand) |
| `cloud.openavc.com` | 443 | WSS | Cloud management platform (see below) | Yes (disabled by default) |

If your network policy blocks outbound HTTPS, all local functionality is unaffected. Update checks will fail silently, and the cloud agent (if enabled) will retry with exponential backoff up to a 5-minute interval, then remain dormant until connectivity is restored.

---

## Inter-System Communication (ISC)

When deploying multiple OpenAVC instances (e.g., one per room), ISC allows them to share state and coordinate. ISC is optional and can be disabled entirely.

| Parameter | Value |
|-----------|-------|
| Discovery protocol | UDP broadcast on port 19872 |
| Discovery range | Local broadcast domain only (255.255.255.255) |
| Peer communication | WebSocket on each instance's HTTP port (path: `/isc/ws`) |
| Authentication | Optional shared key (configurable per project) |
| Beacon interval | Every 5 seconds |
| Auto-discovery scope | Same subnet/VLAN only |
| Cross-subnet support | Manual peer addressing (no broadcast required) |
| Inbound message limit | 300 messages per minute per peer (the budget persists across reconnects) |
| Remote command limit | At most 8 peer-requested device commands execute at once (all peers combined) |

**To disable ISC entirely**, set `isc.enabled: false` in `system.json`. No UDP traffic will be sent or received on port 19872.

**For cross-VLAN multi-instance setups**, disable auto-discovery and configure peer addresses manually. This requires only TCP access to each instance's HTTP port (default 8080), with no UDP broadcast traffic.

**Security model.** ISC authenticates peers with a shared key, not with TLS. Every message is signed with an HMAC over a per-connection challenge, and the handshake is mutual: each instance proves it holds the `isc.auth_key` to the other before either acts on the other's shared state or remote commands, so a peer that lacks the key cannot join the mesh, read shared state, or issue a remote command. When the instances also run HTTPS, the ISC link is encrypted, but its TLS is encryption-only. Peers use each other's self-signed LAN certificates without certificate-authority verification (private-LAN instances have no public CA to validate against), so the certificate does not identify the peer. The shared key does. The practical consequence is the standard AV-VLAN posture: keep multi-instance hosts on a trusted control network and set a distinct, strong `isc.auth_key`. The shared key stops any outsider, but it is the whole of the trust model, so the segment it runs on must be trusted.

---

## Authentication and Access Control

### Web interface and API

Admin access is **secure by default on every shipped deployment.** An installed instance (Windows, Linux, Docker, Raspberry Pi) refuses the Programmer and the mutating/admin API until an admin password is set on first open — returning HTTP 401 until then, while the room panel and health/status endpoints stay reachable. See [Who can access the web interface](#who-can-access-the-web-interface) above for the full model. (A from-source developer checkout is the one exception: it stays open on localhost for frictionless development. Force either posture explicitly with `OPENAVC_ALLOW_ANONYMOUS`.)

The admin credential is one of the following, set during first-run setup or provisioned ahead of time. You do not need to set both:

| Method | Configuration | When to use |
|--------|--------------|-------------|
| HTTP Basic (username + password) | `OPENAVC_PROGRAMMER_USERNAME` and `OPENAVC_PROGRAMMER_PASSWORD` env vars, or `auth.programmer_username` and `auth.programmer_password` in `system.json` | The standard admin login, and what the first-run setup screen creates. The browser prompts for both username and password. This is for humans logging in via a browser. |
| API key (token) | `OPENAVC_API_KEY` env var or `auth.api_key` in `system.json` | Set this if you have third-party integrations (control scripts, middleware, or external software) that connect to the REST API or WebSocket. Provide the key via the `X-API-Key` header. Not needed unless you are building custom integrations. |

Either one protects the Programmer IDE and API endpoints. The username/password is for humans (browser login), the API key is for machines (HTTP headers). If both are set, either credential is accepted.

If a programmer password is set without a username, any username entered at the browser prompt is accepted as long as the password matches. Setting a username as well is recommended.

In all cases:
- The **Panel** (end-user touch interface) is reachable without credentials
- The **Programmer** (configuration interface) requires the admin credential
- All configuration-changing API endpoints require the credential
- The **code-writing endpoints** (Python drivers, scripts) require a set credential even on a checkout otherwise configured for open access — an unclaimed instance refuses them outright
- Username and password comparisons both use constant-time algorithms to prevent timing attacks

**Browser sessions never retain the password.** When someone signs in to the Programmer, the browser exchanges the password for a short-lived random session token (`POST /api/auth/session`) and keeps only that token, scoped to the browser tab. The password itself is sent once, over the login request, and is not stored client-side in any form. Session tokens are held in server memory only (never written to disk), expire after 12 hours of inactivity (each authenticated request extends them), and are all invalidated immediately when the admin password changes or the server restarts. Signing out revokes the token server-side. Basic and API-key authentication are unaffected; they remain available for scripted and third-party clients on every protected endpoint.

**Opening an instance is a full-trust decision.** Setting `OPENAVC_ALLOW_ANONYMOUS=true` (or `auth.allow_anonymous: true`) removes the credential gate entirely: everyone who can reach the instance has complete admin control. That is not limited to reading configuration. An anonymous caller on an open instance can set or overwrite the admin password (locking out the owner, and the change survives a restart), change the bind address, or turn TLS off, because those are ordinary configuration writes and there is no credential to tell the owner apart from a stranger. Open an instance only where reachability is already restricted out of band (bind to localhost, or front it with an authenticating reverse proxy), and treat the network it listens on as fully trusted. The shipped default never opens the box this way: packaged installs are claimed on first run (see [Who can access the web interface](#who-can-access-the-web-interface)), so this posture applies only when an operator deliberately turns anonymous access on.

### TLS/HTTPS

OpenAVC defaults to plain HTTP because most deployments live on an isolated AV VLAN. HTTPS is available as a built-in opt-in via **Settings > Security** in the Programmer IDE, or by setting `OPENAVC_TLS_ENABLED=true` (or `tls.enabled: true` in `system.json`) and restarting the server.

When enabled, the server runs the HTTPS listener on port 8443 with an enforced **TLS 1.2 floor** — only TLS 1.2 and 1.3 are negotiated, and TLS 1.0/1.1 are refused — using RSA-2048 server keys and a modern ECDHE (GCM / CHACHA20) cipher suite. It keeps a tiny HTTP listener on port 8080 that redirects to the HTTPS URL (temporary 302/307, so no browser caches a permanent redirect that would outlive a later decision to turn HTTPS back off) so existing clients keep working.

Three cert modes are supported:

- **Auto-generated self-signed cert.** Built-in CA and server cert under `{data_dir}/tls/`, 10-year validity, SANs covering the OS hostname and every local IPv4. The CA is downloadable at `GET /api/certificate` for install on panel devices.
- **User-provided cert.** Point `tls.cert_file` / `tls.key_file` at PEM files signed by your internal CA. No browser warnings if the CA is already trusted by your fleet.
- **Cloud-issued trusted cert.** Systems paired with OpenAVC Cloud can serve a publicly trusted certificate with no client-side setup at all. Details in the next section.

A reverse proxy in front of OpenAVC (nginx, Caddy, Apache, HAProxy) is still fully supported — leave OpenAVC's TLS off and let the proxy terminate.

### Trusted certificates (cloud-issued, optional)

Systems paired with OpenAVC Cloud can serve a publicly trusted HTTPS certificate instead of the self-signed one. Browsers on the LAN then get a normal padlock, with no warnings to click through and no CA to roll out to guest or BYOD devices. It is a one-click opt-in under **Settings > Security**, and one click turns it back off.

How it works:

- The cloud assigns the system a random hostname label under the public zone `i.openavc.net` and obtains a wildcard certificate for it from Let's Encrypt, using a DNS challenge handled entirely by the cloud. The OpenAVC host needs no inbound connectivity and no new outbound rules; issuance and renewal ride the existing cloud connection (`cloud.openavc.com`, port 443).
- The private key is generated on the OpenAVC host and never leaves it. The cloud receives a certificate signing request and returns the signed certificate.
- Addresses encode the system's LAN IP in the hostname: `https://192-168-1-20.<label>.i.openavc.net:8443/` resolves to `192.168.1.20`. While the feature is active, the HTTP listener on port 8080 sends clients to the certified URL automatically, so users still just type the plain IP address.
- Whether the certified hostname resolves can only be determined on the client device, so browser navigations get a brief in-page reachability check before being forwarded: if the certified origin answers, the browser lands on the padlocked page; if the name cannot be resolved or reached (blocked resolver, no internet), the browser lands on the bare-IP HTTPS URL with the standard self-signed warning instead of a dead error page. The check adds no delay when the name resolves, and at most a few seconds when it does not. Non-browser clients (API integrations, scripts, monitoring) always receive a plain 302/307 redirect.
- Renewal is automatic. The renewed certificate is served immediately, with no restart and no dropped connections.
- Bare-IP HTTPS (`https://<ip>:8443/`) continues to serve the self-signed certificate, so devices that already trust the local CA keep working unchanged.

Notes for network administrators:

- Client devices resolve the certified hostname through normal public DNS, and the answer points at a private (RFC 1918) address. Only the name lookup leaves the network; the actual traffic to the OpenAVC host stays on the LAN.
- **DNS rebind protection.** Some routers and firewalls refuse public DNS names that resolve to private addresses. The symptom: browsers on the LAN land on the bare-IP HTTPS URL with the self-signed certificate warning instead of the padlocked certified page (the reachability check above falls back automatically, so nothing breaks — but the padlock is lost). To get the certified page, add an exception for the zone. On dnsmasq-based routers (including OpenWrt): `rebind-domain-ok=/i.openavc.net/`. On Unbound-based firewalls (OPNsense, pfSense): `private-domain: "i.openavc.net"`. On an AVM FRITZ!Box: add `i.openavc.net` to the DNS rebind protection exceptions. For other equipment, search its documentation for "DNS rebind" exceptions.
- Like every publicly trusted certificate, issuance is recorded in public Certificate Transparency logs. The logged name contains only the random system label (for example `*.a3f9c2e81b4d0f37.i.openavc.net`). Your internal IP addresses, hostnames, and organization name do not appear in the certificate.
- If the site loses internet access, devices that have already resolved the name keep working for the DNS TTL (24 hours). Browsers on new devices fall back to the bare-IP URL automatically (standard self-signed warning); control of the AV equipment is never affected.
- Turning the feature off reverts the redirect immediately and revokes the hostname on the cloud side; clients with cached DNS answers lose resolution as their cache expires.

### Rate limiting

Rate limiting is enabled by default on the HTTP REST API for remote clients. Requests from localhost (127.0.0.1, ::1) are exempt, since the primary use case is a single user on the same machine. Remote clients are subject to the limits below. These tiers do not apply to the touch panel UI (which uses WebSocket) or to the command pipeline between the server and AV hardware — commands are sent to devices the instant they are received. The WebSocket channel has its own guards: each connection is limited to 200 messages per second, and the server accepts at most 100 simultaneous WebSocket connections.

| Tier | Limit | Applies to |
|------|-------|-----------|
| Open | 120 requests/min per IP | Status, health-check, and setup-state endpoints |
| Standard | 60 requests/min per IP | General API operations (including library/catalog reads) |
| Control | 120 requests/min per IP | Commissioning operations: device commands and tests, discovery, driver install, and project save. These all require authentication; the higher budget keeps normal setup work (command bursts, volume ramps) from being throttled. |
| Strict | 10 requests/min per IP | Security-sensitive operations: sign-in, cloud pairing, and backup restore |

Failed authentication attempts are throttled at the strict (10/min) rate on every endpoint, not just strict-tier ones, so credential probing is capped wherever it is aimed. Successful control traffic is tracked separately: volume ramps, rapid command sequences, and multi-room control from the touch panel are unaffected by the brute-force limit.

---

## Data Storage and Privacy

### What is stored locally

| Data | Location | Sensitive? |
|------|----------|-----------|
| Project configuration (devices, macros, UI layouts) | `project.avc` (JSON) | Low. Contains device IP addresses and connection parameters. |
| System configuration | `system.json` | Medium. May contain auth passwords and API keys in plaintext. Protect with filesystem permissions. |
| Persistent variables | `state.json` | Low. Key-value pairs for automation state. |
| Application logs | `logs/` directory | Low. Standard application logs at INFO level. Device protocol traffic (which can include device credentials) is never written to disk — it is held in a fixed-size in-memory buffer, visible only in the live log view behind an authenticated Programmer login. Configurable rotation (default: 50 MB, 5 files). |
| Cloud pairing data | `cloud.json` | High. Contains system key for cloud authentication. Protect with filesystem permissions. |

### What is NOT stored

- No user credentials for the web interface are stored in a database (the password is a single config value)
- No personal data, user accounts, or usage analytics
- No device credentials beyond what is configured in the project file (e.g., PJLink passwords)
- No data is written outside the application's data directory

### Filesystem permissions

The application runs as a standard user process. It does not require administrator/root privileges. It reads and writes only within its own data directory:

| Platform | Default data path |
|----------|------------------|
| Windows | `C:\ProgramData\OpenAVC\` |
| Linux | `/var/lib/openavc/` |
| Docker | `/data/` (mounted volume) |
| Development | `./data/` (relative to repo) |

---

## Cloud Platform (Optional)

The OpenAVC Cloud platform provides remote monitoring, fleet management, and AI-assisted configuration for organizations managing multiple AV spaces. **Cloud connectivity is entirely optional and disabled by default.** All local functionality works without it.

### Connection details

| Parameter | Value |
|-----------|-------|
| Endpoint | `wss://cloud.openavc.com/agent/v1` |
| Protocol | WebSocket over TLS 1.2+ |
| Direction | **Outbound only.** The device initiates the connection. No inbound ports required. |
| Port | 443 (standard HTTPS) |
| Certificate validation | Required. Standard CA-signed certificate. |
| Keepalive | Server-initiated ping/pong over the WebSocket |
| Reconnection | Exponential backoff: 5s, 10s, 20s, 40s ... up to 5 minutes |

### Authentication

The cloud connection uses a multi-step challenge-response handshake. The system's secret key never crosses the network.

1. Device sends a `hello` message identifying itself (system ID, software version, OS, hardware, deployment type)
2. Cloud responds with a random 32-byte challenge nonce
3. Device computes an HMAC-SHA256 proof using a locally-derived authentication key (HKDF-SHA256 from the system key) and sends it back
4. Cloud independently derives the same key from the stored key hash and verifies the proof
5. On success, cloud issues a session with a unique signing key

All subsequent messages are signed with HMAC-SHA256 using the session-specific signing key. Messages with invalid or missing signatures are rejected.

**Key derivation:** HKDF-SHA256 (RFC 5869), stdlib-only implementation (no external crypto libraries)
**Message signing:** HMAC-SHA256 with constant-time verification
**System key:** 64 bytes, cryptographically random, generated locally, never transmitted. Cloud stores only the SHA-256 hash.

### What data is sent to the cloud

When cloud is enabled, the agent streams a small amount of telemetry automatically:

| Data | Frequency | Content |
|------|-----------|---------|
| Heartbeat | Every 30 seconds | CPU %, memory %, disk %, uptime, device count, connected device count, error count, WebSocket client count, temperature (if a sensor is available) |
| State changes | Batched (2 s for active device state; slower tiers for lower-priority keys) | Device, variable, system, UI, and plugin state key-value changes (e.g., `device.projector1.power: "on"`). Internal cloud/ISC keys are excluded. |
| Alerts | As they occur (throttled) | Alert ID, severity, category, and message, plus the rule and device that fired it and the state key/value that tripped the threshold |

This automatic telemetry is runtime state and metrics only. It never contains the project file, device connection settings, or any credential.

**On-demand pulls.** Separately, an operator working in the cloud console can request specific data from a paired instance. Most significantly, the cloud can pull the **full project file** — which contains device IP addresses, connection parameters, and any device passwords stored in it (for example PJLink or SSH credentials) — along with the device/command list and recent logs. These are returned only in response to an authenticated console action, not streamed continuously. If your policy is that device configuration must never leave the site, do not pair the instance with the cloud.

**Never sent, under any circumstance:** the system's cloud key or derived signing keys, the web-interface admin password or API key, and data from other systems on the network.

### What the cloud can do

When an instance is paired, the cloud console can send it the following:

| Command | Description |
|---------|-------------|
| Device command | Execute a device command already defined in the local project (e.g., "turn on projector") |
| Diagnostic | Run a network diagnostic from the device's perspective — ping, TCP port check, DNS lookup, or a port scan of a host |
| Software update | Trigger a software update check and install |
| Restart | Restart the OpenAVC service |
| Configuration push | Replace or update the project file on the device (a backup is taken first, then the engine reloads) |
| Alert rules | Push alert rule definitions to be evaluated locally |
| Remote access tunnel | Open a proxied connection to the local web interface (see below) |
| AI-assisted management | Run the same management actions an administrator has in the Programmer — read project state, change state and send device commands, start a discovery scan, install drivers and plugins, and create or edit drivers and **scripts, which run on the host** |

**This is an administrator-equivalent management plane.** When you pair an instance, the cloud can do what a local administrator can do at the Programmer, reached remotely — including pushing configuration and scripts that execute on the host. It does not exceed local-admin scope: there is no general shell beyond these defined actions, and no command reconfigures the host operating system or its network settings. Because this is full administrative control, pairing is **opt-in and off by default** (see Disabling cloud entirely, below), each instance pairs to a single account, and every message is mutually authenticated (see the handshake under Authentication above). If your security model does not allow an external management plane with this authority over the host, leave cloud disabled — every capability here exists only on a paired instance.

### Remote access tunnels

The cloud platform can open a remote access tunnel to the device's web interface. This works by proxying HTTP traffic through a secondary WebSocket connection, similar to how tools like ngrok or Cloudflare Tunnel work.

- The device initiates the tunnel connection (outbound to cloud on port 443)
- No inbound ports are opened on the device or the network
- Tunnel connections are authenticated with a per-tunnel token
- Tunnels are closed when the remote session ends or when the device disconnects

### Disabling cloud entirely

Cloud is disabled by default. To confirm it is disabled, verify that `cloud.enabled` is `false` in `system.json` (or that the `OPENAVC_CLOUD_ENABLED` environment variable is not set to `true`). When disabled, no cloud-related code runs and no outbound connections to `cloud.openavc.com` are made.

---

## Firewall Rule Summary

### Minimum (single room, no internet)

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| Web UI access | Inbound | Touch panels / browsers | OpenAVC host | 8080/tcp | HTTP |
| Web UI access (TLS) | Inbound | Touch panels / browsers | OpenAVC host | 8443/tcp | HTTPS (only when enabled) |
| Short-URL redirect | Inbound | Browsers | OpenAVC host | 80/tcp | HTTP redirect only (only when enabled) |
| AV device control | Outbound | OpenAVC host | AV device IPs | Per device (see table above) | TCP |

### Typical (with updates and discovery)

Add to the minimum rules:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| ICMP (discovery) | Outbound | OpenAVC host | Local subnet | ICMP | Echo request |
| NetBIOS (discovery) | Outbound | OpenAVC host | Local subnet | 137/udp | NetBIOS name query (Standard / Thorough only) |
| SNMP (discovery) | Outbound | OpenAVC host | Local subnet | 161/udp | SNMP v2c |
| mDNS (discovery) | Outbound + Inbound | OpenAVC host | 224.0.0.251 | 5353/udp | mDNS |
| SSDP (discovery) | Outbound + Inbound | OpenAVC host | 239.255.255.250 | 1900/udp | SSDP |
| AMX DDP (discovery) | Inbound | 239.255.250.250 | OpenAVC host | 9131/udp | Passive listen |
| Camera WS-Discovery | Outbound + Inbound | OpenAVC host | Subnet broadcast | 3702/udp | Opens when an installed camera driver declares a `udp_probe:` on this port |
| PJLink Class 2 SRCH | Outbound + Inbound | OpenAVC host | Subnet broadcast | 4352/udp | Opens when the `pjlink_class1` driver is installed |
| PJLink Class 1 INFO | Outbound | OpenAVC host | Class 2 responders | 4352/tcp | Per-responder follow-up from the PJLink driver |
| Crestron CIP probe | Outbound + Inbound | OpenAVC host | Subnet broadcast | 41794/udp | Opens when the `utility/crestron_cip` driver is installed |
| Driver-declared UDP probes | Outbound + Inbound | OpenAVC host | Subnet broadcast | Vendor-specific | If installed drivers declare a `udp_probe:` or sibling `_discovery.py` Python companion |
| Driver-declared TCP probes | Outbound | OpenAVC host | Live hosts with port open | Vendor-specific | If installed drivers declare a `tcp_probe:` |
| Device push notifications (multicast) | Inbound | Device multicast group | OpenAVC host | Device-specific /udp | Only for drivers that declare a multicast push channel (e.g. 239.0.0.100:17000); receive-only while the device is connected |
| Device push notifications (dial-back) | Inbound | Device IPs | OpenAVC host | Device-specific /tcp | Only for drivers that declare a TCP dial-back push channel (e.g. Panasonic PTZ cameras, port 31004); receive-only, connections accepted from registered device addresses only |
| Device push notifications (event stream) | Outbound | OpenAVC host | Device | Device's API port | Drivers that declare an SSE push channel hold the device's existing HTTP(S) control connection open — no new port, no inbound listener |
| Device push notifications (HTTP callback) | Inbound | AV device IPs | OpenAVC host | 8080/tcp (the web port) | Drivers that declare an HTTP-listener push channel register a callback URL on the device; the device posts to `/api/push/` on the existing web port — no new port, accepted only from the device's own address |
| Update checks | Outbound | OpenAVC host | api.github.com | 443/tcp | HTTPS |

### With media plugins (Video Panel / Present)

Add for the optional media plugins actually installed:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| Video Panel WebRTC media | Inbound | Panel devices | OpenAVC host | 8189/udp | WebRTC (Video Panel plugin) |
| Present WebRTC media | Inbound | Presenter laptops + display devices | OpenAVC host | 8190/udp | WebRTC (Present plugin) |
| Present stream display (RTSP) | Inbound | Hardware decoders | OpenAVC host | 8554/tcp | RTSP, TCP-interleaved (Present plugin) |
| Present stream display (SRT) | Inbound | Hardware decoders | OpenAVC host | 8899/udp | SRT (Present plugin) |

### With cloud platform

Add to the typical rules:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| Cloud connection | Outbound | OpenAVC host | cloud.openavc.com | 443/tcp | WSS |

### Multi-instance (ISC)

Add if running multiple OpenAVC instances that need to coordinate:

| Rule | Direction | Source | Destination | Port | Protocol |
|------|-----------|--------|-------------|------|----------|
| ISC discovery | Both | OpenAVC hosts | Broadcast (255.255.255.255) | 19872/udp | UDP |
| ISC peer WebSocket | Both | OpenAVC hosts | Other OpenAVC hosts | 8080/tcp | HTTP (WS) |

---

## VLAN and Network Segmentation Recommendations

OpenAVC is designed to work well on segmented networks. A common and recommended topology for managed environments:

**AV Control VLAN** - Contains the OpenAVC host and all AV equipment (projectors, DSPs, switchers, cameras). This VLAN can be fully isolated from the corporate network. If OpenAVC and all controlled devices are on this VLAN, no cross-VLAN rules are needed for device control.

**User/Presentation VLAN** - If users need to access the touch panel UI from devices on a different VLAN (tablets, room PCs), allow inbound TCP on the OpenAVC host's HTTP port (8080 by default) from that VLAN.

**Internet/WAN** - Only required for update checks and cloud connectivity. Can be fully blocked if these features are not needed.

OpenAVC does not use UPnP port mapping, NAT traversal, or any technique that modifies network infrastructure. It operates strictly as a client or server on the ports listed in this document.

---

## Security Posture Summary

| Aspect | Default | Notes |
|--------|---------|-------|
| Inbound ports | HTTP 8080 (+ UDP 5353 for panel auto-discovery) | Configurable. Adds 8443 when HTTPS is enabled. |
| Bind address | Packaged installs: all interfaces (`0.0.0.0`); source run: localhost | Installers bind all interfaces so panels can reach the host. Force localhost with `OPENAVC_BIND=127.0.0.1`. |
| Admin authentication | Secure by default | Shipped deployments refuse the Programmer and admin API until an admin password is set; the room panel stays open. A source checkout is open on localhost for development. |
| TLS | Off, opt-in built-in | Enable via Settings > Security. TLS 1.2/1.3 only (1.0/1.1 refused). Auto-generated self-signed cert, supply your own, or a cloud-issued publicly trusted cert for paired systems. Reverse-proxy TLS also supported. |
| Outbound internet | Not required | Only for optional updates and cloud |
| Cloud connectivity | Disabled by default | Opt-in. When paired, it is an administrator-equivalent management plane (see Cloud Platform above). |
| Privileged access | None required | Runs as standard user, no root/admin |
| External dependencies at runtime | None | No external database, message broker, or third-party service required |
| Discovery scans | On-demand only | Never automatic. Started by an integrator (or an authenticated cloud request). |
| Background multicast/broadcast | mDNS advertising; ISC beacons if enabled | mDNS on 5353 (disable with `discovery.advertise: false`); ISC UDP broadcast on 19872 (disable with `isc.enabled: false`). |
| Data exfiltration risk | Low | No data leaves the site unless cloud is explicitly paired. When paired: automatic telemetry plus on-demand config pulls (both itemized above). |

---

## Quick-Start: Evaluating OpenAVC on Your Network

You can evaluate OpenAVC on a spare Windows or Linux machine in a few minutes, with no AV hardware. Install it by whichever method suits you — Windows installer, Linux `install.sh`, Docker, or from source — following the [Getting Started guide](getting-started.md), then open `http://localhost:8080/programmer`.

Two things matter for a network review:

- **Zero traffic until you connect devices.** A fresh install generates no network traffic of its own. You can explore the full interface and test against the built-in device simulator entirely offline.
- **Reachability is explicit.** A from-source run binds to localhost only. The packaged installers bind to all interfaces so panels can reach the host, and require you to set an admin password the first time you open the Programmer.

To point it at real equipment, add devices by IP in the Programmer; OpenAVC then opens outbound connections to just those devices (no inbound change needed, since device control is outbound).

---

## Frequently Asked Questions

**Does it need Active Directory or LDAP integration?**
No. OpenAVC uses a simple password for the configuration interface and optional API keys for automation. There is no user database or directory integration.

**Does it phone home?**
Not by default. Update checks (to GitHub's public API) can be enabled or disabled. Cloud connectivity is a separate opt-in feature that is off by default.

**Does it need a database server?**
No. All data is stored in JSON files on the local filesystem. There is no PostgreSQL, MySQL, Redis, or any external data store.

**Does it modify the host system?**
Minimally. The Windows installer creates a Windows service (via NSSM) and one program-scoped Windows Firewall rule for the OpenAVC server executable — inbound traffic is accepted only on ports the server is actually listening on (the HTTP port, plus HTTPS and the port-80 short-URL listener when those features are enabled). The Linux install script creates a systemd service and an `openavc` user, and a root helper syncs ufw/firewalld (when active) with the configured listener ports at each service start — ports it opened are closed again when the feature is disabled, and rules added by an administrator are never touched. Docker and from-source installations make no system modifications. In all cases, application data is confined to a single data directory.

**What if we block all outbound internet?**
OpenAVC will work normally. Update checks will fail silently and cloud features (if configured) will be dormant. All AV control, automation, and UI functionality is fully local.

**Is the source code available for review?**
Yes. OpenAVC is MIT-licensed open source. The full source code, including the cloud agent that handles remote connectivity, is available at [github.com/open-avc/openavc](https://github.com/open-avc/openavc).

---

*Document version: 1.3. For the latest version, see [docs.openavc.com](https://docs.openavc.com).*
