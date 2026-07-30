"""Every Python example in the driver guide must parse, and every platform
reference in it must resolve.

``docs/creating-drivers.md`` teaches the Python driver path by example, and an
author copies an example verbatim. ``test_prose_documents_the_contract.py``
already pins the *declarative* half — that the guide's YAML uses real contract
fields — but it never opens a ```python fence, so the code half was pinned by
nothing. Two things went wrong there and both shipped:

* **A helper that does not exist.** The guide's sibling in the community repo
  imported ``crc16`` from ``binary_helpers`` for as long as anyone can
  remember; the function is ``crc16_ccitt`` and ``crc16`` never existed. That
  fails loudly at import, which is the *good* case.
* **An async call that is never awaited.** ``writing-simulators.md`` called an
  ``async def`` helper without ``await``, so the warm-up it demonstrated never
  ran. Python says nothing at parse time and the coroutine is simply dropped,
  so an author copying it gets code that runs, prints no error, and does
  nothing. That is the bad case, and it is the one worth a check.

So this file asserts three things about every ```python fence:

* **It parses.** ``ast.parse``, after the documented elisions below are
  normalized away. A guide example that is not syntactically Python is one an
  author cannot run.
* **Every platform reference resolves.** ``from server.… import X`` and
  ``from simulator.… import X`` against the real module; ``self.<attr>``
  against ``BaseDriver``; ``self.transport.<attr>`` against the transport
  classes. This closes both directions — a helper the guide invents, and a
  method the platform renames out from under the guide.
* **No coroutine is dropped.** A call to a name the same block defines with
  ``async def``, that is neither awaited nor handed to a task factory.

Three limits, recorded so nobody re-derives them:

* **Resolution is by name, not by signature.** The guide can still pass the
  wrong arguments to a real method. Signature comparison is what
  ``openavc-drivers``' stub-fidelity test does against a stub it owns; there is
  no equivalent object to compare a prose example to.
* **``self.<attr>`` cannot tell platform surface from the example's own
  helper**, so every helper an example invents is listed in
  ``EXAMPLE_HELPERS`` with what it belongs to. That list is the price of the
  check, and it is also the point: adding an entry is a deliberate "this is my
  driver's, not the platform's" decision, and a platform rename makes a real
  reference fall out of the platform set and *not* be in the list.
* **The async check only sees a block-local definition.** A dropped call to a
  coroutine defined elsewhere (the platform's own ``await self.poll()``) is
  invisible to it. Widening it would mean resolving every attribute call
  against the platform and asking whether it is a coroutine function, which
  flags every legitimately-fire-and-forget call the guide makes.
"""

from __future__ import annotations

import ast
import inspect
import re
from functools import lru_cache
from pathlib import Path

import pytest

from server.drivers import base as driver_base
from server.transport import (
    http_client,
    mqtt,
    osc,
    serial_transport,
    ssh,
    tcp,
    udp,
)

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "creating-drivers.md"

# Names an example calls on ``self`` that belong to the example's own driver,
# not to the platform. Each entry says what it is, so a wrong one is visible
# rather than inherited — an entry here is a claim that the platform does NOT
# own the name, and a wrong claim hides a rename.
EXAMPLE_HELPERS = {
    "_api_post": "The HTTP example driver's own request helper.",
    "_detail_due": "The controller example's slower-cadence gate — its own "
                   "bookkeeping, not a platform hook.",
    "_identify": "The example driver's identity read, called from _initial_sync.",
    "_login": "The example driver's authentication step, called from "
              "_post_connect.",
    "_parse_status": "The controller example's roster parser.",
    "_refresh_state": "The example driver's post-command state refresh.",
    "_send": "The example driver's thin wrapper over transport.send — a very "
             "common driver-side convenience, and deliberately not a platform "
             "method (the platform's is self.transport.send).",
}

# Transport classes a ``self.transport.<attr>`` reference may resolve against.
# The union is correct: a fenced example rarely says which transport it is
# holding, so the question the check can answer is "does any platform
# transport offer this", which is what catches a rename.
_TRANSPORTS = (
    tcp.TCPTransport,
    serial_transport.SerialTransport,
    udp.UDPTransport,
    http_client.HTTPClientTransport,
    osc.OSCTransport,
    mqtt.MQTTTransport,
    ssh.SSHTransport,
)

