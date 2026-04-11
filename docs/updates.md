# System Updates

OpenAVC checks for updates automatically and can install them from the Programmer IDE. This guide covers how updates work, how to install them, and what to do if something goes wrong.

## How Updates Are Discovered

OpenAVC checks for new releases every hour by querying GitHub Releases. No cloud connection is needed. If a newer version is found, you'll see:

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

Do not close the browser or power off the system during this process. After the restart, the Programmer IDE reconnects automatically and shows a confirmation toast.

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
| **Linux (install.sh)** | Yes | Stops the service, extracts the new archive, rebuilds dependencies, restarts |
| **Docker** | No | Shows instructions: `docker compose pull && docker compose up -d` |
| **Git (development)** | No | Shows a notification to pull the latest code |

Docker and Git deployments still check for updates and show notifications, but you apply them manually using your normal workflow.

## Rollback

If an update causes problems, you can roll back to the previous version.

### Automatic Rollback

If the server crashes immediately after an update (fails to start twice in a row), it automatically restores the previous version. No action needed.

### Manual Rollback

From the Updates view, scroll to the **Rollback** section and click **Rollback to vX.Y.Z**. This restores the previous application code and restarts the server. Your projects, drivers, and configuration are preserved.

## Update Channels

OpenAVC supports two update channels:

- **Stable** (default): only sees final releases like v1.0.0
- **Beta**: also sees pre-releases like v1.0.0-beta.1

The channel is set in system.json under `updates.channel`, or via the environment variable `OPENAVC_UPDATE_CHANNEL`.

## Cloud-Managed Updates

If your system is connected to OpenAVC Cloud, your integrator may manage updates remotely:

- They can see your system's current version in the cloud portal
- They can trigger updates from the portal without needing local access
- They can set an update policy for your organization: manual, notify-only, or auto-update during a maintenance window

Even with cloud management, you can always check for and install updates locally from the Programmer IDE.

## Disabling Automatic Checks

To disable the background update check (the system will never check GitHub on its own):

Set `updates.check_enabled` to `false` in system.json, or set the environment variable `OPENAVC_UPDATE_CHECK=false`.

You can still check manually from the Programmer IDE at any time.

## See Also

- [Deployment Guide](deployment.md). Production deployment and system configuration
- [Getting Started](getting-started.md). Installation methods
