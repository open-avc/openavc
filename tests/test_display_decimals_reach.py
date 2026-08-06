"""`display_decimals` must reach every surface, not just the two that had it.

The property was declared in the project schema and offered in the Builder, but
only the slider and fader renderers read it. So a label bound to a device float
printed the float whole: a bench amplifier reporting a float32 0.06 A crossed
the wire at float64 width and the panel drew `0.06000000238418579 A`.

Three surfaces have to agree or the gap reopens somewhere else:

* the **renderer** honours it (proved by execution in tests/test_panel_js.py,
  which renders the real panel.js in jsdom -- this file does not re-prove it),
* the **Builder** offers the field for the same element types, because a
  capability the runtime has and the authoring UI hides is the same bug in the
  other direction,
* the **field comments** name those types, since the stale "slider/fader" one
  is what made this look like a slider feature for as long as it did.

There is no vitest/jest harness for the Builder's React components, so -- like
the other frontend regression tests here -- the Builder half is pinned to source.
"""

from __future__ import annotations

from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_display_decimals_reach.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = OPENAVC_ROOT / "web" / "panel" / "panel.js"
PROJECT_LOADER = OPENAVC_ROOT / "server" / "core" / "project_loader.py"
TYPES_TS = OPENAVC_ROOT / "web" / "programmer" / "src" / "api" / "types.ts"
BASIC_PROPS = (
    OPENAVC_ROOT / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "PropertySections" / "BasicProperties.tsx"
)

#: Every element type that draws a number and therefore reads the property.
HONORING_TYPES = ("slider", "fader", "gauge", "label")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _element_type_block(src: str, el_type: str) -> str:
    """The JSX guarded by `{element.type === "<el_type>" && (`, to the next guard.

    Slicing is what makes the assertion about the section an author actually
    sees when that element is selected. A bare substring search over the whole
    file would pass on the slider's copy of the field.
    """
    guard = f'{{element.type === "{el_type}" && ('
    start = src.index(guard)
    rest = src[start + len(guard):]
    end = rest.find("{element.type ")
    return rest[:end] if end != -1 else rest


def test_panel_label_rounds_through_the_shared_helper() -> None:
    src = _read(PANEL_JS)
    assert "_labelValueText" in src, (
        "the label's text evaluator must round a numeric value; it printed "
        "String(value) raw, so a device float showed all 17 digits"
    )
    # Both paths: the {value} placeholder AND the bare no-format case.
    assert ".join(shown)" in src and "setText(shown)" in src, (
        "both label paths (with and without a format string) must go through "
        "the rounding helper"
    )


def test_panel_gauge_readout_is_not_hardcoded_to_one_decimal() -> None:
    src = _read(PANEL_JS)
    assert "`${Math.round(value * 10) / 10}${unit}`" not in src, (
        "the gauge readout must not hard-code one decimal place"
    )
    assert "value.toFixed(displayDecimals)" in src, (
        "the gauge readout must honour the element's display_decimals"
    )
    # Unset has to stay byte-identical or every existing gauge shifts to "50.0".
    assert "String(Math.round(value * 10) / 10)" in src, (
        "an unset gauge must still draw one decimal with trailing zeros "
        "dropped, exactly as it always has"
    )


def test_decimals_are_clamped_before_tofixed() -> None:
    """`toFixed` throws a RangeError outside 0..100, mid-render, on a live panel."""
    src = _read(PANEL_JS)
    assert "_displayDecimals(elementDef)" in src
    assert "Math.max(0, Math.min(20, Math.round(n)))" in src, (
        "a stray project value must be clamped, not passed to toFixed raw"
    )


def test_builder_offers_shown_decimals_for_every_honoring_type() -> None:
    """A runtime capability the Builder hides cannot be reached by hand."""
    src = _read(BASIC_PROPS)
    assert src.count("<ShownDecimalsRow") == 3, (
        "expected one ShownDecimalsRow per authoring section: the shared "
        "slider/fader block, the gauge block, and the label block"
    )
    # The shared slider/fader component, by the one prop only it passes.
    assert 'placeholder="Auto"' in src, "the slider/fader row lost its placeholder"
    for el_type in ("gauge", "label"):
        assert "<ShownDecimalsRow" in _element_type_block(src, el_type), (
            f"the {el_type} properties panel never offers Shown decimals, so the "
            f"panel honours a setting nobody can reach"
        )


def test_builder_placeholders_state_each_default() -> None:
    """The empty-field default differs per type; the placeholder is where it shows."""
    gauge = _element_type_block(_read(BASIC_PROPS), "gauge")
    assert 'placeholder="1"' in gauge, "an unset gauge draws one decimal"
    label = _element_type_block(_read(BASIC_PROPS), "label")
    assert 'placeholder="As reported"' in label, "an unset label prints the value as-is"
    assert "Text is shown exactly as the device reports it" in label, (
        "the label needs the note that only numbers are rounded -- otherwise "
        "setting it on a text label looks broken"
    )


def test_field_comments_name_every_honoring_type() -> None:
    """The stale `slider/fader` comment is what hid this for as long as it did."""
    for path in (PROJECT_LOADER, TYPES_TS):
        declarations = [
            ln for ln in _read(path).splitlines()
            if "display_decimals" in ln and ("#" in ln or "//" in ln)
        ]
        assert len(declarations) == 1, (
            f"expected exactly one commented display_decimals declaration in "
            f"{path.name}, found {len(declarations)}"
        )
        line = declarations[0]
        for el_type in HONORING_TYPES:
            assert el_type in line, (
                f"{path.name} describes display_decimals as {line.strip()!r}, "
                f"which omits {el_type}"
            )