# Calls that consume a coroutine without awaiting it here. Handing a coroutine
# to one of these is correct — the object is scheduled, not dropped.
_TASK_FACTORIES = {
    "create_task",
    "ensure_future",
    "gather",
    "wait_for",
    "shield",
    "run",
    "run_until_complete",
    "wait",
    "as_completed",
    "run_coroutine_threadsafe",
}

_FENCE = re.compile(r"^```python\n(.*?)^```", re.DOTALL | re.MULTILINE)

# A line that is exactly ``...`` is an elision — "the rest of the dict / the
# rest of the class goes here". Dropping it is what makes the surrounding
# literal parse; it carries no reference of its own.
_ELISION = re.compile(r"^\s*\.\.\.\s*$")


def _normalize(block: str) -> str:
    return "\n".join(
        line for line in block.splitlines() if not _ELISION.match(line)
    )


@lru_cache(maxsize=1)
def _doc_text() -> str:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert len(text) > 50_000, "the driver guide went missing — the path is wrong"
    return text


@lru_cache(maxsize=1)
def _blocks() -> tuple[tuple[int, str, str], ...]:
    """(ordinal, first non-blank line, normalized source) per ```python fence."""
    out = []
    for n, body in enumerate(_FENCE.findall(_doc_text()), 1):
        first = next((ln for ln in body.splitlines() if ln.strip()), "")
        out.append((n, first.strip(), _normalize(body)))
    return tuple(out)


@lru_cache(maxsize=1)
def _parsed() -> tuple[tuple[int, str, ast.Module], ...]:
    out = []
    for n, first, src in _blocks():
        try:
            out.append((n, first, ast.parse(src)))
        except SyntaxError:
            continue
    return tuple(out)


def _self_assigned_attrs(cls: type) -> set[str]:
    """Instance attributes a class assigns to ``self`` in its own source.

    ``dir(cls)`` sees class-level names only, so ``self.config`` and
    ``self.transport`` — set in ``__init__`` — would read as unresolvable
    without this.
    """
    found: set[str] = set()
    try:
        tree = ast.parse(inspect.getsource(cls))
    except (OSError, TypeError, SyntaxError):
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Store):
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "self":
            found.add(node.attr)
    return found


@lru_cache(maxsize=1)
def _driver_surface() -> frozenset[str]:
    cls = driver_base.BaseDriver
    return frozenset(set(dir(cls)) | _self_assigned_attrs(cls))


@lru_cache(maxsize=1)
def _transport_surface() -> frozenset[str]:
    names: set[str] = set()
    for cls in _TRANSPORTS:
        names |= set(dir(cls)) | _self_assigned_attrs(cls)
    return frozenset(names)


def _import_targets(tree: ast.Module) -> list[tuple[str, str]]:
    """(module, name) for every ``from server.… / simulator.… import name``."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module.split(".")[0] not in ("server", "simulator"):
            continue
        for alias in node.names:
            out.append((node.module, alias.name))
    return out


def _block_local_names(tree: ast.Module) -> set[str]:
    """Names the block itself defines — functions, classes, and anything it
    assigns to ``self``."""
    local: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            local.add(node.name)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            local.add(node.attr)
    return local


def _self_reads(tree: ast.Module) -> set[str]:
    """``self.<attr>`` read (not assigned), excluding ``self.transport.<attr>``
    which resolves against a different surface."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            out.add(node.attr)
    return out


def _transport_reads(tree: ast.Module) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
            and node.value.attr == "transport"
        ):
            out.add(node.attr)
    return out


