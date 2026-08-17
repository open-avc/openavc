# OpenAVC v0.28.0

OpenAVC v0.28.0 adds monitored readings: state values flagged in the project, with optional limits for what normal is, shown on the Programmer Dashboard and the cloud system health card and raising alerts when they go out of range. Alerts can now also be resolved from the portal, and service reports can be printed with the integrator's branding.

## Monitored readings

Add one from the State tab's Variables or Device States view, or from a row of a device's Live State list. The label, unit, type and range are filled in from the driver.

* Limits are optional. A reading with no limits is displayed without a status colour.
* Numeric readings take a minimum, a maximum, or both. Other readings take a list of values, each with a label and a normal or not-normal setting.
* A limit can require the value to stay out of range for a set duration before an alert is raised.

Alert rules take a duration as well, and add `in` and `not_in` operators.

## Alerts and service reports

* Alerts can be resolved from the portal, individually or as all open alerts for a space at once.
* Systems report their firing alerts when they connect, so alerts left open by a restart are cleared.
* Alert charts plot the reading against its limits, and history can be shown over seven days.
* **Printable report** on the Reports page renders the report with the integrator's logo, company name and accent colour.
* Spaces with less than an hour of connection history show Not measured instead of an uptime percentage. Dismissals, reboots and deleted rules are excluded from repair-time figures.

## Custom controls

* Panels reload a custom control or page when its file is saved.
* A page can be switched between built-in controls and a custom file in either direction, and its render mode, file, config and access grant can be changed after it is created.
* An element's custom config can no longer reach devices outside its access grant.
* The AI assistant can read and write custom UI files and the project stylesheet. This needs a cloud account.

## Also in this release

* **Set up from a device** sizes a matrix from the device's configured port count, for example Output Count, instead of the driver's full addressable ID range.
* Sign-in errors report the actual failure instead of showing an invalid email or password for every case. The per-IP rate limit no longer counts successful sign-ins, so users behind one public address do not lock each other out.
* Invite User shows the role being granted and can invite a Viewer into a single organization. Previously every invite from the account Users page created an account admin.
* The Child Entities screen refreshes when the child roster changes, and notifies connected panels.

## Before you update

* Staff support requires v0.27.0 or newer. Systems on v0.26.0 or older use the previous remote-session protocol, and staff support will stop at the system's sign-in screen.
* Project format 0.11.0. Existing projects migrate automatically.
* The per-variable `dashboard` flag is converted to a monitored reading with no limits. Variable validation rules are not converted into monitor limits.
* Community drivers that declare routing information require this version.
