"""A service stop signals the server alone, never the simulator beside it.

systemd's default (``KillMode=control-group``) sends the stop signal to every
process in the service at once. The simulator then exited before the server
had begun its own stop, and the server, seeing its simulator gone while it
still ran, pointed every simulated device back at its real address and dialed
it on the way down. ``KillMode=mixed`` signals the server alone and kills
whatever is left only once it has exited; the server's stop ends the simulator
itself, leaving the devices where they were.

Every copy of the unit that reaches a box says so: the shipped file (install.sh
installs it, the Pi image is built with it, and update-helper.sh puts it on
every existing install at its next start) and the unit the deployment guide
prints for a hand install.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT = REPO_ROOT / "installer" / "openavc.service"
DEPLOYMENT_GUIDE = REPO_ROOT / "docs" / "deployment.md"


def _guide_unit() -> str:
    """The openavc.service the deployment guide shows for a hand install."""
    text = DEPLOYMENT_GUIDE.read_text(encoding="utf-8")
    body = re.search(r"```ini\n# /etc/systemd/system/openavc\.service\n(.*?)```", text, re.S)
    assert body, "docs/deployment.md no longer shows openavc.service -- this parser is stale"
    return body.group(1)


_UNITS = {
    "installer/openavc.service": lambda: UNIT.read_text(encoding="utf-8"),
    "the deployment guide's unit": _guide_unit,
}


def _service_section(unit_text: str) -> str:
    section = re.search(r"^\[Service\]\n(.*?)(?=^\[|\Z)", unit_text, re.S | re.M)
    assert section, "no [Service] section"
    return section.group(1)


@pytest.mark.parametrize("name", sorted(_UNITS))
def test_the_unit_signals_the_server_alone(name: str) -> None:
    settings = re.findall(r"^KillMode=(\S+)\s*$", _service_section(_UNITS[name]()), re.M)
    assert settings == ["mixed"], (
        f"{name} sets KillMode={settings or 'nothing (control-group)'}: a stop then signals the "
        f"simulator with the server, and a simulated room is pointed at its real equipment on "
        f"the way down"
    )
