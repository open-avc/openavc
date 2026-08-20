"""Every control the IDE has today, it still has tomorrow.

A restyle is supposed to change how the Programmer looks and nothing else. The
failure mode that makes that promise worthless is silent: a control stops being
drawn, the screen still looks right, and nobody notices until somebody reaches
for the button that used to copy a macro id.

Nothing else in the suite can see that. A unit test asserts what a component
renders when you ask it to; it cannot tell you that a component stopped being
asked. So this walks the real IDE in a real browser, writes down every
interactive control on every screen -- its tag and the name a person would call
it by -- and fails when one of them is gone.

It records three things per control, and a change to any of them is a diff:

*   that it EXISTS,
*   what it is CALLED (``aria-label``, then ``title``, then ``placeholder``,
    then its visible text), because renaming a control is also a change
    somebody should have decided,
*   whether it is REACHABLE, marked ``[hidden]`` when it is transparent, has
    ``pointer-events: none`` or is invisible. Moving a control to hover-only is
    a real change; it must not read as no change.

The inventory is committed at ``fixtures/ide_affordances.json``. Regenerate it
deliberately, never to make a red run green::

    OPENAVC_REQUIRE_E2E=1 pytest tests/e2e/test_ide_affordance_inventory.py --regen-inventory

A regeneration is a claim that every difference in it is one you meant, so read
the diff before committing it -- the same rule ``golden-master/`` carries.

It reads and never writes. A walk that clicks things to see what they reveal
will eventually click something that CHANGES the project, and then it is not an
inventory, it is an edit -- so the only rows it opens are the ones named in
DETAIL_ROWS.

What this does NOT cover, so nobody reads it as more than it is: dialogs and
menus that open on click, controls that only appear once a row is hovered or an
element selected, anything gated behind a device being online, the detail pane
of any screen not named in DETAIL_ROWS, and the contents of any screen that
needs data this seed project does not have. Those are worth adding; the absence
of a screen from the fixture is not evidence that screen is safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# Skip-gate only: the browser comes from pytest-playwright's session fixtures.
pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Page  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "ide_affordances.json"

RAIL_TIMEOUT = 20_000
#: The IDE lazy-loads each view; give a switched screen time to paint before
#: reading it. Deliberately generous -- a short wait here reads as a removed
#: control, which is the one failure this test must never invent.
SETTLE_MS = 1_200

#: Rail entries that DO something rather than going somewhere. Recorded in the
#: rail inventory, never clicked: starting the simulator spawns a subprocess
#: this test has no business owning.
#:
#: All three spellings, because the button relabels itself by state and the
#: idle one is "Simulate Devices" -- which the first version of this list did
#: not have. So the walk clicked it, started a simulator, went nowhere, and
#: filed the Settings screen it was still looking at under the name
#: "Simulate Devices". The hash check below is what caught that.
RAIL_ACTIONS = {"Simulate Devices", "Starting...", "Stop Simulation"}


# ---------------------------------------------------------------------------
# The seed project — enough content that each screen has something to draw
# ---------------------------------------------------------------------------

def _seed() -> dict[str, Any]:
    """Macros, variables and a page, so the screens are not all empty states.

    An empty screen has almost no controls on it, so an inventory taken
    against one would happily pass while the populated screen lost half its
    buttons. The step rows and their five row actions only exist because there
    is a macro with steps in it.
    """
    return {
        "variables": [
            {"id": "system_power", "type": "string", "default": "off",
             "label": "System Power"},
            {"id": "volume", "type": "number", "default": 40, "label": "Volume"},
        ],
        "macros": [
            {
                "id": "system_on",
                "name": "System On",
                "steps": [
                    {"action": "device.command", "device": "ctrl1",
                     "command": "identify"},
                    {"action": "state.set", "key": "var.system_power",
                     "value": "on"},
                ],
            },
            {
                "id": "system_off",
                "name": "System Off",
                "steps": [
                    {"action": "state.set", "key": "var.system_power",
                     "value": "off"},
                ],
            },
        ],
        "ui": {
            "settings": {"theme": "dark"},
            "pages": [{
                "id": "main", "name": "Main", "page_type": "page",
                "grid": {"columns": 12, "rows": 8},
                "elements": [
                    {"id": "btn_on", "type": "button", "label": "System On",
                     "position": {"x": 0, "y": 0, "w": 3, "h": 2}},
                ],
            }],
            "master_elements": [],
            "page_groups": [],
        },
    }


# ---------------------------------------------------------------------------
# Reading the page
# ---------------------------------------------------------------------------

#: Runs in the page. Returns one string per control, in DOM order.
_COLLECT_JS = r"""
(inRail) => {
  const SEL = 'button,input,select,textarea,a[href],[role="button"],[role="tab"],[role="switch"],[role="checkbox"],[role="option"]';
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();

  const effectivelyHidden = (el) => {
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.display === 'none' || cs.visibility === 'hidden') return true;
      if (parseFloat(cs.opacity) < 0.05) return true;
      if (cs.pointerEvents === 'none') return true;
      node = node.parentElement;
    }
    return false;
  };

  const name = (el) => {
    const aria = clean(el.getAttribute('aria-label'));
    if (aria) return aria;
    const title = clean(el.getAttribute('title'));
    if (title) return title;
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      const ph = clean(el.getAttribute('placeholder'));
      if (ph) return 'placeholder: ' + ph;
      return 'type: ' + (el.getAttribute('type') || 'text');
    }
    if (el.tagName === 'SELECT') return 'select';
    const text = clean(el.textContent);
    if (text) return text.slice(0, 60);
    return '(unnamed)';
  };

  const out = [];
  const counted = [];

  const record = (el, tag) => {
    // A zero-box control is not "hidden", it is absent -- an element with no
    // layout at all is usually a portal root or a detached measuring node,
    // not something a person can reach.
    const r = el.getBoundingClientRect();
    if (r.width < 1 && r.height < 1) return;
    const flag = effectivelyHidden(el) ? ' [hidden]' : '';
    counted.push(el);
    out.push(tag + ' ' + name(el) + flag);
  };

  for (const el of document.querySelectorAll(SEL)) {
    if (!!el.closest('nav') !== inRail) continue;
    record(el, el.tagName);
  }

  // Anything the IDE made clickable WITHOUT giving it a role -- an onClick
  // and a pointer cursor on a plain element. A first pass that looked only
  // for real controls recorded the Dashboard as having NONE, when what it
  // actually has is one: a <strong> styled as a link. Over-recording is the
  // safe direction here -- a false positive sits in the fixture and stays
  // stable, where a missed control is the whole failure this test exists to
  // prevent.
  for (const el of document.querySelectorAll('*')) {
    if (!!el.closest('nav') !== inRail) continue;
    if (getComputedStyle(el).cursor !== 'pointer') continue;
    if (el.closest(SEL)) continue;                       // inside a real control
    if (counted.some((c) => c.contains(el) || el.contains(c))) continue;
    record(el, 'CLICKABLE');
  }
  return out;
}
"""


def _collect(page: Page, *, in_rail: bool = False) -> list[str]:
    return list(page.evaluate(_COLLECT_JS, in_rail))


def _rail_labels(page: Page) -> list[str]:
    return list(page.evaluate(
        """() => [...document.querySelectorAll('nav button')]
                 .map(b => (b.getAttribute('aria-label') || b.getAttribute('title') || '').trim())
                 .filter(Boolean)"""
    ))


def _subtabs(page: Page) -> list[str]:
    """Names of the sub-tab strip on the current screen, outside the rail."""
    return list(page.evaluate(
        """() => [...document.querySelectorAll('[role="tab"]')]
                 .filter(t => !t.closest('nav'))
                 .map(t => (t.textContent || '').replace(/\\s+/g,' ').trim())
                 .filter(Boolean)"""
    ))


def _open_subtab(page: Page, label: str) -> bool:
    clicked = page.evaluate(
        """(label) => {
             const t = [...document.querySelectorAll('[role="tab"]')]
               .filter(x => !x.closest('nav'))
               .find(x => (x.textContent || '').replace(/\\s+/g,' ').trim() === label);
             if (!t) return false;
             t.click();
             return true;
           }""",
        label,
    )
    if clicked:
        page.wait_for_timeout(SETTLE_MS)
    return bool(clicked)


#: Screens whose left column is a LIST, and the selector that opens its first
#: row, so the detail pane behind it is recorded too. It matters: the first
#: inventory taken here recorded six controls on Macros, because nothing had
#: opened one -- so the step rows and the five actions on each were not covered
#: at all.
#:
#: Named per screen rather than found by shape, and that is the whole point. A
#: first version guessed at "the topmost control in the left column", which on
#: the UI Builder is the element palette -- so the walk ADDED a button to the
#: page, eleven times over a run, and eventually wedged the app it was supposed
#: to be reading. A test that takes an inventory must not change what it is
#: counting. Anything not listed here simply is not row-opened.
DETAIL_ROWS: dict[str, str] = {
    "Macros": '[role="option"]',
    "Devices": 'button:has-text("Test Controller")',
}


def _open_first_row(page: Page, screen: str) -> bool:
    selector = DETAIL_ROWS.get(screen)
    if not selector:
        return False
    row = page.locator(selector).first
    if row.count() == 0:
        return False
    row.click()
    page.wait_for_timeout(SETTLE_MS)
    return True


def _walk(page: Page, base_url: str) -> dict[str, list[str]]:
    """Every rail destination, and every sub-tab on each one."""
    page.goto(f"{base_url}/programmer/", wait_until="domcontentloaded")
    page.locator('nav button').first.wait_for(state="visible", timeout=RAIL_TIMEOUT)
    page.wait_for_timeout(SETTLE_MS)

    inventory: dict[str, list[str]] = {"(rail)": _collect(page, in_rail=True)}
    previous_hash = ""

    for label in _rail_labels(page):
        if label in RAIL_ACTIONS:
            continue
        # Dispatched rather than clicked: the rail shows a hover tooltip that
        # sits over the next button down, and Playwright's actionability check
        # rightly refuses to click through it. Nothing here is testing that the
        # rail is clickable -- it is taking an inventory of what the screen
        # holds once you are on it.
        page.evaluate(
            """(label) => document
                 .querySelector(`nav button[aria-label="${label}"]`)
                 ?.click()""",
            label,
        )
        page.wait_for_timeout(SETTLE_MS)

        # The IDE routes on the URL hash, so the hash is the honest answer to
        # "did we actually leave the last screen?". Without this check a walk
        # that stops navigating -- a dialog in the way, a click that lands on
        # nothing -- records the SAME screen under every later name and calls
        # it an inventory. That happened, and the numbers looked plausible
        # enough to believe: 2,668 controls across 33 screens, most of them
        # the UI Builder wearing another screen's name.
        current_hash = str(page.evaluate("() => location.hash"))
        if current_hash and current_hash == previous_hash:
            raise AssertionError(
                f"Clicking {label!r} in the rail did not leave the previous "
                f"screen (hash stayed {current_hash!r}). The walk is not "
                f"reading what it thinks it is; the inventory would be wrong."
            )
        previous_hash = current_hash

        inventory[label] = _collect(page)

        if _open_first_row(page, label):
            # Keyed by the screen, not by the row's name: the row is whatever
            # the seed project put first, and renaming it must not read as a
            # whole screen appearing and another disappearing.
            inventory[f"{label} [first row]"] = _collect(page)

        tabs = _subtabs(page)
        for tab in tabs[1:]:  # tabs[0] is what the screen already opened on
            if _open_subtab(page, tab):
                inventory[f"{label} > {tab}"] = _collect(page)
        if len(tabs) > 1:
            _open_subtab(page, tabs[0])

    return inventory


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

def _describe(missing: list[str], added: list[str], screen: str) -> list[str]:
    lines = []
    for control in missing:
        lines.append(f"  {screen}: GONE      {control}")
    for control in added:
        lines.append(f"  {screen}: NEW       {control}")
    return lines


def test_no_control_disappears(server_factory, page: Page, request):
    """The IDE's controls, screen by screen, against the committed inventory."""
    handle = server_factory(project_overrides=_seed())
    fresh = _walk(page, handle.base_url)

    if request.config.getoption("--regen-inventory", default=False):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(fresh, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        pytest.skip(f"rewrote {FIXTURE.name} — read the diff before committing it")

    if not FIXTURE.exists():
        pytest.fail(
            f"{FIXTURE} is missing. Take the first inventory with:\n"
            "  OPENAVC_REQUIRE_E2E=1 pytest tests/e2e/test_ide_affordance_inventory.py "
            "--regen-inventory"
        )

    recorded: dict[str, list[str]] = json.loads(FIXTURE.read_text(encoding="utf-8"))

    problems: list[str] = []

    for screen in sorted(set(recorded) - set(fresh)):
        problems.append(f"  WHOLE SCREEN GONE: {screen}")
    for screen in sorted(set(fresh) - set(recorded)):
        problems.append(f"  NEW SCREEN: {screen}")

    for screen in sorted(set(recorded) & set(fresh)):
        was, now = recorded[screen], fresh[screen]
        # Multiset difference: three unnamed buttons becoming two is a loss,
        # and comparing sets would hide it.
        remaining = list(now)
        missing = []
        for control in was:
            if control in remaining:
                remaining.remove(control)
            else:
                missing.append(control)
        if missing or remaining:
            problems.extend(_describe(missing, remaining, screen))

    if problems:
        pytest.fail(
            "The IDE's controls changed.\n\n"
            + "\n".join(problems)
            + "\n\nA control marked GONE is no longer on that screen. If every "
            "line here is a change you meant, re-record with "
            "--regen-inventory and commit the fixture in the same change.\n"
        )
