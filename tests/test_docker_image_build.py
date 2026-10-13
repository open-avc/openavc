"""The Docker image builds on an ordinary Docker host.

CI's Docker job runs on a 16 GB runner, so a build step that needs more memory
than a smaller host gives it passes there and fails for the person building
the image themselves.
"""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _stage(name: str) -> list[str]:
    """The lines of one ``FROM ... AS <name>`` stage."""
    stage: list[str] | None = None
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM "):
            if stage is not None:
                return stage
            if line.split()[-1] == name:
                stage = []
            continue
        if stage is not None:
            stage.append(line.strip())
    assert stage is not None, f"the Dockerfile has no {name!r} stage -- this parser is stale"
    return stage


def test_the_frontend_build_has_room_on_a_smaller_host():
    """Node's default heap (about 2 GB) aborts the Programmer's build with
    "JavaScript heap out of memory" on a Docker host under about 16 GB."""
    stage = _stage("frontend")
    heap = stage.index("ENV NODE_OPTIONS=--max-old-space-size=4096")
    build = next(i for i, line in enumerate(stage) if "npm run build" in line)
    assert heap < build
