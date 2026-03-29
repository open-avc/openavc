#!/bin/bash
# OpenAVC Panel Kiosk Launcher
#
# Reads system.json to determine if kiosk mode is enabled.
# If enabled, waits for the server to be ready, then launches
# Chromium in fullscreen kiosk mode pointing at the Panel UI.
#
# Touch input (USB HID) is handled by the Linux kernel automatically.
# No special drivers needed for HDMI + USB touch displays.

CONFIG_FILE="${OPENAVC_DATA_DIR:-/var/lib/openavc}/system.json"
DEFAULT_URL="http://localhost:8080/panel"

# --- Read kiosk settings from system.json ---

read_config() {
    python3 -c "
import json, sys
try:
    with open('$CONFIG_FILE') as f:
        config = json.load(f)
    kiosk = config.get('kiosk', {})
    print(kiosk.get('enabled', False))
    print(kiosk.get('target_url', '$DEFAULT_URL'))
    print(kiosk.get('cursor_visible', False))
except Exception:
    print('False')
    print('$DEFAULT_URL')
    print('False')
"
}

CONFIG_OUTPUT=$(read_config)
KIOSK_ENABLED=$(echo "$CONFIG_OUTPUT" | sed -n '1p')
TARGET_URL=$(echo "$CONFIG_OUTPUT" | sed -n '2p')
CURSOR_VISIBLE=$(echo "$CONFIG_OUTPUT" | sed -n '3p')

# Exit cleanly if kiosk mode is not enabled
if [ "$KIOSK_ENABLED" != "True" ]; then
    echo "Kiosk mode is disabled in system.json. Exiting."
    exit 0
fi

echo "Kiosk mode enabled. Target: $TARGET_URL"

# --- Wait for the OpenAVC server to be ready ---

echo "Waiting for OpenAVC server..."
ATTEMPTS=0
MAX_ATTEMPTS=60

while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    if curl -sf http://localhost:8080/api/health > /dev/null 2>&1; then
        echo "Server is ready."
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 2
done

if [ $ATTEMPTS -eq $MAX_ATTEMPTS ]; then
    echo "WARNING: Server did not respond after ${MAX_ATTEMPTS} attempts. Launching anyway."
fi

# --- Configure display environment ---

# Disable screen blanking (works on both X11 and Wayland via DPMS)
if [ -n "$DISPLAY" ]; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
fi

# Hide cursor if configured (for touch-only panels)
if [ "$CURSOR_VISIBLE" != "True" ]; then
    # unclutter hides the cursor after brief inactivity
    if command -v unclutter &> /dev/null; then
        unclutter -idle 0.5 -root &
    fi
fi

# --- Launch Chromium in kiosk mode ---

# Clean up any previous Chromium crash flags (prevents "restore session" prompts)
CHROMIUM_DIR="/home/openavc/.config/chromium"
mkdir -p "$CHROMIUM_DIR/Default"
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' \
    "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null || true
sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' \
    "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null || true

echo "Launching Chromium kiosk: $TARGET_URL"

exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-translate \
    --no-first-run \
    --start-fullscreen \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --disable-features=TranslateUI \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --password-store=basic \
    --touch-events=enabled \
    --enable-touch-drag-drop \
    "$TARGET_URL"
