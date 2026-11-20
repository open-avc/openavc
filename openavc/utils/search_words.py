"""THE search rule: how what a person types finds a thing.

What is typed reads as words: lowercased, split at any run of spaces, tabs or
line breaks, nothing empty (``search_words``). A thing is found when every
word appears, in any case, somewhere inside at least one of its fields, each
word wherever it is: "brindle switch" finds "Brindle Network Switch", "switch
acme" finds it too, and "switch" finds "Switchers" (``finds_every_word``;
``matches_search`` does both). Nothing typed finds everything; a field that is
missing or not text finds no word.

Which fields a search reads is its own (what it holds of each thing), and so
is the order it answers in: ranking is decided only among what this rule
found, so a search can never list a thing the rule did not find.

The Programmer's copy is ``components/shared/searchWords.ts``; both run
``tests/fixtures/search_words_cases.json`` (``tests/test_search_words.py``,
``searchWords.test.ts``). Asked by the AI's catalog search
(``cloud/tools/catalog_tools``) and, through the copy, by Browse Community,
the Installed list and the Add Device driver picker.

The separators are written out as the characters JavaScript's ``\\s``
matches, so both copies split the same text the same way: ``str.split()``
would also split at U+001C to U+001F and U+0085, and never at U+FEFF.

Pure stdlib.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

#: JavaScript's ``\s``: its white space and line terminators.
_SEPARATORS = re.compile(
    "[\t\n\v\f\r    -     　﻿]+"
)


def search_words(query: str) -> tuple[str, ...]:
    """The words of what was typed: lowercased, split at any run of white
    space, none empty. Only spaces, or nothing, reads as no words."""
    return tuple(word for word in _SEPARATORS.split(query.lower()) if word)


def finds_every_word(words: Sequence[str], fields: Iterable[object]) -> bool:
    """Whether every word (as ``search_words`` gives them) is inside at least
    one of the fields, in any case. No words finds everything; a field that is
    not text, or is empty, finds nothing."""
    texts = [field.lower() for field in fields if isinstance(field, str) and field]
    return all(any(word in text for text in texts) for word in words)


def matches_search(fields: Iterable[object], query: str) -> bool:
    """Whether what was typed finds a thing with these fields."""
    return finds_every_word(search_words(query), fields)
