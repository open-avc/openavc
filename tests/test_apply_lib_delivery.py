"""The apply library has to be in every install the update helper runs on.

`installer/update-helper.sh` applies an update through `installer/apply-lib.sh`,
which it reads from the `installer/` folder of the install it runs in. The
helper reaches an install by three roads, and the library has to travel each:

- the Linux release archives (amd64 and arm64): what `install.sh` unpacks and
  what an in-app update swaps in, on Linux, the Raspberry Pi and appliance
  hardware alike;
- the Pi image's server archive, built twice (the published CI job and the
  local build script), which lays down the tree the image's helper starts on.

An archive without it ships a helper that refuses every update, which reads in
the field as an update that did not apply and nothing more. Read the pack
lists rather than run them, and not gated on bash: a delivery guard that skips
is the same green as one that passes.

A re-run of `install.sh` over an install replaces it by the same library, and
`install.sh` is fetched and run on its own, with no release beside it (the
release it installs may be older than the library), so it carries the
library's text, written by `scripts/embed_apply_lib.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = "installer/apply-lib.sh"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# Each file whose `tar czf` packs an install tree, and how many such archives
# it builds.
PACKERS = {
    "linux release archives": (REPO_ROOT / ".github" / "workflows" / "release.yml", 2),
    "pi image (CI, the published one)": (REPO_ROOT / ".github" / "workflows" / "release-pi.yml", 1),
    "pi image (local build script)": (REPO_ROOT / "installer" / "pi-image" / "build.sh", 1),
}


def _install_tree_archives(text: str) -> list[list[str]]:
    """The pack targets of each `tar czf` that packs the `openavc/` package,
    continuation lines joined, options and comments left out."""
    archives: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if current is None:
            if "tar czf" not in line:
                continue
            current = []
        words = line.strip().rstrip("\\").split()
        current.extend(w for w in words if not w.startswith("-") and w not in ("tar", "czf"))
        if not line.rstrip().endswith("\\"):
            if "openavc/" in current:
                archives.append(current)
            current = None
    return archives


def test_the_library_is_where_the_pack_lists_say() -> None:
    assert (REPO_ROOT / LIBRARY).is_file(), f"{LIBRARY} is missing -- this module points at the wrong file"


@pytest.mark.parametrize("label", sorted(PACKERS))
def test_every_install_tree_archive_packs_the_library(label: str) -> None:
    path, expected = PACKERS[label]
    assert path.is_file(), f"{path} is missing -- this test points at the wrong file"
    archives = _install_tree_archives(path.read_text(encoding="utf-8"))
    assert len(archives) == expected, (
        f"expected {expected} `tar czf` of the install tree in {path.name}, found "
        f"{len(archives)}; the parser or this table is stale"
    )
    missing = sum(1 for targets in archives if LIBRARY not in targets)
    assert not missing, (
        f"{missing} of the {label} built by {path.name} do not pack {LIBRARY}. The "
        f"update helper on such an install refuses every update; add it to the tar list."
    )


def test_the_mac_bundle_carries_the_library_beside_its_wrapper() -> None:
    """The fourth road: on a Mac the LaunchDaemon runs the wrapper inside the
    installed OpenAVC.app, which reads the library from its own folder, so
    build-macos.sh puts it there in every bundle (the .pkg's and the update
    tarball's are the same bundle)."""
    build = (REPO_ROOT / "installer" / "build-macos.sh").read_text(encoding="utf-8")
    assert f'cp {LIBRARY} "$APP/Contents/MacOS/apply-lib.sh"' in build, (
        "build-macos.sh no longer copies the library beside the wrapper in the bundle; "
        "a Mac on that bundle applies no update"
    )
    assert 'cp installer/openavc-macos-run.sh "$APP/Contents/MacOS/openavc-macos-run.sh"' in build, (
        "the wrapper moved inside the bundle; put the library beside it"
    )
    wrapper = (REPO_ROOT / "installer" / "openavc-macos-run.sh").read_text(encoding="utf-8")
    assert '"$WRAPPER_DIR/apply-lib.sh"' in wrapper, (
        "openavc-macos-run.sh no longer reads apply-lib.sh beside itself; find where it reads it now"
    )


def test_the_helper_reads_the_library_from_its_own_install() -> None:
    """The premise of the pack lists: the helper sources the library from
    installer/ beside itself, which is where these archives put it."""
    helper = (REPO_ROOT / "installer" / "update-helper.sh").read_text(encoding="utf-8")
    assert '"$HELPER_DIR/installer/apply-lib.sh"' in helper, (
        "update-helper.sh no longer reads installer/apply-lib.sh beside itself; "
        "the archives above pack it there, so find where it reads it now"
    )


def test_install_sh_carries_the_library_as_it_is() -> None:
    import embed_apply_lib

    install = (REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    library = (REPO_ROOT / LIBRARY).read_text(encoding="utf-8")
    assert embed_apply_lib.is_current(install, library), (
        f"installer/install.sh carries a copy of {LIBRARY} that differs from it, so a "
        f"re-run of install.sh replaces an install by another rule than an update does. "
        f"Run: python scripts/embed_apply_lib.py"
    )


def test_the_copy_is_written_from_the_library() -> None:
    """What the fix the test above names does, on a stale copy."""
    import embed_apply_lib

    install = (REPO_ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
    library = (REPO_ROOT / LIBRARY).read_text(encoding="utf-8")
    stale = install.replace(library, "apply_release() { :; }\n")
    assert stale != install, "the copy in install.sh is not the library's text -- this test is stale"
    assert not embed_apply_lib.is_current(stale, library)
    assert embed_apply_lib.render(stale, library) == install
