#!/bin/bash
# OpenAVC Boot Info Display
#
# Shows the IP address and access URLs on the HDMI console (TTY1).
# Runs early in boot, before the desktop session starts, so the user
# can see how to connect even if they have no keyboard or mDNS.
#
# The message stays visible on the console until the desktop takes over.
# If no desktop (headless), it stays visible indefinitely.

CONFIG_FILE="${OPENAVC_DATA_DIR:-/var/lib/openavc}/system.json"
TTY="/dev/tty1"
PORT=8080

# Read port from system.json if available
if [ -f "$CONFIG_FILE" ]; then
    CONFIGURED_PORT=$(python3 -c "
import json
try:
    with open('$CONFIG_FILE') as f:
        print(json.load(f).get('network', {}).get('http_port', $PORT))
except:
    print($PORT)
" 2>/dev/null)
    if [ -n "$CONFIGURED_PORT" ]; then
        PORT=$CONFIGURED_PORT
    fi
fi

# Wait for network (up to 30 seconds)
ATTEMPTS=0
IP_ADDR=""
while [ $ATTEMPTS -lt 15 ]; do
    # Get the first non-loopback IPv4 address
    IP_ADDR=$(ip -4 route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+')
    if [ -n "$IP_ADDR" ]; then
        break
    fi
    ATTEMPTS=$((ATTEMPTS + 1))
    sleep 2
done

HOSTNAME=$(hostname)

# Build the URL strings
if [ "$PORT" = "80" ]; then
    URL="http://${IP_ADDR}"
    MDNS_URL="http://${HOSTNAME}.local"
else
    URL="http://${IP_ADDR}:${PORT}"
    MDNS_URL="http://${HOSTNAME}.local:${PORT}"
fi

# Clear the TTY and display the info banner
clear > "$TTY" 2>/dev/null

if [ -n "$IP_ADDR" ]; then
    cat > "$TTY" 2>/dev/null << EOF


    ╔══════════════════════════════════════════════════╗
    ║            OpenAVC Room Control                  ║
    ╠══════════════════════════════════════════════════╣
    ║                                                  ║
    ║  IP Address:   ${IP_ADDR}$(printf '%*s' $((27 - ${#IP_ADDR})) '')║
    ║                                                  ║
    ║  Programmer:   ${URL}/programmer$(printf '%*s' $((27 - ${#URL} - 11)) '')║
    ║  Panel:        ${URL}/panel$(printf '%*s' $((27 - ${#URL} - 6)) '')║
    ║  mDNS:         ${MDNS_URL}$(printf '%*s' $((27 - ${#MDNS_URL})) '')║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝

EOF
else
    cat > "$TTY" 2>/dev/null << EOF


    ╔══════════════════════════════════════════════════╗
    ║            OpenAVC Room Control                  ║
    ╠══════════════════════════════════════════════════╣
    ║                                                  ║
    ║  No network connection detected.                 ║
    ║  Connect Ethernet and reboot, or configure       ║
    ║  Wi-Fi via raspi-config.                         ║
    ║                                                  ║
    ║  mDNS:  http://openavc.local:${PORT}$(printf '%*s' $((18 - ${#PORT})) '')║
    ║                                                  ║
    ╚══════════════════════════════════════════════════╝

EOF
fi
