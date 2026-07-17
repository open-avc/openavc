"""Regression tests for the Driver Builder validator's transport-shape rules
(validateDriver.ts).

The runtime routes every command and device-setting write by SHAPE (an
``address`` goes to the OSC sender, ``path``/``method`` to HTTP, else the raw
``send`` string) and its senders refuse a transport mismatch — so wire-format
fields left behind by a transport switch make a command silently dead at
runtime. These tests bundle the real validateDriver.ts with the esbuild in
web/programmer/node_modules and assert the author-time half: stale shapes are
flagged as errors, matching-shape leftovers as warnings, the transport-switch
scrub removes exactly the non-applicable fields (reporting authored content
for the confirm prompt), and OSC argument values that would crash the send
(empty / non-numeric / fractional int64) are errors before the LiveTestPanel
ever fires them. Skips when the Node toolchain or esbuild is absent rather
than failing the Python-only CI gate.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo root = openavc/ (this file is openavc/tests/test_driver_builder_validate.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]

HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "driver_builder_validate_harness.cjs"
VALIDATOR = (
    OPENAVC_ROOT
    / "web"
    / "programmer"
    / "src"
    / "components"
    / "driver-builder"
    / "validateDriver.ts"
)
NODE_MODULES = OPENAVC_ROOT / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in web/programmer)"
    if not HARNESS.is_file():
        return "driver builder validate harness missing"
    if not VALIDATOR.is_file():
        return "validateDriver.ts missing"
    return None


@pytest.fixture(scope="module")
def validate_results() -> dict:
    reason = _toolchain_reason()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(VALIDATOR)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"driver builder validate harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc


# One pytest case per harness scenario, so a failure names the exact behaviour.
SCENARIOS = [
    "h097_tcp_with_osc_fields_error",
    "h097_serial_with_http_fields_error",
    "h097_osc_without_address_error",
    "h097_http_without_method_path_error",
    "h097_osc_leftover_send_warning",
    "h097_osc_empty_address_error",
    "h097_setting_write_mismatch_error",
    "h097_clean_drivers_no_shape_issues",
    "h097_scrub_to_tcp_removes_osc_fields",
    "h097_scrub_to_osc_clears_send",
    "h097_scrub_setting_write_dropped",
    "m152_empty_numeric_arg_error",
    "m152_non_numeric_arg_error",
    "m152_placeholder_string_ok_int64_fraction_error",
    "m152_helper_matrix",
    "route_precedence_matches_runtime",
    "h121_disallowed_port_8080_error",
    "h121_vendor_port_ok",
    "h122_probe_two_matchers_error",
    "h122_probe_one_matcher_ok",
    "m170_blank_oui_error",
    "m170_blank_mdns_service_error",
    "discovery_clean_no_issues",
    "h123_header_size_3_error",
    "h123_header_size_4_negoffset_ok",
    "l102_fixed_negative_length_error",
    "l102_fixed_length_ok",
    "frame_parser_unknown_type_error",
    "frame_parser_absent_no_issues",
    "m172_state_var_no_label_error",
    "m172_state_var_with_label_ok",
    "m172_state_var_unknown_type_error",
    "m173_command_no_wire_format_error",
    "m173_command_with_send_ok",
    "m173_response_no_pattern_error",
    "m173_response_osc_address_no_slash_error",
    "h124_response_osc_address_on_tcp_error",
    "m173_response_with_pattern_ok",
    "setting_missing_write_error",
    "setting_empty_write_error",
    "setting_with_write_ok",
    "secret_field_default_error",
    "secret_schema_default_error",
    "secret_field_no_default_ok",
    "config_default_type_mismatch_warning",
    "config_boolean_string_default_warning",
    "config_typed_defaults_ok",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_driver_builder_validate(validate_results: dict, scenario: str) -> None:
    assert scenario in validate_results, f"harness did not report {scenario}"
    outcome = validate_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"
