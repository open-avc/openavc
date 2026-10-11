# OpenAVC built into equipment

Equipment using the [OpenAVC SDK](https://github.com/open-avc/openavc-sdk) can be
controlled by an OpenAVC server elsewhere on the network. A product can also
include the full OpenAVC server and control its own hardware locally.

## Add equipment over the network

Use **Devices > Discovery** and add the discovered product, or choose
**OpenAVC equipment** in **Add Device**. Enter the product's address, control
port and credentials. The product supplies its commands, settings, state and
channels when connected. Use them in panels, macros and scripts like other
devices.

HTTPS certificate verification is on by default. Products should provide a
certificate trusted by the server. The advanced **Verify HTTPS certificate**
setting controls this check for a remote connection. Turning it off accepts
untrusted certificates for that connection.

## Use the equipment hosting OpenAVC

The product appears automatically as **Built-in equipment** in every project.
Its connection and hardware integration are supplied by the manufacturer.
You can change its project label, use its controls, save declared device
settings and label its channels. It cannot be uninstalled, duplicated or
replaced with another driver.

**Disable** stops OpenAVC control in the current project. It does not turn off
the equipment. **Test** checks the local connection even while project control
is disabled. Programmer, panels, automation and other device connections use
the normal OpenAVC interfaces.

When the product runs its local OpenAVC server, its SDK host must stop exporting
the same hardware to other controllers. Normal server discovery for panels
remains available.

## Move a project

Use the normal **Program > Export** and **Import** controls. A project from the
same equipment model uses the receiving unit's local connection while retaining
device IDs and programming. Connection credentials for built-in equipment are
not stored in the project.

On another product or a standalone server, an unavailable built-in integration
keeps its programming and displays **Built-in equipment unavailable**. Choose
**Reassign Driver** on its device page to select compatible equipment.

To use the receiving product's hardware, select **Built-in equipment**. OpenAVC
keeps the imported device's ID and checks declared commands, required parameters,
saved settings and channel types. The receiving built-in reference must be
connected and have no existing programming or saved settings. If it is already
used, resolve those references before reassigning. Test the resulting controls
and scripts against the replacement equipment.

Project Import transfers programming. Use **Backups > Restore** when restoring
a backup's persisted variable values as well.

## Firmware integration

The SDK supplies the hardware adapter. For a server embedded in firmware, set
`OPENAVC_EQUIPMENT_MANIFEST` to an absolute path to a manufacturer-owned JSON
file:

```json
{
  "device_id": "local",
  "identity": "acme-unit-001",
  "integration": "acme_amplifier",
  "name": "Built-in amplifier",
  "connection": {
    "url": "https://127.0.0.1:9443",
    "token": "<locally provisioned endpoint credential>",
    "ca_file": "/etc/acme/equipment-ca.pem"
  }
}
```

`identity` identifies the physical unit; `integration` must match the equipment
model's `id`. Keep them stable across firmware updates. `device_id` is the
preferred project reference when a new project needs one. An imported project
may retain another ID. Protect this file and its credential with the product's
firmware permissions. Projects cannot select the endpoint or replace its
credential. Local endpoint certificates are always verified.

The normal OpenAVC data directory remains writable project storage. Keep it
separate from the firmware-owned manifest and runtime. A server without the
environment variable remains an ordinary installation.

If firmware owns updates, set `OPENAVC_FIRMWARE_HOST` to an absolute path containing:

```json
{"schema": 1, "update_owner": "firmware", "firmware_version": "1.0.0"}
```

The Updates page then directs users to the product's firmware settings. Generic
OpenAVC update and rollback operations are disabled. An explicitly configured
file that cannot be read or validated keeps those operations disabled and
reports the configuration problem.
