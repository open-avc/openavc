"""Regression tests for the Programmer SPA fetch-auth layer (api/auth.ts).

Two properties, each of which regressed or nearly regressed once:

- URL matching: the interceptor used to decide "this is an API request" with
  a path-only regex, so a fetch to a cross-origin URL whose path merely
  contained an ``api`` segment silently carried the admin credential off-box.
  ``isSameOriginApiUrl`` resolves the URL against the live location and only
  matches when the resolved origin is the SPA's own.
- Token-only sessions: the SPA stores a server-minted session token, never
  the password. Requests carry ``Authorization: Bearer``, the WebSocket
  subprotocol carries ``auth.bearer.<token>``, the legacy ``{user, pass}``
  sessionStorage blob is purged on sight, and no header ever contains a
  password in any form.

This bundles the real ``auth.ts`` with the esbuild in
``web/programmer/node_modules`` and asserts the matcher plus the installed
interceptor (faked window/sessionStorage, capturing the attached headers).
Skips when the Node toolchain or esbuild is absent rather than failing the
Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_auth_url_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "auth_url_harness.cjs"
AUTH_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "auth.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "auth url harness missing"
    if not AUTH_TS.is_file():
        return "api/auth.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(AUTH_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"auth url harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Same-origin API URLs keep getting the credential
    "same_origin_relative_api",
    "same_origin_bare_relative_api",
    "same_origin_absolute_api",
    "same_origin_api_with_query",
    "tunnel_api_path",
    # Anything else never does
    "cross_origin_api_path_rejected",
    "protocol_relative_rejected",
    "different_port_rejected",
    "same_origin_non_api",
    "api_only_in_query_rejected",
    "unparseable_url_rejected",
    # The installed interceptor end-to-end
    "interceptor_attaches_same_origin",
    "interceptor_no_credential_cross_origin",
    # Session-token posture: the browser never holds or sends the password
    "raw_password_never_in_headers",
    "legacy_password_blob_purged",
    "ws_subprotocol_is_bearer_token",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_auth_url_matching(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report {scenario}"
    outcome = helper_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
