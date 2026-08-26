#!/bin/bash
# OpenAVC Firewall Sync
#
# Runs before the openavc service starts via ExecStartPre=-+ and keeps the
# host firewall in step with the configured listeners: the HTTP port, the
# HTTPS port when TLS is enabled, and port 80 when the Short URLs redirect
# is enabled. Every one of those settings requires a service restart to take
# effect, and this runs at exactly that moment — so enabling a feature in
# Settings opens its port, and disabling it closes the port again.
#
# It ALSO opens ports a plugin declared, read from plugin_ports.json (written
# by the server; see openavc/core/plugin_ports.py). A plugin that bundles a
# sidecar listens on a port nothing else here knows about — the Video Panel's
# MediaMTX binds UDP 8189 for WebRTC — and until it is open a panel on the
# network gets no picture and no explanation.
#
# Ports are `port/proto` throughout, because a plugin port may be UDP and the
# configured listeners never are. A state file from an older version holds
# bare numbers; those are read as /tcp, which is what they were.
#
# TIMING, worth knowing: this runs BEFORE the server, so it acts on what the
# previous run left behind. Enabling a plugin opens its port at the next
# service start, not immediately. The server says so at the time.
#
# - The "+" prefix runs this script as root (ufw/firewall-cmd need it)
# - The "-" prefix ensures non-zero exit doesn't block service startup
# - Environment= variables are NOT available to ExecStartPre (systemd #2545),
#   so the data directory is passed as a command-line argument and ports are
#   read from system.json (the file Settings writes) with shipped defaults
# - No active firewall (no ufw, no firewalld, or both inactive) -> no-op.
#   Only ports this script opened earlier (tracked in $DATA_DIR/.firewall_ports)
#   are ever removed; rules an admin added by hand are never touched.
#
# MUST exit 0 always. Non-zero exit from ExecStartPre permanently stops the
# service (Restart=always does NOT retry ExecStartPre failures).
#
# --dry-run prints the detected backend and planned changes without applying
# anything (used by the test suite; also handy for support).

DATA_DIR="${1:-/var/lib/openavc}"
CONFIG_FILE="$DATA_DIR/system.json"
PLUGIN_PORTS_FILE="$DATA_DIR/plugin_ports.json"
STATE_FILE="$DATA_DIR/.firewall_ports"
LOG_TAG="firewall-sync"
PYTHON="${PYTHON:-/usr/bin/python3}"

DRY_RUN=0
for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && DRY_RUN=1
done

log() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "$1"
    else
        logger -t "$LOG_TAG" "$1" 2>/dev/null || echo "$LOG_TAG: $1"
    fi
}

# --- Desired ports from system.json (defaults match openavc/system_config.py) ---

desired_ports() {
    "$PYTHON" - "$CONFIG_FILE" "$PLUGIN_PORTS_FILE" <<'PYEOF'
import json, sys

cfg = {}
try:
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
except Exception:
    pass  # missing/unreadable config -> shipped defaults

network = cfg.get("network", {}) if isinstance(cfg, dict) else {}
tls = cfg.get("tls", {}) if isinstance(cfg, dict) else {}

# The instance's own listeners. All TCP; none of these is ever UDP.
ports = {(int(network.get("http_port", 8080)), "tcp")}
if tls.get("enabled", False):
    ports.add((int(tls.get("port", 8443)), "tcp"))
if network.get("port80_redirect", False):
    ports.add((80, "tcp"))

# Ports a plugin declared. A malformed entry is skipped rather than failing the
# whole sync: one bad plugin manifest must not close the HTTP port.
try:
    with open(sys.argv[2]) as f:
        declared = json.load(f).get("ports") or []
except Exception:
    declared = []
for entry in declared:
    try:
        port = int(entry["port"])
        proto = str(entry.get("protocol", "tcp")).lower()
    except (TypeError, ValueError, KeyError):
        continue
    if 1 <= port <= 65535 and proto in ("tcp", "udp"):
        ports.add((port, proto))

print(" ".join(f"{p}/{proto}" for p, proto in sorted(ports)))
PYEOF
}

# --- Backend detection (FIREWALL_SYNC_BACKEND overrides, for tests) ---

detect_backend() {
    if [ -n "${FIREWALL_SYNC_BACKEND:-}" ]; then
        echo "$FIREWALL_SYNC_BACKEND"
        return
    fi
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        echo "ufw"
        return
    fi
    if command -v firewall-cmd >/dev/null 2>&1 && [ "$(firewall-cmd --state 2>/dev/null)" = "running" ]; then
        echo "firewalld"
        return
    fi
    echo "none"
}

run_or_print() {
    if [ "$DRY_RUN" = "1" ]; then
        echo "WOULD RUN: $*"
    else
        "$@" >/dev/null 2>&1 || log "command failed (continuing): $*"
    fi
}

main() {
    DESIRED="$(desired_ports)"
    if [ -z "$DESIRED" ]; then
        log "could not compute ports; leaving firewall unchanged"
        return
    fi

    BACKEND="$(detect_backend)"
    [ "$DRY_RUN" = "1" ] && echo "BACKEND=$BACKEND" && echo "DESIRED=$DESIRED"
    if [ "$BACKEND" = "none" ]; then
        return
    fi

    MANAGED=""
    if [ -f "$STATE_FILE" ]; then
        # A state file written before UDP existed holds bare port numbers.
        # Those were all TCP, so read them as such -- otherwise every one of
        # them looks unmanaged, and the next run re-adds rules that are already
        # there and never closes the ones that should go.
        for _p in $(cat "$STATE_FILE" 2>/dev/null); do
            case "$_p" in
                */tcp|*/udp) MANAGED="$MANAGED $_p" ;;
                *) MANAGED="$MANAGED $_p/tcp" ;;
            esac
        done
        MANAGED="${MANAGED# }"
    fi

    # Ports to add: desired but not yet managed. Ports to remove: managed
    # but no longer desired. Everything else is left exactly as it is.
    ADD=""
    for p in $DESIRED; do
        case " $MANAGED " in *" $p "*) ;; *) ADD="$ADD $p" ;; esac
    done
    REMOVE=""
    for p in $MANAGED; do
        case " $DESIRED " in *" $p "*) ;; *) REMOVE="$REMOVE $p" ;; esac
    done

    [ "$DRY_RUN" = "1" ] && echo "ADD=${ADD# }" && echo "REMOVE=${REMOVE# }"
    if [ -z "$ADD" ] && [ -z "$REMOVE" ]; then
        return
    fi

    RELOAD=0
    for p in $ADD; do
        case "$BACKEND" in
            ufw) run_or_print ufw allow "$p" comment "OpenAVC (managed)" ;;
            firewalld) run_or_print firewall-cmd --permanent --add-port="$p"; RELOAD=1 ;;
        esac
        log "opened port $p ($BACKEND)"
    done
    for p in $REMOVE; do
        case "$BACKEND" in
            ufw) run_or_print ufw delete allow "$p" ;;
            firewalld) run_or_print firewall-cmd --permanent --remove-port="$p"; RELOAD=1 ;;
        esac
        log "closed port $p ($BACKEND)"
    done
    [ "$RELOAD" = "1" ] && run_or_print firewall-cmd --reload

    if [ "$DRY_RUN" != "1" ]; then
        echo "$DESIRED" > "$STATE_FILE" 2>/dev/null || \
            log "could not write $STATE_FILE (ports stay open; next run may re-add)"
    fi
}

main
exit 0
