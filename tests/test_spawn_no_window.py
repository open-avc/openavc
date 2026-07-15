"""Every child-process spawn must be console-window-safe on Windows.

On Windows, a console program spawned from a process that has no console
window of its own gets a brand-new visible console. The server can
legitimately run console-less on a user's desktop (the in-app restart
relaunches it that way), and a discovery scan spawns one ping per address —
one missed flag turns a scan into hundreds of console windows popping open.

The rule: every subprocess spawn under server/ and simulator/ passes
``creationflags`` (use ``server.utils.spawn.CREATE_NO_WINDOW``, which is 0 on
POSIX so no call site needs a platform check). Calls passing
``start_new_session=True`` are exempt — that keyword is POSIX-only, so such a
call is an explicitly POSIX-only code path.
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("server", "simulator")

_SUBPROCESS_CALLS = {"Popen", "run", "call", "check_call", "check_output"}
_ASYNCIO_CALLS = {"create_subprocess_exec", "create_subprocess_shell"}


def _spawn_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        if (func.value.id == "subprocess" and func.attr in _SUBPROCESS_CALLS) or (
            func.value.id == "asyncio" and func.attr in _ASYNCIO_CALLS
        ):
            yield node


def test_every_spawn_passes_creationflags():
    offenders = []
    for scan_dir in SCAN_DIRS:
        for path in sorted((REPO_ROOT / scan_dir).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for call in _spawn_calls(tree):
                keywords = {kw.arg for kw in call.keywords}
                if "creationflags" in keywords:
                    continue
                if "start_new_session" in keywords:  # POSIX-only path
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{call.lineno}")

    assert not offenders, (
        "Child-process spawn(s) missing creationflags — pass "
        "server.utils.spawn.CREATE_NO_WINDOW so a console-less server on "
        "Windows doesn't pop a console window per child: "
        + ", ".join(offenders)
    )
