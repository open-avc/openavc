"""THE version rule: what a version string reads as, and which of two is newer.

"Is this release newer than that one" and "does this system meet that floor"
are answered here, so a system on a release candidate reads the same to
everything that asks. The Programmer IDE's mirror is ``src/api/versionRule.ts``; both run
the corpus in ``tests/fixtures/version_rule_cases.json`` (``tests/test_version_rule.py``
and its vitest twin), which also holds this module to PEP 440's own order
through the ``packaging`` library on every row it reads.

What it reads, case-insensitively and with surrounding whitespace ignored:

* an optional ``v`` (a release tag, ``v0.40.0``);
* a release of one or more numbers of up to nine digits (``0.22`` reads the
  same as ``0.22.0``);
* then at most one of:

  - a pre-release: ``a`` / ``alpha``, ``b`` / ``beta``, or ``rc`` (also ``c``,
    ``pre``, ``preview``), with an optional number (none is 0), in the tag's
    spelling or Python's: ``0.40.0-rc2``, ``0.40.0-rc.2`` and ``0.40.0rc2`` are
    one version;
  - a development build: ``dev`` with an optional number (``0.41.0.dev1``).

How they order: by release first; within one release, a development build,
then alpha, beta and release candidates by number (``rc10`` is after ``rc9``),
then the release itself. So 0.40.0-rc2 is older than 0.40.0, and 0.41.0.dev1
is newer than 0.40.0 and older than 0.41.0.

Anything else is not a version and reads as ``None``: a build or local suffix
(``+build7``), a post-release, an epoch, a pre-release with a development
suffix, any other pre-release word. Each caller decides what an unreadable
version means for it (the newest-release check skips the tag, Discovery sorts
the copy lowest), so this module never turns one into ``0.0.0``.

Pure stdlib and imports nothing from the package, so it can be copied as it
stands to wherever the same question is asked. The cloud service does: it
takes this module, the IDE's mirror and the corpus byte for byte and runs the
corpus on each, so a change here reaches it when it copies them again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

# Each number is at most nine digits, so a long run of digits is no version
# rather than an error (int() refuses a few thousand digits).
_VERSION_RE = re.compile(
    r"v?"
    r"(?P<release>[0-9]{1,9}(?:\.[0-9]{1,9})*)"
    r"(?:"
    r"[-_.]?(?P<pre_kind>alpha|a|beta|b|preview|pre|rc|c)[-_.]?(?P<pre_n>[0-9]{1,9})?"
    r"|"
    r"[-_.]?(?P<dev>dev)[-_.]?(?P<dev_n>[0-9]{1,9})?"
    r")?",
    re.IGNORECASE | re.ASCII,
)

#: Each pre-release word, as the one it is read as.
_PRE_KINDS = {
    "a": "a", "alpha": "a",
    "b": "b", "beta": "b",
    "rc": "rc", "c": "rc", "pre": "rc", "preview": "rc",
}

#: The order of the pre-release kinds within one release.
_PRE_RANK = {"a": 0, "b": 1, "rc": 2}


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    """A version as read: its release numbers, its pre-release
    (``("rc", 2)``) or development build number, if any."""

    release: tuple[int, ...]
    pre: tuple[str, int] | None = None
    dev: int | None = None

    @property
    def is_prerelease(self) -> bool:
        """Whether this is a release candidate, beta, alpha or development build."""
        return self.pre is not None or self.dev is not None

    def _key(self) -> tuple:
        release = self.release
        while release and release[-1] == 0:
            release = release[:-1]
        if self.dev is not None:
            phase: tuple = (0, self.dev)
        elif self.pre is not None:
            phase = (1, _PRE_RANK[self.pre[0]], self.pre[1])
        else:
            phase = (2,)
        return (release, phase)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __hash__(self) -> int:
        return hash(self._key())


def read_version(text: object) -> Version | None:
    """What ``text`` reads as, or None when it is not a version."""
    if not isinstance(text, str):
        return None
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        return None
    release = tuple(int(part) for part in match["release"].split("."))
    if match["pre_kind"]:
        kind = _PRE_KINDS[match["pre_kind"].lower()]
        return Version(release, pre=(kind, int(match["pre_n"] or 0)))
    if match["dev"]:
        return Version(release, dev=int(match["dev_n"] or 0))
    return Version(release)


def compare_versions(a: object, b: object) -> int | None:
    """-1, 0 or 1 as ``a`` is older than, the same as, or newer than ``b``;
    None when either is not a version."""
    va, vb = read_version(a), read_version(b)
    if va is None or vb is None:
        return None
    return (va > vb) - (va < vb)
