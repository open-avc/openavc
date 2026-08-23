"""The firewall helper has to reach the install, not just work.

`installer/firewall-sync.sh` was correct, covered by its own tests, copied by
install.sh and by update-helper.sh, and named in a comment as an ExecStartPre --
and it ran on no shipped install for its entire life. Two things were missing
and both are silent. No release archive packed the file, so both copies were an
`if [ -f ]` that found nothing. And the shipped unit carried no ExecStartPre
line for it, so even where the file did exist (the Pi image installs it)
nothing invoked it -- the only unit naming it was install.sh's inline fallback,
taken only when installer/openavc.service is missing, which it never is.

None of that is visible from a dev box. No dev box runs a firewall, the `-`
prefix means a missing helper never blocks startup, and the install script only
warns. What a firewalled site sees instead is HTTPS reported as saved and 8443
not answering, with nothing saying why.

So this reads the build paths and the unit rather than the script -- the
delivery, which is the half that broke. Deliberately not gated on bash the way
test_firewall_sync.py is: these only read files, and a delivery guard that
skips is the same green as one that passes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "installer" / "firewall-sync.sh"
UNIT = REPO_ROOT / "installer" / "openavc.service"
INSTALL_SH = REPO_ROOT / "installer" / "install.sh"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

# Every build path that has to carry the file. The release archives are checked
# separately below, because there are two of them and a substring match over the
# whole workflow passes when only one carries it.
BUILD_PATHS = {
    "pi image (CI, the published one)":
        REPO_ROOT / ".github" / "workflows" / "release-pi.yml",
    "pi image (local build script)":
        REPO_ROOT / "installer" / "pi-image" / "build.sh",
    "pi-gen stage that installs it into the rootfs":
        REPO_ROOT / "installer" / "pi-image" / "stage-openavc" / "01-install-openavc" / "00-run.sh",
}

_ARCHIVE_NAME = re.compile(r'ARCHIVE="([^"]+)"')
_EXEC_START_PRE = re.compile(r"^ExecStartPre=(?P<prefixes>[-+@:!]*)(?P<cmd>\S+)", re.M)


def _tar_invocations(text: str) -> list[tuple[str, str]]:
    """(archive name, whole command) for each `tar czf`, continuations joined.

    The name is the nearest `ARCHIVE=` assignment above it, which is how the
    workflow labels each artifact -- so a failure can say which one is short.
    """
    lines = text.splitlines()
    found: list[tuple[str, str]] = []
    current: list[str] | None = None
    start = 0
    for i, line in enumerate(lines):
        if current is None:
            if "tar czf" not in line:
                continue
            current, start = [line], i
        else:
            current.append(line)
        if not line.rstrip().endswith("\\"):
            label = next(
                (m.group(1) for prev in reversed(lines[:start])
                 for m in [_ARCHIVE_NAME.search(prev)] if m),
                "an unnamed archive",
            )
            found.append((label, "\n".join(current)))
            current = None
    return found


def _exec_start_pre(unit_text: str) -> list[tuple[str, str]]:
    """(prefix characters, command path) for each ExecStartPre, in order."""
    return [(m.group("prefixes"), m.group("cmd")) for m in _EXEC_START_PRE.finditer(unit_text)]


def _inline_fallback_unit() -> str:
    """The unit install.sh writes when installer/openavc.service is missing."""
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = re.search(r"<< 'UNIT'\n(.*?)\nUNIT\n", source, re.S)
    assert body, "install.sh no longer writes an inline unit heredoc -- this parser is stale"
    return body.group(1)


def test_the_helper_is_where_everything_expects_it() -> None:
    assert HELPER.is_file(), f"{HELPER} is missing -- this whole module points at the wrong file"


# --- it has to be in the box -------------------------------------------------

def test_every_release_archive_that_ships_the_update_helper_ships_this_one() -> None:
    """Both Linux archives, not just whichever one someone remembered.

    install.sh and update-helper.sh each copy the file out of the extracted
    archive with a bare `if [ -f ]`. An archive that omits it produces an
    install that reports success and has no helper.
    """
    archives = _tar_invocations(RELEASE_YML.read_text(encoding="utf-8"))
    assert archives, "no `tar czf` found in release.yml -- the parser is wrong, not the workflow"
    carriers = [(name, cmd) for name, cmd in archives if "installer/update-helper.sh" in cmd]
    assert len(carriers) >= 2, (
        f"expected the amd64 and arm64 Linux archives to pack the ExecStartPre helpers; "
        f"found {len(carriers)}. If an archive was renamed or removed, fix this list."
    )
    missing = [name for name, cmd in carriers if "installer/firewall-sync.sh" not in cmd]
    assert not missing, (
        f"these release archives ship update-helper.sh but not installer/firewall-sync.sh: "
        f"{missing}. Both copy steps that place it are silent `if [ -f ]` checks, so an "
        f"install off that archive has no firewall helper and says nothing about it."
    )


@pytest.mark.parametrize("label", sorted(BUILD_PATHS))
def test_the_build_path_carries_the_firewall_helper(label: str) -> None:
    path = BUILD_PATHS[label]
    assert path.is_file(), f"{path} is missing -- this test points at the wrong file"
    assert "firewall-sync.sh" in path.read_text(encoding="utf-8"), (
        f"{path.name} does not carry installer/firewall-sync.sh, so {label} would ship without it"
    )


# --- and something has to run it ---------------------------------------------

def test_the_shipped_unit_runs_the_helper_at_every_start() -> None:
    """This is the unit that matters: install.sh installs it and
    update-helper.sh's sync_unit enforces it on every existing install. The
    inline fallback in install.sh is reached only when this file is absent."""
    commands = [cmd for _, cmd in _exec_start_pre(UNIT.read_text(encoding="utf-8"))]
    assert any(c.endswith("/firewall-sync.sh") for c in commands), (
        "installer/openavc.service has no ExecStartPre for firewall-sync.sh, so the helper "
        "ships and never runs. On a firewalled host, enabling HTTPS in Settings then reports "
        "saved while the port stays shut."
    )


def test_the_inline_fallback_unit_agrees_with_the_shipped_one() -> None:
    """Two units that disagree about what runs at start is worse than one gap:
    which behaviour you get depends on whether a file happened to be there."""
    shipped = {cmd for _, cmd in _exec_start_pre(UNIT.read_text(encoding="utf-8"))}
    fallback = {cmd for _, cmd in _exec_start_pre(_inline_fallback_unit())}
    assert shipped == fallback, (
        f"installer/openavc.service and install.sh's inline fallback run different "
        f"ExecStartPre steps. Only in the shipped unit: {sorted(shipped - fallback)}. "
        f"Only in the fallback: {sorted(fallback - shipped)}."
    )


@pytest.mark.parametrize("unit", ["shipped", "inline fallback"])
def test_the_helper_runs_as_root_and_cannot_wedge_the_service(unit: str) -> None:
    text = UNIT.read_text(encoding="utf-8") if unit == "shipped" else _inline_fallback_unit()
    prefixes = [p for p, cmd in _exec_start_pre(text) if cmd.endswith("/firewall-sync.sh")]
    assert prefixes, f"the {unit} unit does not invoke firewall-sync.sh at all"
    for prefix in prefixes:
        assert "+" in prefix, (
            f"the {unit} unit runs firewall-sync.sh without the `+` prefix, so it runs as the "
            f"unprivileged service user and every ufw/firewall-cmd call fails"
        )
        assert "-" in prefix, (
            f"the {unit} unit runs firewall-sync.sh without the `-` prefix, so a non-zero exit "
            f"stops the service permanently -- Restart=always does not retry ExecStartPre"
        )


@pytest.mark.parametrize("unit", ["shipped", "inline fallback"])
def test_the_helper_runs_after_the_update_helper(unit: str) -> None:
    """An in-app update refreshes firewall-sync.sh from the new release inside
    update-helper.sh. Running first would sync the ports with the old copy."""
    commands = [cmd for _, cmd in _exec_start_pre(
        UNIT.read_text(encoding="utf-8") if unit == "shipped" else _inline_fallback_unit()
    )]
    update = next(i for i, c in enumerate(commands) if c.endswith("/update-helper.sh"))
    firewall = next(i for i, c in enumerate(commands) if c.endswith("/firewall-sync.sh"))
    assert update < firewall, (
        f"the {unit} unit runs firewall-sync.sh before update-helper.sh, so on the boot that "
        f"applies an update the ports are synced by the outgoing release's helper"
    )
