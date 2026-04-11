#!/bin/bash
# OpenAVC Panel / Info Screen Launcher
#
# Runs from the labwc autostart inside the graphical session.
#
# If kiosk mode is enabled:  fullscreen Chromium showing the Panel UI.
# If kiosk mode is disabled: fullscreen Chromium showing a local info page
#   with IP address, access URLs, and setup instructions.
#
# Touch input (USB HID) is handled by the Linux kernel automatically.
# No special drivers needed for HDMI + USB touch displays.

CONFIG_FILE="${OPENAVC_DATA_DIR:-/var/lib/openavc}/system.json"
DEFAULT_URL="http://localhost:8080/panel"
INFO_PAGE="/tmp/openavc-info.html"

# --- Read settings from system.json ---

read_config() {
    python3 -c "
import json, sys
try:
    with open('$CONFIG_FILE') as f:
        config = json.load(f)
    kiosk = config.get('kiosk', {})
    network = config.get('network', {})
    print(kiosk.get('enabled', False))
    print(kiosk.get('target_url', '$DEFAULT_URL'))
    print(kiosk.get('cursor_visible', False))
    print(network.get('http_port', 8080))
except Exception:
    print('False')
    print('$DEFAULT_URL')
    print('False')
    print('8080')
"
}

CONFIG_OUTPUT=$(read_config)
KIOSK_ENABLED=$(echo "$CONFIG_OUTPUT" | sed -n '1p')
TARGET_URL=$(echo "$CONFIG_OUTPUT" | sed -n '2p')
CURSOR_VISIBLE=$(echo "$CONFIG_OUTPUT" | sed -n '3p')
PORT=$(echo "$CONFIG_OUTPUT" | sed -n '4p')

# --- Wait for the OpenAVC server to be ready ---

echo "Waiting for OpenAVC server..."
ATTEMPTS=0
MAX_ATTEMPTS=60

while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    if curl -sf http://localhost:${PORT}/api/health > /dev/null 2>&1; then
        echo "Server is ready."
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 2
done

if [ $ATTEMPTS -eq $MAX_ATTEMPTS ]; then
    echo "WARNING: Server did not respond after ${MAX_ATTEMPTS} attempts. Launching anyway."
fi

# --- Determine IP and URLs ---

IP_ADDR=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+')
HOSTNAME=$(hostname)

if [ "$PORT" = "80" ]; then
    BASE_URL="http://${IP_ADDR}"
    MDNS_URL="http://${HOSTNAME}.local"
else
    BASE_URL="http://${IP_ADDR}:${PORT}"
    MDNS_URL="http://${HOSTNAME}.local:${PORT}"
fi

# --- Determine what to show ---

if [ "$KIOSK_ENABLED" = "True" ]; then
    CHROMIUM_URL="$TARGET_URL"
    echo "Kiosk mode enabled. Target: $CHROMIUM_URL"
else
    # Generate info page
    echo "Kiosk mode disabled. Showing info screen."

    if [ -n "$IP_ADDR" ]; then
        PROGRAMMER_URL="${BASE_URL}/programmer"
        PANEL_URL="${BASE_URL}/panel"
        NETWORK_INFO="<div class=\"field\"><span class=\"label\">IP Address</span><span class=\"value\">${IP_ADDR}</span></div>"
    else
        PROGRAMMER_URL="${MDNS_URL}/programmer"
        PANEL_URL="${MDNS_URL}/panel"
        NETWORK_INFO="<div class=\"field\"><span class=\"label\">Network</span><span class=\"value\">No connection detected. Connect Ethernet and reboot.</span></div>"
    fi

    cat > "$INFO_PAGE" << INFOHTML
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenAVC</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e;
    color: #e0e0e0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 1.5rem;
  }
  .container {
    max-width: 540px;
    width: 100%;
    text-align: center;
  }
  .logo {
    font-size: 2rem;
    font-weight: 700;
    color: #8AB493;
    margin-bottom: 0.15rem;
    letter-spacing: -0.5px;
  }
  .subtitle {
    font-size: 0.85rem;
    color: #888;
    margin-bottom: 1.5rem;
  }
  .card {
    background: #16213e;
    border: 1px solid #2a2a4a;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
    text-align: left;
  }
  .card h2 {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: #8AB493;
    margin-bottom: 0.75rem;
  }
  .field {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 0.4rem 0;
    border-bottom: 1px solid #2a2a4a;
  }
  .field:last-child { border-bottom: none; }
  .label {
    color: #888;
    font-size: 0.8rem;
    flex-shrink: 0;
    margin-right: 1rem;
  }
  .value {
    font-family: "SF Mono", "Consolas", "Liberation Mono", monospace;
    font-size: 0.8rem;
    color: #fff;
    text-align: right;
    word-break: break-all;
  }
  .hint {
    background: #1a2340;
    border: 1px solid #2a2a4a;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    text-align: center;
  }
  .hint p {
    color: #888;
    font-size: 0.8rem;
    line-height: 1.4;
  }
  .hint strong {
    color: #8AB493;
  }
</style>
</head>
<body>
<div class="container">
  <div class="logo">OpenAVC</div>
  <div class="subtitle">Room Control System</div>

  <div class="card">
    <h2>Network</h2>
    ${NETWORK_INFO}
    <div class="field"><span class="label">Hostname</span><span class="value">${HOSTNAME}.local</span></div>
    <div class="field"><span class="label">Port</span><span class="value">${PORT}</span></div>
  </div>

  <div class="card">
    <h2>Access From Another Computer</h2>
    <div class="field"><span class="label">Programmer</span><span class="value">${PROGRAMMER_URL}</span></div>
    <div class="field"><span class="label">Panel</span><span class="value">${PANEL_URL}</span></div>
    <div class="field"><span class="label">SSH</span><span class="value">ssh openavc@${HOSTNAME}.local</span></div>
  </div>

  <div class="hint">
    <p>To show the <strong>Panel UI</strong> on this display, enable <strong>Kiosk Mode</strong><br>in the Programmer under Settings, then reboot this device.</p>
  </div>
</div>
</body>
</html>
INFOHTML

    CHROMIUM_URL="file://${INFO_PAGE}"
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
    "$CHROMIUM_URL"
