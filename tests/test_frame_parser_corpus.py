"""
Frame-parser contract sweep over the community driver corpus.

The platform-contract property, true of every driver and specific to none:

    CallableFrameParser must never hold bytes the driver's parse function
    asked it to drop.

Stated as an invariant that can be checked without knowing any protocol: after
feed() returns, the buffer the parser retained must be a *fixed point* of the
parse function — feeding that buffer back must yield no message and consume
nothing. If the parse function would still shrink it, the parser is holding
bytes it was told to discard, and the stream is wedged until the max_buffer
guard clears it.

That is the whole of the finding this sweep exists for: the buffer returned
alongside a `None` message used to be discarded, so a parse function could not
drop leading garbage or resync past a corrupt frame. Any driver whose parse
function trims on the None branch was silently unable to recover.

This is a corpus-wide *contract* sweep, not device validation: it names no
product, ships no captured fixture, and asserts nothing about any particular
protocol. Per-driver protocol correctness is tested next to each driver in
openavc-drivers. It cannot live there either, because that repo's CI installs
no `server` package and so cannot import the parser under test.
"""

from __future__ import annotations

import importlib.util
import os
import random
import sys
from pathlib import Path

import pytest

from openavc.core.event_bus import EventBus
from openavc.core.state_store import StateStore
from openavc.drivers.base import BaseDriver
from openavc.transport.frame_parsers import CallableFrameParser
from tests import gates

# The community driver library normally sits beside this repo in the
# workspace. OPENAVC_DRIVERS_ROOT points somewhere else -- an automated run
# clones it into a subdirectory of the checkout, because that is the only
# place a checkout step is allowed to write.
DRIVERS_ROOT = Path(
    os.environ.get("OPENAVC_DRIVERS_ROOT")
    or Path(__file__).resolve().parent.parent.parent / "openavc-drivers"
)

# A parse function that neither parses nor consumes would loop forever. The
# parser guards against that; this cap turns a regression in the guard into a
# failed test instead of a hung CI run.
MAX_PARSE_CALLS = 50_000


def _discover_callable_parser_drivers() -> list[Path]:
    """Every Python driver in the corpus that frames with a parse function."""
    if not DRIVERS_ROOT.exists():
        return []
    found = []
    for path in sorted(DRIVERS_ROOT.rglob("*.py")):
        parts = set(path.parts)
        if parts & {"tests", "scripts", ".venv", "__pycache__"}:
            continue
        # Simulators and discovery companions are not drivers.
        if path.name.endswith(("_sim.py", "_discovery.py")):
            continue
        if "CallableFrameParser" in path.read_text(encoding="utf-8"):
            found.append(path)
    return found


DRIVER_FILES = _discover_callable_parser_drivers()


def _load_parse_functions(path: Path) -> list[tuple[str, object]]:
    """Import a driver module and return (label, parse_fn) for each parser.

    The parse function is taken from the parser the driver itself builds in
    _create_frame_parser(), so this exercises what the runtime would use --
    including a stateful bound method, which one shipped driver uses.
    """
    module_name = f"_corpus_driver_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)

    out: list[tuple[str, object]] = []
    for name, obj in vars(module).items():
        if (
            isinstance(obj, type)
            and issubclass(obj, BaseDriver)
            and obj is not BaseDriver
            and "_create_frame_parser" in vars(obj)
        ):
            driver = obj("corpus_probe", {}, StateStore(), EventBus())
            parser = driver._create_frame_parser()
            if isinstance(parser, CallableFrameParser):
                out.append((name, parser._parse_fn))
    return out


# A spread of bytes that show up as start markers, delimiters and length fields
# across binary AV protocols. Nothing here is device-specific -- the point is to
# reach each parse function's resync and discard branches with bytes it might
# plausibly act on, rather than only bytes it ignores.
_MARKERISH = bytes(
    [0x00, 0x02, 0x0A, 0x0D, 0x1B, 0x20, 0x21, 0x23, 0x30, 0x3A, 0x41,
     0x7B, 0x80, 0xA5, 0xAA, 0xFF]
)


