"""Regression tests for a batch of small frontend display fixes.

Five display-layer defects, one per component:

- SystemSettingsView: the settings-saved toast ternary had identical branches,
  so it never mentioned that a restart was pending.
- MeterControl: the 0-1 fraction heuristic included 1 itself, so a channel
  idling at 1 on a 0-100 scale rendered as a pegged 100% bar.
- SliderControl: the value readout used a fixed one-decimal format, so a
  0.01-step slider showed 0.07 as "0.1".
- GenericPanel: the state editor coerced anything Number() accepts, so hex
  strings silently became numbers and overflowing literals (1e999) became
  Infinity, which JSON-serializes to null on the wire.
- ProjectorPanel: the power button sent "off" while the projector was cooling
  (a no-op) and read "Power Off", while the dedicated PowerControl treats
  cooling as "turn back on" — the two surfaces disagreed.

There is no vitest/jest harness in web/programmer or web/simulator, so — like
the other frontend regression tests — these pin the source to the fixed shape.
"""

from __future__ import annotations

from pathlib import Path

# Repo root = openavc/ (this file is openavc/tests/test_frontend_display_fixes.py).
OPENAVC_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_VIEW = OPENAVC_ROOT / "web" / "programmer" / "src" / "views" / "SystemSettingsView.tsx"
SIM_COMPONENTS = OPENAVC_ROOT / "web" / "simulator" / "src" / "components"
METER_CONTROL = SIM_COMPONENTS / "controls" / "MeterControl.tsx"
SLIDER_CONTROL = SIM_COMPONENTS / "controls" / "SliderControl.tsx"
POWER_CONTROL = SIM_COMPONENTS / "controls" / "PowerControl.tsx"
GENERIC_PANEL = SIM_COMPONENTS / "devices" / "GenericPanel.tsx"
PROJECTOR_PANEL = SIM_COMPONENTS / "devices" / "ProjectorPanel.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_settings_saved_toast_mentions_restart() -> None:
    src = _read(SETTINGS_VIEW)
    assert '(needsRestart ? "." : ".")' not in src, (
        "the settings-saved toast ternary must not have identical branches "
        "(the restart hint was dead code)"
    )
    assert "Restart required" in src, (
        "the settings-saved toast must tell the user a restart is pending "
        "when a restart-required setting changed"
    )


def test_meter_treats_exactly_one_as_absolute_level() -> None:
    src = _read(METER_CONTROL)
    assert "raw <= 1 && raw >= 0" not in src, (
        "the fraction heuristic must not include 1 itself (a 0-100 meter "
        "reading 1 rendered as a full bar)"
    )
    assert "raw < 1 && raw >= 0" in src, (
        "values in [0, 1) read as fractions; 1 and above clamp as absolute levels"
    )


def test_slider_readout_precision_follows_step() -> None:
    src = _read(SLIDER_CONTROL)
    assert "value.toFixed(1)" not in src, (
        "the readout must not hard-code one decimal (a 0.01-step slider "
        "showed 0.07 as '0.1')"
    )
    assert "value.toFixed(decimals)" in src and "const decimals" in src, (
        "readout decimals must be derived from the slider step"
    )


def test_generic_panel_rejects_hex_and_nonfinite_numbers() -> None:
    src = _read(GENERIC_PANEL)
    assert "!isNaN(Number(v))" not in src, (
        "the state editor must not use the bare Number() coercion (it turned "
        "hex strings into numbers and 1e999 into Infinity -> null over JSON)"
    )
    assert "Number.isFinite(Number(v))" in src, (
        "numeric coercion must reject non-finite results"
    )
    # The decimal-format guard is what keeps 0x10 a string.
    assert "/^[+-]?" in src, (
        "numeric coercion must be gated on a decimal-looking format check"
    )


def test_projector_panel_power_button_matches_power_control() -> None:
    panel = _read(PROJECTOR_PANEL)
    control = _read(POWER_CONTROL)
    cooling_turns_on = 'power === "off" || power === "cooling" ? "on" : "off"'
    assert cooling_turns_on in panel, (
        "the projector panel power button must treat cooling as 'turn back on'"
    )
    assert 'power === "off" ? "on" : "off"' not in panel, (
        "the projector panel must not send a no-op 'off' while cooling"
    )
    assert '"Power On"' in panel and 'power === "off" || power === "cooling"' in panel, (
        "the button label must read 'Power On' during cool-down"
    )
    # The behavior contract both surfaces share lives in PowerControl.
    assert cooling_turns_on in control, (
        "PowerControl is the reference behavior the panel mirrors; if this "
        "changes, change both surfaces together"
    )
