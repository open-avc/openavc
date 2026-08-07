"use strict";
/*
 * jsdom + React harness for the shared anchored panel
 * (web/programmer/src/components/shared/AnchoredPanel.tsx).
 *
 * Five pickers used to carry their own copy of "measure the trigger, decide
 * whether to flip up, place a fixed panel, close on an outside click or
 * scroll", and the copies had drifted: two flip-up thresholds, two width
 * floors, and only two of the five clamped the panel back into the viewport.
 * These are the rules all five now inherit, driven through the real
 * `ParamCombobox` — the one caller with no store or API dependency, so what is
 * exercised here is the panel and nothing else.
 *
 * jsdom has no layout engine, so every getBoundingClientRect is zero. The
 * trigger's rect is stubbed per scenario; that IS the input to the arithmetic
 * under test, so stubbing it is the test, not a shortcut around it.
 *
 * Invoked as: node anchored_panel_harness.cjs <abs path to AnchoredPanel.tsx>
 * Prints JSON results on stdout for tests/test_anchored_panel.py.
 */
const path = require("path");
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const panelPath = process.argv[2];
const resolveDir = path.dirname(panelPath);

const dom = new JSDOM(
  `<!DOCTYPE html><html><body>
     <button id="outside">outside</button>
     <div id="root"></div>
   </body></html>`,
  { url: "http://localhost:8080/programmer", pretendToBeVisual: true },
);
const { window } = dom;
global.window = window;
global.document = window.document;
Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true });
for (const name of [
  "HTMLElement", "HTMLInputElement", "HTMLDivElement", "Element", "Node",
  "Event", "KeyboardEvent", "MouseEvent", "UIEvent", "getComputedStyle",
]) {
  global[name] = window[name];
}
global.requestAnimationFrame = window.requestAnimationFrame = (cb) => { cb(0); return 0; };
global.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
global.IS_REACT_ACT_ENVIRONMENT = true;
// react-colorful (inside the colour popover) observes its own box; jsdom has no
// layout, so a no-op observer is both sufficient and honest here.
global.ResizeObserver = window.ResizeObserver = class {
  observe() {} unobserve() {} disconnect() {}
};

const entry = `
import { createElement, act, useRef } from "react";
import { createRoot } from "react-dom/client";
import { ParamCombobox } from "./ParamCombobox";
import { SearchableDropdown } from "./SearchableDropdown";
import { InlineColorPicker } from "./InlineColorPicker";
import { RoutingMatrix } from "../plugins/surface/RoutingMatrix";
import { useAnchoredPanel } from "./AnchoredPanel";
export { createElement, act, useRef, createRoot, ParamCombobox, SearchableDropdown,
         InlineColorPicker, RoutingMatrix, useAnchoredPanel };
`;
const STUBS = {
  restClient: `
    export async function emitContextAction() {}
    export async function request() { return {}; }
  `,
  connectionStore: `
    const state = () => ({ liveState: globalThis.__liveState || {} });
    export const useConnectionStore = Object.assign((s) => s(state()), { getState: state });
  `,
};
const stubPlugin = {
  name: "q074-panel-stubs",
  setup(build) {
    const filter = new RegExp(`(?:^|/)(${Object.keys(STUBS).join("|")})$`);
    build.onResolve({ filter }, (a) => ({ path: a.path.split("/").pop(), namespace: "q074-stub" }));
    build.onLoad({ filter: /.*/, namespace: "q074-stub" }, (a) => ({
      contents: STUBS[a.path], loader: "tsx", resolveDir,
    }));
  },
};
globalThis.__liveState = {};
async function loadComponents() {
const built = await esbuild.build({
  stdin: { contents: entry, resolveDir, loader: "tsx" },
  bundle: true,
  format: "cjs",
  platform: "node",
  jsx: "automatic",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
  plugins: [stubPlugin],
  write: false,
  logLevel: "silent",
});
const moduleObj = { exports: {} };
new Function("exports", "require", "module", "__filename", "__dirname", built.outputFiles[0].text)(
  moduleObj.exports, require, moduleObj, panelPath, resolveDir,
);
return moduleObj.exports;
}
let h, act, useRef, createRoot, ParamCombobox, SearchableDropdown, InlineColorPicker,
    RoutingMatrix, useAnchoredPanel;

