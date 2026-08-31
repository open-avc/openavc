# System Updates

OpenAVC checks for updates automatically and can install them from the Programmer IDE. This guide covers how updates work, how to install them, and what to do if something goes wrong.

## How Updates Are Discovered

OpenAVC checks for new releases once a day by querying GitHub Releases. No cloud connection is needed. The interval is configurable in `system.json` under `updates.auto_check_interval_hours` if you want it more or less often. If a newer version is found, you'll see:

- An **update indicator** in the Programmer IDE sidebar (an upward arrow icon above the connection dot)
- An **update card** on the Dashboard: "OpenAVC vX.Y.Z available"

Both link to the Update view where you can see details and install.

## Checking Manually

Open the Programmer IDE and navigate to the **Updates** view (click the sidebar indicator, or click the dashboard card). Click **Check for Updates** to query GitHub immediately instead of waiting for the next automatic check.

## Installing an Update

From the Updates view, click **Install vX.Y.Z**. A progress dialog walks through each step:

1. **Creating backup** of your projects, drivers, and configuration
2. **Downloading** the update from GitHub (with a progress bar)
3. **Verifying** the download's SHA256 checksum
4. **Applying** the update to your system
5. **Restarting** the server

Verification is mandatory. If a download cannot be checked against a published SHA256 checksum, the update is refused and nothing is applied, leaving your current version untouched.

On Linux, the Raspberry Pi appliance, and macOS, updates are also **cryptographically signed**. Each release artifact carries a detached signature, and a privileged pre-start step verifies that signature against a trusted key shipped with your installation before extracting anything. A tampered or unsigned artifact is refused, so a compromised download or release asset cannot be applied. This is what makes the automatic-update path safe to run unattended.

Do not close the browser or power off the system during this process. After the restart, the Programmer IDE reconnects automatically and shows a confirmation toast.

### If an update cannot be installed right now

On Linux, the Raspberry Pi image and appliance hardware, an update is applied by a privileged step that runs just before the server starts. Part of that step downloads the new version's Python dependencies, which needs internet access at that moment. If it cannot get them, or the Python environment cannot be rebuilt, the system is put back exactly as it was and keeps running the version it was on.

That update is not lost. It is set aside and tried again the next time the system restarts, up to three times, so a system that was offline or on a network with blocked DNS at the wrong moment picks it up by itself once the network is working. Until it succeeds, the Updates view says which version has not been installed and what stopped it.

After the third attempt nothing tries again on its own. The Updates view keeps saying so, and installing the update again from that page starts over.

## What Gets Updated

Only the application code is updated. Your data is never touched:

- Projects and scripts are preserved
- Installed drivers are preserved
- System configuration (system.json) is preserved
- Cloud pairing credentials are preserved

A backup zip is created before every update, stored in your data directory under `backups/`. The five most recent backups are kept automatically.

## Deployment Types

How updates are applied depends on how OpenAVC was installed:

| Deployment | Can self-update? | How it works |
|-----------|-----------------|-------------|
| **Windows Installer** | Yes | Downloads and runs the new installer silently |
| **macOS Installer (.pkg)** | Yes | Downloads the archive, restarts. The launchd wrapper swaps the app bundle on startup. |
| **Linux (install.sh)** | Yes | Downloads the archive, restarts the service. A helper script applies the update on startup. |
| **Docker** | No | Shows instructions: `docker compose pull && docker compose up -d` |
| **Git (development)** | No | Shows a notification to pull the latest code |

Docker and Git deployments still check for updates and show notifications, but you apply them manually using your normal workflow.

