# Device Audit

A device audit points OpenAVC at one device on your network, tests a driver against it, and
writes everything it found into a report you can send to whoever asked for it. A manufacturer
can run one on a unit on their bench to show how the driver works with their product. An
integrator can run one when a driver does not behave as expected.

The audit works with devices on the network. A device on a serial port, or one reached through
a bridge, cannot be audited.

## Before You Start

- Run OpenAVC on a computer on the same network as the device, so the audit can read the
  device's MAC address and hear its network announcements.
- Close any other software connected to the device, such as the manufacturer's control app.
  Some devices answer only one connection at a time.
- Have someone at the device for the power and cable tests, if you run them.

A device in your project that uses the same address is paused while the audit runs, and
reconnects when you finish.

## Starting an Audit

Open the wizard from any of these:

- **Devices > Drivers > Audit a Device**, then type the device's IP address or host name.
- **Audit this device** on a device's page. The address and the device's saved connection
  settings are filled in.
- **Audit this device** on a Discovery result.

One audit runs at a time, and a Discovery scan cannot run during one. An audit left with no
activity for 30 minutes ends on its own, and closing the page cancels it. Either way, what it
found so far is saved under **Recent reports** on the first step.

## The Steps

Everything after the network check is optional, and every step after it can take you back to
the one before.

1. **Device.** The address. Under **Options**, choose **Standard** (about a minute) or
   **Extended**, which also checks every port from 1 to 1024 and reads every SNMP value. If the
   device uses its own SNMP read community, add it; `public` is always tried.
2. **Network check.** Shows each check as it runs and whether OpenAVC recognizes the device.
   **Why?** lists the signals it saw and the drivers each one points at.
3. **Which driver?** Choose the manufacturer and model, then the driver. Each driver shows the
   catalog's confidence for the model you chose. A catalog driver you have not installed can
   be installed here. If there is no driver for the device yet, choose **No driver yet: skip
   to the report**, and the report covers the network check.
4. **Connection.** The driver's connection settings, filled in from the driver's defaults or
   the paused device's saved settings. The driver always connects to the audited address over
   the network. **Save and show what connecting sends** lists what the
   driver will send when it connects, before anything is sent.
5. **Connect and listen.** Connects the way adding the device to a space does, sends none of
   the driver's commands, and listens for 45 seconds or three of the driver's status polls,
   whichever is longer (**Keep listening** adds a minute at a time, up to five minutes).
   You see each status value as the device reports it, a timeline, and the traffic both ways.
   The optional front-panel check asks you to change something on the device itself and say
   whether OpenAVC showed it.
6. **Commands.** Open a command, press **Send**, then watch or listen to the device and say
   whether it happened. The suggested commands come first. Commands other than status queries
   change the device, so send only the ones that are safe to run on it. **Run all status
   queries** sends every query that needs no value. Under **Device settings**, **Write and put
   back** writes a new value, checks that the device reports it, then writes the old value back.
7. **Power and cable.** The **power cycle** and **cable pull** tests show whether OpenAVC
   notices when the device goes away and how the driver reconnects when it comes back. The step
   says when to turn the device off or unplug it, and when to put it back. Each takes a few
   minutes.
8. **Report.** A summary of what the audit found and what it could not see. Fill in **About
   you** if you want to (every field is optional), then **Download report**. **Test another
   driver** goes back to **Which driver?** and keeps these results. **Finish** ends the audit
   and reconnects any project device it paused.

## The Report

The download is one zip file, named for the device and the time:
`openavc-device-audit-<manufacturer>-<model>-<date>.zip`.

| File | What it holds |
|------|---------------|
| `summary.html` | What the audit found, readable in any browser |
| `report.json` | The complete record |
| `timeline.txt` | Every event in order, with the driver's traffic |
| `log.txt` | OpenAVC's own log lines about the audit |
| `driver/` | The exact driver files that ran |

For a driver from the community catalog, the Report step also offers **Open a driver test
report on GitHub**. It opens the driver library's test report form with the results filled in.
Download the report first and attach the zip to it.

OpenAVC keeps the ten newest reports, listed under **Recent reports** on the first step.

## Privacy

- The report stays on your computer until you send it.
- Every password and credential you type, and any SNMP community other than `public`, is
  replaced with `[redacted]` everywhere in the report (`***` in the traffic). A driver file
  that matches the catalog is included as the catalog publishes it. If a password you typed
  is the driver's published default, the Report step says so before you download.
- **Leave the serial number out of the report** removes every serial number the audit heard,
  from the network check and from the driver, the same way. It applies as soon as you tick it,
  including to the report saved if you cancel.
- The report never includes OpenAVC's own settings.
- The audit listens for network announcements, but keeps only the ones from the device being
  audited.

## What the Audit Sends on the Network

Everything goes to the device's address, except the multicast queries and the driver catalog.

**The network check:**

- A ping, a reverse DNS lookup, and a NetBIOS name query (UDP 137).
- A TCP connection to each port on Discovery's thorough port list, started 50 ms apart (with
  **Extended**, also every port from 1 to 1024).
- On each open port, a connection that only listens for a few seconds.
- On web ports, a `GET /`, and the certificate on TLS ports.
- SNMP v2c reads (UDP 161) with `public`, then any community you added.
- mDNS and SSDP queries, and listening for mDNS, SSDP and AMX DDP announcements until the audit
  ends.
- Each catalog driver's identification check: TCP probes on the device's open ports, and UDP
  probes sent to the device's address.
- A request to `raw.githubusercontent.com` to read the community driver catalog.

**The driver test** sends what the driver sends when a device is added to a space: its sign-in,
start-up steps and status polls. A command or a setting is sent only when you press **Send** or
**Write and put back**. During a power or cable test, the audit pings the device once a second
if it answered ping in the network check.

To network monitoring, the network check looks like a port scan of one host. See the
[Network and Security Cut Sheet](it-network-guide.md) if you need to clear it with IT first.
