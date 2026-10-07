# Hardened Deployment Profile

**Audience:** IT administrators and the integrators who hand a system over to them.

**Scope:** The settings, firewall rules and checks for running OpenAVC on a managed network. The [Network & Security Cut Sheet](it-network-guide.md) describes every port and behavior; this page says what to set.

---

## The settings

Set these values in `system.json`. Keep everything else that is already in the file: it also holds the admin password hash and, on a paired system, the cloud pairing.

```json
{
    "auth": {
        "allow_anonymous": "false"
    },
    "panels": {
        "access": "approved"
    },
    "tls": {
        "enabled": true,
        "redirect_http": true
    },
    "network": {
        "port80_redirect": false,
        "trust_forwarded_for": false
    },
    "discovery": {
        "advertise": false
    },
    "isc": {
        "enabled": false
    },
    "cloud": {
        "enabled": false
    },
    "updates": {
        "check_enabled": true
    }
}
```

`system.json` is in the data folder:

| Platform | File |
|----------|------|
| Windows | `C:\ProgramData\OpenAVC\system.json` |
| Linux | `/var/lib/openavc/system.json` |
| macOS | `/Library/Application Support/OpenAVC/system.json` |
| Docker | `/data/system.json` inside the container (the mounted volume) |

Stop the service before editing the file and start it again afterwards. A change saved in Settings while the service is running overwrites a hand edit.

| Platform | Stop | Start |
|----------|------|-------|
| Windows (administrator PowerShell) | `Stop-Service OpenAVC` | `Start-Service OpenAVC` |
| Linux | `sudo systemctl stop openavc` | `sudo systemctl start openavc` |
| macOS | `sudo launchctl bootout system/com.openavc.server` | `sudo launchctl bootstrap system /Library/LaunchDaemons/com.openavc.server.plist` |
| Docker | `docker compose stop` | `docker compose start` |

Most of these settings are also in the Programmer under **Settings**, and changing them there needs no file edit.

