"""Every directory `installer/build.bat` walks into has to be there.

The script is a plain batch file, so a wrong path is not an error anyone sees
until someone runs it: `cd` fails, the shell stays where it was, and the next
command runs in the wrong directory. That is how the move of the frontends
under `openavc/` got past a green suite and a zero-reference grep -- the grep
looked for `web/` and the batch file spells it `web\\`, and no test read the
paths at all. The CI release job builds the shipped installer from its own
steps, so the miss only broke the local build, which is exactly the kind of
thing nobody notices for a release or two.

Reading the `cd` targets and checking they exist is the whole guard. It costs
nothing and it fails the moment a directory moves again.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_BAT = REPO_ROOT / "installer" / "build.bat"

# `cd /d "%~dp0.."` re-anchors on the repo root; the plain `cd <path>` lines
# after it are relative to that anchor, and `cd ..\..` walks back up.
_CD = re.compile(r"^\s*cd\s+(?!/d)(?P<path>[^\s\"]+)\s*$", re.IGNORECASE | re.MULTILINE)


def _cd_targets() -> list[str]:
    return [m.group("path") for m in _CD.finditer(BUILD_BAT.read_text(encoding="utf-8"))]


def test_build_bat_only_enters_directories_that_exist() -> None:
    """Each `cd <relative path>` names a real directory in the repo."""
    descents = [p for p in _cd_targets() if not p.startswith("..")]
    assert descents, "no `cd` targets found — the parser is wrong, not the script"
    missing = [p for p in descents if not (REPO_ROOT / p.replace("\\", "/")).is_dir()]
    assert not missing, (
        f"installer/build.bat walks into directories that do not exist: {missing}. "
        f"The `cd` silently fails and the step after it runs in the wrong place."
    )


def test_build_bat_returns_to_the_repo_root_after_each_descent() -> None:
    """A descent of N levels is followed by a climb of N, so the root is restored."""
    depth = 0
    for target in _cd_targets():
        parts = [p for p in target.replace("/", "\\").split("\\") if p]
        if parts and all(p == ".." for p in parts):
            depth -= len(parts)
        else:
            depth += len(parts)
        assert depth >= 0, (
            f"installer/build.bat climbs above the repo root at `cd {target}` — "
            f"the steps after it run outside the checkout."
        )
    assert depth == 0, (
        f"installer/build.bat ends {depth} level(s) away from the repo root; "
        f"every `cd` into a subdirectory needs a matching climb back."
    )
