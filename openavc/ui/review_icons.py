"""THE page-review check for an icon name the panel draws nothing for.

The panel draws a control's icon by its name from its sprite
(``web/panel/icons.svg``), draws an old Lucide name as the sprite id it has
now, and draws an uploaded picture (``assets://``) as it is
(``web/panel/modules/words.js``). A name in neither list draws nothing, while
the UI Builder may still preview it: Lucide's React icons answer to more names
than the sprite has. ``PANEL_ICON_NAMES`` is every name the panel draws, written
by the Builder's ``scripts/build-icons.mjs`` beside the sprite.

What is read is what ``drawIcon`` draws: the style's icon over the control's
own, and each state's icon in a ``show.look``, in both of the look's forms. A
value that is not text names no icon (``namesIcon``), so it is not a name to
warn about. ``page_review.icon_findings`` lists this check and says which of the
two a type draws; a type that draws neither is warned by the property and look
checks instead.

Mirrored by the Builder's ``reviewIcons.ts``, message for message
(``tests/test_ui_review_parity.py``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openavc.ui.panel_icon_names import PANEL_ICON_NAMES
from openavc.ui.review_finding import Finding

UPLOADED_ICON = "assets://"

KIND = "unknown_icon"

_REMEDY = "Use one the icon picker offers."


def draws_nothing(icon: Any) -> bool:
    """Whether the panel draws nothing for an icon written as ``icon``: text that
    names neither a sprite icon nor an old name for one, nor an uploaded picture."""
    return (
        isinstance(icon, str)
        and icon != ""
        and not icon.startswith(UPLOADED_ICON)
        and icon not in PANEL_ICON_NAMES
    )


def _set(value: Any) -> bool:
    """A value JavaScript's ``||`` would take, for a value read off JSON."""
    return value not in (None, "", False, 0)


def _drawn_icon(element: Mapping[str, Any]) -> Any:
    """The icon ``drawIcon`` draws for the control itself: the style's, else its own."""
    style = element.get("style")
    style_icon = style.get("icon") if isinstance(style, Mapping) else None
    return style_icon if _set(style_icon) else element.get("icon")


def _look(element: Mapping[str, Any]) -> Mapping[str, Any] | None:
    bindings = element.get("bindings")
    show = bindings.get("show") if isinstance(bindings, Mapping) else None
    look = show.get("look") if isinstance(show, Mapping) else None
    return look if isinstance(look, Mapping) else None


def _state_icons(look: Mapping[str, Any]) -> list[tuple[str, Any]]:
    """Each state of a look that names an icon, with it: the ``states`` map's,
    else the on/off form's two halves."""
    states = look.get("states")
    if isinstance(states, Mapping):
        sides = sorted(((str(name), spec) for name, spec in states.items()), key=lambda side: side[0])
    else:
        sides = [("active", look.get("style_active")), ("inactive", look.get("style_inactive"))]
    return [
        (name, spec["icon"]) for name, spec in sides
        if isinstance(spec, Mapping) and spec.get("icon")
    ]


def unknown_icon_findings(element: Mapping[str, Any], *, own: bool, per_state: bool) -> list[Finding]:
    """An icon name the panel draws nothing for: the control's own (``own``: its
    type draws one) and each state's (``per_state``: its type draws those)."""
    el_id = str(element.get("id", ""))
    el_type = str(element.get("type", ""))
    findings: list[Finding] = []
    icon = _drawn_icon(element) if own else None
    if draws_nothing(icon):
        findings.append(Finding(
            el_id, KIND,
            f"{el_id} ({el_type}): the panel has no icon named '{icon}', so it draws "
            f"none. {_REMEDY}",
            key=(KIND, el_id, "icon"),
        ))
    look = _look(element) if per_state else None
    unknown = [
        f"{name} ('{icon}')" for name, icon in (_state_icons(look) if look else [])
        if draws_nothing(icon)
    ]
    if unknown:
        findings.append(Finding(
            el_id, KIND,
            f"{el_id} ({el_type}): show.look names an icon the panel does not have for "
            f"{', '.join(unknown)}, and draws none there. {_REMEDY}",
            key=(KIND, el_id, "look.icon"),
        ))
    return findings