| Setting | Value | In the Programmer | What it does |
|---------|-------|-------------------|--------------|
| `tls.enabled` | `true` | Settings > Security > **Enable HTTPS** | Encrypts panel and Programmer traffic, including the admin password at sign-in, on port 8443. Install your organization's certificate with **Use my own certificate**, or on a system paired with OpenAVC Cloud use the trusted certificate offered on the same card. With the built-in self-signed certificate, every browser warns until the OpenAVC CA is installed on it. |
| `tls.redirect_http` | `true` | Settings > Security > **Redirect HTTP to HTTPS** | Keeps port 8080 open as a redirect: a network client is sent to HTTPS, while the machine's own screen and device status callbacks (`/api/push/`) are served on 8080 directly. Turned off, nothing listens on 8080, including for the machine's own screen. |
| `panels.access` | `"approved"` | Settings > Access > **Approved panels only** | A new tablet or browser shows a waiting screen with a six-digit code until an administrator approves it once. Never set **Anyone on the network**, which lets any device that reaches the port send device commands. |
| `auth.allow_anonymous` | `"false"` | None | Refuses the Programmer and the configuration API to anyone without the admin credential. Packaged installs already set this in the service environment; set it in the file too for a system installed any other way. |
| `network.port80_redirect` | `false` | Settings > Network > **Short URLs** | Keeps port 80 closed. |
| `network.trust_forwarded_for` | `false` | None | Leave `false` unless a reverse proxy is in front of OpenAVC. See [Behind a reverse proxy](#behind-a-reverse-proxy). |
| `discovery.advertise` | `false` | Settings > Network > **Advertise on the network** | Stops the mDNS announcements on UDP 5353, which go out on every network adapter. Leave it on only where the OpenAVC Panel app's list of systems is used; a panel can always connect by address or by QR code. |
| `isc.enabled` | `false` | None | Turns inter-system communication off for the whole machine, whatever the project says: no UDP 19872 traffic and no peer connections. If several OpenAVC systems do need to share state, leave it on and set a long random key under **Inter-System > Authentication** in each project, and keep the **Remote Command Allowlist** to the commands peers need. |
| `cloud.enabled` | `false` | The **Cloud** page | No connection to OpenAVC Cloud. Pairing a system turns this on, and gives the cloud administrator-level control of it: see [What the cloud can do](it-network-guide.md#what-the-cloud-can-do). |
| `updates.check_enabled` | `true` | Settings > Updates > **Check for updates** | Checks GitHub (`api.github.com`, HTTPS) for new releases once a day. Turn it off where outbound internet is blocked, and install updates from a file instead ([System Updates](updates.md)). |

Two more depend on the site:

- **Settings > Network > Control interface.** On a machine with more than one network adapter, pick the AV adapter. See [More than one network adapter](#more-than-one-network-adapter).
- **Settings > Devices > Retry every.** OpenAVC retries an offline device every 5 seconds by default. On a network whose monitoring flags repeated connection attempts, raise it (30 or 60 seconds). This one is saved with the project.

### Setting values in the environment instead

A value in the service's environment takes precedence over `system.json` and over Settings. Settings shows a field set this way locked, with the variable's name.

| Variable | Setting |
|----------|---------|
| `OPENAVC_TLS_ENABLED` | `tls.enabled` |
| `OPENAVC_TLS_CERT_FILE`, `OPENAVC_TLS_KEY_FILE` | `tls.cert_file`, `tls.key_file` (PEM files signed by your CA) |
| `OPENAVC_TLS_REDIRECT_HTTP` | `tls.redirect_http` |
| `OPENAVC_PANEL_ACCESS` | `panels.access` |
| `OPENAVC_ALLOW_ANONYMOUS` | `auth.allow_anonymous` |
| `OPENAVC_PORT80_REDIRECT` | `network.port80_redirect` |
| `OPENAVC_TRUST_FORWARDED_FOR` | `network.trust_forwarded_for` |
| `OPENAVC_TRUSTED_PROXIES` | `network.trusted_proxies` (comma-separated) |
| `OPENAVC_BIND` | `network.bind_address` |
| `OPENAVC_CONTROL_INTERFACE` | `network.control_interface` |
| `OPENAVC_MDNS_ADVERTISE` | `discovery.advertise` |
| `OPENAVC_CLOUD_ENABLED` | `cloud.enabled` |
| `OPENAVC_UPDATE_CHECK` | `updates.check_enabled` |
| `OPENAVC_PROGRAMMER_PASSWORD` | the admin password, for a system provisioned before anyone opens it |

- **Linux:** `sudo systemctl edit openavc`, then add the variables under `[Service]` as `Environment=NAME=value` lines. The file this creates is kept across updates.
- **Docker:** the `environment:` section of the compose file.
- **Windows and macOS:** use `system.json`. The installer rewrites the service's environment each time it runs, and on Windows every update runs it.

Every packaged install sets `OPENAVC_BIND=0.0.0.0` (listen on every adapter) and `OPENAVC_ALLOW_ANONYMOUS=false` in its service environment, so on those systems **Bind address** in Settings is locked and `network.bind_address` in `system.json` has no effect. Change the bind address with `OPENAVC_BIND` on Linux and Docker; on Windows and macOS, restrict access with the firewall instead.

---

## Restrict the web ports to the AV network

The installers allow ports 8080 and 8443 from every address. Restrict them at the host firewall, at the firewall between VLANs, or both. Allow TCP 8080 and 8443 from:

- the AV network (touch panels, the room PC),
- the network IT manages the system from, if the Programmer is used from there,
- the other OpenAVC systems, if inter-system communication is on.

The machine's own screen connects over loopback and needs no rule.

The examples below use `10.20.30.0/24` for the AV network. Replace it with yours, and repeat the allow lines for each additional network.

### Linux with ufw

```bash
sudo ufw delete allow 8080/tcp
sudo ufw delete allow 8443/tcp
sudo ufw allow from 10.20.30.0/24 to any port 8080 proto tcp comment 'OpenAVC'
sudo ufw allow from 10.20.30.0/24 to any port 8443 proto tcp comment 'OpenAVC'
sudo systemctl restart openavc
sudo ufw status
```

The first two lines remove the rules OpenAVC added at install. OpenAVC does not add them back while the ports stay the same. **Expected:** after the restart, `ufw status` lists 8080 and 8443 only from `10.20.30.0/24`. If ufw was not active when OpenAVC was installed, enable it and restart OpenAVC once before running these lines (`sudo ufw allow from <your admin network> to any port 22 proto tcp` first if you reach the machine over SSH).

Changing the HTTP or HTTPS port, or turning on HTTPS or Short URLs later, opens the new port to every address at the next service start. Scope it the same way.

### Linux with firewalld

```bash
sudo firewall-cmd --permanent --remove-port=8080/tcp
sudo firewall-cmd --permanent --remove-port=8443/tcp
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.20.30.0/24" port port="8080" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.20.30.0/24" port port="8443" protocol="tcp" accept'
sudo firewall-cmd --reload
sudo systemctl restart openavc
sudo firewall-cmd --list-all
```

**Expected:** two rich rules naming `10.20.30.0/24`, and neither port listed under `ports:`. These commands change the default zone, which is where OpenAVC opens its ports; if the AV adapter is assigned to another zone, add `--zone=<name>` to each line. The same two notes as ufw apply: OpenAVC does not re-add the ports it opened while they stay the same, and a newly enabled port opens to every address until you scope it.

### Windows

The installer adds an inbound rule named **OpenAVC** that allows the server program from every address, and re-creates it at every update. Restrict it with a block rule of your own: Windows Firewall applies an explicit block rule ahead of any allow rule, and updates do not touch rules they did not create.

The block rule lists every address except the networks you allow and `127.0.0.0/8` (loopback, which the machine's own screen uses). For `10.20.30.0/24`, in an administrator PowerShell:

```powershell
New-NetFirewallRule -DisplayName "OpenAVC - block outside AV network" -Direction Inbound -Action Block -Protocol TCP -LocalPort 8080,8443 -RemoteAddress "0.0.0.0-10.20.29.255","10.20.31.0-126.255.255.255","128.0.0.0-255.255.255.255"
Get-NetFirewallRule -DisplayName "OpenAVC*" | Format-Table DisplayName, Action, Enabled
```

To allow a second network, split the ranges around it as well. On a domain-joined machine whose firewall policy does not merge local rules, the installer's rule has no effect: add an allow rule for TCP 8080 and 8443 from the AV network to the policy instead.

### macOS

The macOS application firewall allows or blocks a program, not an address. Restrict 8080 and 8443 at the network: an access list on the switch, or the firewall between VLANs.

### Docker

With host networking (the single-system compose file), the host's firewall applies and the Linux rules above are what to use. Ports that Docker publishes (the multi-system compose file) are routed before ufw sees them, so publish each port on the host's AV address instead of on every address:

```yaml
ports:
  - "10.20.30.5:8081:8080"
```

### Plugin ports

The Video Panel and Present plugins open UDP and TCP ports of their own while they run, listed in the [cut sheet](it-network-guide.md#listening-ports-inbound-to-the-openavc-host). Restrict them the same way, to the networks their panels, presenters and decoders are on.

---

## Behind a reverse proxy

If nginx, Caddy, HAProxy or another proxy is in front of OpenAVC:

1. Have the proxy set `X-Forwarded-For` to the client's address. In nginx that is `proxy_set_header X-Forwarded-For $remote_addr;`. Caddy sets it by default.
2. Set `network.trust_forwarded_for` to `true`. Unless the proxy reaches OpenAVC over loopback, also list it in `network.trusted_proxies`, as an address or a range: `["10.20.30.5"]`, or `OPENAVC_TRUSTED_PROXIES=10.20.30.5`. That means a proxy on another machine, and any proxy in front of Docker with published ports, which arrives from the Docker network: list that network's range, which `docker network inspect <network> --format '{{(index .IPAM.Config 0).Subnet}}'` prints. OpenAVC reads `X-Forwarded-For` only from loopback and these proxies.
3. Make the proxy the only way to reach OpenAVC.
   - **Proxy on the same machine, Linux or Docker with host networking:** set `OPENAVC_BIND=127.0.0.1`.
   - **Proxy on the same machine, Docker with published ports:** publish on loopback, `"127.0.0.1:8081:8080"`.
   - **Proxy on the same machine, Windows:** a block rule for every address except loopback:
     ```powershell
     New-NetFirewallRule -DisplayName "OpenAVC - block all but loopback" -Direction Inbound -Action Block -Protocol TCP -LocalPort 8080,8443 -RemoteAddress "0.0.0.0-126.255.255.255","128.0.0.0-255.255.255.255"
     ```
   - **Proxy on another machine:** allow 8080 and 8443 from the proxy's address only.

Without step 1, every request through a proxy on the same machine arrives from the machine itself, so OpenAVC treats it as the machine's own screen: panels connect without approval, sign-in attempts are not rate-limited, and the host network settings, on a system that offers them, open without the admin password. Without the `network.trusted_proxies` entry, OpenAVC counts every client of a proxy that is not on loopback as the proxy, so one client's failed sign-ins slow down sign-in for all of them.

Behind a proxy every panel needs approval, including a browser on the OpenAVC machine that goes through the proxy. To terminate TLS at the proxy instead of in OpenAVC, see [Deployment](deployment.md).

---

## More than one network adapter

A machine with one adapter on the AV network and another on the campus network is the usual arrangement.

- **Settings > Network > Control interface:** pick the AV adapter. Device connections and discovery scans then use that adapter only.
- **The web ports listen on every adapter.** Restrict them with the firewall rules above, allowing the AV network and, if the Programmer is used from there, the management network.
- **Advertise on the network** announces on every adapter, whichever control interface is picked. Turn it off unless the Panel app's list of systems is used.

```
            Campus / management network
     (IT workstations using the Programmer, optional)
                          |
            +----------------------------+
            |   firewall between VLANs   |
            |  allow TCP 8080, 8443 to   |
            |  the OpenAVC machine only  |
            +----------------------------+
                          |
   ---------------- AV VLAN 10.20.30.0/24 ---------------------
       |                |                  |               |
   OpenAVC          touch panels      projectors,     other OpenAVC
   machine          and room PC       DSPs, switchers systems (if ISC)
   8080, 8443
       |
       +--> outbound HTTPS only: updates (api.github.com, github.com),
            OpenAVC Cloud if paired
```

---

## Protect the data folder

The data folder holds the project file (with any device passwords it contains), `system.json` and, on a paired system, the cloud key.

- **Linux:** `/var/lib/openavc` belongs to the `openavc` service account, and OpenAVC writes its configuration files readable by that account only. To also hide the file names and the logs from other local accounts:
  ```bash
  sudo chmod 750 /var/lib/openavc /var/log/openavc
  ```
- **macOS:** the folder belongs to the system account the service runs as; no change is needed.
- **Windows:** from OpenAVC 0.37.0, the installer restricts `C:\ProgramData\OpenAVC` to the service and administrators at every install and update, so no other local account can read its files or add new ones. The one exception is its `status` folder, which every local account can read but not change: it holds the ports the server listens on and the reason it last failed to start, for the OpenAVC tray app. To check, in a command prompt:
  ```powershell
  icacls "C:\ProgramData\OpenAVC"
  ```
  **Expected:** `NT AUTHORITY\SYSTEM` and `BUILTIN\Administrators`, each with full control `(F)`, and no other account. If `BUILTIN\Users` is listed, update OpenAVC. Until you can, an administrator prompt can apply the same restriction by hand, but on versions before 0.37.0 the tray app then fails to start:
  ```powershell
  icacls "C:\ProgramData\OpenAVC" /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F"
  ```

---

## Checklist to hand to IT

Run these after the system is configured, and again after each OpenAVC update.

1. From a computer outside the allowed networks, open `http://<host>:8080/panel` and `https://<host>:8443/panel`. **Expected:** neither connects (the attempt times out or is refused).
2. From a device on the AV network that has never been approved, open `http://<host>:8080/panel`. **Expected:** the browser moves to `https://`, and the page shows the waiting screen with a six-digit code.
3. Open `https://<host>:8443/programmer`. **Expected:** no certificate warning (with your organization's certificate or the cloud's trusted certificate), and a sign-in prompt.
4. In the Programmer, **Settings > Access** shows **Approved panels only**; **Settings > Network** shows **Advertise on the network** off and the AV adapter as **Control interface**; the **Cloud** page shows the system is not paired (unless it is meant to be); the **Inter-System** page says ISC is switched off for this whole system.
5. List the firewall rules (`sudo ufw status`, `sudo firewall-cmd --list-all`, or `Get-NetFirewallRule -DisplayName "OpenAVC*"`). **Expected:** no rule allows 8080 or 8443 from every address, or on Windows the block rule is present and enabled.
6. Signed in as an ordinary (non-administrator) account on the OpenAVC machine, try to read `system.json` (`type C:\ProgramData\OpenAVC\system.json` or `cat /var/lib/openavc/system.json`). **Expected:** access is denied.
