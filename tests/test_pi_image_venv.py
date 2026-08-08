"""Guards for the Pi image's Python dependency install.

The service unit hardcodes ExecStart=/opt/openavc/venv/bin/python with
Restart=always, so the dependency install must succeed into THAT venv or the
image build must fail. The install script used to fall back to a system-wide
``pip3 install --break-system-packages`` when venv pip failed (and to a
warn-and-skip when requirements.txt was missing) — both masked a hard build
failure as an image whose service crash-loops on import at first boot,
because the venv is created isolated and never sees system site-packages.

Text-level guards like test_pi_image_seed.py — the image only builds on
Linux CI.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

STAGE_DIR = REPO_ROOT / "installer" / "pi-image" / "stage-openavc"
DEPS_SH = STAGE_DIR / "01-install-openavc" / "01-run-chroot.sh"
CHROOT_SH = STAGE_DIR / "02-configure" / "00-run-chroot.sh"


def test_no_system_pip_fallback():
    """Deps must never fall back to system site-packages — the isolated venv
    the service runs from would not see them."""
    text = DEPS_SH.read_text(encoding="utf-8")
    assert "--break-system-packages" not in text, (
        "01-run-chroot.sh falls back to a system-wide pip install the "
        "service venv cannot import from"
    )


def test_missing_requirements_is_fatal():
    """A missing requirements.txt means the server archive is broken; the
    build must abort, not skip the install and ship a dependency-less image."""
    text = DEPS_SH.read_text(encoding="utf-8")
    assert "skipping pip install" not in text, (
        "01-run-chroot.sh warns and skips when requirements.txt is missing"
    )
    assert "exit 1" in text, (
        "01-run-chroot.sh has no fatal path for a missing requirements.txt"
    )


def test_deps_install_into_service_venv():
    text = DEPS_SH.read_text(encoding="utf-8")
    assert '"$VENV_DIR/bin/pip" install' in text, (
        "01-run-chroot.sh no longer installs dependencies with the venv pip"
    )


def test_build_verification_imports_server_from_venv():
    """The image build's hard-check block must prove the service interpreter
    can import the server, so a dependency-install regression aborts the
    build instead of shipping a crash-looping image."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert './venv/bin/python -c "import openavc.main"' in text, (
        "00-run-chroot.sh build verification does not import-check the venv"
    )
