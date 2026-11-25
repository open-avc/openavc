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
APPLY_LIB = REPO_ROOT / "installer" / "apply-lib.sh"
LINUX_HELPER = REPO_ROOT / "installer" / "update-helper.sh"


@pytest.fixture(scope="module")
def macos_script() -> str:
    assert MACOS_RUN.is_file(), f"missing {MACOS_RUN}"
    return MACOS_RUN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def apply_lib() -> str:
    assert APPLY_LIB.is_file(), f"missing {APPLY_LIB}"
    return APPLY_LIB.read_text(encoding="utf-8")


def _tar_extract_lines(script: str) -> list[str]:
    """Every line that extracts the update artifact."""
    return [
        line.strip()
        for line in script.splitlines()
        if "tar " in line and "xzf" in line and not line.strip().startswith("#")
    ]


def _function_body(script: str, name: str) -> str:
    start = script.index(f"{name}() {{")
    return script[start:script.index("\n}\n", start)]


class TestMacOSExtractOwnership:
    """The wrapper applies an update through the apply library: the library
    extracts, and the check the wrapper hands it roots the staged bundle."""

    def test_the_wrapper_extracts_nothing_itself(self, macos_script):
        # Guards the assertions below: an extract of the wrapper's own needs
        # the same treatment and this file should fail until someone looks.
        assert _tar_extract_lines(macos_script) == []

    def test_the_library_extracts_exactly_once(self, apply_lib):
        assert len(_tar_extract_lines(apply_lib)) == 1

    def test_extract_does_not_restore_archive_ownership(self, apply_lib):
        line = _tar_extract_lines(apply_lib)[0]
        assert "--no-same-owner" in line, (
            "root tar restores the archive's uid/gid; without --no-same-owner the "
            "swapped-in bundle is owned by the CI builder's uid and the root "
            "daemon runs a binary an unprivileged user can overwrite"
        )

    def test_staged_bundle_is_chowned_to_root_by_the_check(self, macos_script):
        check = _function_body(macos_script, "root_and_check_bundle")
        assert "chown -R root:wheel" in check, (
            "state the ownership requirement outright rather than inferring it "
            "from who happens to run the script"
        )

    def test_the_check_is_what_the_wrapper_applies_an_update_with(self, macos_script):
        assert 'apply_update "$APP" OpenAVC.app root_and_check_bundle' in macos_script

    def test_the_library_runs_the_check_before_the_swap(self, apply_lib):
        # A chown after the swap would leave a window where the promoted bundle
        # is user-owned, and would miss entirely on any early-return path.
        release = _function_body(apply_lib, "apply_release")
        assert release.index('"$check" "$staged"') < release.index("apply_swap")


class TestLinuxStillHandlesIt:
    """Pinned so the platform that already got this right cannot quietly lose it."""

    def test_linux_helper_chowns_the_installed_tree(self):
        assert LINUX_HELPER.is_file()
        text = LINUX_HELPER.read_text(encoding="utf-8")
        assert "chown -R openavc:openavc" in text
        assert "chown root:root" in text
