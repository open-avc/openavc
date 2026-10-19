# OpenAVC built into equipment

Equipment using the [OpenAVC SDK](https://github.com/open-avc/openavc-sdk) can be
controlled by an OpenAVC server elsewhere on the network. A product can also
include the full OpenAVC server and control its own hardware locally.

## Add equipment over the network

Use **Devices > Discovery** and add the discovered product: its card asks for the
product's username and password before adding it. Or choose **OpenAVC built-in
product** in **Add Device** and enter the product's address, control port and
credentials. The product supplies its commands, settings, state and
channels when connected. Use them in panels, macros and scripts like other
devices.

After the equipment restarts, OpenAVC reconnects and reads its current protocol
and capabilities before accepting more commands. An incompatible product stays
disconnected. Commands interrupted by the restart are not replayed.

A command is reported as done when the product confirms it finished. OpenAVC
waits for that confirmation for up to the advanced **Wait for completion**
setting, 5 seconds unless you change it; raise it for hardware that takes
longer, such as a motor. A command still running after the wait is reported as
not confirmed. It may still finish, so check the product's status before
sending it again.

HTTPS certificate verification is on by default. Products should provide a
certificate trusted by the server. The advanced **Verify HTTPS certificate**
setting controls this check for a remote connection. Turning it off accepts
untrusted certificates for that connection.

## Control the product running OpenAVC

The product running OpenAVC appears automatically in every project, marked
**Product hardware**. This is the product itself, such as the audio channels of
an amplifier running OpenAVC. Its manufacturer supplies the hardware connection.
You can change its project label and its **Wait for completion** (as above, on
its device page), use its controls, save declared device settings and label its
channels. It cannot be uninstalled, duplicated or replaced with another driver.

**Disable** stops OpenAVC control in the current project. It does not turn off
the equipment. **Test** checks the local connection even while project control
is disabled. Programmer, panels, automation and other device connections use
the normal OpenAVC interfaces.

While OpenAVC runs on the product, the SDK prevents other OpenAVC servers from
discovering or controlling that same hardware. Normal server discovery for panels
remains available.

## Move a project

Use the normal **Program > Export** and **Import** controls. A project from the
same equipment model uses the receiving unit's local connection while retaining
device IDs and programming. Connection credentials for product hardware are
not stored in the project.

If the new server cannot connect to the original product hardware, it retains
the programming and displays **Product hardware unavailable**. Choose
**Reassign Driver** on its device page to select compatible equipment.

To use the receiving product's hardware, select **Product hardware**. OpenAVC
keeps the imported device's ID and checks declared commands, required parameters,
saved settings and channel types. The receiving product hardware must be
connected and have no existing programming or saved settings in the project.
If it is already used, resolve those references before reassigning. Test the resulting controls
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
  "name": "Acme Amplifier",
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
separate from the manifest and software installed by the manufacturer. A server
without the environment variable remains an ordinary installation.

If firmware owns updates, set `OPENAVC_FIRMWARE_HOST` to an absolute path containing:

```json
{"schema": 1, "update_owner": "firmware", "firmware_version": "1.0.0"}
```

The Updates page then directs users to the product's firmware settings. Generic
OpenAVC update and rollback operations are disabled. An explicitly configured
file that cannot be read or validated keeps those operations disabled and
reports the configuration problem.