def _dropped_coroutines(tree: ast.Module) -> list[str]:
    """Calls to a block-local ``async def`` that nothing consumes.

    A coroutine is consumed when it is awaited, handed to a task factory, or
    returned / stored for a caller to await. Anything else evaluates the call
    into a coroutine object that is then thrown away, which is silent at
    runtime beyond a "never awaited" warning nobody sees in a doc example.
    """
    coroutines = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    if not coroutines:
        return []

    consumed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            consumed.add(id(node.value))
        elif isinstance(node, (ast.Return, ast.Assign, ast.AnnAssign)):
            value = getattr(node, "value", None)
            if value is not None:
                consumed.add(id(value))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else ""
            )
            if name in _TASK_FACTORIES:
                for arg in node.args:
                    consumed.add(id(arg))

    dropped = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or id(node) in consumed:
            continue
        func = node.func
        called = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else ""
        )
        if called in coroutines:
            dropped.append(called)
    return dropped


def test_every_python_example_parses() -> None:
    broken = []
    for n, first, src in _blocks():
        try:
            ast.parse(src)
        except SyntaxError as exc:
            broken.append(f"block {n} ({first!r}): {exc.msg} at line {exc.lineno}")
    assert not broken, (
        "```python examples in the guide are not valid Python: "
        + "; ".join(broken)
        + ". An author copies an example verbatim, so one that does not parse "
        "is one they cannot run. A deliberate elision is written as a bare "
        "'...' line, which this check drops before parsing."
    )


def test_every_platform_import_resolves() -> None:
    import importlib

    missing = []
    for n, first, tree in _parsed():
        for module, name in _import_targets(tree):
            try:
                mod = importlib.import_module(module)
            except ImportError:
                missing.append(f"block {n}: no module {module!r}")
                continue
            if not hasattr(mod, name):
                missing.append(f"block {n}: {module}.{name} does not exist")
    assert not missing, (
        f"the guide imports platform names that are not there: {missing}. "
        f"Either the name was renamed and the guide has to follow, or the "
        f"example invented it."
    )


def test_every_self_reference_resolves() -> None:
    surface = _driver_surface()
    unresolved = {}
    for n, first, tree in _parsed():
        local = _block_local_names(tree)
        for attr in sorted(_self_reads(tree)):
            if attr in local or attr in surface or attr in EXAMPLE_HELPERS:
                continue
            unresolved.setdefault(attr, []).append(n)
    assert not unresolved, (
        f"the guide calls these on self, and BaseDriver does not have them: "
        f"{unresolved}. If the platform renamed the method, fix the guide; if "
        f"the example driver owns the name, add it to EXAMPLE_HELPERS with "
        f"what it is."
    )


def test_every_transport_reference_resolves() -> None:
    surface = _transport_surface()
    unresolved = {}
    for n, first, tree in _parsed():
        for attr in sorted(_transport_reads(tree)):
            if attr not in surface:
                unresolved.setdefault(attr, []).append(n)
    assert not unresolved, (
        f"the guide calls these on self.transport and no platform transport "
        f"class offers them: {unresolved}."
    )


def test_no_example_drops_a_coroutine() -> None:
    dropped = {}
    for n, first, tree in _parsed():
        for name in _dropped_coroutines(tree):
            dropped.setdefault(name, []).append(n)
    assert not dropped, (
        f"these async helpers are called without await and without being "
        f"handed to a task factory, so the coroutine is created and thrown "
        f"away: {dropped}. The example runs, prints nothing, and does not do "
        f"what it says."
    )


def _connect_overrides_calling_super(tree: ast.Module) -> list[str]:
    """``async def connect`` bodies that call ``await super().connect()``.

    This is the one example shape the guide's own rule forbids. A driver that
    replaces ``connect()`` wholesale (no ``super()`` call) is a different,
    legitimate thing — a transport-less driver like Wake-on-LAN has no
    lifecycle to run. But an override that runs the whole platform lifecycle
    and then bolts a step onto the end is exactly what the ``_pre_connect`` /
    ``_post_connect`` / ``_initial_sync`` hooks exist to replace, and getting
    it wrong is not cosmetic: ``super().connect()`` marks the device connected
    and starts polling, so anything after it races the first poll.
    """
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "connect":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "connect"
                and isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name)
                and func.value.func.id == "super"
            ):
                found.append(node.name)
    return found


