"""What still shows an uploaded file, asked before anything unlinks one.

The third of the "what still points at this" walks, beside
``device_references`` and ``script_references``, and the one whose subject
cannot be put back: an asset is bytes on disk that somebody uploaded, so a
delete on a guess is unrecoverable from every door except the one that has the
original file. The panel says nothing about it either -- a page whose
background has gone draws the colour underneath, and an icon that has gone
draws nothing at all, both of them silently.

**It matches on the stored form, not on a field list.** Every asset reference
in a project is the exact string ``assets://<filename>`` -- the panel resolves
on that prefix in six places (``panel.js resolveAssetUrl`` and its callers:
an image's ``src``, an ``icon``, a ``button_image``, ``style.background_image``,
a page background and a sound id) and the upload door hands the same string
back as ``reference``. So this walks the value tree of each holder looking for
that string rather than reading named fields, which is the only way a
per-state ``button_image``, a style dict, a plugin's configuration or a field
added next year is covered without anybody remembering to come back here.

The match is on the WHOLE value, because that is what the panel matches on:
every resolution site tests ``startsWith('assets://')`` against the field and
then takes the rest of it as the filename, so a reference is the entire value
or it is not a reference. An ``assets://`` inside a stylesheet or a control's
markup resolves to nothing at runtime and is deliberately not counted here --
counting it would refuse a delete over a string that was never going to draw.

Everything the project can hold is walked. What it can name, it names the way
a refusal reads -- "page 'lights'", "element 'logo' on page 'main'" -- and
what it cannot, it still reports, by section, so nothing is silently missed.
Themes live in their own files rather than in the project, so a caller that
can read them passes them in; a caller that cannot passes nothing and the
answer says so by omission rather than by claiming the theme is clean.

Reports, never refuses, and never edits. The caller decides what a reference
means: the two delete doors refuse on a non-empty answer, because unlike a
device or a script there is nothing to point somewhere else afterwards.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any

#: The one form an asset reference is ever stored in.
ASSET_SCHEME = "assets://"


def asset_reference(filename: str) -> str:
    """The stored form of ``filename``, which is what a holder is searched for.

    The upload door builds this same string as an asset's ``reference``; a
    caller that already holds one may pass it back in and get it unchanged.
    """
    name = str(filename).strip()
    if name.startswith(ASSET_SCHEME):
        name = name[len(ASSET_SCHEME):]
    return ASSET_SCHEME + name.replace("\\", "/").strip("/")


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    return dump() if callable(dump) else None


def _names(value: Any, wanted: str) -> bool:
    """Does anything anywhere under ``value`` hold this exact reference?

    A whole-value walk on purpose: a reference can sit in a declared field, in
    a free-form ``style`` dict, inside one feedback state's override, or in a
    plugin's own configuration blob, and a walk that knew the field names would
    have to be edited every time one of those grew a new one.
    """
    if isinstance(value, str):
        return value == wanted
    mapping = _mapping(value)
    if mapping is not None:
        return any(_names(v, wanted) for v in mapping.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_names(v, wanted) for v in value)
    return False


def _without(value: Any, *keys: str) -> Mapping[str, Any]:
    """``value`` as a mapping with some keys dropped.

    A page is asked about itself without its ``elements``, so a background that
    is clean does not inherit the blame for an element on it.
    """
    mapping = _mapping(value) or {}
    return {k: v for k, v in mapping.items() if k not in keys}


def _id(value: Any, default: str = "?") -> str:
    mapping = _mapping(value)
    if mapping is not None and mapping.get("id") is not None:
        return str(mapping["id"])
    got = getattr(value, "id", None)
    return str(got) if got is not None else default


def _ui_holders(project: Any, wanted: str) -> Iterator[str]:
    ui = getattr(project, "ui", None)
    for page in getattr(ui, "pages", None) or []:
        page_id = _id(page)
        if _names(_without(page, "elements"), wanted):
            yield f"page '{page_id}'"
        for element in getattr(page, "elements", None) or []:
            if _names(element, wanted):
                yield f"element '{_id(element)}' on page '{page_id}'"
    for element in getattr(ui, "master_elements", None) or []:
        if _names(element, wanted):
            yield f"master element '{_id(element)}'"
    if _names(_without(ui, "pages", "master_elements"), wanted):
        yield "the project's panel settings"


def _named_holders(project: Any, wanted: str) -> Iterator[str]:
    """The sections whose members carry an id worth printing."""
    # A trigger lives inside its macro rather than at the top level, so a
    # reference in one reports as that macro -- which is also where somebody
    # sent to fix it has to go.
    for macro in getattr(project, "macros", None) or []:
        if _names(macro, wanted):
            yield f"macro '{_id(macro)}'"
    plugins = getattr(project, "plugins", None) or {}
    entries = plugins.items() if isinstance(plugins, Mapping) else (
        (_id(p), p) for p in plugins
    )
    for plugin_id, config in entries:
        if _names(config, wanted):
            yield f"plugin '{plugin_id}' configuration"


#: Sections walked by name above, so the catch-all does not report them twice.
_CLAIMED = ("ui", "macros", "plugins")


def _remaining_sections(project: Any, wanted: str) -> Iterator[str]:
    """Anything else in the project file that holds the reference.

    The point of the sweep is that it needs no list of what can hold an asset:
    a section nobody thought of still reports, by its own name, rather than
    leaving a delete to look safe. Its phrasing is deliberately vaguer than the
    named holders' -- it says where to look, not what to fix.
    """
    for key, value in (_mapping(project) or {}).items():
        if key in _CLAIMED:
            continue
        if _names(value, wanted):
            yield f"the project's '{key}' section"


def _theme_holders(themes: Any, wanted: str) -> Iterator[str]:
    if not themes:
        return
    entries = themes.items() if isinstance(themes, Mapping) else (
        (_id(t), t) for t in themes
    )
    for theme_id, theme in entries:
        if _names(theme, wanted):
            yield f"theme '{theme_id}'"


def asset_users(project: Any, filename: str, *, themes: Any = None) -> list[str]:
    """Who still shows ``filename``, named the way a refusal reads.

    THE answer every door that deletes out of ``assets/`` asks before it
    unlinks anything: the REST door the IDE's Asset Browser deletes through and
    the AI's ``delete_asset``. Both used to ask nothing, so an asset three
    pages were drawing could be removed by either of them and the only report
    was the panel on the wall drawing nothing where it had been.

    ``themes`` is optional and takes the themes as a mapping of id to
    definition, or any iterable of definitions carrying an ``id``. A caller
    that has them passes them; a caller that does not leaves them out, and no
    claim is made about them either way.

    The list is sorted so a refusal is stable to read. Empty means nothing in
    what was searched points at it and the delete may go ahead.
    """
    wanted = asset_reference(filename)
    who = set(_ui_holders(project, wanted))
    who.update(_named_holders(project, wanted))
    who.update(_remaining_sections(project, wanted))
    who.update(_theme_holders(themes, wanted))
    return sorted(who)
