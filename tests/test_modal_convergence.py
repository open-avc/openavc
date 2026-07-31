"""The Programmer IDE has ONE modal and ONE z-index ladder. This keeps it that way.

`components/shared/Modal.tsx` is the only full-screen backdrop in the IDE, and
`components/shared/layers.ts` is the only place a floating layer's height is
chosen. Both facts are easy to erode one dialog at a time — that is exactly how
the IDE ended up with twenty overlays on five accidental tiers, none of the
hand-rolled ones trapping focus and most of them impossible to close from the
keyboard.

So this is a source scan, not a behaviour test (`test_modal_behaviour.py` is
the behaviour half). It needs no Node toolchain, which means it runs in every
job — including the Python-only ones where the TypeScript harnesses skip.

If a dialog genuinely needs something Modal doesn't do, the fix is a prop on
Modal, not a twenty-first overlay.
"""

from __future__ import annotations

import re
from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_modal_convergence.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
SRC = OPENAVC_ROOT / "web" / "programmer" / "src"

MODAL_TSX = SRC / "components" / "shared" / "Modal.tsx"
LAYERS_TS = SRC / "components" / "shared" / "layers.ts"

# Layers that float above the page start here. Anything below is in-page
# stacking (canvas handles, sticky headers, drag ghosts) and is none of this
# test's business.
FLOATING_FLOOR = 1000


def _sources() -> list[Path]:
    return sorted(
        p for p in SRC.rglob("*") if p.suffix in {".ts", ".tsx"} and p.is_file()
    )


def _rel(path: Path) -> str:
    return str(path.relative_to(OPENAVC_ROOT))


def test_only_modal_paints_a_backdrop() -> None:
    """`position: fixed` + `inset: 0` — a full-screen scrim — is Modal's alone."""
    offenders: list[str] = []
    for path in _sources():
        if path == MODAL_TSX:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'position:\s*"fixed"', text):
            # The style object continues for a few properties; a backdrop
            # declares inset within them, an anchored popover declares
            # left/top/bottom instead.
            window = text[match.end() : match.end() + 240]
            if re.search(r"\binset:\s*0\b", window):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{_rel(path)}:{line}")
    assert not offenders, (
        "hand-rolled modal backdrop(s) found: "
        + ", ".join(offenders)
        + ". Every dialog in the Programmer IDE renders through "
        "components/shared/Modal.tsx, which brings the backdrop, Escape, the "
        "focus trap and the z-index with it. Add a prop to Modal if it doesn't "
        "do what this dialog needs."
    )


def test_no_hand_picked_floating_z_index() -> None:
    """Above the in-page range, a z-index comes from the LAYER ladder."""
    offenders: list[str] = []
    for path in _sources():
        if path == LAYERS_TS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"zIndex:\s*(\d+)", text):
            if int(match.group(1)) >= FLOATING_FLOOR:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{_rel(path)}:{line} (z {match.group(1)})")
    assert not offenders, (
        "floating layer(s) with a hand-picked z-index: "
        + ", ".join(offenders)
        + ". Import LAYER from components/shared/layers.ts instead — the ladder "
        "is what keeps a picker above the dialog it was opened from."
    )


def test_ladder_is_ordered() -> None:
    """modal < nested modal < popover < toast, and the rungs are far enough apart."""
    text = LAYERS_TS.read_text(encoding="utf-8")
    values = {
        name: int(value)
        for name, value in re.findall(r"^\s*(\w+):\s*(\d+),", text, re.MULTILINE)
    }
    for name in ("modal", "modalNested", "popover", "toast"):
        assert name in values, f"layers.ts must define LAYER.{name}"
    assert values["modal"] >= FLOATING_FLOOR, "the ladder must clear in-page stacking"
    assert values["modal"] < values["modalNested"] < values["popover"] < values["toast"], (
        f"the ladder is out of order: {values}"
    )

    step = int(re.search(r"MODAL_LAYER_STEP\s*=\s*(\d+)", text).group(1))
    max_depth = int(re.search(r"MAX_MODAL_DEPTH\s*=\s*(\d+)", text).group(1))
    assert values["modalNested"] == values["modal"] + step, (
        "LAYER.modalNested must name the first nesting step so the constant and "
        "the computed depth can't disagree"
    )
    assert values["modal"] + step * max_depth < values["popover"], (
        "a stack of modals nested to MAX_MODAL_DEPTH must still sit below the "
        "popover tier, or a picker would open behind the dialog that spawned it"
    )


def test_view_shortcuts_stand_down_while_a_dialog_is_open() -> None:
    """A view's canvas shortcuts must not act on keys meant for a dialog.

    The UI Builder binds Escape (deselect), Delete and the arrow keys at the
    window. With a dialog open the canvas is behind a backdrop, so those keys
    belong to the dialog — and worse, the view's handler renders in response to
    the same keypress, which used to swallow the dialog's Escape entirely.
    """
    text = (SRC / "views" / "UIBuilderView.tsx").read_text(encoding="utf-8")
    assert "isModalOpen" in text, (
        "UIBuilderView must ask Modal whether a dialog is open before acting on "
        "a keypress"
    )
    handler = text[text.index("const handleKeyDown"):]
    guard = handler.index("if (isModalOpen()) return;")
    first_action = handler.index("preventDefault")
    assert guard < first_action, (
        "the isModalOpen() guard must come before any shortcut handling"
    )


def test_shared_dialogs_are_built_on_modal() -> None:
    """The three shared dialogs compose Modal rather than re-implementing it."""
    for name in ("Dialog.tsx", "ConfirmDialog.tsx", "PromptDialog.tsx"):
        path = SRC / "components" / "shared" / name
        text = path.read_text(encoding="utf-8")
        assert 'from "./Modal"' in text, f"{name} must render through Modal"
        assert "addEventListener" not in text, (
            f"{name} must not keep its own key handling — Escape and the focus "
            "trap belong to Modal, which is the only one that knows which "
            "dialog is on top"
        )
