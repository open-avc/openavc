"""How a typed search finds a thing, checked against the corpus the Programmer is checked against.

``openavc/utils/search_words.py`` is the rule the AI's catalog search asks;
``components/shared/searchWords.ts`` is the copy Browse Community, the
Installed list and the Add Device driver picker ask. Both run
``fixtures/search_words_cases.json``, so a query reads as the same words, and
finds the same things, wherever it is typed.

The guards at the bottom check the corpus itself: agreement over cases that
never reach a branch passes for free.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.utils.search_words import finds_every_word, matches_search, search_words

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule, so
#: a corpus under tests/data/ is silently untracked.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "search_words_cases.json"
CASES = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_words_a_query_reads_as(case):
    assert list(search_words(case["query"])) == case["words"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_whether_the_query_finds_the_fields(case):
    assert finds_every_word(search_words(case["query"]), case["fields"]) is case["finds"]
    assert matches_search(case["fields"], case["query"]) is case["finds"]


def test_a_field_that_is_not_text_finds_nothing():
    assert matches_search([5, ["acme"], {"acme": 1}], "acme") is False
    assert matches_search([5, "Acme"], "acme") is True


# --- the corpus itself ----------------------------------------------------------------


def test_the_corpus_has_unique_names():
    names = [c["name"] for c in CASES]
    assert len(names) == len(set(names))


def test_the_corpus_reaches_every_outcome():
    several = [c for c in CASES if len(c["words"]) > 1]
    assert any(c["finds"] for c in several), "several words, all found"
    assert any(not c["finds"] for c in several), "several words, one missing"
    assert any(not c["words"] for c in CASES), "nothing typed"
    assert any(c["finds"] and len(c["words"]) == 1 for c in CASES)
    assert any(not c["finds"] and len(c["words"]) == 1 for c in CASES)


def test_the_corpus_holds_words_that_are_never_side_by_side():
    """The case the rule exists for: every word is there, the phrase is not."""
    apart = [
        c for c in CASES
        if c["finds"] and len(c["words"]) > 1
        and not any(" ".join(c["words"]) in (f or "").lower() for f in c["fields"])
    ]
    assert apart


def test_the_corpus_holds_a_space_python_and_javascript_disagree_on():
    """Each side's own split would pass a corpus of plain spaces."""
    queries = "".join(c["query"] for c in CASES)
    assert "﻿" in queries  # JavaScript's \s, not str.split()
    assert "\u001f" in queries and "\u0085" in queries  # str.split(), not JavaScript's \s
