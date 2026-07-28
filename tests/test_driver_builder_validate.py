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
ever fires them. Also covers the declared command semantics rules mirrored
from avcdriver_semantic.py: `sets` keys and `query_for` must name declared
state variables (device-level, or the addressed child type's on a command
with exactly one child_id param) and a "{param}" value must name a declared
parameter. Also covers the top-level field rules the Builder surfaces:
`transports` entries must come from the interchangeable set, bridge ports
need id/kind with passthrough_port bounds (duplicate ids warn — the runtime
silently keeps the last), config_derived templates must reference declared
config fields (unknown tokens and name collisions warn), and ir_codes must
be a boolean (warning when true off the bridge transport).

Finally, it replays the whole shared rejection corpus
(``tests/fixtures/driver_validation_cases.json``) — every definition the
driver loader refuses — and asserts the Builder reports an error for each
one. The hand-written scenarios above cover authoring rules the loader has
no opinion on, which the corpus cannot reach; the corpus covers the loader's
rules, which hand-written scenarios kept missing. Every case now passes:
``TS_CORPUS_GAPS`` is empty, so a definition the loader refuses is an error
in the Builder too, across the whole corpus. A rule that regresses — or a
case added to the corpus with no Builder rule behind it — fails here.

What the corpus does NOT prove, and why it is written this way: it asserts
that *an* error was raised, not that the matching rule fired. It cannot
assert more without a second hand-written corpus of expected Builder
messages — the two validators word their messages differently on purpose
(one is read in a log, the other by an author mid-edit) and spell their
field paths differently too (``actions[0]`` against ``actions``), so there
is nothing to compare mechanically. Building that second corpus would
recreate exactly the hand-maintained mirror this validator was wired to the
shared corpus to retire. So a case can pass on an unrelated error, and one
did: three child ``id_format`` cases were carried by a false positive that
demanded ``id_format.type`` the loader happily defaults. The counter-measure
is the block of scenarios above it — when a corpus case covers a rule worth
pinning, add a scenario that names the message, and pair it with a valid
draft that must raise nothing.

Skips when the Node toolchain or esbuild is absent — unless the run promised
Node (see tests/gates.py), in which case it fails instead.
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
# The shared rejection corpus: every definition the loader refuses, generated
# from the same rules the community catalog vendors (see
# test_driver_validation_messages.py).
CASES_FIXTURE = OPENAVC_ROOT / "tests" / "fixtures" / "driver_validation_cases.json"


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
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(VALIDATOR), str(CASES_FIXTURE)],
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
    "whitespace_send_is_a_wire_value_ok",
    "m173_response_no_pattern_error",
    "m173_response_osc_address_no_slash_error",
    "h124_response_osc_address_on_tcp_error",
    "m173_response_with_match_ok",
    "setting_missing_write_error",
    "setting_empty_write_error",
    "setting_with_write_ok",
    "secret_field_default_warning",
    "secret_schema_default_warning",
    "secret_field_no_default_ok",
    "config_default_type_mismatch_warning",
    "config_boolean_string_default_warning",
    "config_typed_defaults_ok",
    "sem_device_sets_query_for_ok",
    "sem_child_sets_query_for_ok",
    "sem_sets_unknown_var_error",
    "sem_sets_unknown_param_ref_error",
    "sem_sets_two_child_id_params_error",
    "sem_query_for_unknown_var_error",
    "sem_sets_not_mapping_error",
    "json_set_string_path_ok",
    "json_set_object_form_ok",
    "json_mappings_form_ok",
    "json_require_without_json_error",
    "json_require_blank_error",
    "json_require_bad_type_error",
    "json_unknown_state_var_error",
    "json_unknown_row_type_error",
    "json_child_set_error",
    "json_no_fields_error",
    "json_empty_path_error",
    "json_on_osc_transport_error",
    "p4_transports_bad_entry_error",
    "p4_transports_valid_ok",
    "p4_bridge_port_missing_kind_error",
    "p4_bridge_passthrough_range_error",
    "p4_bridge_duplicate_port_id_warning",
    "p4_bridge_valid_ok",
    "p4_config_derived_unknown_placeholder_warning",
    "p4_config_derived_name_collision_warning",
    "p4_config_derived_valid_ok",
    "p4_ir_codes_non_boolean_error",
    "p4_ir_codes_non_bridge_warning",
    "p4_ir_bridge_driver_ok",
    "p4_full_featured_driver_ok",
    "child_id_format_without_type_ok",
    "child_id_format_not_a_block_error",
    "child_id_format_min_not_whole_error",
    "child_id_format_pad_width_zero_ok",
    "safe_unquoted_year_author",
    "safe_numeric_auth_prompt",
    "safe_null_param_definition",
    "safe_numeric_child_label",
    "safe_wrapper_adds_nothing_to_a_readable_draft",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_driver_builder_validate(validate_results: dict, scenario: str) -> None:
    assert scenario in validate_results, f"harness did not report {scenario}"
    outcome = validate_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


