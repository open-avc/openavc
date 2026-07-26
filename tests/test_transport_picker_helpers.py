"""Regression tests for the Driver Builder transport panel (TransportPicker).

The transport config form had four silent-mangling paths:

- The delimiter dropdown's option values compiled to escaped text (JSX
  attribute strings keep backslashes) while its custom-value guard compiled
  to real control characters, so an installed driver's yaml-decoded CR
  delimiter matched nothing — the select rendered unselected — and
  re-picking an option silently swapped the stored representation. The
  canonical form is now real control characters (what YAML decoding
  produces); legacy escaped drafts are normalized on read.
- The 'Verify SSL Certificate' checkbox defaulted to unchecked while the
  runtime verifies by default (config.get('verify_ssl', True)), showing the
  opposite of the effective behavior.
- Numeric config inputs coerced blank and 0 to a magic default via
  ``|| <default>`` on every keystroke, so the field fought the user.
- Bearer-token / API-key default credentials rendered as plaintext inputs
  and exported to a shareable file with no warning; the HTTP transport's
  ``default_headers`` (read by the runtime, accepted by the API model) had
  no authoring surface at all.

Two layers: the harness bundles the real ``transportPickerHelpers.ts`` with
the esbuild in ``web/programmer/node_modules`` and exercises the pure logic
(skips when the Node toolchain is absent rather than failing the
Python-only CI gate), and source-level checks pin TransportPicker.tsx and
driverBuilderStore.ts to the fixed shapes so the old patterns can't quietly
come back.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

# Repo root = openavc/ (this file is openavc/tests/test_transport_picker_helpers.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "transport_picker_helpers_harness.cjs"
HELPERS_TS = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "transportPickerHelpers.ts"
)
PICKER_TSX = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "TransportPicker.tsx"
)
STORE_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "store" / "driverBuilderStore.ts"
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "transport picker harness missing"
    if not HELPERS_TS.is_file():
        return "transportPickerHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def helper_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS_TS)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"transport picker harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


SCENARIOS = [
    # Delimiter canonicalization: legacy escaped drafts and yaml-decoded
    # installed drivers both resolve to the same dropdown option.
    "normalize_escaped_cr",
    "normalize_escaped_crlf",
    "normalize_real_cr_untouched",
    "normalize_real_crlf_untouched",
    "normalize_plain_text",
    # Custom delimiters render visibly instead of as invisible control chars.
    "display_cr",
    "display_crlf",
    "display_stx",
    "display_printable",
    # Numeric fields hold blank and 0 instead of snapping to a default.
    "blank_clears",
    "zero_is_kept",
    "int_parses",
    "garbage_ignored",
    "float_parses",
    "int_mode_truncates",
    # Secret detection for masking + export warning.
    "no_config",
    "empty_config",
    "token_detected",
    "blank_token_ignored",
    "both_detected",
    "schema_flagged_secret_detected",
    "schema_flagged_blank_ignored",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_helper_scenarios(helper_results: dict, scenario: str) -> None:
    assert scenario in helper_results, f"harness did not report '{scenario}'"
    assert helper_results[scenario] is True, f"scenario '{scenario}' failed"


# ── Source-level pins ──────────────────────────────────────────────────────


def test_picker_uses_canonical_delimiter_handling() -> None:
    """The select compares normalized delimiters and renders custom values
    through displayDelimiter — never the raw draft string."""
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert "normalizeDelimiter" in src
    assert "displayDelimiter" in src
    # Option values must be JS expressions (escape-processed real chars),
    # not JSX attribute strings (which keep the backslashes as text).
    assert 'value={"\\r\\n"}' in src
    assert 'value="\\r\\n"' not in src


def test_picker_verify_ssl_shows_effective_default() -> None:
    """Runtime verifies certificates by default; the checkbox must too."""
    src = PICKER_TSX.read_text(encoding="utf-8")
    match = re.search(r"verify_ssl[^\n]*\?\?\s*(\w+)", src)
    assert match, "verify_ssl checkbox binding not found"
    assert match.group(1) == "true", (
        f"verify_ssl checkbox falls back to {match.group(1)!r}; the runtime "
        f"default is True (base.py config.get('verify_ssl', True))"
    )


def test_picker_numeric_inputs_do_not_coerce_to_defaults() -> None:
    """No `parseInt(...) || 80`-style fallbacks: blank and 0 must hold."""
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert not re.search(r"parse(Int|Float)\([^)]*\)\s*\|\|", src), (
        "numeric input still coerces falsy values to a magic default"
    )
    assert "setNumericConfig" in src


def test_picker_masks_credential_inputs() -> None:
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert src.count('autoComplete="new-password"') >= 2, (
        "token/api_key inputs must not offer autofill"
    )
    assert 'revealSecrets ? "text" : "password"' in src, (
        "token/api_key inputs must be masked with a reveal toggle"
    )


def test_picker_authors_default_headers() -> None:
    """The runtime reads config default_headers on every HTTP request; the
    Builder must be able to author it."""
    src = PICKER_TSX.read_text(encoding="utf-8")
    assert "default_headers" in src


def test_store_default_delimiter_is_canonical() -> None:
    src = STORE_TS.read_text(encoding="utf-8")
    assert 'delimiter: "\\r"' in src, "EMPTY_DEFINITION should default to a real CR"
    assert 'delimiter: "\\\\r"' not in src, (
        "EMPTY_DEFINITION must not use the escaped text form"
    )


def test_store_export_warns_on_saved_secrets() -> None:
    src = STORE_TS.read_text(encoding="utf-8")
    assert "secretFieldsInConfig" in src, (
        "exportDriver must check for saved credentials before writing a "
        "cleartext driver file"
    )