def test_no_example_overrides_connect_to_append_a_step() -> None:
    """The guide forbids this in prose and then did it in two examples.

    ``creating-drivers.md`` says "Don't override ``connect()`` to customize one
    stage" and lists the hook for each stage — then the controller example and
    the ``redact_in_log`` example both overrode ``connect()``, called
    ``super().connect()``, and appended their step. A controller author reading
    both had no way to tell which half was current, and the example is the half
    that gets copied.
    """
    offenders = [n for n, _, tree in _parsed() if _connect_overrides_calling_super(tree)]
    assert not offenders, (
        f"```python blocks {offenders} override connect() and call "
        f"super().connect(), which the guide's own lifecycle section tells "
        f"authors not to do. Anything after super().connect() runs after the "
        f"device is declared connected and polling has started — use "
        f"_pre_connect / _post_connect / _initial_sync for the stage you need."
    )


def test_the_opt_out_list_holds_nothing_stale() -> None:
    """EXAMPLE_HELPERS may only shrink.

    An entry that names real platform surface is a wrong claim about who owns
    the name, and it would silently absorb a real reference. An entry the
    guide no longer writes is dead weight that hides the next rename.
    """
    became_platform = sorted(
        name for name in EXAMPLE_HELPERS if name in _driver_surface()
    )
    assert not became_platform, (
        f"now real BaseDriver surface — delete from EXAMPLE_HELPERS: "
        f"{became_platform}"
    )
    used: set[str] = set()
    for _, _, tree in _parsed():
        used |= _self_reads(tree)
    unused = sorted(set(EXAMPLE_HELPERS) - used)
    assert not unused, (
        f"EXAMPLE_HELPERS names helpers the guide no longer writes: {unused}"
    )


def test_the_sweep_still_reaches_the_document() -> None:
    """Guard the extractor.

    Every assertion above passes vacuously if the fence sweep stops finding
    anything, and a formatting change could do that silently — a renamed fence
    language, a switch to indented blocks. These are floors on what it must
    still see.
    """
    blocks = _blocks()
    assert len(blocks) > 15, f"only {len(blocks)} ```python fences found"
    assert len(_parsed()) == len(blocks), "some fence stopped parsing"

    imports = sum(len(_import_targets(t)) for _, _, t in _parsed())
    assert imports > 5, f"the import sweep found only {imports} platform imports"

    reads: set[str] = set()
    transport: set[str] = set()
    for _, _, tree in _parsed():
        reads |= _self_reads(tree)
        transport |= _transport_reads(tree)
    assert len(reads) > 15, f"the self sweep found only {len(reads)} attributes"
    assert transport, "the guide stopped calling anything on self.transport"

    # The platform surfaces themselves — a failed import or a moved class would
    # otherwise make every reference "resolve" against an empty set... which is
    # the wrong direction, but an empty surface means the comparison is junk.
    assert len(_driver_surface()) > 50, "the BaseDriver surface walk collapsed"
    assert len(_transport_surface()) > 50, "the transport surface walk collapsed"


def test_the_coroutine_check_can_actually_fire() -> None:
    """The async check is the one with no shipped instance to point at.

    Every other assertion here has a real defect behind it. This one exists
    because ``writing-simulators.md`` had the defect in the sibling repo, so
    prove the detector on both shapes rather than trusting that it works.
    """
    dropped = ast.parse(
        "async def warm(self):\n    pass\n"
        "def go(self):\n    self.warm()\n"
    )
    assert _dropped_coroutines(dropped) == ["warm"]

    for consumed in (
        "async def warm(self):\n    pass\n"
        "async def go(self):\n    await self.warm()\n",
        "async def warm(self):\n    pass\n"
        "def go(self):\n    asyncio.ensure_future(self.warm())\n",
        "async def warm(self):\n    pass\n"
        "def go(self):\n    self._task = asyncio.create_task(self.warm())\n",
    ):
        assert _dropped_coroutines(ast.parse(consumed)) == [], consumed


if __name__ == "__main__":  # pragma: no cover - convenience
    pytest.main([__file__, "-v"])
