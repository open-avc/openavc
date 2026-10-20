"""Every field the macro lint can say is missing has a label in the editor.

The lint's records say which field a step or trigger is missing
(``core/macro_validation``, the ``missing`` key), and the macro editor words
the finding by the label on its form ("Device is required.") from
``MISSING_FIELD_LABELS`` in ``components/macros/macroLint.ts``. A field with
no entry there falls back to the platform's sentence, which names the field by
its key ("device.command step requires 'device'"). This holds the table to the
platform's list, so a required field added on one side reaches the other.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.macro_validation import STEP_REQUIRED_FIELDS, macro_issues

ROOT = Path(__file__).resolve().parent.parent
MACRO_LINT_TS = (
    ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "macros" / "macroLint.ts"
)

# The fields a trigger or a wait can be missing beyond STEP_REQUIRED_FIELDS.
OTHER_MISSING = {
    ("wait_until", "timeout"),
    ("schedule", "cron"),
    ("state_change", "state_key"),
    ("event", "event_pattern"),
}


def _editor_labels() -> dict[tuple[str, str], str]:
    src = MACRO_LINT_TS.read_text(encoding="utf-8")
    table = re.search(
        r"export const MISSING_FIELD_LABELS[^=]*=\s*\{(.*?)\n\};", src, re.S,
    )
    assert table, "MISSING_FIELD_LABELS not found in macroLint.ts"
    labels: dict[tuple[str, str], str] = {}
    for action, fields in re.findall(r'"?([a-z_.]+)"?:\s*\{([^}]*)\}', table.group(1)):
        for field, label in re.findall(r'"?([a-z_.]+)"?:\s*"([^"]+)"', fields):
            labels[(action, field)] = label
    return labels


def test_every_required_step_field_has_a_label_in_the_editor():
    labels = _editor_labels()
    wanted = {
        (action, field)
        for action, fields in STEP_REQUIRED_FIELDS.items()
        for field in fields
    } | OTHER_MISSING
    assert sorted(wanted - labels.keys()) == []


def test_the_other_missing_fields_are_the_ones_the_lint_reports():
    """OTHER_MISSING is read off the lint itself, not written from memory."""
    issues = macro_issues(
        [{"action": "wait_until", "condition": {"key": "var.ready", "operator": "truthy"}}],
        [
            {"id": "a", "type": "schedule", "cron": ""},
            {"id": "b", "type": "state_change", "state_key": ""},
            {"id": "c", "type": "event", "event_pattern": ""},
        ],
    )
    assert {(i["action"], i["missing"]) for i in issues if "missing" in i} == OTHER_MISSING
