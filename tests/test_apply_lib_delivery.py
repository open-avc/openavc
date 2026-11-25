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
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LIBRARY = "installer/apply-lib.sh"

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


def test_the_helper_reads_the_library_from_its_own_install() -> None:
    """The premise of the pack lists: the helper sources the library from
    installer/ beside itself, which is where these archives put it."""
    helper = (REPO_ROOT / "installer" / "update-helper.sh").read_text(encoding="utf-8")
    assert '"$HELPER_DIR/installer/apply-lib.sh"' in helper, (
        "update-helper.sh no longer reads installer/apply-lib.sh beside itself; "
        "the archives above pack it there, so find where it reads it now"
    )
