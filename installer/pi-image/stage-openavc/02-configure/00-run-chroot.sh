#!/bin/bash -e
# Final system configuration: user, services, auto-login, kiosk integration.

OPENAVC_USER="openavc"
DATA_DIR="/var/lib/openavc"

# --- User and permissions ---
#
# The 'openavc' user is created by pi-gen's stage1 from FIRST_USER_NAME
# with the password from FIRST_USER_PASS. The first-boot rename wizard is
# skipped via DISABLE_FIRST_BOOT_USER_RENAME=1 in config, so the user we
# get out of stage1 is the user the system boots into.
chown -R "$OPENAVC_USER:$OPENAVC_USER" /opt/openavc
chown -R "$OPENAVC_USER:$OPENAVC_USER" "$DATA_DIR"
mkdir -p /var/log/openavc
chown -R "$OPENAVC_USER:$OPENAVC_USER" /var/log/openavc

# Add openavc user to video and input groups (needed for display + touch)
usermod -aG video,input,dialout "$OPENAVC_USER"

# --- Privileged action helper (C10) ---
#
# The server runs as the unprivileged 'openavc' user with NoNewPrivileges=true,
# so it CANNOT use sudo (setuid is ignored — that also means the old
# `sudo /sbin/reboot` approach never worked). Privileged actions — syncing the
# OS account password to the web admin password, toggling SSH, rebooting — are
# performed by this root-owned helper, triggered by a systemd .path unit when
# the server drops a request file in the spool directory.
#
# The helper lives in /usr/local/sbin (root-only-writable) — NOT under
# /opt/openavc, which is chowned to 'openavc' and could otherwise be rewritten
# by a compromised server to gain root. It is hard-coded to the openavc user
# and a fixed action set, so the server can never target root or run arbitrary
# commands. (Host network config does NOT go through here — NetworkManager
# authorizes over D-Bus, so it gets a scoped polkit rule below instead.)
mkdir -p /usr/local/sbin
cat > /usr/local/sbin/openavc-privileged-helper.sh << 'HELPER'
#!/bin/bash
# OpenAVC privileged action helper. Runs as ROOT via openavc-privileged.service,
# triggered by openavc-privileged.path when the unprivileged server drops a
# request file. Hard-coded to the 'openavc' user and a fixed action vocabulary;
# it never executes content from a request file. set_password reads the password
# from system.json (which the server already controls), so it grants no
# privilege the server didn't already have.
set -u

DATA_DIR="${1:-/var/lib/openavc}"
REQ_DIR="$DATA_DIR/priv-requests"
RES_DIR="$DATA_DIR/priv-results"
CONFIG_FILE="$DATA_DIR/system.json"
OPENAVC_USER="openavc"
PYTHON="${PYTHON:-/usr/bin/python3}"
LOG_TAG="openavc-privileged"

mkdir -p "$RES_DIR"
chown "$OPENAVC_USER:$OPENAVC_USER" "$RES_DIR" 2>/dev/null || true

