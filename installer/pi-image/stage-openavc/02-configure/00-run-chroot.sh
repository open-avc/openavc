#!/bin/bash -e
# Final system configuration: user, services, auto-login, kiosk integration.

OPENAVC_USER="openavc"
DATA_DIR="/var/lib/openavc"

# --- User and permissions ---

# The first user (openavc) is already created by pi-gen from the config.
# Ensure the user owns all OpenAVC directories.
chown -R "$OPENAVC_USER:$OPENAVC_USER" /opt/openavc
chown -R "$OPENAVC_USER:$OPENAVC_USER" "$DATA_DIR"
mkdir -p /var/log/openavc
chown -R "$OPENAVC_USER:$OPENAVC_USER" /var/log/openavc

# Add openavc user to video and input groups (needed for display + touch)
usermod -aG video,input,dialout "$OPENAVC_USER" 2>/dev/null || true

# --- Enable services ---

systemctl enable openavc.service
systemctl enable openavc-panel.service
systemctl enable openavc-firstboot.service
systemctl enable openavc-info.service
systemctl enable avahi-daemon.service

# Create first-boot marker
touch "$DATA_DIR/.firstboot"

# --- Auto-login for kiosk display ---

# Configure auto-login so the desktop session starts without interaction.
# This is required for the kiosk display to work (Chromium needs a
# graphical session). If no display is connected, the desktop session
# starts but has no visible output. The server runs regardless.

# Raspberry Pi OS uses lightdm for display management
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"
if [ -f "$LIGHTDM_CONF" ]; then
    # Enable auto-login in lightdm
    sed -i "s/^#autologin-user=.*/autologin-user=$OPENAVC_USER/" "$LIGHTDM_CONF"
    # If the line doesn't exist, add it under [Seat:*]
    if ! grep -q "^autologin-user=" "$LIGHTDM_CONF"; then
        sed -i "/^\[Seat:\*\]/a autologin-user=$OPENAVC_USER" "$LIGHTDM_CONF"
    fi
fi

# Also configure via raspi-config nonint (belt and suspenders)
if command -v raspi-config &> /dev/null; then
    raspi-config nonint do_boot_behaviour B4 2>/dev/null || true
fi

# --- Kiosk display integration ---

# Set up labwc autostart for the openavc user.
# The panel-kiosk.sh script checks system.json at runtime, so this autostart
# entry is always present but the script exits immediately if kiosk is disabled.
OPENAVC_HOME="/home/$OPENAVC_USER"
LABWC_DIR="$OPENAVC_HOME/.config/labwc"
mkdir -p "$LABWC_DIR"

# Append our kiosk launcher to labwc autostart
AUTOSTART_FILE="$LABWC_DIR/autostart"
KIOSK_LINE="/opt/openavc/scripts/panel-kiosk.sh &"
if [ -f "$AUTOSTART_FILE" ]; then
    # Append if not already present
    if ! grep -qF "$KIOSK_LINE" "$AUTOSTART_FILE"; then
        echo "" >> "$AUTOSTART_FILE"
        echo "# OpenAVC panel kiosk (checks system.json, exits if disabled)" >> "$AUTOSTART_FILE"
        echo "$KIOSK_LINE" >> "$AUTOSTART_FILE"
    fi
else
    # Create autostart with default Pi OS entries + our kiosk line
    cat > "$AUTOSTART_FILE" << 'AUTOSTART'
# Raspberry Pi OS default autostart
pcmanfm --desktop --profile LXDE-pi &
lxpanel --profile LXDE-pi &

# OpenAVC panel kiosk (checks system.json, exits if disabled)
/opt/openavc/scripts/panel-kiosk.sh &
AUTOSTART
fi

chown -R "$OPENAVC_USER:$OPENAVC_USER" "$OPENAVC_HOME/.config"

# --- Disable screen blanking (system-wide) ---

# Prevent DPMS from turning off the display
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-no-blanking.conf << 'XCONF'
Section "ServerFlags"
    Option "BlankTime" "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime" "0"
EndSection
XCONF

# --- SSH banner ---

cat > /etc/motd << 'MOTD'

  ╔═══════════════════════════════════════╗
  ║         OpenAVC Room Control          ║
  ╠═══════════════════════════════════════╣
  ║  Programmer: http://openavc.local     ║
  ║  Panel:      http://openavc.local/p   ║
  ║  API:        http://openavc.local/api ║
  ╚═══════════════════════════════════════╝

MOTD

# --- Copy seed project if not already present ---

SEED_PROJECT="/opt/openavc/installer/seed/default/project.avc"
TARGET_PROJECT="$DATA_DIR/projects/default/project.avc"
if [ -f "$SEED_PROJECT" ] && [ ! -f "$TARGET_PROJECT" ]; then
    mkdir -p "$(dirname "$TARGET_PROJECT")"
    cp "$SEED_PROJECT" "$TARGET_PROJECT"
    chown "$OPENAVC_USER:$OPENAVC_USER" "$TARGET_PROJECT"
fi

echo "OpenAVC Pi image configuration complete."