Every deployment above can also be updated without an internet connection. See [Updating a System With No Internet Access](#updating-a-system-with-no-internet-access).

## Updating a System With No Internet Access

OpenAVC needs an internet connection to *find* and *download* a new version, not to install one. A system on an isolated network can be updated from files you carry to it.

Download the files on any machine that has internet, from the Assets list of the release at `https://github.com/open-avc/openavc/releases`. Take the file for your deployment, and take the matching `.sig` file listed next to it. The `.sig` is a signature the system uses to confirm the file is genuine before it installs anything.

### Windows

Copy `OpenAVC-Setup-<version>.exe` to the system and run it. It upgrades the existing installation in place. Projects, drivers, plugins and settings live in `C:\ProgramData\OpenAVC` and are not touched.

### macOS

Copy `OpenAVC-<version>-macos-<arch>.pkg` to the system and run it. Use `arm64` for Apple Silicon and `x86_64` for Intel. It upgrades in place. Data lives in `/Library/Application Support/OpenAVC` and is not touched.

### Linux and Raspberry Pi

Copy the archive and its signature into the data directory, keeping the two files next to each other, then stage the update and restart the service:

```bash
sudo cp openavc-0.29.0-linux-arm64.tar.gz openavc-0.29.0-linux-arm64.tar.gz.sig /var/lib/openavc/

sudo tee /var/lib/openavc/apply-update.json >/dev/null <<'JSON'
{"artifact": "/var/lib/openavc/openavc-0.29.0-linux-arm64.tar.gz",
 "from_version": "0.28.0",
 "to_version": "0.29.0"}
JSON

sudo systemctl restart openavc
```

Use `arm64` on a Raspberry Pi and `amd64` on a standard PC or server. Set `from_version` to the version currently running and `to_version` to the one you are installing.

A privileged step that runs just before the server starts picks the file up and applies it. This is the same step the in-app update uses. Watch it work with:

```bash
journalctl -u openavc -b | grep update-helper
```

Do not use `install.sh` for this. That script always fetches the release from GitHub and cannot install a file you already have.

Two things to know before you start:

- **Make a backup first.** In the Programmer IDE, open the **Project** view and click **Create Backup**. An update staged by hand does not create the automatic pre-update backup, so this is your copy of the projects and settings. Rolling the application code back afterwards still works normally from the Updates view.
- **Dependencies.** After swapping in the new version, the system re-checks its Python packages. Almost every release uses the packages already installed and needs no internet for this. If a release does change one, the system cannot fetch it, so it rolls itself back and keeps running the version you had. The journal line above names the package it wanted. Install that release with internet available. On a system with no internet at all, expect the next two restarts to be slower: each one tries the update again before giving up, which is what lets a system that was only temporarily offline install it by itself. After the third attempt it stops and the Updates view says so.

### Docker

On a machine with internet, pull the image and write it to a file:

```bash
docker pull ghcr.io/open-avc/openavc:latest
docker save ghcr.io/open-avc/openavc:latest -o openavc.tar
```

Copy `openavc.tar` to the isolated system, then load and restart:

```bash
docker load -i openavc.tar
docker compose up -d
```

### Raspberry Pi image file

The `.img.xz` image writes a fresh card and is for setting up a new system, not for updating one that is already running. A Pi already running OpenAVC updates with the Linux procedure above.

### Appliance hardware

The all-in-one appliance applies updates through its device supervisor, and has no manual file-based procedure. To update one on an isolated network, connect it temporarily to a network that has internet access.

### Development checkouts

Update the source with your normal git workflow and restart the server.


## Rollback

If an update causes problems, you can roll back to the previous version.

### Automatic Rollback

If the server crashes immediately after an update (fails to start twice in a row), it automatically restores the previous version. Your project data is restored from the backup taken just before the update, so code and data go back together. The project files from the failed update are kept in a `projects.pre-rollback` folder next to your projects in case you need anything from them. No action needed.

### Manual Rollback

From the Updates view, scroll to the **Rollback** section and click **Rollback to vX.Y.Z**. This restores the previous application code and restarts the server. Your projects, drivers, and configuration are preserved as they are — manual rollback does not rewind your project data.

### Rolling back to a version older than v0.29.0

v0.29.0 changed how the admin password is stored, from the password itself to a hash of it. Rolling back to v0.28.0 or earlier works normally, but that older version cannot read the new form, so the Programmer sign-in will reject the password. Everything else, including the room panel, is unaffected.

Two ways to get back in, both needing access to the host's files:

1. Restore the previous `system.json` from the backup taken just before the update. It is in the `backups` folder inside the data directory, named `pre-update-v0.28.0-<date>.zip`, with `system.json` at the top of the archive. This brings back the password you were using.
2. Or set `auth.programmer_password` to `""` in `system.json` and restart. The system returns to unclaimed and the next visit to the Programmer offers the "create admin password" screen. Projects, devices and settings are untouched.

This applies only when crossing back over v0.29.0. Rollbacks between v0.29.0 and later versions are not affected.

### Rolling back across the API key change

v0.30.0 does the same thing to the API key: it is stored as a hash instead of the key itself. Rolling back to a version before v0.30.0 leaves integrations getting 401 responses, because the older version compares the key literally against a value that is now a hash. The Programmer sign-in and the room panel are unaffected.

The same two routes apply. Restore `system.json` from the pre-update backup, which brings back the key your integrations are already using. Or set `auth.api_key` to a key of your choosing in `system.json`, restart, and update the integrations to match.

## Update Channels

OpenAVC supports two update channels:

- **Stable** (default): only sees final releases like v1.0.0
- **Beta**: also sees pre-releases like v1.0.0-beta.1

The channel is set in system.json under `updates.channel`, or via the environment variable `OPENAVC_UPDATE_CHANNEL`.

## Cloud-Managed Updates

If your system is connected to OpenAVC Cloud, your integrator may manage updates remotely:

- They can see your system's current version in the cloud portal
- They can trigger updates from the portal without needing local access
- They can stage an update without restarting: it appears in the Programmer IDE's Updates view as "staged from the cloud", ready to install whenever it suits the space
- They can set an update policy for your organization: manual, notify-only, or auto-update during a maintenance window

Even with cloud management, you can always check for and install updates locally from the Programmer IDE.

## Disabling Automatic Checks

To disable the background update check (the system will never check GitHub on its own):

Set `updates.check_enabled` to `false` in system.json, or set the environment variable `OPENAVC_UPDATE_CHECK=false`.

You can still check manually from the Programmer IDE at any time.

## See Also

- [Deployment Guide](deployment.md). Production deployment and system configuration
- [Getting Started](getting-started.md). Installation methods
