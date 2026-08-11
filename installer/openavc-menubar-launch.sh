#!/bin/bash
# OpenAVC.app's double-click entry point — brings the menu bar item back.
#
# This is the bundle's CFBundleExecutable, so it is what runs when someone
# opens OpenAVC from Finder, Spotlight, or `open -a OpenAVC`. On a Mac the
# app icon is the obvious thing to reach for after quitting a menu bar app,
# and before this existed it ran the *server's* launchd wrapper instead —
# silently starting a second, unprivileged server with no window to show for
# it. Now the icon does the one thing you'd expect it to.
#
# It does NOT run the menu bar binary directly. The menu bar is a LaunchAgent
# (com.openavc.menubar), so asking launchctl to restart the job is what keeps
# exactly one icon in the bar no matter the starting state: quit, never
# started, or already running. Running the binary by hand would give you a
# second icon whenever the agent was already up.
#
# The server is untouched by all of this. It is a separate LaunchDaemon and
# keeps running whether this app is open or not.

set -u

LABEL="com.openavc.menubar"
AGENT_PLIST="/Library/LaunchAgents/com.openavc.menubar.plist"
UID_NUM="$(id -u)"

# Already loaded (the usual case — "Quit" stops the process but leaves the job
# registered). -k restarts it if it happens to be running, so this one call
# covers both quit-then-reopen and open-while-already-running.
if launchctl kickstart -k "gui/$UID_NUM/$LABEL" 2>/dev/null; then
    exit 0
fi

# Not loaded: the agent was booted out (the uninstaller does this), or this
# user has never had it bootstrapped — a second account on a Mac where someone
# else ran the installer.
if [ -f "$AGENT_PLIST" ] &&
   launchctl bootstrap "gui/$UID_NUM" "$AGENT_PLIST" 2>/dev/null; then
    exit 0
fi

# No agent plist at all: a bundle that was copied into place by hand rather
# than installed from the .pkg. Run the menu bar directly so the icon still
# appears; it just won't come back by itself at the next login.
MENUBAR="$(cd "$(dirname "$0")/../Resources/menubar" && pwd)/openavc-menubar"
if [ -x "$MENUBAR" ]; then
    exec "$MENUBAR"
fi

echo "OpenAVC: menu bar app not found at $MENUBAR" >&2
exit 1