# ── The shared rejection corpus ───────────────────────────────────────────
# Every definition in driver_validation_cases.json is one the driver loader
# refuses. The community driver catalog already replays this corpus against
# its vendored copy of the Python rules; the Builder's validator is the one
# consumer that was never wired to it. Without this, a rule the loader
# enforces can be missing from the Builder and nobody finds out until an
# author fills in a form, hits Save, and gets rejected by the server.
#
# Known gaps: cases the loader rejects and the Builder does not flag yet.
# EMPTY, and meant to stay that way — the Builder now flags all 219. An entry
# here is marked xfail(strict=True), so the moment the Builder learns a rule
# its case fails as an unexpected pass and the entry has to come out; the
# list only shrinks. Adding one back needs a reason in review: it means a
# definition the loader refuses saves cleanly in the Builder and is rejected
# by the server afterwards, which is the failure this corpus exists to stop.
TS_CORPUS_GAPS: set[str] = set()

CORPUS_CASES = sorted(json.loads(CASES_FIXTURE.read_text(encoding="utf-8")))

CORPUS_PARAMS = [
    pytest.param(
        name,
        marks=pytest.mark.xfail(
            strict=True,
            reason="the Builder has no rule for this yet (TS_CORPUS_GAPS)",
        ),
    )
    if name in TS_CORPUS_GAPS
    else name
    for name in CORPUS_CASES
]


def _corpus(validate_results: dict) -> dict:
    corpus = validate_results.get("corpus")
    assert corpus, "harness did not replay the rejection corpus"
    return corpus


@pytest.mark.parametrize("case", CORPUS_PARAMS)
def test_builder_flags_what_the_loader_rejects(
    validate_results: dict, case: str
) -> None:
    """A definition the loader refuses must be an error in the Builder too."""
    verdict = _corpus(validate_results)[case]
    assert verdict["rejected"], (
        f"{case}: the loader rejects this definition and the Builder reports "
        f"no error. Issues raised: {verdict['messages'] or 'none'}"
    )


@pytest.mark.parametrize("case", CORPUS_CASES)
def test_builder_survives_every_corpus_definition(
    validate_results: dict, case: str
) -> None:
    """A malformed definition must produce a message, never a crash.

    A thrown TypeError is caught upstream and shown as a raw JavaScript
    string, which tells the author nothing — and it aborts the whole pass,
    so every rule after it is skipped for that draft.
    """
    verdict = _corpus(validate_results)[case]
    assert verdict["threw"] is None, (
        f"{case}: validateDriver threw instead of reporting an issue: "
        f"{verdict['threw']}"
    )


def test_every_corpus_case_was_replayed(validate_results: dict) -> None:
    """The harness must report on all of them — a silent drop hides a gap."""
    missing = sorted(set(CORPUS_CASES) - set(_corpus(validate_results)))
    assert not missing, f"harness did not replay: {missing}"


def test_nothing_calls_the_unguarded_validator() -> None:
    """Every consumer must go through validateDriverSafely.

    validateDriver throws on a draft whose field types are unexpected, and a
    driver file is arbitrary YAML. The editor validates inside a render, so
    one unguarded call is a blank screen with no way back. Rules get hardened
    one at a time; the wrapper is what makes the whole class survivable, and
    it only works if it is the only door.
    """
    src = OPENAVC_ROOT / "web" / "programmer" / "src"
    offenders = []
    for path in sorted(src.rglob("*.ts")) + sorted(src.rglob("*.tsx")):
        if path.name == "validateDriver.ts":
            continue  # where both live
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            code = line.split("//", 1)[0]
            if code.lstrip().startswith(("*", "/*")):
                continue  # a JSDoc line naming the function, not a call
            if re.search(r"\bvalidateDriver\s*\(", code):
                offenders.append(f"{path.relative_to(OPENAVC_ROOT)}:{lineno}")
    assert not offenders, (
        "these call validateDriver directly and will blank the UI on a draft "
        "it cannot read — call validateDriverSafely instead: "
        + ", ".join(offenders)
    )
