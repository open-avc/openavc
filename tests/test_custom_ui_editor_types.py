"""Every file type ``ui/`` accepts is classified by the IDE's editor.

The server decides what may be written into a project's ``ui/`` tree
(``core/custom_ui.py`` ``ALLOWED_EXTENSIONS``). The Code view then has to
decide, for each of those, whether there is anything to type into: an
``.html`` opens in Monaco, a ``.png`` is a real file with bytes and gets a
line saying so instead.

That second table is a genuine front-end decision, not a copy of the first —
but the two have to stay exhaustive against each other. A type added to the
server list and to neither half of the editor's would fall into whichever
branch happens to be the default and be silently wrong: a new text type
refusing to open, or a new binary one rendered as garbage in an editor. So
this asserts the editor's two lists PARTITION the server's, and names the file
to fix when they do not.

There is no Python half to test here — this is a frontend/backend contract, so
it is pinned by reading the source, like the other cross-language tables.
"""

from __future__ import annotations

import re
from pathlib import Path

from openavc.core.custom_ui import ALLOWED_EXTENSIONS

# Repo root = openavc/ (this file is openavc/tests/test_custom_ui_editor_types.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
CUSTOM_UI_FILES_TS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src"
    / "components" / "scripts" / "customUiFiles.ts"
)


def _text_extensions(src: str) -> set[str]:
    block = re.search(
        r"export const TEXT_UI_LANGUAGES[^=]*=\s*\{(.*?)\n\};", src, re.S
    )
    assert block, "TEXT_UI_LANGUAGES table not found in customUiFiles.ts"
    return set(re.findall(r'"(\.[a-z0-9]+)"\s*:', block.group(1)))


def _binary_extensions(src: str) -> set[str]:
    block = re.search(
        r"export const BINARY_UI_EXTENSIONS[^=]*=\s*\[(.*?)\n\];", src, re.S
    )
    assert block, "BINARY_UI_EXTENSIONS list not found in customUiFiles.ts"
    return set(re.findall(r'"(\.[a-z0-9]+)"', block.group(1)))


def test_editor_classifies_every_allowed_type() -> None:
    src = CUSTOM_UI_FILES_TS.read_text(encoding="utf-8")
    classified = _text_extensions(src) | _binary_extensions(src)

    unclassified = set(ALLOWED_EXTENSIONS) - classified
    assert not unclassified, (
        f"These types can be written into ui/ but the Code view does not know "
        f"whether to open them: {sorted(unclassified)}. Add each to "
        f"TEXT_UI_LANGUAGES (with its Monaco language) or to "
        f"BINARY_UI_EXTENSIONS in {CUSTOM_UI_FILES_TS.name}."
    )


def test_editor_claims_no_type_the_tree_refuses() -> None:
    src = CUSTOM_UI_FILES_TS.read_text(encoding="utf-8")
    claimed = _text_extensions(src) | _binary_extensions(src)

    phantom = claimed - set(ALLOWED_EXTENSIONS)
    assert not phantom, (
        f"The Code view classifies types that can never be in ui/: "
        f"{sorted(phantom)}. Either add them to ALLOWED_EXTENSIONS in "
        f"core/custom_ui.py or drop them from {CUSTOM_UI_FILES_TS.name}."
    )


def test_text_and_binary_do_not_overlap() -> None:
    src = CUSTOM_UI_FILES_TS.read_text(encoding="utf-8")

    both = _text_extensions(src) & _binary_extensions(src)
    assert not both, f"Classified as both text and binary: {sorted(both)}"