def _probe_streams() -> list[tuple[str, bytes]]:
    """Deterministic byte streams. Seeded so a failure is reproducible."""
    rnd = random.Random(20260730)
    return [
        ("zeros", b"\x00" * 128),
        ("high-bits", b"\xff" * 128),
        ("random", bytes(rnd.randrange(256) for _ in range(1024))),
        ("markerish", bytes(rnd.choice(_MARKERISH) for _ in range(1024))),
        # Larger than any single frame, to reach the bounded-tail guards a
        # parse function may only apply once its buffer has grown.
        ("markerish-8k", bytes(rnd.choice(_MARKERISH) for _ in range(8192))),
    ]


def _assert_fixed_point(parse_fn, parser, label: str, note: str) -> None:
    """The retained buffer must be one the parse function would not shrink."""
    retained = parser._buffer
    if not retained:
        return
    msg, remaining = parse_fn(retained)
    if msg is not None:
        pytest.fail(
            f"{label}: {note}: the parser retained {len(retained)} bytes that "
            f"still contain a complete message -- feed() returned early."
        )
    assert len(remaining) >= len(retained), (
        f"{label}: {note}: the parser is holding {len(retained)} bytes but the "
        f"parse function asked it to keep only {len(remaining)}. The buffer "
        f"returned with a None message must be honored, or this stream cannot "
        f"resync past garbage or a corrupt frame."
    )


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if DRIVER_FILES else f"no community drivers found at {DRIVERS_ROOT}",
)
@pytest.mark.parametrize("driver_path", DRIVER_FILES, ids=lambda p: p.name)
def test_parser_never_retains_bytes_the_parse_function_dropped(
    driver_path: Path,
) -> None:
    """Every shipped parse function's dropped bytes are actually dropped."""
    parsers = _load_parse_functions(driver_path)
    assert parsers, (
        f"{driver_path.name} mentions CallableFrameParser but no driver class "
        f"in it builds one -- update this sweep's discovery if the driver "
        f"contract changed."
    )

    for label, parse_fn in parsers:
        for stream_name, blob in _probe_streams():
            calls = {"n": 0}

            def counted(buf, _fn=parse_fn, _calls=calls):
                _calls["n"] += 1
                if _calls["n"] > MAX_PARSE_CALLS:
                    raise AssertionError(
                        f"{label}: parse function called over "
                        f"{MAX_PARSE_CALLS} times on one feed -- the "
                        f"no-forward-progress guard is not holding."
                    )
                return _fn(buf)

            # Whole blob at once.
            parser = CallableFrameParser(counted)
            parser.feed(blob)
            _assert_fixed_point(
                parse_fn, parser, label, f"{stream_name} fed whole"
            )

            # Split into small chunks: a parse function sees partial frames and
            # its "need more data" branch, which must still leave a fixed point.
            parser = CallableFrameParser(counted)
            for i in range(0, len(blob), 7):
                parser.feed(blob[i : i + 7])
                _assert_fixed_point(
                    parse_fn, parser, label, f"{stream_name} chunked at {i}"
                )


@gates.skipif_missing(
    gates.DRIVER_CORPUS,
    None if DRIVER_FILES else f"no community drivers found at {DRIVERS_ROOT}",
)
def test_sweep_reaches_parse_functions_that_drop_on_the_none_branch() -> None:
    """Guard against this sweep quietly becoming vacuous.

    The invariant above passes trivially for a parse function that never trims
    on the None branch. If a corpus change left no driver exercising a drop,
    the sweep would still be green while testing nothing, so assert that the
    probes do reach the branch the finding was about.
    """
    dropping = []
    for path in DRIVER_FILES:
        for label, parse_fn in _load_parse_functions(path):
            for _name, blob in _probe_streams():
                hit = False
                for i in range(0, len(blob), 13):
                    window = blob[: i + 13]
                    if not window:
                        continue
                    msg, remaining = parse_fn(window)
                    if msg is None and len(remaining) < len(window):
                        hit = True
                        break
                if hit:
                    dropping.append(label)
                    break

    assert dropping, (
        "no shipped parse function dropped bytes on the None branch under any "
        "probe stream -- either the probes no longer reach that branch or the "
        "corpus changed. Either way this sweep is no longer testing the "
        "contract it was written for."
    )
