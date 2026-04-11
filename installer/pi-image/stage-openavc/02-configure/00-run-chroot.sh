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

# Allow passwordless reboot from the server (used by Programmer UI reboot button)
echo "$OPENAVC_USER ALL=(ALL) NOPASSWD: /sbin/reboot" > /etc/sudoers.d/openavc-reboot
chmod 440 /etc/sudoers.d/openavc-reboot

# --- Enable services ---

systemctl enable openavc.service
# Note: openavc-panel.service is NOT enabled. The kiosk is launched from
# the labwc autostart instead, which runs inside the graphical session
# and has proper access to the Wayland display.
systemctl enable openavc-firstboot.service
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
    # Set auto-login user — handles both commented and uncommented lines
    # (Pi OS may already have autologin-user=rpi-first-boot-wizard set)
    if grep -q "^autologin-user=" "$LIGHTDM_CONF"; then
        sed -i "s/^autologin-user=.*/autologin-user=$OPENAVC_USER/" "$LIGHTDM_CONF"
    elif grep -q "^#autologin-user=" "$LIGHTDM_CONF"; then
        sed -i "s/^#autologin-user=.*/autologin-user=$OPENAVC_USER/" "$LIGHTDM_CONF"
    else
        sed -i "/^\[Seat:\*\]/a autologin-user=$OPENAVC_USER" "$LIGHTDM_CONF"
    fi
fi

# Also configure via raspi-config nonint (belt and suspenders)
if command -v raspi-config &> /dev/null; then
    raspi-config nonint do_boot_behaviour B4 2>/dev/null || true
fi

# Disable the Raspberry Pi OS first-boot wizard. It hijacks the graphical
# session (runs labwc as its own user) which prevents auto-login as openavc
# and blocks the kiosk launcher. Everything it configures is already set by
# pi-gen (user, password, locale, SSH).
rm -f /etc/xdg/autostart/piwiz.desktop

# --- Kiosk display integration ---

# --- labwc display configuration ---
#
# RPi OS runs labwc via labwc-pi which passes -m (merge-config), so both
# the system autostart (/etc/xdg/labwc/autostart) and user autostart run.
# We must strip the desktop shell from the system autostart, otherwise
# pcmanfm (wallpaper + icons) and wf-panel-pi (taskbar) appear before
# Chromium loads, and users can interact with them.

OPENAVC_HOME="/home/$OPENAVC_USER"
LABWC_DIR="$OPENAVC_HOME/.config/labwc"
mkdir -p "$LABWC_DIR"

# Replace system autostart: remove desktop shell, keep display detection
SYSTEM_AUTOSTART="/etc/xdg/labwc/autostart"
if [ -f "$SYSTEM_AUTOSTART" ]; then
    cat > "$SYSTEM_AUTOSTART" << 'SYSAUTO'
# OpenAVC: desktop shell removed (no pcmanfm, no wf-panel-pi)
/usr/bin/kanshi &
SYSAUTO
fi

# User autostart: launch OpenAVC display (panel or info screen)
cat > "$LABWC_DIR/autostart" << 'AUTOSTART'
/opt/openavc/scripts/panel-kiosk.sh &
AUTOSTART

# User rc.xml: disable touch mouse emulation for native swipe scrolling,
# and remove window decorations from Chromium
cat > "$LABWC_DIR/rc.xml" << 'RCXML'
<?xml version="1.0"?>
<labwc_config>
  <touch deviceName="" mouseEmulation="no"/>
  <windowRules>
    <windowRule identifier="chromium">
      <serverDecoration>no</serverDecoration>
      <skipTaskbar>yes</skipTaskbar>
    </windowRule>
  </windowRules>
</labwc_config>
RCXML

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
