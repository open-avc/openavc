# OpenAVC v0.25.1

A follow-up to 0.25.0 that fixes what upgrading to it turned up. If you are on
0.25.0, take this one.

## Updating and rolling back

The progress dialog now closes when the update finishes. On a system with a
password set, which is every real deployment, a successful update sat under
"Restarting server" until it gave up and called itself slow. The restart signs
this browser out, and the dialog was waiting on a connection that could not come
back, so the only way to learn the update had worked was to reload the page and
sign in again. It now checks the version the server is actually running.

Rolling back on macOS names the version it will restore, instead of asking you
to confirm "Rollback to v?".

A rollback you asked for is no longer written into the log as a failure of the
version you left. Reading a system's log later, a deliberate downgrade and a
crash looked the same.

Asking the installed server for its version now answers, instead of reporting
that the port is already in use.

## Starter projects on an upgraded system

Opening any of the four built-in starter projects on a system upgraded from
0.24.x gave you a device with no driver behind it. The starters kept the drivers
their original version shipped, and those no longer load on this release, while
the correct ones sat unused inside the installed software. Starters now refresh
to the drivers this release ships. A starter you have edited keeps your edits,
and a driver you have changed yourself is left alone.

## macOS

The updater now installs the application owned by the system administrator
account, matching what a fresh install produces. A self-update left it owned by
the account that built the release, which on a shared Mac means a standard user
could replace a file the system runs with full privileges. Worth applying on any
Mac that has already self-updated to 0.25.0.

## Browsing drivers

The driver library marks the drivers your system is too old to install and names
the release they need, rather than offering an Install button that fails once you
press it. This matters more than usual right now: every Python driver in the
catalog requires 0.25.0 or later, so on an older system most of the library
cannot be installed until you update.

A plugin that fails to install reports the reason instead of a generic error.
