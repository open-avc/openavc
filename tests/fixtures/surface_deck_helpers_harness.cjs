"use strict";
// Loads the deck helpers (components/plugins/surface/deckHelpers.ts —
// React-free pure logic behind the plugin surface configurator's deck
// workbench) bundled on the fly with the esbuild in
// web/programmer/node_modules, and exercises the rules that have to agree
// with the plugin runtime: pages exist by being referenced, a navigate
// action can hide in a nested off_action/hold_action, and the "start from
// the current zones" seed has to reproduce what the runtime generates from
// the dials on its own.
//
// These lived inside a 6,470-line component file until the surface editor
// was split, which is why they had no test: reaching them meant bundling
// React, the project store and the REST client. Mirrors
// routing_matrix_helpers_harness.cjs; the Python wrapper skips when the
// Node toolchain is absent.
const path = require("path");

const helpersPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [helpersPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, helpersPath, path.dirname(helpersPath));
const H = moduleObj.exports;

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const results = {};

// ── effectivePageCount: a page exists because something references it ──
results.empty_view_is_one_page = H.effectivePageCount({}) === 1;
results.button_page_counts = H.effectivePageCount({ buttons: [{ index: 0, page: 3 }] }) === 4;
results.missing_page_is_page_zero = H.effectivePageCount({ buttons: [{ index: 0 }] }) === 1;
// A name alone keeps a page alive — renaming a page you then emptied must
// not make it vanish from the tab row.
results.page_name_counts = H.effectivePageCount({ page_names: { "2": "Sources" } }) === 3;
// So does a paging rule that targets it.
results.auto_page_rule_counts = H.effectivePageCount({ auto_page: [{ page: 4 }] }) === 5;
// And so does a key that navigates there, which is the one a plain scan of
// the entries would miss.
results.navigate_target_counts =
  H.effectivePageCount({
    buttons: [{ index: 0, bindings: { press: [{ action: "navigate", page: 5 }] } }],
  }) === 6;
// The relative targets are not page indexes and must not inflate the count.
results.relative_navigate_ignored =
  H.effectivePageCount({
    buttons: [
      { index: 0, bindings: { press: [{ action: "navigate", page: "__next_page__" }] } },
      { index: 1, bindings: { press: [{ action: "navigate", page: "__prev_page__" }] } },
    ],
  }) === 1;
// Highest wins, whichever source it came from.
results.highest_reference_wins =
  H.effectivePageCount({
    buttons: [{ index: 0, page: 1 }],
    page_names: { "6": "Cameras" },
    auto_page: [{ page: 2 }],
  }) === 7;

