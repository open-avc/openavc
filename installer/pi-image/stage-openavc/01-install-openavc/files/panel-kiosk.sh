#!/bin/bash
# OpenAVC Panel / Setup Screen Launcher
#
# Runs from the labwc autostart inside the graphical session.
#
# If kiosk mode is enabled:  fullscreen Chromium showing the Panel UI.
# If kiosk mode is disabled: fullscreen Chromium showing the server-served
#   setup screen (/setup) with live IP address, access URLs, and first-run
#   instructions.
#
# If the server never comes up, a minimal local fallback page is shown that
# keeps probing /api/health and forwards to the real page once it passes.
#
# Touch input (USB HID) is handled by the Linux kernel automatically.
# No special drivers needed for HDMI + USB touch displays.

CONFIG_FILE="${OPENAVC_DATA_DIR:-/var/lib/openavc}/system.json"
FALLBACK_PAGE="/tmp/openavc-fallback.html"

# --- Read settings from system.json ---

read_config() {
    python3 -c "
import json, sys
try:
    with open('$CONFIG_FILE') as f:
        config = json.load(f)
    kiosk = config.get('kiosk', {})
    network = config.get('network', {})
    tls = config.get('tls') or {}
    tls_enabled = bool(tls.get('enabled', False))
    proto = 'https' if tls_enabled else 'http'
    effective_port = int(tls.get('port', 8443)) if tls_enabled else int(network.get('http_port', 8080))
    default_url = f'{proto}://localhost:{effective_port}/panel'
    print(kiosk.get('enabled', False))
    print(kiosk.get('target_url', default_url))
    print(kiosk.get('cursor_visible', False))
    print(int(network.get('http_port', 8080)))
    print(proto)
    print(effective_port)
except Exception:
    print('False')
    print('http://localhost:8080/panel')
    print('False')
    print('8080')
    print('http')
    print('8080')
"
}

CONFIG_OUTPUT=$(read_config)
KIOSK_ENABLED=$(echo "$CONFIG_OUTPUT" | sed -n '1p')
TARGET_URL=$(echo "$CONFIG_OUTPUT" | sed -n '2p')
CURSOR_VISIBLE=$(echo "$CONFIG_OUTPUT" | sed -n '3p')
PORT=$(echo "$CONFIG_OUTPUT" | sed -n '4p')
PROTO=$(echo "$CONFIG_OUTPUT" | sed -n '5p')
EFFECTIVE_PORT=$(echo "$CONFIG_OUTPUT" | sed -n '6p')

# --- Determine what this display should show ---
#
# Kiosk mode: the configured target (the panel by default).
# Otherwise:  the server-served setup screen. It renders live network info
# and first-run instructions from /api/setup/status.

SETUP_URL="${PROTO}://localhost:${EFFECTIVE_PORT}/setup"

if [ "$KIOSK_ENABLED" = "True" ]; then
    NEXT_URL="$TARGET_URL"
    echo "Kiosk mode enabled. Target: $NEXT_URL"
else
    NEXT_URL="$SETUP_URL"
    echo "Kiosk mode disabled. Showing setup screen."
fi

# --- Wait for the OpenAVC server to be ready ---
#
# When TLS is on, the server's cert is self-signed by default — curl's -k skips
# verification during the boot health-check wait loop. The flag is a no-op for
# plain http://.

echo "Waiting for OpenAVC server..."
ATTEMPTS=0
MAX_ATTEMPTS=60

while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    if curl -sf -k ${PROTO}://localhost:${EFFECTIVE_PORT}/api/health > /dev/null 2>&1; then
        echo "Server is ready."
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 2
done

if [ $ATTEMPTS -eq $MAX_ATTEMPTS ]; then
    # Server didn't come up. Show a local fallback page that keeps probing
    # the health endpoint and forwards to the real page once it passes.
    echo "WARNING: Server did not respond after ${MAX_ATTEMPTS} attempts. Showing fallback page."

    HOSTNAME=$(hostname)
    cat > "$FALLBACK_PAGE" << FALLBACKHTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenAVC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e; color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; padding: 1.5rem; text-align: center;
  }
  .logo { font-size: 2rem; font-weight: 700; color: #8AB493; margin-bottom: 0.5rem; }
  p { color: #888; font-size: 0.85rem; line-height: 1.6; }
  .host { font-family: "Consolas", monospace; color: #fff; }
</style>
</head>
<body>
<div>
  <div class="logo">OpenAVC</div>
  <p>The OpenAVC server on <span class="host">${HOSTNAME}</span> has not started yet.<br>
  This screen keeps checking and will continue automatically.<br>
  If it never does, check the device's power and logs.</p>
</div>
<script>
(function () {
  'use strict';
  setInterval(function () {
    fetch('${PROTO}://localhost:${EFFECTIVE_PORT}/api/health', { mode: 'no-cors', cache: 'no-store' })
      .then(function () { window.location.replace('${NEXT_URL}'); })
      .catch(function () {});
  }, 3000);
})();
</script>
</body>
</html>
FALLBACKHTML

    CHROMIUM_URL="file://${FALLBACK_PAGE}"
else
    CHROMIUM_URL="$NEXT_URL"
fi

# --- Configure display environment ---

# Disable screen blanking
if [ -n "$DISPLAY" ]; then
    xset s off 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    xset s noblank 2>/dev/null || true
fi

# Hide cursor if configured (for touch-only panels)
if [ "$CURSOR_VISIBLE" != "True" ] && [ "$KIOSK_ENABLED" = "True" ]; then
    if command -v unclutter &> /dev/null; then
        unclutter -idle 0.5 -root &
    fi
fi

# --- Clean up Chromium crash flags ---

CHROMIUM_DIR="/home/openavc/.config/chromium"
mkdir -p "$CHROMIUM_DIR/Default"
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/' \
    "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null || true
sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/' \
    "$CHROMIUM_DIR/Default/Preferences" 2>/dev/null || true

# --- Launch Chromium ---

echo "Launching Chromium: $CHROMIUM_URL"

exec chromium \
    --ozone-platform=wayland \
    --kiosk \
    --start-maximized \
    --start-fullscreen \
    --noerrdialogs \
    --disable-infobars \
    --disable-translate \
    --no-first-run \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    --disable-features=TranslateUI \
    --disable-session-crashed-bubble \
    --disable-component-update \
    --password-store=basic \
    --touch-events=enabled \
    --enable-touch-drag-drop \
    --allow-insecure-localhost \
    "$CHROMIUM_URL"
