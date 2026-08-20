#!/bin/bash -e
# Copy OpenAVC files into the image rootfs (runs on host, not in chroot).

FILES_DIR="$(dirname "$0")/files"

# Extract server archive
echo "Extracting OpenAVC server..."
tar xzf "$FILES_DIR/openavc.tar.gz" -C "${ROOTFS_DIR}/opt/openavc/"

# Copy service files
install -m 644 "$FILES_DIR/openavc.service" \
    "${ROOTFS_DIR}/etc/systemd/system/openavc.service"
install -m 644 "$FILES_DIR/openavc-panel.service" \
    "${ROOTFS_DIR}/etc/systemd/system/openavc-panel.service"

# Copy update helper script (referenced by ExecStartPre in openavc.service)
install -m 755 "$FILES_DIR/update-helper.sh" \
    "${ROOTFS_DIR}/opt/openavc/update-helper.sh"

# Privileged action helper (root-owned, outside the server-writable tree — see
# 02-configure, which installs the systemd units that drive it). Kept as a real
# file rather than written inline so the same copy ships in the release tarball
# and an in-app update can refresh it.
mkdir -p "${ROOTFS_DIR}/usr/local/sbin"
install -m 755 "$FILES_DIR/openavc-privileged-helper.sh" \
    "${ROOTFS_DIR}/usr/local/sbin/openavc-privileged-helper.sh"

# Copy firewall sync helper (referenced by ExecStartPre in openavc.service;
# no-op on stock Pi OS, keeps ports open if the integrator enables ufw)
install -m 755 "$FILES_DIR/firewall-sync.sh" \
    "${ROOTFS_DIR}/opt/openavc/firewall-sync.sh"

# Copy kiosk launcher script
mkdir -p "${ROOTFS_DIR}/opt/openavc/scripts"
install -m 755 "$FILES_DIR/panel-kiosk.sh" \
    "${ROOTFS_DIR}/opt/openavc/scripts/panel-kiosk.sh"

# Copy first-boot script
install -m 755 "$FILES_DIR/openavc-firstboot.sh" \
    "${ROOTFS_DIR}/opt/openavc/scripts/openavc-firstboot.sh"
install -m 644 "$FILES_DIR/openavc-firstboot.service" \
    "${ROOTFS_DIR}/etc/systemd/system/openavc-firstboot.service"

# Copy boot info display script (shows IP address on HDMI console)
install -m 755 "$FILES_DIR/openavc-info.sh" \
    "${ROOTFS_DIR}/opt/openavc/scripts/openavc-info.sh"
install -m 644 "$FILES_DIR/openavc-info.service" \
    "${ROOTFS_DIR}/etc/systemd/system/openavc-info.service"

# Copy default system.json
mkdir -p "${ROOTFS_DIR}/var/lib/openavc"
install -m 644 "$FILES_DIR/default-system.json" \
    "${ROOTFS_DIR}/var/lib/openavc/system.json"

# Copy seed project (staged from installer/seed/default/ by the release
# workflow / build.sh — fail the build if staging didn't happen rather than
# shipping an image with no starter project)
mkdir -p "${ROOTFS_DIR}/var/lib/openavc/projects/default"
install -m 644 "$FILES_DIR/project.avc" \
    "${ROOTFS_DIR}/var/lib/openavc/projects/default/project.avc"

# Copy mDNS service definition
install -m 644 "$FILES_DIR/openavc-http.service.avahi" \
    "${ROOTFS_DIR}/etc/avahi/services/openavc-http.service"

# Install udev rule for Stream Deck USB access
mkdir -p "${ROOTFS_DIR}/etc/udev/rules.d"
install -m 644 "$FILES_DIR/99-streamdeck.rules" \
    "${ROOTFS_DIR}/etc/udev/rules.d/99-streamdeck.rules"

# Copy labwc kiosk config overlay
mkdir -p "${ROOTFS_DIR}/etc/skel/.config/labwc"
install -m 644 "$FILES_DIR/labwc-autostart" \
    "${ROOTFS_DIR}/etc/skel/.config/labwc/autostart-openavc"
install -m 644 "$FILES_DIR/labwc-rc-kiosk.xml" \
    "${ROOTFS_DIR}/etc/skel/.config/labwc/rc-kiosk.xml"
