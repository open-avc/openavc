"""A monitored reading is rounded once, in the declaration, for every surface.

A float32 crossing a float64 wire reads 0.08000000566244125. The panel's Label,
slider, fader and gauge have honoured a ``display_decimals`` for a while, so an
author who put that reading on a touch panel could round it -- and the same
author watching the same reading on the Dashboard could not, because the monitor
declaration had no such field. There was nowhere to say it.

The field is on the monitor now, and the point of these tests is that it lands
on all three of the surfaces that hang off one declaration:

* the **Dashboard tile**, through ``monitor_reading`` (the IDE draws it with the
  mirror in ``monitorHelpers.ts``, pinned to this module by the shared corpus in
  ``tests/fixtures/monitor_parity_cases.json`` -- this file does not re-prove
  the two agree, ``test_monitor_parity.py`` does);
* the **alert**, which quotes the reading in a sentence somebody gets on their
  phone, and used to quote all seventeen digits of it;
* the **cloud health card**, which renders through the vendored byte copy of
  ``core/monitors.py`` -- so what is pinned here is that the declaration is
  carried to it, the copy itself being ``scripts/vendor_monitor_rule.py``'s job.

The rounding is a statement about a QUANTITY. A word the author wrote for a
value, a boolean and any text the device reports are shown exactly as they are,
which is the rule the panel's Label already follows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pydantic import ValidationError

from openavc.cloud.alert_monitor import AlertMonitor
from openavc.core import monitors
from openavc.core.event_bus import EventBus
from openavc.core.project_loader import MonitorConfig
from openavc.core.state_store import StateStore

#: The reading that prompted all of this, at the width it actually arrives.
NOISY = 0.08000000566244125

CURRENT = {
    "key": "device.acme_amp_192_168_4_75.ac_line_current",
    "label": "AC Line Current",
    "unit": "A",
    "type": "number",
    "display_decimals": 2,
    "normal_max": 0.05,
    "duration_seconds": 0,
}

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
MONITOR_CONTROL = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "shared"
    / "MonitorControl.tsx"
)
DASHBOARD = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "views" / "DashboardView.tsx"
)


# --- The tile -----------------------------------------------------------------


def test_the_tile_rounds_the_reading_that_caused_this():
    assert monitors.monitor_reading(CURRENT, NOISY) == "0.08 A"


def test_an_undeclared_monitor_is_untouched():
    """Every monitor written before the field existed, and every one whose
    author never asked for rounding. Unset is "as reported", not zero."""
    plain = {k: v for k, v in CURRENT.items() if k != "display_decimals"}
    assert monitors.monitor_reading(plain, NOISY) == "0.08000000566244125 A"


def test_rounding_is_only_ever_about_a_number():
    """A word, a boolean and text are shown as they are."""
    enum = {"key": "device.p.power", "display_decimals": 2,
            "states": {"1": {"label": "Warming"}}}
    assert monitors.monitor_reading(enum, 1) == "Warming"
    assert monitors.monitor_reading({"key": "k", "display_decimals": 2}, True) == "Yes"
    assert monitors.monitor_reading({"key": "k", "display_decimals": 2}, "PA653U") == "PA653U"


def test_the_judgement_reads_the_raw_value_not_the_rounded_one():
    """Rounding is about what a person is shown. A reading outside its limits
    that happens to round to something inside them is still outside them, and a
    tile that went green because of a display setting would be a lie."""
    monitor = dict(CURRENT, display_decimals=0, normal_min=0.5, normal_max=10)
    assert monitors.monitor_reading(monitor, 10.4) == "10 A"
    assert monitors.monitor_status(monitor, 10.4) == monitors.ABNORMAL


def test_a_stray_value_cannot_take_a_tile_down():
    """``toFixed`` throws a RangeError outside 0..100 -- mid-render, on a live
    panel. Both sides clamp to the panel's 0..20 rather than pass it through."""
    assert monitors.display_decimals({"display_decimals": 999}) == 20
    assert monitors.display_decimals({"display_decimals": -3}) == 0
    assert monitors.display_decimals({"display_decimals": "two"}) is None
    assert monitors.display_decimals({}) is None


def test_ties_round_the_way_javascript_rounds_them():
    """Python's own ``f"{x:.0f}"`` rounds halves to even and ``toFixed`` rounds
    them away from zero. A fader sitting on -6.5 dB is not an exotic input, and
    the tile is drawn by the mirror while the health card is drawn by this."""
    half = {"key": "k", "display_decimals": 0}
    assert monitors.monitor_reading(half, 2.5) == "3"
    assert monitors.monitor_reading(half, -6.5) == "-7"
    assert monitors.monitor_reading({"key": "k", "display_decimals": 1}, 0.25) == "0.3"
    # Not a tie at all once the binary value is looked at, and must not become one.
    assert monitors.monitor_reading({"key": "k", "display_decimals": 2}, 2.675) == "2.67"


def test_a_value_that_arrived_as_text_rounds_too():
    """The cloud stores every relayed value as text and deliberately leaves
    numbers that way, so the health card asks this rule to read one."""
    assert monitors.monitor_reading(CURRENT, str(NOISY)) == "0.08 A"


# --- The alert ----------------------------------------------------------------


def test_a_compiled_rule_carries_what_the_reading_reads_as():
    """The alert is compiled here and rendered in the agent, so the declaration
    has to travel with it -- limits, key and duration deliberately left behind."""
    rule = monitors.compile_alert_rules([CURRENT])[0]
    assert rule["display"] == {"unit": "A", "display_decimals": 2, "states": {}}


