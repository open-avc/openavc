"""A command parameter's name, checked against the corpus the IDE is checked against.

``openavc/drivers/param_labels.py`` names a parameter in the text the server
writes (the device audit's trace); ``paramLabel.ts`` names it on every form a
person fills in. A field must read the same in both, so both run this corpus
and compare answer for answer, the arrangement that holds ``monitors.py`` and
``monitorHelpers.ts`` together.

The guards at the bottom check the corpus itself: parity over cases that never
reach a branch passes for free.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from openavc.drivers.param_labels import ACRONYMS, UNITS, param_label, readable_key

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule, so
#: a corpus under tests/data/ is silently untracked -- green here, ENOENT in CI.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "param_label_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


def _def(case: dict) -> dict:
    return {"type": "string", "label": case["label"]} if "label" in case else {"type": "string"}


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_corpus_case(case):
    assert param_label(case["key"], _def(case)) == case["expected"], case["name"]


def test_no_definition_reads_the_key():
    assert param_label("input_id", None) == "Input ID"
    assert param_label("input_id", "not a definition") == "Input ID"


PARAM_LABEL_TS = (
    Path(__file__).parent.parent
    / "openavc" / "web" / "programmer" / "src" / "components" / "shared" / "paramLabel.ts"
)


def _ts_table(name: str) -> dict[str, str]:
    source = PARAM_LABEL_TS.read_text(encoding="utf-8")
    body = re.search(rf"export const {name}: Record<string, string> = \{{(.*?)\}};", source, re.S)
    assert body, f"{name} not found in paramLabel.ts"
    return dict(re.findall(r'(\w+): "([^"]*)"', body.group(1)))


@pytest.mark.parametrize("name, table", [("ACRONYMS", ACRONYMS), ("UNITS", UNITS)])
def test_the_ide_spells_every_word_the_same(name, table):
    """The corpus only proves the words it uses; the two tables must match whole."""
    assert _ts_table(name) == table


def test_the_corpus_reaches_every_branch():
    """A corpus that never takes a branch proves nothing about it."""
    keys = [c["key"] for c in CASES if "label" not in c]
    words = {w.lower() for k in keys for w in readable_key(k).replace("(", " ").split()}
    assert words & {a.lower() for a in ACRONYMS.values()}, "no acronym in the corpus"
    assert any(readable_key(k).endswith(")") for k in keys), "no unit in the corpus"
    assert any(k.lower() in UNITS for k in keys), "no unit standing alone in the corpus"
    assert any(k != k.lower() and "_" not in k for k in keys), "no camelCase in the corpus"
    assert "" in keys, "no empty key in the corpus"
    labelled = [c for c in CASES if "label" in c]
    assert any(c["expected"] == c["label"] for c in labelled), "no label that wins"
    assert any(c["expected"] != c["label"] for c in labelled), "no label that counts as none"