reboot_after=0
shopt -s nullglob
for req in "$REQ_DIR"/*.json; do
    id="$(basename "$req" .json)"
    action="$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1])).get('action',''))" "$req" 2>/dev/null)"
    want_result="$("$PYTHON" -c "import json,sys; print(bool(json.load(open(sys.argv[1])).get('want_result', False)))" "$req" 2>/dev/null)"
    ok=false
    error=""

    case "$action" in
        set_password)
            pw="$("$PYTHON" -c "import json,sys; print(json.load(open(sys.argv[1])).get('auth',{}).get('programmer_password',''))" "$CONFIG_FILE" 2>/dev/null)"
            if [ -n "$pw" ]; then
                if printf '%s:%s\n' "$OPENAVC_USER" "$pw" | chpasswd; then
                    ok=true
                    echo "$LOG_TAG: synced OS password for $OPENAVC_USER"
                else
                    error="chpasswd failed"
                fi
            else
                # No web password (unclaimed / cleared) -> keep the account
                # locked so it never has a usable password.
                if passwd -l "$OPENAVC_USER" >/dev/null 2>&1; then
                    ok=true
                    echo "$LOG_TAG: no web password set; locked $OPENAVC_USER"
                else
                    error="lock failed"
                fi
            fi
            ;;
        set_ssh)
            enabled="$("$PYTHON" -c "import json,sys; print(bool(json.load(open(sys.argv[1])).get('enabled', False)))" "$req" 2>/dev/null)"
            if [ "$enabled" = "True" ]; then
                if systemctl enable --now ssh >/dev/null 2>&1; then ok=true; else error="ssh enable failed"; fi
            else
                systemctl disable --now ssh >/dev/null 2>&1
                systemctl disable --now ssh.socket >/dev/null 2>&1
                ok=true
            fi
            ;;
        reboot)
            ok=true
            reboot_after=1
            ;;
        *)
            error="unknown action"
            ;;
    esac

    # Result file (only when the server is waiting on one). error strings are a
    # fixed ASCII set with no quotes/backslashes, so this raw JSON is safe.
    if [ "$want_result" = "True" ]; then
        if [ "$ok" = "true" ]; then
            printf '{"ok": true, "error": ""}\n' > "$RES_DIR/$id.json"
        else
            printf '{"ok": false, "error": "%s"}\n' "$error" > "$RES_DIR/$id.json"
        fi
        chown "$OPENAVC_USER:$OPENAVC_USER" "$RES_DIR/$id.json" 2>/dev/null || true
    fi
    rm -f "$req"
    [ -n "$error" ] && echo "$LOG_TAG: action=$action id=$id error=$error"
done

if [ "$reboot_after" -eq 1 ]; then
    ( sleep 2; /sbin/reboot ) &
fi
exit 0
HELPER
chown root:root /usr/local/sbin/openavc-privileged-helper.sh
chmod 755 /usr/local/sbin/openavc-privileged-helper.sh

# systemd path unit: watch the request spool, trigger the helper. A separate
# root unit is unaffected by the server's NoNewPrivileges (systemd.exec(5):
# "no effect on processes ... invoked ... through ... arbitrary IPC services").
cat > /etc/systemd/system/openavc-privileged.path << 'PATHUNIT'
[Unit]
Description=Watch for OpenAVC privileged action requests

[Path]
DirectoryNotEmpty=/var/lib/openavc/priv-requests
Unit=openavc-privileged.service

[Install]
WantedBy=multi-user.target
PATHUNIT

cat > /etc/systemd/system/openavc-privileged.service << 'SVCUNIT'
[Unit]
Description=OpenAVC privileged action helper

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/openavc-privileged-helper.sh /var/lib/openavc
SVCUNIT

# Request/result spool (server-writable; the watcher watches only requests so
# result files can't re-trigger it). The data dir itself is created by the host
# 01-install stage; ensure the subdirs exist and are owned by the server user.
mkdir -p "$DATA_DIR/priv-requests" "$DATA_DIR/priv-results"
chown "$OPENAVC_USER:$OPENAVC_USER" "$DATA_DIR/priv-requests" "$DATA_DIR/priv-results"
chmod 700 "$DATA_DIR/priv-requests" "$DATA_DIR/priv-results"

# --- Host network configuration authorization ---
#
# The Network page (Programmer System Settings + the /setup screen) drives
# nmcli as the unprivileged 'openavc' user. NetworkManager authorizes over
# D-Bus via polkit — which NoNewPrivileges does not affect — so a scoped
# polkit rule, not the request-file helper, is the right mechanism here:
# reads (status, WiFi scans) need rich results the helper protocol doesn't
# carry, and polkit is NetworkManager's native authorization layer.
# Scope: NetworkManager actions + hostname changes, nothing else.
mkdir -p /etc/polkit-1/rules.d
cat > /etc/polkit-1/rules.d/50-openavc-network.rules << 'POLKIT'
// Allow the OpenAVC service user to manage host network configuration
// (Network settings in the OpenAVC web UI). Installed by the Pi image.
polkit.addRule(function(action, subject) {
    if (subject.user == "openavc") {
        if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0) {
            return polkit.Result.YES;
        }
        if (action.id == "org.freedesktop.hostname1.set-hostname" ||
            action.id == "org.freedesktop.hostname1.set-static-hostname") {
            return polkit.Result.YES;
        }
    }
});
POLKIT
chmod 644 /etc/polkit-1/rules.d/50-openavc-network.rules

# --- Lock the OS account; ship SSH off (C10) ---
#
# The build-time FIRST_USER_PASS is a throwaway. Lock the account so no
# published credential ships. The integrator's web setup syncs a real password
# via the helper above (chpasswd unlocks + sets in one step). Kiosk autologin
# does not use the password, so locking does not affect the kiosk display.
passwd -l "$OPENAVC_USER"

# SSH ships disabled (ENABLE_SSH=0 in config). Defensive in case the base image
# enabled it; the integrator turns it on from Settings > Security.
systemctl disable ssh 2>/dev/null || true
systemctl disable ssh.socket 2>/dev/null || true

# --- Enable services ---

# Defensive: even with DISABLE_FIRST_BOOT_USER_RENAME=1, the userconf-pi
# package is still installed by export-image/01-user-rename/00-packages,
# so the userconfig.service unit file remains on disk (just not enabled).
# Disable it explicitly in case a future package update or postinst
# enables it. See pi-gen issue #913.
systemctl disable userconfig.service 2>/dev/null || true

systemctl enable openavc.service
# Note: openavc-panel.service is NOT enabled. The kiosk is launched from
# the labwc autostart instead, which runs inside the graphical session
# and has proper access to the Wayland display.
systemctl enable openavc-firstboot.service
systemctl enable avahi-daemon.service
# Boot info display: prints the device IP + access URLs on the HDMI console
# so a headless unit can be commissioned without a keyboard or mDNS. The unit
# is installed by the 01-install stage but does nothing until it is enabled.
systemctl enable openavc-info.service
# Watcher for privileged actions (OS password sync, SSH toggle, reboot).
systemctl enable openavc-privileged.path

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
# remove window decorations from Chromium, and force fullscreen on first map
# (workaround for labwc/Chromium race condition — labwc issue #1994)
cat > "$LABWC_DIR/rc.xml" << 'RCXML'
<?xml version="1.0"?>
<labwc_config>
  <touch deviceName="" mouseEmulation="no"/>
  <windowRules>
    <windowRule identifier="chromium">
      <serverDecoration>no</serverDecoration>
      <skipTaskbar>yes</skipTaskbar>
      <onFirstMap>
        <action name="ToggleFullscreen"/>
      </onFirstMap>
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

# --- Build verification ---
#
# Hard-check the final image state. If any of these fail, abort the build
# rather than producing an image that boots into the wrong user / blank
# desktop. Every check here corresponds to a real failure mode we have
# previously shipped.
echo "=== OpenAVC pi-image build verification ==="
errors=0

if ! id "$OPENAVC_USER" >/dev/null 2>&1; then
    echo "FATAL: $OPENAVC_USER user does not exist"
    errors=$((errors + 1))
fi

if id rpi-first-boot-wizard >/dev/null 2>&1; then
    echo "FATAL: rpi-first-boot-wizard user still exists (userconf-pi purge incomplete)"
    errors=$((errors + 1))
fi

if [ -f "$LIGHTDM_CONF" ]; then
    if ! grep -q "^autologin-user=$OPENAVC_USER\$" "$LIGHTDM_CONF"; then
        echo "FATAL: lightdm autologin-user is not '$OPENAVC_USER':"
        grep -i autologin "$LIGHTDM_CONF" || echo "  (no autologin-user line found)"
        errors=$((errors + 1))
    fi
else
    echo "FATAL: $LIGHTDM_CONF does not exist"
    errors=$((errors + 1))
fi

state=$(systemctl is-enabled userconfig.service 2>&1 || true)
case "$state" in
    masked|disabled|not-found) ;;
    *)
        echo "FATAL: userconfig.service is in unexpected state: $state"
        errors=$((errors + 1))
        ;;
esac

if [ -e /etc/xdg/autostart/piwiz.desktop ]; then
    echo "FATAL: piwiz.desktop autostart still present"
    errors=$((errors + 1))
fi

# The starter project is installed by the 01-install stage from the seed the
# build staged out of installer/seed/default/. Missing/empty means the image
# would boot with no project at all.
if [ ! -s "$DATA_DIR/projects/default/project.avc" ]; then
    echo "FATAL: seed project missing or empty at $DATA_DIR/projects/default/project.avc"
    errors=$((errors + 1))
fi

# The service runs /opt/openavc/venv/bin/python -m server.main as the openavc
# user. Prove that interpreter can import the server and its dependencies —
# an install that landed anywhere but this venv (or not at all) would ship an
# image whose service crash-loops on import at first boot. Run as the service
# user with the service's env so path resolution matches the real unit and
# anything the import touches stays user-owned.
if ! runuser -u "$OPENAVC_USER" -- sh -c 'cd /opt/openavc && OPENAVC_DATA_DIR=/var/lib/openavc OPENAVC_LOG_DIR=/var/log/openavc ./venv/bin/python -c "import server.main"'; then
    echo "FATAL: venv python cannot import server.main (dependencies missing from /opt/openavc/venv?)"
    errors=$((errors + 1))
fi

if [ ! -f "$LABWC_DIR/autostart" ]; then
    echo "FATAL: openavc labwc autostart missing at $LABWC_DIR/autostart"
    errors=$((errors + 1))
fi

# The boot info display ships as a unit file but only runs if enabled; a unit
# left disabled means a headless first boot shows no IP/access URLs.
info_state=$(systemctl is-enabled openavc-info.service 2>&1 || true)
if [ "$info_state" != "enabled" ]; then
    echo "FATAL: openavc-info.service is not enabled: $info_state (boot info banner would not appear)"
    errors=$((errors + 1))
fi

# C10: the published OS login must be gone, the privileged helper must be in
# place and root-owned, and SSH must not ship enabled.
acct_status=$(passwd -S "$OPENAVC_USER" 2>/dev/null | awk '{print $2}')
if [ "$acct_status" != "L" ]; then
    echo "FATAL: $OPENAVC_USER account is not locked (status='$acct_status'; published password would ship)"
    errors=$((errors + 1))
fi

if [ ! -x /usr/local/sbin/openavc-privileged-helper.sh ]; then
    echo "FATAL: privileged helper missing or not executable"
    errors=$((errors + 1))
else
    helper_owner=$(stat -c '%U' /usr/local/sbin/openavc-privileged-helper.sh 2>/dev/null || echo '?')
    if [ "$helper_owner" != "root" ]; then
        echo "FATAL: privileged helper not owned by root (owner='$helper_owner' could let the server escalate)"
        errors=$((errors + 1))
    fi
fi

priv_state=$(systemctl is-enabled openavc-privileged.path 2>&1 || true)
if [ "$priv_state" != "enabled" ]; then
    echo "FATAL: openavc-privileged.path is not enabled: $priv_state"
    errors=$((errors + 1))
fi

for unit in ssh ssh.socket; do
    if [ "$(systemctl is-enabled "$unit" 2>&1 || true)" = "enabled" ]; then
        echo "FATAL: $unit is unexpectedly enabled (SSH must ship off)"
        errors=$((errors + 1))
    fi
done

if [ "$errors" -gt 0 ]; then
    echo "Pi-image build aborted: $errors verification error(s) above"
    exit 1
fi

echo "Pi-image build verification: OK"
echo "OpenAVC Pi image configuration complete."
