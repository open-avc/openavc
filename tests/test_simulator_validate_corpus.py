"""Every shipped driver validates against its simulator, with zero errors.

``simulator.validate`` is the tool every contributor guide tells an author to
run before submitting a driver. Nothing ran it. It was a manual CLI in the
platform repo, and the community repo's CI — which is where drivers actually
land — could not call it, because that repo does not check out the platform.
So the one check standing between a driver and a simulator that does not work
was advice.

It had drifted accordingly. Measured over the corpus on 2026-07-31: 12 of 92
drivers failed, 56 errors. Twenty-eight of those were qlab, whose simulator
answered nothing a real client sends — the auto-generated OSC simulator built
its handlers from the driver's ``responses:`` block, which is QLab's *reply*
namespace, so it listened on the addresses QLab sends and was deaf to every
address QLab receives. The driver polls four values and watchdogs on /thump,
so that device would have connected and then gone offline after two silent
probes. It shipped as ``simulated: true``.

This is the same shape as tests/test_driver_round_trip_parity.py and lands
under the same carve-out: a corpus-wide sweep of a *platform contract* that
names no product. It cannot live in openavc-drivers — that repo's requirements
carry no ``openavc`` package, so it cannot import the validator it runs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from simulator.validate import find_drivers, validate_python_driver, validate_yaml_driver
from tests import gates

# Same resolution as the round-trip sweep: beside this repo in the workspace,
# or wherever OPENAVC_DRIVERS_ROOT points -- an automated run clones it into a
# subdirectory of the checkout, the only place a checkout step may write.
DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or Path(__file__).resolve().parent.parent.parent / "openavc-drivers"
)


def _discover() -> list[tuple[Path, str]]:
    if not DRIVERS_ROOT.exists():
        return []
    return sorted(find_drivers(DRIVERS_ROOT), key=lambda pair: pair[0].name)


DRIVERS = _discover()


# A missing driver library is a skip, not a hard failure -- someone may have
# cloned only this repo. A run that clones both says so by setting
# OPENAVC_REQUIRE_DRIVER_CORPUS, and then an empty corpus fails instead of
# quietly turning this sweep into nothing.
@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if DRIVERS else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize(
    ("driver_path", "driver_type"), DRIVERS, ids=lambda v: getattr(v, "name", v)
)
def test_driver_validates_against_its_simulator(driver_path: Path, driver_type: str):
    """Zero errors. Warnings and infos are advice; an error is a broken bench.

    An error here means the simulated device does not behave like the driver
    expects -- a command with no handler, a poll query nothing answers, a
    response the driver cannot parse. Testing against it then proves nothing,
    which is worse than having no simulator at all, because the bench says
    "pass".
    """
    result = (
        validate_python_driver(driver_path)
        if driver_type == "python"
        else validate_yaml_driver(driver_path)
    )
    assert not result.errors, "\n".join(
        [f"{driver_path.name} does not validate against its simulator:"]
        + [f"  [{i.check}] {i.message}" for i in result.errors]
    )
