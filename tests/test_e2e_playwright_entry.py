"""One Playwright per pytest session, for the whole browser suite.

pytest-playwright opens a single Playwright the first time any test asks for
its ``page`` or ``browser`` fixture, and keeps it open until the session ends.
A test file that additionally calls ``sync_playwright()`` itself gets

    Error: It looks like you are using Playwright Sync API inside the asyncio
    loop. Please use the Async API instead.

...for every test in that file. Ask for the ``browser`` fixture instead and
open a context on it.

**Why this guard exists rather than a comment.** The failure is invisible to
the obvious check: run the offending file on its own and nothing has started
the plugin's instance yet, so the whole file passes. It only breaks in a run
that also touches a ``page``-using file, and then it breaks as a setup ERROR
rather than a failed assertion. Two files were written that way; CI went red
for a full day and the recorded diagnosis blamed a stray asyncio loop from an
async test, which `tests/e2e` does not contain.

This reads source only -- no browser, no Playwright install -- so it runs in
the default suite on every platform, including the legs that never run the
browser tests at all. That is the point: the Windows leg does not run
`tests/e2e`, so nothing else here would have caught it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
E2E_DIR = OPENAVC_ROOT / "tests" / "e2e"

#: The entry points that start a Playwright of one's own.
_ENTRY_NAMES = {"sync_playwright", "async_playwright"}


def _e2e_sources() -> list[Path]:
    return sorted(p for p in E2E_DIR.glob("*.py") if p.name != "__init__.py")


def _playwright_entry_calls(path: Path) -> list[str]:
    """Lines that CALL a Playwright entry point.

    Parsed rather than grepped: the rule has to be explainable in the files it
    governs, and a text search trips over its own explanation -- the first
    version of this guard failed on the comments telling people not to do it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.attr if isinstance(func, ast.Attribute)
            else func.id if isinstance(func, ast.Name)
            else None
        )
        if name in _ENTRY_NAMES:
            hits.append(f"{path.name}:{node.lineno}: {lines[node.lineno - 1].strip()}")
    return hits


def test_the_e2e_suite_exists() -> None:
    """A guard over an empty directory passes for free."""
    assert _e2e_sources(), f"no e2e sources found under {E2E_DIR}"


def test_no_e2e_file_opens_its_own_playwright() -> None:
    offenders = [hit for path in _e2e_sources() for hit in _playwright_entry_calls(path)]

    assert not offenders, (
        "these e2e files start a Playwright of their own:\n  "
        + "\n  ".join(offenders)
        + "\n\npytest-playwright already holds one open for the session, so every "
        "test in such a file errors at setup with 'Sync API inside the asyncio "
        "loop' -- but ONLY when the run also includes a file that uses the "
        "plugin's page fixture. Run it alone and it passes, which is how this "
        "reached main. Take the `browser` fixture as an argument and call "
        "browser.new_context(...) instead."
    )


def test_the_browser_fixture_is_the_way_pages_are_made() -> None:
    """The positive half: the fixtures that build a page take `browser`.

    Without this, deleting both fixtures would satisfy the check above.
    """
    makers = []
    for path in _e2e_sources():
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^def (panel_page)\(([^)]*)\)", text, re.MULTILINE):
            makers.append((path.name, match.group(1), match.group(2)))

    assert makers, (
        "no panel_page fixture found in tests/e2e -- if it was renamed, update "
        "this guard so the browser-entry rule keeps being checked"
    )
    for filename, name, args in makers:
        assert "browser" in args, (
            f"{filename}: {name}({args}) does not take the `browser` fixture, so "
            f"it must be opening a browser some other way"
        )
