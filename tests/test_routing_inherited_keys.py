"""The keys a routing plane takes from the ``routing:`` block above it.

``routing_checks.ROUTING_INHERITED`` is the one list: the load-time checks
merge a plane over it, and so does the runtime that reads the block
(``ui/matrix_inference``). These hold it to the contract (``spec.DEFS``):
every key the block declares but its ``planes`` and its ``params`` (a plane's
fixed extras are its own), in the order the block declares them, each one a
key a plane may also set. A key added to the block and not here, or a list
written a second time, fails here.
"""
from __future__ import annotations

from openavc.drivers import routing_checks
from openavc.drivers.spec import DEFS
from openavc.ui import matrix_inference


def test_a_plane_inherits_every_key_the_block_declares_but_planes_and_params() -> None:
    block_keys = [key for key in DEFS["routingBlock"]["fields"] if key not in ("planes", "params")]
    assert list(routing_checks.ROUTING_INHERITED) == block_keys


def test_each_inherited_key_is_one_a_plane_may_set() -> None:
    assert set(routing_checks.ROUTING_INHERITED) <= set(DEFS["routingPlane"]["fields"])


def test_the_runtime_merges_a_plane_over_the_same_keys() -> None:
    assert matrix_inference.ROUTING_INHERITED is routing_checks.ROUTING_INHERITED
