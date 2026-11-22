"""Write a generator's committed files.

Each artifact is a path relative to the openavc repo root, rendered by the
generator and written with LF line endings whatever the platform, so the
byte comparison a generated file's test makes holds on every machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]


def write_artifacts(artifacts: Iterable[str], render: Callable[[str], str]) -> int:
    """Render and write each artifact, saying which; 0 for a generator's exit."""
    for artifact in artifacts:
        path = REPO_ROOT / artifact
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(artifact), encoding="utf-8", newline="\n")
        print(f"wrote {artifact}")
    return 0
