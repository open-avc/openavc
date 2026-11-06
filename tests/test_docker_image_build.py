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


START_SCRIPT = Path(__file__).resolve().parents[1] / "installer" / "docker-start.sh"


def _production_stage() -> list[str]:
    """The lines after the last FROM: the image that ships."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    last = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
    return [line.strip() for line in lines[last + 1:]]


def test_ports_below_1024_come_from_a_copy_of_python_never_python_itself():
    """A device that sends to a low port (SNMP traps to UDP 162) needs the
    server to open it, and under host networking the unprivileged user can't
    without CAP_NET_BIND_SERVICE. A program carrying a file capability cannot
    start at all in a container that drops it, so the capability goes on a
    copy, and the start script falls back to plain python."""
    stage = "\n".join(_production_stage())
    setcaps = [line for line in stage.splitlines() if "setcap" in line and "net_bind" in line]
    assert setcaps == ["&& setcap cap_net_bind_service+ep /usr/local/bin/python3-openavc"]
    assert 'cp "$(readlink -f "$(command -v python3)")" /usr/local/bin/python3-openavc' in stage
    assert 'ENTRYPOINT ["/usr/local/bin/openavc-start"]' in stage
    assert "COPY installer/docker-start.sh /usr/local/bin/openavc-start" in stage


def test_the_start_script_falls_back_to_plain_python():
    script = START_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n")
    try_copy = script.index('if /usr/local/bin/python3-openavc -c "" >/dev/null 2>&1; then')
    use_copy = script.index("exec /usr/local/bin/python3-openavc -m openavc.main \"$@\"")
    fall_back = script.index('exec python -m openavc.main "$@"')
    assert try_copy < use_copy < fall_back
    # The image makes it executable itself (the file's own mode bits are not
    # readable on every OS a checkout lives on).
    assert "RUN chmod 755 /usr/local/bin/openavc-start" in _production_stage()
