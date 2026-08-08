"""What the Driver Builder still decides for itself (validateDriver.ts).

The driver contract has one implementation, and it is not this one. The editor
asks ``POST /api/driver-definitions/validate`` — the same rules the save route
runs — and renders what comes back. That endpoint is covered by
tests/test_driver_definition_validate.py (verdict and message parity with the
save path over the whole rejection corpus) and by
tests/test_driver_builder_reverse_corpus.py (every shipped driver opens clean).

What is left in TypeScript, and what this file covers, is everything that
carries no contract knowledge:

* **The path -> tab mapping.** Each issue comes back tagged with where in the
  definition it belongs; the editor turns that into one of its six tabs. It is
  the only decision the editor makes about an issue, and a wrong entry is
  invisible except as a message on a tab the author isn't looking at.
* **Editor mechanics** — which sender the runtime will route a command to, what
  a transport switch has to strip (and report before stripping), whether an OSC
  argument box holds something numeric.
* **The duplicate-id check.** The endpoint validates one definition in
  isolation and cannot see the library the editor is holding.
* **Housekeeping advice.** Warnings about drivers the platform loads and runs
  happily — a child type with no label, a computed field that shadows a config
  field. None of it blocks a save.

The load-bearing assertion is ``builder_raises_no_contract_errors``: a draft
the platform refuses for a dozen reasons raises none of its own here. That is
what stops the second rule set from growing back.

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

from openavc.drivers.spec import FIELDS
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
    "server_issue_paths_land_on_the_right_tab",
    "server_issues_keep_their_severity_and_wording",
    "builder_raises_no_contract_errors",
    "duplicate_id_is_the_editors_own_check",
    "route_precedence_matches_runtime",
    "osc_leftover_send_warning",
    "clean_drivers_no_stray_field_issues",
    "scrub_to_tcp_removes_osc_fields",
    "scrub_to_osc_clears_send",
    "scrub_setting_write_dropped",
    "whitespace_send_is_authored_content",
    "osc_arg_helper_matrix",
    "config_default_type_mismatch_warning",
    "config_boolean_string_default_warning",
    "config_typed_defaults_ok",
    "bridge_duplicate_port_id_warning",
    "bridge_valid_ok",
    "config_derived_unknown_placeholder_warning",
    "config_derived_name_collision_warning",
    "config_derived_valid_ok",
    "ir_codes_non_bridge_warning",
    "ir_bridge_driver_ok",
    "command_unknown_placeholder_warning",
    "link_url_unknown_placeholder_warning",
    "child_entity_advice_warnings",
    "full_featured_driver_no_issues",
    "safe_unquoted_year_author",
    "safe_numeric_child_label",
    "safe_wrapper_adds_nothing_to_a_readable_draft",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_driver_builder_validate(validate_results: dict, scenario: str) -> None:
    assert scenario in validate_results, f"harness did not report {scenario}"
    outcome = validate_results[scenario]
    assert outcome["pass"], f"{scenario} failed: detail={outcome.get('detail')!r}"


def test_every_scenario_the_harness_reports_is_asserted_on(
    validate_results: dict,
) -> None:
    """A scenario nobody asserts on is a test that quietly stopped running."""
    unclaimed = sorted(set(validate_results) - set(SCENARIOS))
    assert not unclaimed, f"harness reports scenarios no test asserts on: {unclaimed}"


# ── Every contract field knows which tab it belongs to ────────────────────


def _section_table() -> dict[str, str]:
    """Read SECTION_BY_PATH_ROOT out of validateDriver.ts.

    Parsed rather than imported: this is a Python test and the table is a
    TypeScript literal. The shape is fixed (`  key: "section",`), and a
    parse that found nothing fails loudly below.
    """
    source = VALIDATOR.read_text(encoding="utf-8")
    body = re.search(
        r"const SECTION_BY_PATH_ROOT: Record<string, IssueSection> = \{(.*?)\n\};",
        source,
        re.DOTALL,
    )
    assert body, "SECTION_BY_PATH_ROOT not found — did the mapping move or change shape?"
    return dict(re.findall(r'^\s*(\w+):\s*"(\w+)",', body.group(1), re.MULTILINE))


def test_the_tab_mapping_covers_every_top_level_contract_field() -> None:
    """A new contract field has to say which tab its issues land on.

    The platform reports an unknown key against any top-level block, so any
    of these can turn up as the root of an issue path. A root with no entry
    falls through to General, which is not wrong so much as unhelpful — the
    message ends up nowhere near the control that fixes it. Adding a field to
    spec.py is what fails this, and the fix is one line in the table.
    """
    table = _section_table()
    assert len(table) > 30, f"parsed only {len(table)} entries — the regex is stale"

    valid_sections = {"general", "connection", "behavior", "discovery", "simulation", "test"}
    bad = {k: v for k, v in table.items() if v not in valid_sections}
    assert not bad, f"entries naming a tab that doesn't exist: {bad}"

    missing = sorted(set(FIELDS) - set(table))
    assert not missing, (
        "these contract fields have no tab in validateDriver.ts's "
        f"SECTION_BY_PATH_ROOT, so their issues would land on General: {missing}"
    )


def test_the_tab_mapping_has_no_entries_for_fields_that_dont_exist() -> None:
    """A renamed or removed field leaves a dead row behind otherwise."""
    stale = sorted(set(_section_table()) - set(FIELDS))
    assert not stale, (
        f"SECTION_BY_PATH_ROOT places fields the contract no longer has: {stale}"
    )


def test_nothing_calls_the_unguarded_validator() -> None:
    """Every consumer must go through validateDriverSafely.

    validateDriver throws on a draft whose field types are unexpected, and a
    driver file is arbitrary YAML. The editor validates inside a render, so
    one unguarded call is a blank screen with no way back. The wrapper is what
    makes the class survivable, and it only works if it is the only door.
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