// ── forEachNavigateTarget: every place a navigate can hide ──
const nested = {
  buttons: [
    {
      index: 0,
      bindings: {
        press: [
          { action: "navigate", page: 1 },
          { action: "macro", macro: "lights" },
        ],
      },
    },
    {
      index: 1,
      // A toggle's off_action and a hold_action are nested one level down.
      bindings: {
        press: {
          action: "macro",
          off_action: { action: "navigate", page: 2 },
          hold_action: { action: "navigate", page: 3 },
        },
      },
    },
  ],
  global_buttons: [
    { index: 7, bindings: { press: [{ action: "navigate", page: 4 }] } },
  ],
  dials: [
    {
      index: 0,
      cw: [{ action: "navigate", page: 5 }],
      ccw: { action: "navigate", page: 6 },
      press: [{ action: "navigate", page: 7 }],
    },
  ],
  touchscreen: {
    zones: [
      { touch: [{ action: "navigate", page: 8 }], long_touch: { action: "navigate", page: 9 } },
    ],
  },
};
const seen = [];
H.forEachNavigateTarget(nested, (p) => seen.push(p));
results.navigate_walk_reaches_every_slot = eq(seen.sort((a, b) => a - b), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
results.navigate_walk_counts_pages = H.effectivePageCount(nested) === 10;
results.has_any_navigate_true = H.hasAnyNavigate(nested) === true;
results.has_any_navigate_false =
  H.hasAnyNavigate({ buttons: [{ index: 0, bindings: { press: [{ action: "macro" }] } }] }) === false;
// A single action object (not an array) is a valid binding shape.
results.single_action_object =
  H.hasAnyNavigate({ buttons: [{ index: 0, bindings: { press: { action: "navigate", page: 1 } } }] }) === true;
// Junk in the config must not throw — a hand-edited project file reaches here.
let survivedJunk = true;
try {
  H.effectivePageCount({ buttons: "nonsense", dials: null, touchscreen: 7, auto_page: [null, 3] });
} catch (e) {
  survivedJunk = false;
}
results.malformed_config_survives = survivedJunk;

// ── defaultZonesFromDials: the seed has to match what the runtime draws ──
const dials = [
  {
    index: 0,
    label: "Program",
    icon: "volume-2",
    unit: "dB",
    meter: { enabled: true },
    adjust: { key: "var.program", min: -60, max: 10 },
    fader: true,
    touch: [{ action: "macro", macro: "mute" }],
    long_touch: [{ action: "macro", macro: "reset" }],
  },
  { index: 1, press: [{ action: "macro", macro: "next" }], long_press: [{ action: "macro", macro: "prev" }] },
];
const zones = H.defaultZonesFromDials(dials, 3);
results.one_zone_per_dial = zones.length === 3;
results.zone_carries_dial_fields =
  zones[0].label === "Program" &&
  zones[0].icon === "volume-2" &&
  zones[0].unit === "dB" &&
  zones[0].value_source === "var.program";
// A fader dial's zone drags as a fader — the flag lives on the dial but the
// zone's adjust is what the runtime reads.
results.fader_flag_moves_to_adjust = zones[0].drag_adjust.fader === true;
results.adjust_is_copied_not_shared = zones[0].drag_adjust !== dials[0].adjust;
results.original_dial_adjust_untouched = dials[0].adjust.fader === undefined;
// press/long_press are the older spelling; touch/long_touch win when both exist.
results.press_falls_back_to_touch = eq(zones[1].touch, [{ action: "macro", macro: "next" }]);
results.long_press_falls_back = eq(zones[1].long_touch, [{ action: "macro", macro: "prev" }]);
// A dial with no adjust key gets no drag target at all (rather than an
// adjust that points nowhere).
results.no_adjust_key_no_drag = zones[1].drag_adjust === undefined;
// Dials beyond the assigned ones still get a zone, so the strip is whole.
results.unassigned_dial_gets_blank_zone =
  zones[2].label === undefined && zones[2].value_source === undefined;

// ── networkEntriesOf: only well-formed entries become cards ──
results.no_network_decks_empty = eq(H.networkEntriesOf({}), []);
results.non_array_empty = eq(H.networkEntriesOf({ network_decks: "10.0.0.5" }), []);
results.entry_needs_a_host = eq(
  H.networkEntriesOf({ network_decks: [{ host: "10.0.0.5" }, { port: 5343 }, null, "x"] }),
  [{ host: "10.0.0.5" }],
);
results.entry_key_defaults_port = H.networkEntryKey({ host: "10.0.0.5" }) === "10.0.0.5:5343";
results.entry_key_uses_port = H.networkEntryKey({ host: "10.0.0.5", port: 6000 }) === "10.0.0.5:6000";

// ── addVirtualUnit: adding one never disturbs the units already there ──
const before = { virtual_decks: [{ model: "Studio", serial: "VIRT-AAA" }], buttons: [{ index: 0 }] };
const added = H.addVirtualUnit(before, "XL");
results.virtual_unit_appended = added.next.virtual_decks.length === 2;
results.virtual_unit_keeps_existing = added.next.virtual_decks[0].serial === "VIRT-AAA";
results.virtual_unit_returns_its_serial =
  added.next.virtual_decks[1].serial === added.serial && added.serial.startsWith("VIRT-");
results.virtual_unit_keeps_rest_of_config = eq(added.next.buttons, [{ index: 0 }]);
results.virtual_unit_does_not_mutate = before.virtual_decks.length === 1;
results.virtual_unit_from_empty_config = H.addVirtualUnit({}, "XL").next.virtual_decks.length === 1;

// ── DECK_SECTION_KEYS: what an own layout replaces ──
// The workbench copies exactly these when a deck is given its own layout, so
// a section added to the runtime and forgotten here would silently stay
// shared. Pinned as a set, not an order.
results.deck_sections = eq([...H.DECK_SECTION_KEYS].sort(), [
  "auto_brightness",
  "auto_page",
  "buttons",
  "dials",
  "global_buttons",
  "idle_dim",
  "info_strip",
  "page_names",
  "touchscreen",
]);
results.surface_actions = eq(H.SURFACE_ACTIONS, ["macro", "device.command", "state.set", "navigate"]);

console.log(JSON.stringify(results));
