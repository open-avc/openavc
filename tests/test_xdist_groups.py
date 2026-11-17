"""Test files that must never run at the same moment share one xdist group.

Under ``--dist loadgroup`` a group runs on one worker, one test after another,
so two files in one group can never overlap. The pairs here were found
overlapping in the failures they caused.
"""

from __future__ import annotations

from types import ModuleType

from tests import test_ssh_real_client, test_windows_firewall_live
from tests.conftest import xdist_group_for


def test_the_real_ssh_client_never_connects_while_the_firewall_is_being_changed():
    assert xdist_group_for(test_ssh_real_client) == xdist_group_for(test_windows_firewall_live)


def test_a_file_names_its_own_group_unless_it_spreads():
    plain = ModuleType("tests.test_plain")
    spread = ModuleType("tests.test_spread")
    spread.XDIST_SPREAD = True

    assert xdist_group_for(plain) == "tests.test_plain"
    assert xdist_group_for(spread) is None
