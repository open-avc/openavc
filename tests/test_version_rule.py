"""The version rule, checked against the corpus the IDE's copy is checked against.

``openavc/utils/version_rule.py`` decides what a version reads as and which of
two is newer; ``src/api/versionRule.ts`` decides the same in the Programmer
IDE, and ``versionRule.test.ts`` runs this same corpus there. The corpus is a
list of classes from oldest to newest, so every pair of rows is a case: equal
within a class, ordered by class across them.

Two guards: the corpus must carry the spellings the rule exists for (a release
candidate in the tag's and Python's spelling in one class, a development
build, a two-digit release candidate, unreadable rows), or the parity passes
for free; and every row the rule reads is held to PEP 440's own order through
``packaging``, the library the plugin floor already compares with.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from packaging.version import Version as Pep440Version

from openavc.utils.version_rule import Version, compare_versions, read_version

#: In ``fixtures/``, not ``data/``: .gitignore carries a bare ``data/`` rule.
CORPUS_PATH = Path(__file__).parent / "fixtures" / "version_rule_cases.json"
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
CLASSES = CORPUS["ascending"]
UNREADABLE = CORPUS["unreadable"]

ROWS = [(rank, text) for rank, cls in enumerate(CLASSES) for text in cls["versions"]]


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


@pytest.mark.parametrize("rank,text", ROWS, ids=[repr(t) for _, t in ROWS])
def test_every_row_reads(rank, text):
    version = read_version(text)
    assert isinstance(version, Version), text
    assert version.is_prerelease is CLASSES[rank]["prerelease"], text


def test_every_pair_orders_by_class():
    for (ra, a), (rb, b) in itertools.product(ROWS, repeat=2):
        assert compare_versions(a, b) == _sign(ra - rb), (a, b)


def test_equal_versions_hash_alike():
    for cls in CLASSES:
        assert len({read_version(text) for text in cls["versions"]}) == 1, cls


@pytest.mark.parametrize("value", UNREADABLE, ids=[repr(v) for v in UNREADABLE])
def test_unreadable_reads_as_none_and_compares_with_nothing(value):
    assert read_version(value) is None
    for _, text in ROWS:
        assert compare_versions(value, text) is None
        assert compare_versions(text, value) is None


def test_the_rule_orders_as_pep_440_on_every_row_it_reads():
    for (_, a), (_, b) in itertools.product(ROWS, repeat=2):
        pa, pb = Pep440Version(a), Pep440Version(b)
        assert compare_versions(a, b) == (pa > pb) - (pa < pb), (a, b)


def test_the_corpus_carries_what_the_rule_is_for():
    def rank_of(text: str) -> int:
        return next(rank for rank, t in ROWS if t == text)

    # A release candidate, in the tag's spelling and the frozen build's, is one
    # version, below its release and above the release before it.
    assert rank_of("0.40.0-rc2") == rank_of("0.40.0rc2")
    assert rank_of("0.39.0") < rank_of("0.40.0-rc2") < rank_of("0.40.0")
    # A two-digit release candidate after a one-digit one.
    assert rank_of("0.40.0-rc9") < rank_of("0.40.0-rc10")
    # A development build is read, between the last release and its own.
    assert rank_of("0.40.0") < rank_of("0.41.0.dev1") < rank_of("0.41.0")
    # A short release equals its padded spelling.
    assert rank_of("0.22") == rank_of("0.22.0")
    # Both kinds of row exist.
    assert any(cls["prerelease"] for cls in CLASSES)
    assert any(not cls["prerelease"] for cls in CLASSES)
    assert UNREADABLE
    assert any(not isinstance(v, str) for v in UNREADABLE)
