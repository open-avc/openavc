#!/bin/bash
# OpenAVC macOS uninstaller.
#
# Removes the background services the .pkg installed (the server LaunchDaemon
# and the menu-bar LaunchAgent), the application bundle, and the installer
# receipt. Dragging OpenAVC.app to the Trash does NOT do this on its own, so the
# menu-bar "Uninstall OpenAVC..." item and this script are the supported path.
#
# User projects and settings under /Library/Application Support/OpenAVC are
# KEPT by default so a reinstall finds them again. Pass --purge to remove them
# (and the logs) too.
#
# Must run as root:
#   sudo bash /Applications/OpenAVC.app/Contents/Resources/macos-uninstall.sh [--purge]
set -u

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

APP="/Applications/OpenAVC.app"
DATA_DIR="/Library/Application Support/OpenAVC"
LOG_DIR="/Library/Logs/OpenAVC"
DAEMON_PLIST="/Library/LaunchDaemons/com.openavc.server.plist"
AGENT_PLIST="/Library/LaunchAgents/com.openavc.menubar.plist"

echo "Stopping the OpenAVC server..."
launchctl bootout system/com.openavc.server 2>/dev/null || true

# Identify the logged-in GUI user and clear its first-run marker so a later
# reinstall opens the IDE again. (Stopping the menu-bar agent itself is the LAST
# step below: when this script is launched FROM that menu-bar app, booting the
# agent out tears down the caller, so every other removal must finish first.)
CONSOLE_UID="$(stat -f %u /dev/console 2>/dev/null)"
CONSOLE_USER="$(stat -f %Su /dev/console 2>/dev/null)"
[ -n "${CONSOLE_USER:-}" ] && rm -f "/Users/$CONSOLE_USER/Library/Application Support/OpenAVC/.ide-autoopened" 2>/dev/null || true

echo "Removing launch services..."
rm -f "$DAEMON_PLIST" "$AGENT_PLIST"

echo "Removing the application..."
rm -rf "$APP"
# Clear a copy already dragged to the Trash, if any.
[ -n "${CONSOLE_USER:-}" ] && rm -rf "/Users/$CONSOLE_USER/.Trash/OpenAVC.app" 2>/dev/null || true

echo "Forgetting the installer receipt..."
pkgutil --forget com.openavc.pkg >/dev/null 2>&1 || true

if [ "$PURGE" = "1" ]; then
    echo "Removing projects, settings, and logs..."
    rm -rf "$DATA_DIR" "$LOG_DIR"
else
    echo "Kept your projects and settings in: $DATA_DIR"
    echo "(re-run with --purge to remove those too)"
fi

# LAST: stop the running menu-bar agent. Its plist is already gone (above), so it
# will not return at login; this just closes the currently-running app. Done last
# because a menu-triggered run is a child of this very app.
if [ -n "${CONSOLE_UID:-}" ] && [ "$CONSOLE_UID" != "0" ]; then
    echo "Stopping the menu-bar app..."
    launchctl bootout "gui/$CONSOLE_UID/com.openavc.menubar" 2>/dev/null || true
fi

echo "Done. OpenAVC has been uninstalled."
exit 0
