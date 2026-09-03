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


# --- The two copies of the public key ------------------------------------
#
# The trust anchor exists twice, on purpose, and the two protect different
# moments. `installer/trusted-keys/*.pem` ships inside the tarball and is what
# the root helper checks a SELF-UPDATE against. `install.sh` carries the same
# key inline, because a FRESH install has no tarball on disk yet to read a key
# out of -- it verifies SHA256SUMS.txt.sig before trusting any checksum in it.
#
# Nothing but a comment asked those two to agree. If they drift, a machine
# installed today trusts one key and every update it later applies is checked
# against another, and the failure is invisible until a release signed with the
# rotated key is refused by installs that never got it -- or, worse, accepted by
# a bootstrap still trusting a retired one. Rotation is exactly when this
# happens, because rotation edits one file at a time.

KEYS_DIR = REPO_ROOT / "installer" / "trusted-keys"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"


def _embedded_bootstrap_key() -> str:
    """The PEM inlined in install.sh, as install.sh itself would write it."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    match = re.search(r'^TRUSTED_SIGNING_PUBKEY="(.*?)"$', text, re.MULTILINE | re.DOTALL)
    assert match, (
        "install.sh has no TRUSTED_SIGNING_PUBKEY assignment. It is the "
        "fresh-install trust anchor — find out what replaced it before "
        "deleting this test."
    )
    return match.group(1).strip()


def test_bootstrap_key_matches_a_shipped_trusted_key() -> None:
    """install.sh's inline key is one of the keys the tarball ships.

    Both states are legitimate and both are pinned: armed (a *.pem exists, and
    install.sh must carry one of them) and unarmed (no *.pem, and install.sh
    must be empty). What is never legitimate is one armed and the other not.
    """
    shipped = sorted(KEYS_DIR.glob("*.pem"))
    embedded = _embedded_bootstrap_key()

    if not shipped:
        assert embedded == "", (
            "install.sh embeds a signing key but installer/trusted-keys/ holds "
            "no *.pem. Fresh installs would then verify against a key that "
            "self-updates know nothing about. Commit the public key to "
            "installer/trusted-keys/ as well."
        )
        return

    assert embedded, (
        f"installer/trusted-keys/ holds {len(shipped)} key(s) but "
        f"install.sh's TRUSTED_SIGNING_PUBKEY is empty, so a fresh install "
        f"skips signature verification while every self-update enforces it. "
        f"Mirror the public key into install.sh."
    )

    bodies = {p.name: p.read_text(encoding="utf-8").strip() for p in shipped}
    assert embedded in bodies.values(), (
        "install.sh's embedded key is not byte-identical to any key in "
        f"installer/trusted-keys/ ({', '.join(sorted(bodies))}). The fresh-install "
        "bootstrap and the self-update gate would trust different keys. If this "
        "is a rotation, finish it: the new public key goes in both places."
    )


def test_shipped_trusted_keys_are_parseable_public_keys() -> None:
    """A malformed or truncated PEM disarms the gate it is supposed to arm.

    `openssl dgst -verify` with an unreadable key exits non-zero, which the
    helper reports as "did not verify against any trusted key" — so a mangled
    key does not read as 'unarmed', it refuses every update. Catch it here
    rather than on a panel.
    """
    for pem in sorted(KEYS_DIR.glob("*.pem")):
        body = pem.read_text(encoding="utf-8")
        assert body.startswith("-----BEGIN PUBLIC KEY-----"), (
            f"{pem.name} is not a PEM public key. A private key or a "
            f"certificate here would break verification for every install."
        )
        assert body.rstrip().endswith("-----END PUBLIC KEY-----"), (
            f"{pem.name} is truncated — no END marker."
        )
        assert "PRIVATE KEY" not in body, (
            f"{pem.name} contains a PRIVATE key. This directory is published. "
            f"Treat the key as compromised and rotate it."
        )
