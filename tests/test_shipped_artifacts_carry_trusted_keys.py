"""Every shipped artifact has to carry `installer/trusted-keys/`.

The update helper's signature gate is *warn-and-proceed while the key set is
empty, fail-closed once armed*. That is deliberate -- it lets the signing code
ship before the production key exists without bricking every update. The catch
is that an artifact which simply forgot to pack the keys is indistinguishable,
at runtime, from one on a release where signing genuinely is not armed yet:
both log "release signing not yet armed, skipping signature check" and carry on.

So the day the key ceremony happens, every deployment that packs the keys flips
to enforcement and any deployment that does not keeps accepting unsigned
updates -- silently, and specifically re-opening the openavc-user -> root
escalation the signing work exists to close (H-075, backlog §43).

That is not hypothetical. `release-pi.yml` shipped without the keys while
`installer/pi-image/build.sh` shipped with them, so the Pi image users flash had
no trust anchor while the locally built one did -- which is also the likely
reason a Pi-tested sign-off did not catch it. Same two-parallel-build-paths trap
as `build.bat` in §66/M2: the local script and the CI job are separate hand-kept
copies, and nothing read either of them.

Reading the pack lists and asserting the anchor is in all of them is the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TRUST_ANCHOR = "installer/trusted-keys/"

# Each shipped artifact and the file whose `tar` invocation packs it. The Linux
# and Pi archives are the two that the root update-helper verifies against;
# macOS seeds the anchor into the .app through build-macos.sh instead.
PACKERS = {
    "linux release archive": REPO_ROOT / ".github" / "workflows" / "release.yml",
    "pi image (CI, the published one)": REPO_ROOT / ".github" / "workflows" / "release-pi.yml",
    "pi image (local build script)": REPO_ROOT / "installer" / "pi-image" / "build.sh",
}


@pytest.mark.parametrize("label", sorted(PACKERS))
def test_shipped_artifact_packs_the_trust_anchor(label: str) -> None:
    """The file that builds this artifact names `installer/trusted-keys/`."""
    path = PACKERS[label]
    assert path.is_file(), f"{path} is missing — this test is pointing at the wrong file"
    text = path.read_text(encoding="utf-8")

    # Only count it if it is a pack target, not an --exclude or a comment.
    packed = any(
        TRUST_ANCHOR in line
        and "--exclude" not in line
        and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )
    assert packed, (
        f"{path.relative_to(REPO_ROOT)} builds the {label} without packing "
        f"{TRUST_ANCHOR}. An artifact with no trust anchor reads as 'signing not "
        f"yet armed' forever and accepts unsigned updates, while every other "
        f"deployment fails closed once the production key is enrolled. "
        f"Add {TRUST_ANCHOR} to its tar list."
    )


def test_the_helper_still_treats_an_empty_key_set_as_unarmed() -> None:
    """The premise of the test above.

    If the helper ever changes to fail closed on an empty key set, a missing
    anchor becomes a loud break rather than a silent one and this guard matters
    less. Pin the assumption so the reasoning above cannot go quietly stale.
    """
    helper = (REPO_ROOT / "installer" / "update-helper.sh").read_text(encoding="utf-8")
    assert re.search(r"not yet armed", helper), (
        "update-helper.sh no longer has the warn-and-proceed 'not yet armed' "
        "branch. Re-read backlog §43: if it now fails closed on an empty key "
        "set, the silent-failure reasoning in this module needs rewriting."
    )
