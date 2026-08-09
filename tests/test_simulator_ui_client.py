"""The simulator UI's API client, checked at the source.

This file exists because of a specific failure that no build or type-check
caught: the request helper was edited to call ITSELF instead of ``fetch``.
Infinite recursion is valid TypeScript, so ``tsc`` passed, the bundle built,
and the damage only appeared on real hardware -- Chrome's renderer ran out of
memory and died, taking the Programmer window with it, because a window opened
with ``window.open`` shares a process with its opener. From the outside it
looked like the server had crashed.

The simulator UI has no test runner of its own, and adding one mid-fix was
more change than the moment warranted, so this reads the source the way
``test_ui_page_review_mirrors`` already reads the Builder's.
"""

import re
from pathlib import Path

import pytest

API_TS = (
    Path(__file__).parent.parent
    / "openavc" / "web" / "simulator" / "src" / "store" / "api.ts"
)


def _helper_body() -> str:
    src = API_TS.read_text(encoding="utf-8")
    m = re.search(r"async function api\([^)]*\)[^{]*\{(.*?)\n\}", src, re.DOTALL)
    assert m, "could not find the api() request helper in api.ts"
    return m.group(1)


def test_the_request_helper_calls_fetch_and_not_itself():
    """The whole bug in one assertion."""
    body = _helper_body()
    assert "fetch(" in body, "the request helper must actually issue a request"
    assert not re.search(r"\breturn\s+api\(", body), (
        "api() calls itself — every request becomes unbounded recursion and the "
        "browser tab dies. It must call fetch()."
    )


def test_no_file_in_the_ui_calls_the_simulator_api_directly():
    """A bare fetch() anywhere skips the credential and 401s at runtime.

    Scanning only `api.ts` was not enough and this test knows it: the Stop
    button lived in `App.tsx` and called `fetch` by hand, so it sent no token,
    got a 401, and swallowed the error -- the button simply did nothing. Every
    file gets scanned now, not just the one where the calls are supposed to be.

    Sign-in is the deliberate exception: it runs BEFORE this tab has any
    credential, and it targets the platform, not the simulator.
    """
    src_root = API_TS.parent.parent
    offenders = []
    for ts in sorted(src_root.rglob("*.ts")) + sorted(src_root.rglob("*.tsx")):
        text = ts.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if not re.search(r"\bfetch\(", line):
                continue
            if ts.name == "api.ts" and "${BASE}${path}" in line:
                continue  # the wrapper itself
            if ts.name == "session.ts" and "APP_ROOT" in line:
                continue  # sign-in, pre-credential and not the simulator
            offenders.append(f"{ts.relative_to(src_root)}:{n}: {line.strip()}")
    assert not offenders, (
        "these call the API without the credential the wrapper attaches:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", ["BASE", "APP_ROOT"])
def test_the_mount_prefixes_come_from_one_place(name):
    """Both are derived from the current URL, and the derivation is shared with
    `session.ts`. Two copies would drift the moment the mount path changes."""
    paths_ts = API_TS.parent / "paths.ts"
    assert f"export const {name}" in paths_ts.read_text(encoding="utf-8")


def test_list_fetchers_refuse_a_failed_response():
    """A 503 must not become `undefined` in the device list.

    Serving this UI from the main server created a window that never existed
    before: the page can load while the simulator process is still starting,
    and the proxy correctly answers 503. Returning `data.devices` from that
    body handed `undefined` to the renderer, which read `.length` on it and
    killed the tab -- a blank window and, because the simulator shares a
    renderer with the IDE that opened it, the IDE with it.
    """
    src = API_TS.read_text(encoding="utf-8")
    for fn in ("fetchDevices", "fetchLog"):
        m = re.search(rf"export async function {fn}\(.*?\n\}}", src, re.DOTALL)
        assert m, f"{fn} not found"
        body = m.group(0)
        assert "res.ok" in body, (
            f"{fn} does not check the response; a 503 while the simulator is "
            "still starting becomes undefined and crashes the render"
        )
        assert "??" in body, f"{fn} should fall back to an empty list"
