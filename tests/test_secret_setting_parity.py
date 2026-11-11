"""Which settings hold a secret: one rule, asked everywhere, the same on both sides.

``is_secret_setting`` in ``openavc/drivers/spec.py`` decides it for the server:
the logs and the traffic recorder, problem reports and device audits, the login
file, the space trimming, learned settings and the driver validator.
``isSecretSetting`` in ``secretSetting.ts`` decides it for every form in the
Programmer: what is shown masked, never pre-filled, never trimmed, never read
as a number. A field one side calls a secret and the other does not is a
password shown on screen, or one written to the log, so both run this corpus
and compare answer for answer, as the monitor and parameter-label rules do.

The last test is the tripwire that keeps it one rule: no other module looks at
``secret`` or ``type: password`` itself.

Every field here is invented. This tests a platform rule.
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

from openavc.drivers.spec import PASSWORD_FIELD, is_secret_setting, secret_settings

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule.
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "secret_setting_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "secret_setting_parity_harness.cjs"
PROGRAMMER_SRC = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src"
SECRET_SETTING_TS = PROGRAMMER_SRC / "components" / "shared" / "secretSetting.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    assert is_secret_setting(case["key"], case["definition"]) is case["expected"], case["name"]


def test_secret_settings_names_each_secret_field_of_a_schema():
    schema = {
        "host": {"type": "string"},
        "password": {"type": "string"},
        "site_code": {"type": "string", "secret": True},
        "admin_code": {"type": "password"},
        "api_key": {"type": "string"},
        "broken": "not a definition",
    }
    assert secret_settings(schema) == {"password", "site_code", "admin_code"}
    assert secret_settings(None) == frozenset()
    assert secret_settings(["site_code"]) == frozenset()


def test_the_corpus_reaches_every_branch():
    """Parity over cases that never take a branch passes for free."""
    true_cases = [c for c in CASES if c["expected"]]
    assert any(
        c["key"] == PASSWORD_FIELD and not isinstance(c["definition"], dict) for c in true_cases
    ), "no password without a definition"
    assert any(
        isinstance(c["definition"], dict) and c["definition"].get("secret") is True
        and c["definition"].get("type") != "password" for c in true_cases
    ), "no field marked secret alone"
    assert any(
        isinstance(c["definition"], dict) and c["definition"].get("type") == "password"
        and "secret" not in c["definition"] for c in true_cases
    ), "no field typed password alone"
    false_cases = [c for c in CASES if not c["expected"]]
    truthy_marks = {
        json.dumps(c["definition"].get("secret")) for c in false_cases
        if isinstance(c["definition"], dict) and c["definition"].get("secret") not in (None, False)
    }
    assert {'"true"', "1"} <= truthy_marks, "no secret mark that is truthy but not true"
    assert any(
        c["definition"] is not None and not isinstance(c["definition"], dict) for c in false_cases
    ), "no definition that is not a mapping"
    assert any(c["key"] == "username" for c in false_cases), "no username"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not SECRET_SETTING_TS.is_file():
        return "secretSetting.ts missing"
    return None


@pytest.fixture(scope="module")
def programmer_answers() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(SECRET_SETTING_TS), str(CORPUS_PATH)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"secret setting parity harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_programmer_gives_the_same_answer(case, programmer_answers):
    assert programmer_answers["answers"][case["name"]] is is_secret_setting(
        case["key"], case["definition"]
    ), case["name"]


def test_the_programmer_names_the_same_password_field(programmer_answers):
    assert programmer_answers["password_field"] == PASSWORD_FIELD


# --- the tripwire ---------------------------------------------------------------
#
# A reader that looks at `secret` or `type: password` itself is a second
# definition, and the two drift the first time either is edited. Each exemption
# is one line, with the reason it is not deciding whether a setting is secret.

PY_PATTERNS = (
    re.compile(r"""get\(\s*["']secret["']"""),
    re.compile(r"""\[\s*["']secret["']\s*\]"""),
    re.compile(r"""get\(\s*["']type["']\s*\)\s*==\s*["']password["']"""),
)
TS_PATTERNS = (
    re.compile(r"""\.secret\s*(===|!==|\|\||\?\?|&&|\?)"""),
    re.compile(r"""!\s*\w+(\?)?\.secret\b"""),
    re.compile(r"""[Tt]ype\s*===\s*["']password["']"""),
)

PY_DEFINITION = OPENAVC_ROOT / "openavc" / "drivers" / "spec.py"

#: (file relative to programmer/src, the stripped line) -> why it is not a reader.
TS_EXEMPT: dict[tuple[str, str], str] = {
    ("components/shared/secretSetting.ts",
     'return d.secret === true || d.type === "password";'): "the rule itself",
    ("views/devices/deviceConfigCoerce.ts",
     'if (fieldType === "string" || fieldType === "password" || secret) {'):
        "coercion keeps a declared password as typed; every caller passes the rule as `secret`",
    ("components/driver-builder/LiveTestPanel.tsx",
     'type={field.secret ? "password" : "text"}'): "reads the flag the rule computed above it",
    ("components/driver-builder/paramEdits.ts",
     "return def.pattern !== undefined || def.trim !== undefined || def.secret !== undefined;"):
        "the Builder editing the mark",
    ("components/driver-builder/ParamTypeFields.tsx",
     "checked={def.secret === true}"): "the Builder editing the mark",
    ("components/driver-builder/ConfigSchemaEditor.tsx",
     "{field.secret ? ("): "the Builder editing the mark",
    ("components/driver-builder/ConfigSchemaEditor.tsx",
     "checked={field.secret ?? false}"): "the Builder editing the mark",
    ("components/driver-builder/ConfigSchemaEditor.tsx",
     '{!field.secret && field.type !== "table" && !CONNECTION_FIELDS.has(name) && ('):
        "the Builder editing the mark",
}


def _hits(root: Path, suffixes: tuple[str, ...], patterns) -> list[tuple[str, str]]:
    hits = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in suffixes or "node_modules" in path.parts:
            continue
        if path.name.endswith((".test.ts", ".test.tsx", ".gen.ts")):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if any(p.search(line) for p in patterns):
                hits.append((path.relative_to(root).as_posix(), line.strip()))
    return hits


def test_no_server_module_decides_a_secret_itself():
    found = [
        f"{where}: {line}"
        for where, line in _hits(OPENAVC_ROOT / "openavc", (".py",), PY_PATTERNS)
        if where != PY_DEFINITION.relative_to(OPENAVC_ROOT / "openavc").as_posix()
    ]
    assert not found, (
        "These read `secret` or `type: password` themselves; ask "
        "openavc.drivers.spec.is_secret_setting instead:\n" + "\n".join(found)
    )


def test_no_programmer_form_decides_a_secret_itself():
    found = [
        f"{where}: {line}"
        for where, line in _hits(PROGRAMMER_SRC, (".ts", ".tsx"), TS_PATTERNS)
        if (where, line) not in TS_EXEMPT
    ]
    assert not found, (
        "These read `secret` or `type: password` themselves; ask isSecretSetting "
        "(components/shared/secretSetting.ts) instead:\n" + "\n".join(found)
    )


def test_every_exemption_still_exists():
    """An exemption whose line is gone would let a new reader hide behind it."""
    present = set(_hits(PROGRAMMER_SRC, (".ts", ".tsx"), TS_PATTERNS))
    assert not [key for key in TS_EXEMPT if key not in present]
