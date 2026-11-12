# OpenAVC Cloud Security and Data Handling

OpenAVC Cloud is the optional hosted service for monitoring, backing up and managing paired
OpenAVC systems. This page covers where it runs, how accounts are kept apart, what is encrypted,
who at OpenAVC can reach your data, and how long each kind of data is kept. The connection
itself and what a paired system sends are in the
[Network & Security Cut Sheet](it-network-guide.md#cloud-platform-optional). Personal data is
covered by the [privacy policy](https://openavc.com/privacy), and the AI assistant by
[AI Assistant: What Is Sent and Kept](ai-assistant-data.md).

## Where it runs

| | |
|---|---|
| Hosting | DigitalOcean, one region: New York |
| Application | A DigitalOcean server behind HTTPS (TLS certificates from Let's Encrypt) |
| Database | DigitalOcean Managed PostgreSQL, encrypted at rest and in transit |
| Cache | DigitalOcean Managed Valkey |
| Operated by | OpenAVC LLC, with no 24-hour on-call |

The service runs in one region with no standby elsewhere. If it cannot be reached, every paired
system keeps controlling its space exactly as before. The portal, alert notifications, project
backups, remote access and the AI assistant wait until it is back. After a failure, the service
is restored from the hosting provider's database backups, normally within one business day.

## How accounts are kept apart

- All accounts share one database, and every request is limited to the account the person or
  API key belongs to.
- A paired system belongs to one account. It proves itself with its own key each time it
  connects, and every message after that is signed
  ([Authentication](it-network-guide.md#authentication)).
- Within an account, a person is an owner, an admin or a viewer (read-only). An integrator's
  client organization has its own admins and viewers, who see only that organization's spaces.
- Anyone can turn on two-step verification for their own sign-in. An account can require it for
  all its people, and an integrator can require it for a client organization.

## Encryption

- **In transit:** browsers, API clients and paired systems connect over TLS 1.2 or later.
- **At rest:** the database is encrypted by the hosting provider. These are encrypted again with
  a key the application holds outside the database: every stored project copy (backups, pushed
  projects and the copies kept to roll them back, template versions), each paired system's key,
  webhook signing secrets and two-step verification secrets. A system's certificate private key
  is made on the system and never sent.
- **Stored only as a hash:** account passwords (bcrypt), API keys, sign-in sessions and pairing
  tokens.
- **Device logins:** on OpenAVC 0.40.0 and later they stay on the system, and the project copies
  it sends hold a placeholder in place of each one. A system on an earlier version includes them
  in its copies until it is updated.

## Who at OpenAVC can reach your data

- Staff sign in to a separate staff console with their own accounts, and two-step verification
  is required.
- Staff roles are viewer, support and superadmin. Only support and superadmin can read the
  contents of a project pushed from the portal.
- Every staff sign-in, every change staff make, and every time staff open a customer's data in
  the staff console is recorded in the staff audit log, kept for a year.
- Staff reach one of your systems only during a support session someone in your account grants
  from the portal, for one named system and support request, for one, four or twenty-four hours.
  It reaches the Programmer, the network diagnostic and the project file, nothing else. You can
  end it at any time, and every action taken in it is shown in your portal
  ([Remote access tunnels](it-network-guide.md#remote-access-tunnels)).

## Backups of the service

The hosting provider takes daily database backups with point-in-time recovery. A record deleted
from the service can remain in those backups for up to 30 days. A restore builds a new database
from a backup, checks it, and moves the service onto it.

## How long data is kept

The portal states each of these where the data is shown.

| Data | Kept |
|---|---|
| Project backups | The newest copy while the system is paired. Older copies are deleted 90 days after they were replaced, and at most 30 are kept for each system |
| Health readings and monitored readings | 7 days |
| Current device state values | 24 hours after the system last sends them |
| Alerts | While open, then 90 days after they are resolved |
| Uptime history | 25 months |
| Pushes, updates and commands sent from the portal, with their rollback copies | 90 days after they finish, or after they were due if they never ran |
| Templates | While the template exists, with its newest 20 versions and any version a system still runs |
| AI conversations | Until someone deletes them, or the account is deleted |
| AI usage records (tokens and cost) | 24 months |
| Remote-access history | A year |
| Support requests | Two years after they close |
| Problem reports and device audits sent to OpenAVC | 12 months; the sender's IP address 30 days |
| An unpaired system | Its uptime, alerts, reports and backups, a year after it was unpaired |
| A removed organization | A year after it was removed |
| A person removed from an account | After 30 days, their sign-in and email address are erased and their AI chats deleted. What they did stays, under "Removed user" |
| Expired sign-in sessions and pairing tokens | A week after they expire |
| Staff audit log | A year |
| Billing records | 7 years |
| Request logs (IP address, address requested) | 30 days |
| Application log (sign-ins by email address) | Overwritten as newer entries fill it |

Closing and deleting an account are described in the [privacy policy](https://openavc.com/privacy).
An error a system returns through remote access is logged by its status and address only, never
its contents.

## Service providers

DigitalOcean hosts the service, Postmark sends its email, Stripe takes payments, and Anthropic
runs the AI assistant when someone uses it. The full list is in the
[privacy policy](https://openavc.com/privacy).

## Your data

**My Account > Your data** in the portal downloads everything on the account as one JSON file,
or one table as a spreadsheet. The file lists what it leaves out and where to get it. An owner
closes or deletes the account from **My Account**.

## Related

- [Network & Security Cut Sheet](it-network-guide.md): the connection, what a system sends, what
  the cloud can do on it
- [AI Assistant: What Is Sent and Kept](ai-assistant-data.md)
- [Hardened Deployment](hardened-deployment.md)
