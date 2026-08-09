"""The macOS updater must not hand the root daemon a user-owned binary.

`openavc-macos-run.sh` runs as root out of the LaunchDaemon. Root `tar`
RESTORES the uid/gid recorded in the archive, and the release tarball is built
on a CI runner whose build user is uid 501 — so a plain `tar xzf` leaves the
swapped-in bundle owned by whichever local account holds uid 501. The daemon
then execs, as root, a binary an unprivileged user can overwrite. Measured on a
real Mac after a real in-app update: the `.pkg`-installed bundle was
`root:wheel`, the updater-swapped one was `501:staff`.

Linux already handles this deliberately (`chown -R` on both the update and
rollback paths) and Windows goes through the Inno installer, so this was the
one path that dropped it.

**These are file assertions, not behaviour.** Proving the ownership itself
needs root, and as a normal user `tar` cannot set ownership at all — a
behavioural test here would pass identically with or without the fix, which is
worse than no test. The same reasoning the upgrade-layout tests give for their
Windows and macOS halves.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MACOS_RUN = REPO_ROOT / "installer" / "openavc-macos-run.sh"
LINUX_HELPER = REPO_ROOT / "installer" / "update-helper.sh"


@pytest.fixture(scope="module")
def macos_script() -> str:
    assert MACOS_RUN.is_file(), f"missing {MACOS_RUN}"
    return MACOS_RUN.read_text(encoding="utf-8")


def _tar_extract_lines(script: str) -> list[str]:
    """Every line that extracts the update artifact."""
    return [
        line.strip()
        for line in script.splitlines()
        if "tar " in line and "xzf" in line and not line.strip().startswith("#")
    ]


class TestMacOSExtractOwnership:
    def test_the_script_extracts_exactly_once(self, macos_script):
        # Guards the assertions below: if a second extract appears, it needs the
        # same treatment and this file should fail until someone looks.
        assert len(_tar_extract_lines(macos_script)) == 1

    def test_extract_does_not_restore_archive_ownership(self, macos_script):
        line = _tar_extract_lines(macos_script)[0]
        assert "--no-same-owner" in line, (
            "root tar restores the archive's uid/gid; without --no-same-owner the "
            "swapped-in bundle is owned by the CI builder's uid and the root "
            "daemon runs a binary an unprivileged user can overwrite"
        )

    def test_staged_bundle_is_chowned_to_root_before_promotion(self, macos_script):
        assert "chown -R root:wheel" in macos_script, (
            "state the ownership requirement outright rather than inferring it "
            "from who happens to run the script"
        )

    def test_chown_happens_before_the_swap(self, macos_script):
        # A chown after `mv` would leave a window where the promoted bundle is
        # user-owned, and would miss entirely on any early-return path.
        chown_at = macos_script.index("chown -R root:wheel")
        swap_at = macos_script.index('mv "$NEW_APP" "$APP"')
        assert chown_at < swap_at


class TestLinuxStillHandlesIt:
    """Pinned so the platform that already got this right cannot quietly lose it."""

    def test_linux_helper_chowns_the_installed_tree(self):
        assert LINUX_HELPER.is_file()
        text = LINUX_HELPER.read_text(encoding="utf-8")
        assert "chown -R openavc:openavc" in text
        assert "chown root:root" in text
