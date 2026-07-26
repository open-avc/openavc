"""Gates for tests that need a tool the Python install does not carry.

Some tests need more than the Python packages: Node plus the Programmer UI's
``node_modules`` for the harnesses that bundle real TypeScript, ``bash`` for
the installer scripts, ``openssl`` for certificate fixtures, the community
driver library for the sweeps that read every shipped driver.

Skipping when the tool is missing is a kindness on a developer's machine --
someone with only the Python side installed still gets a green run. In an
automated run it is the opposite: the job that was meant to exercise those
tests skips them and still reports success, so they quietly stop being a
gate at all. That is not hypothetical. Every one of the TypeScript harnesses
skipped in the automated run for months, because the job that runs the test
suite never installed Node, while the suite reported thousands of tests
passing.

So each gate has a name. A run that installs the tool says so by setting the
matching environment variable to 1, and then a missing tool FAILS that run
and names the variable it promised. A run that leaves the variable unset is
stating plainly that these tests are not one of its gates. Either it runs,
or it says out loud that it did not.

Usage::

    from tests import gates

    reason = _toolchain_reason()        # None when everything is present
    if reason:
        gates.skip_or_fail(gates.NODE, reason)

For a module that cannot even be imported without the tool::

    gates.skip_or_fail(gates.NODE, reason, module_level=True)

For a ``skipif`` mark evaluated while the module is being imported::

    pytestmark = gates.skipif_missing(gates.BASH, _bash_reason())
"""

from __future__ import annotations

import os

import pytest

# Node plus web/programmer/node_modules -- the harnesses that bundle the real
# TypeScript sources with esbuild, and the panel DOM tests that need jsdom.
NODE = "OPENAVC_REQUIRE_NODE"

# The community driver library, beside this repo or wherever
# OPENAVC_DRIVERS_ROOT points -- the sweeps that read every shipped driver.
DRIVER_CORPUS = "OPENAVC_REQUIRE_DRIVER_CORPUS"

# A POSIX bash, for the installer, firewall and image shell scripts.
BASH = "OPENAVC_REQUIRE_BASH"

# The openssl command line, used to mint throwaway certificates.
OPENSSL = "OPENAVC_REQUIRE_OPENSSL"

# git, plus a real checkout to run it against.
GIT = "OPENAVC_REQUIRE_GIT"

# Playwright and its browser download, for the end-to-end IDE tests.
E2E = "OPENAVC_REQUIRE_E2E"

_TRUE = {"1", "true", "yes", "on"}


class MissingRequiredTool(AssertionError):
    """A run promised to provide a gate's tool, and the tool is not there."""


def is_required(gate: str) -> bool:
    """True when this run promised to provide the gate's tool."""
    return os.environ.get(gate, "").strip().lower() in _TRUE


def _promise_broken(gate: str, reason: str) -> MissingRequiredTool:
    return MissingRequiredTool(
        f"{gate}=1 promised this tool, but {reason}. Install it on this "
        f"machine, or unset {gate} to declare these tests out of scope here."
    )


def skip_or_fail(
    gate: str, reason: str | None, *, module_level: bool = False
) -> None:
    """Skip because ``reason`` says the tool is missing -- unless it was promised.

    ``reason`` is None when the tool is present, so this does nothing on a
    fully equipped machine. Otherwise it skips, or raises when the run set
    the gate's environment variable.
    """
    if not reason:
        return
    if is_required(gate):
        raise _promise_broken(gate, reason)
    pytest.skip(reason, allow_module_level=module_level)


def fail_if_required(gate: str, reason: str | None) -> bool:
    """Raise when the run promised the tool; else report whether it is missing.

    For the places that cannot skip -- a conftest deciding what to collect,
    say -- and need to take their own fallback path instead.
    """
    if not reason:
        return False
    if is_required(gate):
        raise _promise_broken(gate, reason)
    return True


def skipif_missing(gate: str, reason: str | None) -> pytest.MarkDecorator:
    """A ``skipif`` mark for ``reason`` -- raising now if the run promised the tool.

    Marks are built while the module is imported, which is too early to fail
    a single test, so a broken promise raises here instead. pytest reports
    that as a collection error, which is red, which is the point.
    """
    if reason and is_required(gate):
        raise _promise_broken(gate, reason)
    return pytest.mark.skipif(bool(reason), reason=reason or "tool present")
