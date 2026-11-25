"""What a command asks before it is sent by hand: one rule, the same on both sides.

``confirm_sentence`` in ``openavc/drivers/command_model.py`` reads a
``confirm`` for the server: the actions resolver (a Quick Action button), the
device audit's command list and the device page's Send (``command_confirms``).
``confirmSentence`` in ``commandConfirm.ts`` reads it for a Driver Builder
draft no server has read yet: Live Test's question and the two "Ask before"
ticks. A sentence one side trims and the other does not, or a blank one only
one side asks, is the same command asking two things, so both run this corpus
and compare answer for answer.

The last test is the tripwire that keeps it one rule: no other module reads a
``confirm`` to decide what is asked.

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

from openavc.drivers.command_model import CONFIRM_QUESTION, confirm_sentence, sets_its_own_confirm

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule.
CORPUS_PATH = OPENAVC_ROOT / "tests" / "fixtures" / "command_confirm_cases.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "command_confirm_parity_harness.cjs"
PROGRAMMER_SRC = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src"
COMMAND_CONFIRM_TS = PROGRAMMER_SRC / "components" / "shared" / "commandConfirm.ts"
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    assert confirm_sentence(case["confirm"], case["label"]) == case["sentence"], case["name"]
    assert sets_its_own_confirm(case["confirm"]) is case["sets_its_own"], case["name"]


def test_the_corpus_asks_the_servers_question():
    assert CORPUS["question"] == CONFIRM_QUESTION


def test_the_corpus_reaches_every_branch():
    """Parity over cases that never take a branch passes for free."""
    by_value = {json.dumps(c["confirm"]): c for c in CASES}
    assert "true" in by_value and "false" in by_value and "null" in by_value
    sentences = [c for c in CASES if isinstance(c["confirm"], str) and c["sentence"]]
    assert any(c["sentence"] != c["confirm"] for c in sentences), "no sentence trimmed"
    assert any("\n" in c["sentence"] for c in sentences), "no sentence keeping a line break inside"
    blanks = [c for c in CASES if isinstance(c["confirm"], str) and c["sentence"] is None]
    assert {"", "   "} <= {c["confirm"] for c in blanks}, "no empty or blank sentence"
    # Where Python's own strip() and JavaScript's trim() part ways.
    assert {"﻿", "\u0085", "\u001f"} <= {c["confirm"] for c in CASES if isinstance(c["confirm"], str)}
    assert any(not isinstance(c["confirm"], (bool, str, type(None))) for c in CASES), "no value of another type"
    assert any(any(ch in c["label"] for ch in "${}") for c in CASES), "no label a template would read"


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not (NODE_MODULES / "esbuild").is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not COMMAND_CONFIRM_TS.is_file():
        return "commandConfirm.ts missing"
    return None


@pytest.fixture(scope="module")
def programmer_answers() -> dict:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)
    proc = subprocess.run(
        ["node", str(HARNESS), str(COMMAND_CONFIRM_TS), str(CORPUS_PATH)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"command confirm parity harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_programmer_gives_the_same_answer(case, programmer_answers):
    answer = programmer_answers["answers"][case["name"]]
    sentence = confirm_sentence(case["confirm"], case["label"])
    assert answer["sentence"] == sentence, case["name"]
    assert answer["live_test"] == sentence, case["name"]
    assert answer["asks"] is (sentence is not None), case["name"]
    assert answer["sets_its_own"] is sets_its_own_confirm(case["confirm"]), case["name"]


def test_the_programmer_asks_the_same_question(programmer_answers):
    assert programmer_answers["question"] == CONFIRM_QUESTION


# --- the tripwire ---------------------------------------------------------------
#
# A module that reads a ``confirm`` to decide what is asked is a second rule.
# Each exemption is one line, with the reason it is not deciding that.

_PY_READ = re.compile(r"""\[\s*["']confirm["']\s*\]|\.get\(\s*["']confirm["']""")
_PY_EXEMPT = {
    "drivers/command_model.py": "the rule",
    "drivers/actions.py": "hands an action's own confirm to the rule, or the command's",
    "audit/command_catalog.py": "reads the resolver's answer for a button",
    "drivers/command_checks.py": "the checker: a flag or a sentence, nothing else",
    "drivers/action_checks.py": "the checker: a flag or a sentence, nothing else",
}


def test_no_other_server_module_reads_a_confirm():
    readers = set()
    package = OPENAVC_ROOT / "openavc"
    for path in package.rglob("*.py"):
        if _PY_READ.search(path.read_text(encoding="utf-8")):
            readers.add(path.relative_to(package).as_posix())
    assert readers <= set(_PY_EXEMPT), sorted(readers - set(_PY_EXEMPT))