const { document } = window;
const results = {};
function report(name, pass, detail) {
  results[name] = { pass: !!pass, detail: detail === undefined ? null : detail };
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }

// A 1280x800 window, the same reference screen the panel authoring guide uses.
window.innerWidth = 1280;
window.innerHeight = 800;

/* jsdom has no layout engine, so every getBoundingClientRect is zero. Elements
 * declare their box in a `data-rect` attribute and this reads it back — which
 * works for a panel that only exists once it is open, and lets a component
 * under test declare its own intrinsic size in JSX. */
window.HTMLElement.prototype.getBoundingClientRect = function () {
  const raw = this.getAttribute && this.getAttribute("data-rect");
  const rect = raw ? JSON.parse(raw) : { left: 0, top: 0, width: 0, height: 0 };
  return {
    left: rect.left, right: rect.left + rect.width,
    top: rect.top, bottom: rect.top + rect.height,
    width: rect.width, height: rect.height, x: rect.left, y: rect.top,
    toJSON() { return rect; },
  };
};

function stubRect(el, rect) {
  el.setAttribute("data-rect", JSON.stringify(rect));
}

let root = null;
let container = null;
async function mount(node) {
  container = document.createElement("div");
  document.getElementById("root").appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(node); });
}
async function teardown() {
  // A scenario that already failed must not take the rest of the run with it.
  try {
    if (root) await act(async () => { root.unmount(); });
  } catch { /* reported by the scenario itself */ }
  root = null;
  if (container) container.remove();
  container = null;
}

const OPTIONS = Array.from({ length: 30 }, (_, i) => ({
  value: `opt${i}`, label: `Option ${i}`,
}));

