"""Guards for the starter project every install type starts on.

The one starter is ``openavc/seed/default/``, inside the package, and the
server's first start puts it in place (``core/project_recovery``). No
installer copies it into the data folder: a reinstall over a system whose
project file went missing would put the starter there before the server
could start the project's newest backup. The Pi image used to commit its own
copy, which drifted, and then staged the package's at build time; it now
ships an empty project folder and checks that the server finds its starter.

These are text-level guards (like test_installer_spec.py) because the image
itself only builds on Linux CI.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests import gates

REPO_ROOT = Path(__file__).resolve().parents[1]

STAGE_DIR = REPO_ROOT / "installer" / "pi-image" / "stage-openavc"
RUN_SH = STAGE_DIR / "01-install-openavc" / "00-run.sh"
CHROOT_SH = STAGE_DIR / "02-configure" / "00-run-chroot.sh"
STREAMDECK_RULES = STAGE_DIR / "01-install-openavc" / "files" / "99-streamdeck.rules"
INFO_SH = STAGE_DIR / "01-install-openavc" / "files" / "openavc-info.sh"
GITIGNORE = REPO_ROOT / ".gitignore"


def _render_info_box(*lines: str) -> list[str]:
    """Source openavc-info.sh and render its boot banner for the given body
    lines, returning the rendered (non-blank) output lines.

    The script only ever runs on the Linux Pi image, so this executes real
    bash. Skip on Windows — the ``bash`` on PATH there is the WSL launcher
    stub (no POSIX shell, no installed distro), not a usable bash — and skip
    anywhere bash is genuinely absent. Ubuntu CI (and the Mac dev box) cover
    the alignment behavior. Decode as UTF-8 explicitly so the box-drawing
    characters aren't mangled by a non-UTF-8 locale default."""
    if sys.platform == "win32":
        pytest.skip("openavc-info.sh is Linux-only Pi-image tooling; no POSIX bash on Windows")
    gates.skip_or_fail(
        gates.BASH, None if shutil.which("bash") else "bash not installed"
    )
    body = " ".join(f"'{line}'" for line in lines)
    proc = subprocess.run(
        ["bash", "-c", f"source '{INFO_SH}'; render_box 'OpenAVC Room Control' {body}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == 0, f"render_box failed: {proc.stderr}"
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]

CANONICAL_SEED = "openavc/seed/default/project.avc"
STAGED_SEED = "installer/pi-image/stage-openavc/01-install-openavc/files/project.avc"

#: Every file that installs or builds an install type's data folder.
INSTALLERS = (
    "installer/setup.iss",
    "installer/install-service.bat",
    "installer/install.sh",
    "installer/macos/scripts/postinstall",
    "installer/pi-image/stage-openavc/01-install-openavc/00-run.sh",
    "installer/pi-image/build.sh",
    ".github/workflows/release-pi.yml",
    "Dockerfile",
)


def test_the_seed_ships_inside_the_package():
    """A pip-installed wheel carries only the package, so the one seed every
    install type reads lives in it, where the runtime looks first."""
    from openavc import system_config

    assert REPO_ROOT / CANONICAL_SEED == system_config.SEED_PROJECT_PATH
    assert system_config.SEED_PROJECT_PATH.is_file()
    assert system_config.PACKAGE_DIR in system_config.SEED_PROJECT_PATH.parents
    assert system_config.get_seed_project_path() == system_config.SEED_PROJECT_PATH


def test_nothing_reads_the_old_seed_path():
    """The seed moved out of installer/seed/, and no installer reads a seed
    any more."""
    gates.skip_or_fail(
        gates.GIT, None if shutil.which("git") else "git not installed"
    )
    proc = subprocess.run(
        ["git", "grep", "-lIE", r"installer.seed"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    gates.skip_or_fail(
        gates.GIT, None if proc.returncode in (0, 1) else "not a git checkout"
    )
    readers = set(proc.stdout.split()) - {"tests/test_pi_image_seed.py"}
    assert readers == set(), f"still reading installer/seed/: {sorted(readers)}"


@pytest.mark.parametrize("installer", INSTALLERS)
def test_no_installer_puts_the_starter_project_in_place(installer):
    """The server's first start does, after it has read the backups. A
    project file an installer names is the path it hands the server, and
    nothing else."""
    text = (REPO_ROOT / installer).read_text(encoding="utf-8")
    assert "seed/default" not in text and "SeedDefaultProject" not in text
    copies = [line.strip() for line in text.splitlines() if "project.avc" in line and "OPENAVC_PROJECT" not in line]
    assert copies == [], f"{installer} still puts the starter project in place"


def test_no_forked_seed_is_tracked():
    """No project.avc may be committed anywhere under installer/pi-image/.

    A tracked copy is a fork of the canonical seed by definition — it can
    only drift. The staged copy is created at build time and gitignored.
    """
    gates.skip_or_fail(
        gates.GIT, None if shutil.which("git") else "git not installed"
    )
    proc = subprocess.run(
        ["git", "ls-files", "installer/pi-image"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    gates.skip_or_fail(
        gates.GIT, None if proc.returncode == 0 else "not a git checkout"
    )
    tracked = [line for line in proc.stdout.splitlines() if line.endswith("project.avc")]
    assert not tracked, f"forked seed project committed under installer/pi-image: {tracked}"


def test_staged_seed_is_gitignored():
    """A copy an older build staged is never committed."""
    text = GITIGNORE.read_text(encoding="utf-8")
    assert STAGED_SEED in text, (
        ".gitignore must cover the build-time staged seed so a fork can't "
        "be committed again"
    )


def test_image_ships_an_empty_project_folder():
    """The project folder is there, owned by the service once the chroot
    stage hands it the data folder, with no project file in it."""
    text = RUN_SH.read_text(encoding="utf-8")
    assert 'mkdir -p "${ROOTFS_DIR}/var/lib/openavc/projects/default"' in text
    assert "project.avc" not in text


def test_chroot_has_no_dead_seed_fallback():
    """The server tarball excludes installer/, so /opt/openavc/installer/...
    never exists in the image — a chroot-side fallback that copies from it is
    dead code that only masks staging failures."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert "/opt/openavc/installer" not in text, (
        "00-run-chroot.sh references /opt/openavc/installer, which is never "
        "present in the image rootfs (the server tarball excludes installer/)"
    )


def test_build_verification_checks_the_server_finds_its_starter():
    """The image build's hard-check block asks the server's own reader of
    the starter, as the service user, so an image whose first start would
    find none aborts the build."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert "get_seed_project_path() is None" in text, (
        "00-run-chroot.sh build verification does not check the server finds its starter project"
    )


def test_streamdeck_rule_is_not_world_writable():
    """The Stream Deck udev rule must not grant world read/write (MODE=0666).

    The server runs as the unprivileged 'openavc' system-service user, so a
    world-writable device node is unnecessary and lets any local user or a
    compromised process issue raw USB HID traffic. Least privilege: a group
    the service user belongs to, with 0660.
    """
    text = STREAMDECK_RULES.read_text(encoding="utf-8")
    assert '0666' not in text, (
        "99-streamdeck.rules is world-writable (MODE=0666) — scope it to a "
        "group with 0660 instead"
    )
    assert 'MODE="0660"' in text, (
        "99-streamdeck.rules should set MODE=0660 (group-writable only)"
    )
    assert 'GROUP="plugdev"' in text, (
        "99-streamdeck.rules should be owned by the plugdev group"
    )


def test_streamdeck_group_membership_is_granted():
    """The openavc service user must be in the group the udev rule grants, or
    the 0660 rule would lock the server out of the Stream Deck entirely."""
    text = CHROOT_SH.read_text(encoding="utf-8")
    assert "plugdev" in text, (
        "00-run-chroot.sh does not add the openavc user to plugdev, so the "
        "group-scoped Stream Deck rule would deny the service access"
    )


def test_boot_banner_lines_align():
    """The HDMI boot banner must render with every border aligned, whatever
    the IP/URL/hostname length. The old fixed-width version overflowed for a
    normal IP (the Programmer URL line alone was 51 chars in a 50-wide box)."""
    # A realistic case whose Programmer URL exceeded the old fixed box width.
    ip = "192.168.100.200"
    url = f"http://{ip}:8443"
    lines = _render_info_box(
        f"  IP Address:   {ip}",
        "",
        f"  Programmer:   {url}/programmer",
        f"  Panel:        {url}/panel",
        "  mDNS:         http://openavc.local:8443",
    )
    assert lines, "boot banner rendered no output"
    widths = {len(ln) for ln in lines}
    assert len(widths) == 1, (
        f"boot banner borders do not line up — distinct widths {sorted(widths)}:\n"
        + "\n".join(lines)
    )


def test_boot_banner_has_no_fixed_pad_arithmetic():
    """Guard against reintroducing the fragile fixed-width padding constants
    (e.g. `$((27 - ${#URL} - 11))`) that produced negative widths and
    misaligned borders. Padding must derive from the content, not magic
    numbers."""
    text = INFO_SH.read_text(encoding="utf-8")
    assert "27 - ${#" not in text and "18 - ${#" not in text, (
        "openavc-info.sh reintroduced hardcoded pad arithmetic — size the box "
        "to its content instead"
    )
    assert "render_box" in text, "openavc-info.sh lost its content-sized box renderer"
