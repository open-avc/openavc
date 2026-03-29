#!/bin/bash
# OpenAVC first-boot setup. Runs once on the very first boot after flashing.
# Performs initial setup that can only happen on the actual hardware.

set -e

MARKER="/var/lib/openavc/.firstboot"
DATA_DIR="/var/lib/openavc"
LOG_FILE="/var/log/openavc/firstboot.log"

mkdir -p "$(dirname "$LOG_FILE")"
exec > "$LOG_FILE" 2>&1

echo "OpenAVC first-boot setup starting at $(date)"

# Generate a unique system identifier based on hardware
SYSTEM_ID=$(cat /proc/cpuinfo | grep Serial | awk '{print $3}' | tail -c 9)
if [ -z "$SYSTEM_ID" ]; then
    SYSTEM_ID=$(hostname)
fi
echo "System ID: $SYSTEM_ID"

# Set hostname to openavc (ensures mDNS works as openavc.local)
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" != "openavc" ]; then
    hostnamectl set-hostname openavc
    echo "Hostname set to openavc"
fi

# Ensure data directories exist with correct ownership
mkdir -p "$DATA_DIR/projects/default" "$DATA_DIR/logs"
chown -R openavc:openavc "$DATA_DIR"
chown -R openavc:openavc /var/log/openavc

# Ensure driver_repo and plugin_repo have correct ownership
chown -R openavc:openavc /opt/openavc/driver_repo 2>/dev/null || true
chown -R openavc:openavc /opt/openavc/plugin_repo 2>/dev/null || true

# Expand filesystem to fill the SD card (if not already done)
if command -v raspi-config &> /dev/null; then
    raspi-config --expand-rootfs 2>/dev/null || true
fi

# Remove the first-boot marker so this doesn't run again
rm -f "$MARKER"

echo "OpenAVC first-boot setup complete at $(date)"