/** Mount a ParamCombobox whose input sits at `rect`, and open it. */
async function openComboboxAt(rect) {
  await mount(h(ParamCombobox, { value: "", onChange: () => {}, options: OPTIONS }));
  const input = container.querySelector("input");
  stubRect(input, rect);
  await act(async () => {
    input.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
  return { input, panel: container.querySelector("ul") };
}

/** Mount the real list-dropdown shell with its trigger at `rect`, and open it. */
async function openDropdownAt(rect) {
  await mount(
    h(SearchableDropdown, {
      display: "Pick one",
      searchPlaceholder: "Search...",
      children: () => h("div", { id: "rows" },
        ...OPTIONS.map((o) => h("div", { key: o.value }, o.label))),
    }),
  );
  const trigger = container.querySelector("button");
  stubRect(trigger, rect);
  await act(async () => {
    trigger.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
  const panel = [...container.querySelectorAll("div")]
    .find((d) => d.style.position === "fixed");
  return { trigger, panel };
}

/** An intrinsically-sized popover, like the colour picker: it sets no width and
 *  declares its real box, which is what the panel measures. */
function Popover({ width, height }) {
  const p = useAnchoredPanel({ width: "intrinsic", wantsHeight: height });
  return h("div", { ref: p.containerRef },
    h("button", { ref: p.triggerRef, onClick: p.openPanel, id: "swatch" }, "swatch"),
    p.open
      ? h("div", {
          id: "pop",
          ref: p.panelRef,
          "data-rect": JSON.stringify({ left: 0, top: 0, width, height }),
          style: p.panelStyle,
        }, "popover")
      : null,
  );
}

const tests = {
  // The defect the census found: every other copy ignores a scroll that started
  // inside the panel, this one closed on any scroll at all — so a list longer
  // than the panel could not be scrolled to the bottom.
  async scrolling_the_list_keeps_it_open() {
    const { panel } = await openComboboxAt({ left: 100, top: 200, width: 200, height: 24 });
    assert(panel, "panel must be open");
    await act(async () => {
      panel.dispatchEvent(new window.Event("scroll", { bubbles: false }));
    });
    assert(container.querySelector("ul"), "scrolling inside the panel must not close it");
  },

  async scrolling_the_page_closes_it() {
    await openComboboxAt({ left: 100, top: 200, width: 200, height: 24 });
    await act(async () => {
      document.getElementById("outside").dispatchEvent(new window.Event("scroll", { bubbles: false }));
    });
    assert(!container.querySelector("ul"), "a scroll outside the panel must close it");
  },

  async click_outside_closes() {
    await openComboboxAt({ left: 100, top: 200, width: 200, height: 24 });
    await act(async () => {
      document.getElementById("outside")
        .dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true }));
    });
    assert(!container.querySelector("ul"), "a click outside must close the panel");
  },

  async click_inside_keeps_it_open() {
    const { panel } = await openComboboxAt({ left: 100, top: 200, width: 200, height: 24 });
    await act(async () => {
      panel.dispatchEvent(new window.MouseEvent("mousedown", { bubbles: true }));
    });
    assert(container.querySelector("ul"), "a click inside the panel must not close it");
  },

  // The narrow right-docked properties pane: a 320px panel hung off a 200px
  // trigger at x=1100 would run 140px off the right of a 1280px window.
  async clamped_into_the_viewport_at_the_right_edge() {
    const { panel } = await openDropdownAt({ left: 1100, top: 200, width: 200, height: 24 });
    const left = parseFloat(panel.style.left);
    const width = parseFloat(panel.style.width);
    assert(width === 320, `panel floors at 320px, got ${width}`);
    assert(left === 952, `left must clamp to 1280-320-8=952, got ${left}`);
    assert(left + width <= window.innerWidth - 8, "panel must stay inside the window");
  },

  async unclamped_when_there_is_room() {
    const { panel } = await openDropdownAt({ left: 100, top: 200, width: 200, height: 24 });
    assert(parseFloat(panel.style.left) === 100, "a panel with room keeps the trigger's left");
  },

  async flips_up_near_the_bottom() {
    const { panel } = await openDropdownAt({ left: 100, top: 760, width: 200, height: 24 });
    assert(panel.style.bottom !== "", "a trigger 16px off the bottom must flip up");
    assert(panel.style.top === "", "a flipped panel sets bottom, not top");
    // Anchored above the trigger: 800 - 760 + 4.
    assert(parseFloat(panel.style.bottom) === 44, `bottom must be 44, got ${panel.style.bottom}`);
  },

  async opens_downward_when_there_is_room() {
    const { panel } = await openDropdownAt({ left: 100, top: 100, width: 200, height: 24 });
    assert(panel.style.top !== "", "a trigger with room below opens downward");
    assert(panel.style.bottom === "", "a downward panel sets top, not bottom");
    assert(parseFloat(panel.style.top) === 128, `top must be 124+4, got ${panel.style.top}`);
  },

  // The reason the flip-up threshold is a parameter rather than one constant:
  // a 158px colour popover should not flip up with 200px of room beneath it,
  // and before the merge the two big pickers would have (their 250 is a fact
  // about a list, not about every panel).
  async a_short_panel_does_not_flip_when_it_fits() {
    await mount(h(Popover, { width: 170, height: 130 }));
    const trigger = container.querySelector("#swatch");
    // 200px of room below: less than a list's 250, more than this panel's 130.
    stubRect(trigger, { left: 100, top: 576, width: 24, height: 24 });
    await act(async () => {
      trigger.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    const pop = container.querySelector("#pop");
    assert(pop, "popover must open");
    assert(pop.style.top !== "", "a 130px popover with 200px below must open downward");
    assert(pop.style.bottom === "", "it must not flip up");
  },

  // The colour picker's panel is whatever size a colour wheel is. Estimating
  // that from padding and border by hand is what this replaced -- and the
  // estimate is exactly what the clamp depends on, so a guess that runs small
  // leaves the panel hanging off the edge it was clamped away from.
  async an_intrinsic_panel_is_clamped_by_its_measured_width() {
    await mount(h(Popover, { width: 170, height: 130 }));
    const trigger = container.querySelector("#swatch");
    stubRect(trigger, { left: 1180, top: 200, width: 24, height: 24 });
    await act(async () => {
      trigger.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    const pop = container.querySelector("#pop");
    // 1280 - 170 - 8. Nothing here knows 170 except the panel itself.
    assert(parseFloat(pop.style.left) === 1102,
      `left must clamp off the MEASURED 170px width to 1102, got ${pop.style.left}`);
    assert(pop.style.width === "", "an intrinsic panel must not be given a width");
  },

  // The combobox keeps its input's width. The 320px floor the big pickers want
  // would put a dropdown wider than the field on every param in the pane.
  async a_combobox_panel_stays_its_input_width() {
    const { panel } = await openComboboxAt({ left: 100, top: 200, width: 200, height: 24 });
    assert(parseFloat(panel.style.width) === 200,
      `combobox panel must match its input, got ${panel.style.width}`);
  },

  // Neither of the next two had ever been rendered by a test. They are the call
  // sites that take nothing from the shared panel but its positioning, so "it
  // compiles" was the entire assurance before this. These prove the rewiring --
  // that each still opens, sits on the popover layer, and is left intrinsically
  // sized. They deliberately do NOT re-prove the clamp arithmetic: a real
  // component reports no width in jsdom, so a clamp assertion here would pass
  // whatever the code did. That proof is `an_intrinsic_panel_is_clamped_by_its
  // _measured_width`, where the panel declares a box.
  async the_colour_popover_opens_on_the_shared_panel() {
    await mount(h(InlineColorPicker, { value: "#8ab493", onChange: () => {} }));
    const swatch = container.firstElementChild.firstElementChild; // the colour chip
    stubRect(swatch, { left: 1200, top: 200, width: 22, height: 22 });
    await act(async () => {
      swatch.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    const pop = [...container.querySelectorAll("div")]
      .find((d) => d.style.position === "fixed");
    assert(pop, "the colour popover must open");
    assert(pop.style.zIndex === "1200", `must sit on the popover layer, got ${pop.style.zIndex}`);
    assert(pop.style.width === "", "a colour wheel is not given a width");
    assert(container.querySelector(".react-colorful"), "the colour wheel must render");
  },

  async the_surface_preset_list_opens_on_the_shared_panel() {
    globalThis.__liveState = {};
    await mount(h(RoutingMatrix, {
      layout: { type: "matrix", presets: true, rows_state_pattern: "", cols_state_pattern: "" },
      pluginId: "bench_surface",
      config: { _presets: { "Morning": [], "All Mics Off": [] } },
    }));
    const trigger = container.querySelector("button");
    stubRect(trigger, { left: 1150, top: 300, width: 150, height: 28 });
    await act(async () => {
      trigger.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    });
    const pop = [...container.querySelectorAll("div")]
      .find((d) => d.style.position === "fixed");
    assert(pop, "the preset list must open");
    assert(pop.style.zIndex === "1200", `must sit on the popover layer, got ${pop.style.zIndex}`);
    assert(pop.style.width === "", "a preset list sizes to its longest entry");
    assert(pop.textContent.includes("Morning"), "it must list the presets");
  },

  async a_list_panel_does_flip_in_the_same_spot() {
    const { panel } = await openDropdownAt({ left: 100, top: 576, width: 200, height: 24 });
    assert(panel.style.bottom !== "", "a list wants 250px, so the same 200px gap flips it up");
  },
};

async function main() {
  ({ createElement: h, act, useRef, createRoot, ParamCombobox, SearchableDropdown,
     InlineColorPicker, RoutingMatrix, useAnchoredPanel } = await loadComponents());
  for (const [name, fn] of Object.entries(tests)) {
    try {
      await fn();
      report(name, true);
    } catch (err) {
      report(name, false, err && err.message ? err.message : String(err));
    }
    await teardown();
  }
  process.stdout.write(JSON.stringify(results));
  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(String((err && err.stack) || err));
  process.exit(1);
});
