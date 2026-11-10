"""The keys a routing plane takes from the ``routing:`` block above it.

``spec.ROUTING_INHERITED`` is the one list: the load-time checks
(``routing_checks``) merge a plane over it, and so does the runtime that reads
the block (``ui/matrix_inference``). These hold it to the block's own fields in
``spec.DEFS``: every key the block declares but its ``planes`` and its
``params``, in the order the block declares them, each one a key a plane may
also set. (The block's ``params`` are not merged key by key: the runtime takes
them whole when a plane's own are missing, empty or not a mapping, and the
checks judge only a plane's own.) A
key added to the block and not here fails here; these see only that both
readers use this tuple, not a list written a second time under another name.
"""
from __future__ import annotations

from openavc.drivers import routing_checks
from openavc.drivers.spec import DEFS, ROUTING_INHERITED
from openavc.ui import matrix_inference


def test_a_plane_inherits_every_key_the_block_declares_but_planes_and_params() -> None:
    block_keys = [key for key in DEFS["routingBlock"]["fields"] if key not in ("planes", "params")]
    assert list(ROUTING_INHERITED) == block_keys


def test_each_inherited_key_is_one_a_plane_may_set() -> None:
    assert set(ROUTING_INHERITED) <= set(DEFS["routingPlane"]["fields"])


def test_the_checks_and_the_runtime_merge_a_plane_over_the_same_keys() -> None:
    assert routing_checks.ROUTING_INHERITED is ROUTING_INHERITED
    assert matrix_inference.ROUTING_INHERITED is ROUTING_INHERITED
