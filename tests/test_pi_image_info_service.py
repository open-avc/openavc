"""Guards for the Pi image's boot info display service.

``openavc-info.service`` is a oneshot that prints the device IP and access
URLs on the HDMI console at boot, so an integrator can commission a headless
unit without a keyboard or mDNS. The 01-install stage installs the unit file,
but a unit on disk does nothing until it is ``systemctl enable``d — the
02-configure stage's "Enable services" block must enable it, exactly like
openavc.service / avahi / openavc-privileged.path. It used to install the
unit and forget to enable it, so the banner never appeared at first boot.

Text-level guards like test_pi_image_seed.py — the image only builds on
Linux CI.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

STAGE_DIR = REPO_ROOT / "installer" / "pi-image" / "stage-openavc"
RUN_SH = STAGE_DIR / "01-install-openavc" / "00-run.sh"
UNIT_FILE = STAGE_DIR / "01-install-openavc" / "files" / "openavc-info.service"
CHROOT_SH = STAGE_DIR / "02-configure" / "00-run-chroot.sh"


def test_info_service_unit_is_installed():
    """01-install must copy the unit into the image, or there is nothing to
    enable."""
    text = RUN_SH.read_text(encoding="utf-8")
    assert "openavc-info.service" in text, (
        "00-run.sh no longer installs openavc-info.service into the rootfs"
    )


def test_info_service_unit_has_install_section():
    """``systemctl enable`` silently no-ops on a unit with no [Install]
    section, so the WantedBy target must be present."""
    text = UNIT_FILE.read_text(encoding="utf-8")
    assert "[Install]" in text and "WantedBy=" in text, (
        "openavc-info.service has no [Install] WantedBy — enable would no-op"
    )


def test_info_service_is_enabled():
    """The 02-configure stage must enable the unit — an installed-but-disabled
    unit never runs, so the boot info banner never appears."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert "systemctl enable openavc-info.service" in text, (
        "00-run-chroot.sh installs openavc-info.service but never enables it; "
        "the HDMI boot info banner will not appear at first boot"
    )


def test_build_verification_checks_info_service_enabled():
    """The image build's hard-check block must verify the unit is enabled, so
    a regression that drops the enable line aborts the build."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert "is-enabled openavc-info.service" in text, (
        "00-run-chroot.sh build verification does not confirm "
        "openavc-info.service is enabled"
    )