@pytest.mark.asyncio
async def test_the_alert_sentence_quotes_the_reading_the_tile_shows():
    """The headline. Driven through the real ``compile_alert_rules`` and the
    real AlertMonitor, because the claim is that the two agree."""
    state, events = StateStore(), EventBus()
    agent = _RecordingAgent()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()
    try:
        rules = monitors.compile_alert_rules([CURRENT])
        monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": rules})
        state.set(CURRENT["key"], NOISY)
        fired = await _drain(monitor, agent)

        assert [t for t, _ in fired] == ["alert"], "the limit is exceeded; it must fire"
        message = fired[0][1]["message"]
        assert "0.08 A" in message, message
        assert "0.08000000566244125" not in message, message
        # The raw reading still rides in the detail: that is data for the cloud,
        # not a sentence for a person.
        assert fired[0][1]["detail"]["value"] == NOISY
    finally:
        await monitor.stop()


@pytest.mark.asyncio
async def test_a_rule_pushed_from_the_portal_reads_exactly_as_it_always_did():
    """Nothing declared it, so nothing may be invented for it."""
    state, events = StateStore(), EventBus()
    agent = _RecordingAgent()
    monitor = AlertMonitor(agent, state, events)
    await monitor.start()
    try:
        monitor._on_rules_update_sync("cloud.alert_rules_update", {"rules": [{
            "id": "portal-rule", "name": "Current", "enabled": True,
            "severity": "warning", "category": "device",
            "rule_type": "threshold",
            "condition": {"key": CURRENT["key"], "operator": ">", "value": 0.05},
        }]})
        state.set(CURRENT["key"], NOISY)
        fired = await _drain(monitor, agent)
        assert [t for t, _ in fired] == ["alert"]
        assert "0.08000000566244125" in fired[0][1]["message"]
    finally:
        await monitor.stop()


# --- The authoring door -------------------------------------------------------


def test_the_monitor_form_offers_the_field():
    """A runtime capability the authoring UI hides cannot be reached by hand --
    which is the whole shape of this defect, one layer out: the panel honoured
    ``display_decimals`` and the monitor form had nowhere to type it."""
    src = MONITOR_CONTROL.read_text(encoding="utf-8")
    assert "display_decimals" in src, (
        "the monitor form never offers decimals, so the rounding is unreachable"
    )
    assert 'placeholder="As reported"' in src, (
        "the empty field has to say what unset MEANS, the way the Label's does; "
        "an empty box reads as zero otherwise"
    )


def test_the_form_only_offers_decimals_where_rounding_means_anything():
    """It sits with Unit inside the numeric branch. Offered on a boolean or an
    enum it would be a control that does nothing, on the reading whose author is
    least able to tell."""
    src = MONITOR_CONTROL.read_text(encoding="utf-8")
    numeric_block = src[src.index("{numeric && ("):]
    numeric_block = numeric_block[:numeric_block.index("</div>\n\n")]
    assert "display_decimals" in numeric_block


# --- The tile's own box -------------------------------------------------------


def test_the_tile_cannot_paint_outside_its_card():
    """The other half of this defect, and the reason it was so visible: the key
    line is a `<code>`, and an INLINE element neither contributes width to the
    flex column nor clips -- it simply painted across the value badge and out
    through the right edge of the card. Measured at 311px inside a 320px card.

    Pinned in source because there is no vitest harness for these components.
    """
    src = DASHBOARD.read_text(encoding="utf-8")
    row = src[src.index("function MonitorRow("):]
    row = row[:row.index("\n/** One tile in the dashboard")]
    assert "minWidth: 0" in row and "flex: 1" in row, (
        "the label column must be allowed to shrink AND take the leftover room; "
        "minWidth alone left it sized by its own title"
    )
    assert row.count('display: "block"') >= 1, (
        "the key line must be a block before any overflow rule can apply to it"
    )
    assert "wordBreak: \"break-all\"" in row, (
        "a 45-character state key is the NORMAL length; it wraps rather than "
        "being cut, because the part that identifies the reading is the tail"
    )


# --- Helpers ------------------------------------------------------------------


class _RecordingAgent:
    """Just enough CloudAgent for the AlertMonitor to run against."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, dict]] = []
        self._config = {"features": {"alerts": True}}

    async def send_message(self, msg_type: str, payload: dict) -> None:
        self.sent_messages.append((msg_type, payload))

    def get_config(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    @property
    def connected(self) -> bool:
        return True


async def _drain(monitor: AlertMonitor, agent: _RecordingAgent) -> list:
    """Flush the monitor's pending sends and return the alert traffic."""
    await asyncio.sleep(0)
    async with monitor._pending_lock:
        batch = monitor._pending_sends[:]
        monitor._pending_sends.clear()
    for msg_type, payload in batch:
        await agent.send_message(msg_type, payload)
    out = [(t, p) for t, p in agent.sent_messages if t in ("alert", "alert_resolved")]
    agent.sent_messages.clear()
    return out


def test_the_form_can_never_write_a_value_the_loader_refuses():
    """``display_decimals`` is an integer on the server and Pydantic refuses a
    float with a fraction — so a "2.5" typed into the form would fail the whole
    project's NEXT LOAD, over a display hint. A number input hands back "2.5"
    even when its step says 1, so the form truncates."""
    with pytest.raises(ValidationError):
        MonitorConfig(key="var.n", display_decimals=2.5)
    assert MonitorConfig(key="var.n", display_decimals=2.0).display_decimals == 2

    src = MONITOR_CONTROL.read_text(encoding="utf-8")
    decimals_at = src.index("display_decimals: e.target.value")
    assert "Math.trunc(" in src[decimals_at:decimals_at + 200], (
        "the decimals field must truncate before it writes"
    )
