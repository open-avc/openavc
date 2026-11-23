"""What a page review says: one finding.

A check in ``openavc/ui/page_review.py``, or in a module beside it that owns
one kind of check (``review_icons.py``), answers with these. Its own module so
a check module can make one without importing the review that lists it; the
review imports it back, so ``openavc.ui.page_review.Finding`` is the same
class. The Builder's twin is ``reviewFinding.ts``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One thing that will not draw the way it was written.

    ``kind`` groups them for a caller that wants to count or filter; ``message``
    is the whole finding in one self-contained sentence, because the consumer
    that matters reads prose and acts on it. ``key`` is what makes a finding the
    same finding across two arrangements of one page -- an element can collide
    with three different neighbours, and all three are worth saying once.
    """

    element_id: str
    kind: str
    message: str
    key: tuple = ()

    def __post_init__(self) -> None:
        if not self.key:
            object.__setattr__(self, "key", (self.kind, self.element_id))
