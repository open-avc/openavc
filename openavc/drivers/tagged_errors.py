"""THE list a driver check's messages are gathered in, which remembers
where in the definition each one belongs (``TaggedErrors``): the definition
walk (``avcdriver_semantic``) and a Python driver's (``python_info``) gather
into it, and ``validate_driver_issues`` reads its tags to anchor each issue
in the Driver Builder.

Purity contract: standard library only, like every module the definition
walk imports; vendored into openavc-drivers and openavc-sdk with it.
"""

from __future__ import annotations

from typing import Any, Iterable


class TaggedErrors(list):
    """A real ``list[str]`` that also records *where in the definition* each
    entry came from.

    A message reaches the list one of two ways. A check the walk asks about
    the one place it is standing on (a command, a response rule, a block) is
    tagged with that place: the walk stamps it on the list first, ``errors.ctx
    = "commands.power_on"``, and everything the check appends there, however
    deep inside a helper, carries it. A check that speaks about places of its
    own (a name inside a map, a conversion on whichever variable or parameter
    declares one, a placeholder in whichever template holds it) hands each
    place back with its message, ``errors.at(place, message)`` or
    ``errors.at_each(pairs)``, so the message never borrows the place where
    the walk last stood.

    Two properties make this safe to drop into the existing walk:

    * It **is** a list, so ``validate_driver_definition`` keeps its
      ``list[str]`` contract and no existing caller changes — the loader, the
      save routes, ``build_index.py`` and the vendored catalog copy all see
      exactly what they saw before.
    * ``list.extend`` does not route through a subclass's ``append``, so both
      are overridden. A sub-list that carries its own (finer) tags keeps them;
      anything else inherits the current section.

    ``validate_driver_issues`` reads the tags to give the Driver Builder
    something to anchor each issue to, and ``check_driver_definition`` reads
    them to leave out a shape sentence about a place another message already
    speaks for.
    """

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.ctx: str = ""
        self.tags: list[str] = [""] * len(self)

    def append(self, item: Any) -> None:
        super().append(item)
        self.tags.append(self.ctx)

    def extend(self, items: Any) -> None:
        sub_tags = getattr(items, "tags", None)
        items = list(items)
        super().extend(items)
        if sub_tags is not None and len(sub_tags) == len(items):
            self.tags.extend(sub_tags)
        else:
            self.tags.extend([self.ctx] * len(items))

    def at(self, place: str, message: str) -> None:
        """Add ``message`` tagged with ``place``, the place the check that
        wrote it says it is about."""
        super().append(message)
        self.tags.append(place)

    def at_each(self, placed: Iterable[tuple[str, str]]) -> None:
        """Add each ``(place, message)`` a check hands back, in its order."""
        for place, message in placed:
            self.at(place, message)
