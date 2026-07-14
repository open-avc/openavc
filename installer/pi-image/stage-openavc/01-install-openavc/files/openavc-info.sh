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

# render_box TITLE LINE [LINE ...]
#
# Prints a Unicode box to stdout, sized to its widest line so the borders
# always line up no matter how long the IP address, URL, or hostname is.
# The title is centred; a separator rule is drawn under it; one blank line
# pads the top and bottom of the body. Body lines are left-aligned (they
# carry their own leading indent). All body/title text is ASCII, so ${#...}
# measures display columns regardless of locale.
render_box() {
    local title="$1"; shift
    local -a body=("$@")
    local inner=${#title} line
    for line in "${body[@]}"; do
        [ ${#line} -gt "$inner" ] && inner=${#line}
    done
    inner=$((inner + 2))  # side gutters

    local rule="" i
    for ((i = 0; i < inner; i++)); do rule+="═"; done

    local tpad=$(( (inner - ${#title}) / 2 ))
    printf '    ╔%s╗\n' "$rule"
    printf '    ║%*s%s%*s║\n' "$tpad" '' "$title" "$((inner - tpad - ${#title}))" ''
    printf '    ╠%s╣\n' "$rule"
    printf '    ║%*s║\n' "$inner" ''
    for line in "${body[@]}"; do
        printf '    ║%-*s║\n' "$inner" "$line"
    done
    printf '    ║%*s║\n' "$inner" ''
    printf '    ╚%s╝\n' "$rule"
}

main() {
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
        {
            echo
            echo
            render_box "OpenAVC Room Control" \
                "  IP Address:   ${IP_ADDR}" \
                "" \
                "  Programmer:   ${URL}/programmer" \
                "  Panel:        ${URL}/panel" \
                "  mDNS:         ${MDNS_URL}"
            echo
        } > "$TTY" 2>/dev/null
    else
        {
            echo
            echo
            render_box "OpenAVC Room Control" \
                "  No network connection detected." \
                "  Connect Ethernet and reboot, or configure" \
                "  Wi-Fi via raspi-config." \
                "" \
                "  mDNS:  http://openavc.local:${PORT}"
            echo
        } > "$TTY" 2>/dev/null
    fi
}

# Only run when executed directly, so tests can source the file to exercise
# render_box without triggering the network wait or writing to the console.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main
fi
