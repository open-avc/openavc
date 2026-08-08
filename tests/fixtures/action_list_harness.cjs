"use strict";
/*
 * jsdom + React harness for the shared action list
 * (openavc/web/programmer/src/components/shared/ActionListEditor.tsx) as both press
 * editors now use it.
 *
 * The point of the extraction was that the two editors had drifted into
 * offering different things for the same action: the slider side could Test one
 * and not reorder it, the button side could reorder and not Test. So these
 * scenarios are deliberately written in pairs — each capability is asserted on
 * BOTH editors, and a pair going half-red is exactly the regression this
 * guards.
 *
 * `ActionPicker` and the API/store modules are stubbed: what is under test is
 * the list mechanic (numbering, add, remove, reorder, Test), not what a picker
 * renders or whether a command reaches a device. The stub records the calls so
 * "the Test button actually sends" is still an assertion and not a guess.
 *
 * Invoked as: node action_list_harness.cjs <abs path to ActionListEditor.tsx>
 * Prints JSON results on stdout for tests/test_action_list_editor.py.
 */
const path = require("path");
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const listPath = process.argv[2];
const resolveDir = path.dirname(listPath);

const dom = new JSDOM(
  `<!DOCTYPE html><html><body><div id="root"></div></body></html>`,
  { url: "http://localhost:8080/programmer", pretendToBeVisual: true },
);
const { window } = dom;
global.window = window;
global.document = window.document;
Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true });
for (const name of [
  "HTMLElement", "HTMLInputElement", "HTMLDivElement", "Element", "Node",
  "Event", "KeyboardEvent", "MouseEvent", "getComputedStyle",
]) {
  global[name] = window[name];
}
global.requestAnimationFrame = window.requestAnimationFrame = (cb) => { cb(0); return 0; };
global.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
global.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.__apiCalls = [];
globalThis.__toasts = [];
globalThis.__liveState = {};

// --- stand-ins for everything that isn't the list ---------------------------
const STUBS = {
  ActionPicker: `
    import { createElement } from "react";
    export function ActionPicker({ value }) {
      return createElement("div", {
        "data-testid": "action-picker",
        "data-action": String(value?.action ?? ""),
        "data-command": String(value?.command ?? ""),
      });
    }
  `,
  restClient: `
    export async function sendCommand(device, command, params) {
      globalThis.__apiCalls.push({ fn: "sendCommand", device, command, params });
    }
    export async function executeMacro(id) {
      globalThis.__apiCalls.push({ fn: "executeMacro", id });
    }
  `,
  toastStore: `
    export function showSuccess(m) { globalThis.__toasts.push(["success", m]); }
    export function showError(m) { globalThis.__toasts.push(["error", m]); }
  `,
  connectionStore: `
    const state = () => ({ liveState: globalThis.__liveState || {} });
    export const useConnectionStore = Object.assign(
      (selector) => selector(state()),
      { getState: state },
    );
  `,
  VariableKeyPicker: `
    import { createElement } from "react";
    export function VariableKeyPicker() { return createElement("div", { "data-testid": "key-picker" }); }
  `,
  FeedbackBindingEditor: `
    import { createElement } from "react";
    export function FeedbackBindingEditor() { return createElement("div", { "data-testid": "feedback" }); }
  `,
};

const stubPlugin = {
  name: "q074-stubs",
  setup(build) {
    const filter = new RegExp(`(?:^|/)(${Object.keys(STUBS).join("|")})$`);
    build.onResolve({ filter }, (args) => ({
      path: args.path.split("/").pop(),
      namespace: "q074-stub",
    }));
    build.onLoad({ filter: /.*/, namespace: "q074-stub" }, (args) => ({
      contents: STUBS[args.path],
      loader: "tsx",
      resolveDir,
    }));
  },
};

const entry = `
import { createElement, act } from "react";
import { createRoot } from "react-dom/client";
import { PressBindingEditor } from "../ui-builder/BindingEditor/PressBindingEditor";
import { ButtonBindingEditor } from "./ButtonBindingEditor";
export { createElement, act, createRoot, PressBindingEditor, ButtonBindingEditor };
`;
// Stub resolution needs a plugin, and plugins need the async API — so the
// bundle is built inside main() rather than at module scope.
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
  moduleObj.exports, require, moduleObj, listPath, resolveDir,
);
return moduleObj.exports;
}

let h, act, createRoot, PressBindingEditor, ButtonBindingEditor;

const { document } = window;
const results = {};
function report(name, pass, detail) {
  results[name] = { pass: !!pass, detail: detail === undefined ? null : detail };
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }

const PROJECT = { devices: [{ id: "amp", name: "Amp", driver: "acme_amp" }], macros: [], ui: { pages: [] } };

let root = null;
let container = null;
async function mount(node) {
  globalThis.__apiCalls = [];
  globalThis.__toasts = [];
  container = document.createElement("div");
  document.getElementById("root").appendChild(container);
  root = createRoot(container);
  await act(async () => { root.render(node); });
}
async function teardown() {
  if (root) await act(async () => { root.unmount(); });
  root = null;
  if (container) container.remove();
  container = null;
}
async function click(el) {
  assert(el, "cannot click a missing element");
  await act(async () => {
    el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
}
const testButtons = () =>
  Array.from(container.querySelectorAll('button[title="Test this action now"]'));
const byTitle = (title) =>
  Array.from(container.querySelectorAll(`button[title="${title}"]`));
const commands = () =>
  Array.from(container.querySelectorAll('[data-testid="action-picker"]'))
    .map((el) => el.getAttribute("data-command"));

const cmd = (command) => ({ action: "device.command", device: "amp", command });

/** The slider-side editor, returning whatever it emitted. */
async function mountPress(actions) {
  const emitted = { value: null, cleared: false };
  await mount(
    h(PressBindingEditor, {
      value: actions,
      project: PROJECT,
      onChange: (v) => { emitted.value = v; },
      onClear: () => { emitted.cleared = true; },
    }),
  );
  return emitted;
}

/** The button-side editor, returning whatever it emitted. */
async function mountButton(press) {
  const emitted = { bindings: null };
  await mount(
    h(ButtonBindingEditor, {
      bindings: { press },
      project: PROJECT,
      onBindingsChange: (b) => { emitted.bindings = b; },
      showLabel: false,
      showFeedback: false,
    }),
  );
  return emitted;
}

/** Open one of the button editor's collapsible action slots. */
async function expandSection(label) {
  const header = Array.from(container.querySelectorAll("button"))
    .find((b) => b.textContent.startsWith(label));
  await click(header);
}

const tests = {
  // --- Test: the capability the button side was missing -------------------
  async slider_change_actions_can_be_tested() {
    await mountPress([cmd("power_on")]);
    assert(testButtons().length === 1, `expected one Test button, got ${testButtons().length}`);
    await click(testButtons()[0]);
    assert(globalThis.__apiCalls.length === 1, "Test must send the command");
    assert(globalThis.__apiCalls[0].command === "power_on", "it must send THIS action's command");
  },

  async button_press_action_can_be_tested() {
    await mountButton([cmd("power_on")]);
    await expandSection("Press Action");
    assert(testButtons().length === 1,
      `a button's press action must be testable too, got ${testButtons().length}`);
    await click(testButtons()[0]);
    assert(globalThis.__apiCalls.length === 1, "Test must send the command");
    assert(globalThis.__apiCalls[0].command === "power_on", "it must send THIS action's command");
  },

  async button_extra_actions_can_be_tested() {
    await mountButton([cmd("power_on"), cmd("input_hdmi1")]);
    // The extras list is always visible; no section to expand.
    const buttons = testButtons();
    assert(buttons.length === 1, `expected the extra action's Test button, got ${buttons.length}`);
    await click(buttons[0]);
    assert(globalThis.__apiCalls[0]?.command === "input_hdmi1",
      `expected input_hdmi1, got ${JSON.stringify(globalThis.__apiCalls)}`);
  },

  async a_half_built_command_offers_no_test() {
    await mountPress([{ action: "device.command", device: "amp" }]);
    assert(testButtons().length === 0,
      "a command with no command name cannot be tested and must not offer it");
  },

  async a_blocked_dollar_ref_reports_instead_of_sending() {
    globalThis.__liveState = {};
    await mountPress([{ ...cmd("set_volume"), params: { level: "$var.missing" } }]);
    await click(testButtons()[0]);
    assert(globalThis.__apiCalls.length === 0, "an unresolvable $ref must not reach the device");
    assert(globalThis.__toasts.some(([kind]) => kind === "error"), "it must say why");
  },

  // --- Reorder: the capability the slider side was missing ----------------
  async slider_change_actions_can_be_reordered() {
    const emitted = await mountPress([cmd("a"), cmd("b"), cmd("c")]);
    assert(commands().join(",") === "a,b,c", `rows out of order: ${commands()}`);
    const downs = byTitle("Move down");
    assert(downs.length === 2, `expected 2 move-down buttons, got ${downs.length}`);
    await click(downs[0]);
    assert(emitted.value.map((a) => a.command).join(",") === "b,a,c",
      `moving the first action down must swap it with the second, got ${JSON.stringify(emitted.value)}`);
  },

  async slider_change_actions_can_be_moved_up() {
    const emitted = await mountPress([cmd("a"), cmd("b"), cmd("c")]);
    const ups = byTitle("Move up");
    assert(ups.length === 2, `expected 2 move-up buttons, got ${ups.length}`);
    await click(ups[1]);
    assert(emitted.value.map((a) => a.command).join(",") === "a,c,b",
      `moving the last action up must swap it with the middle, got ${JSON.stringify(emitted.value)}`);
  },

  async button_extra_actions_can_be_reordered() {
    const emitted = await mountButton([cmd("p"), cmd("e1"), cmd("e2")]);
    const downs = byTitle("Move down");
    assert(downs.length === 1, `expected 1 move-down among the extras, got ${downs.length}`);
    await click(downs[0]);
    const got = emitted.bindings.press.map((a) => a.command).join(",");
    assert(got === "p,e2,e1", `the primary action must stay first, got ${got}`);
  },

  async the_ends_of_the_list_have_no_useless_arrows() {
    await mountPress([cmd("a"), cmd("b"), cmd("c")]);
    assert(byTitle("Move up").length === 2, "the first row cannot move up");
    assert(byTitle("Move down").length === 2, "the last row cannot move down");
  },

  // --- The rest of the mechanic, shared by both ---------------------------
  async a_lone_action_is_not_numbered() {
    await mountPress([cmd("a")]);
    assert(!container.textContent.includes("Action 1"),
      "one action needs no number; the caption only earns its place from two");
  },

  async several_actions_are_numbered_from_one() {
    await mountPress([cmd("a"), cmd("b")]);
    assert(container.textContent.includes("Action 1"), "expected Action 1");
    assert(container.textContent.includes("Action 2"), "expected Action 2");
  },

  async button_extras_are_numbered_after_the_primary() {
    await mountButton([cmd("p"), cmd("e1")]);
    assert(container.textContent.includes("Action 2"),
      "the first EXTRA is the button's second action, so it reads Action 2");
    assert(!container.textContent.includes("Action 1"),
      "the primary action is its own section and is not numbered here");
  },

  async removing_one_of_several_keeps_the_rest() {
    const emitted = await mountPress([cmd("a"), cmd("b"), cmd("c")]);
    const removes = Array.from(container.querySelectorAll("button"))
      .filter((b) => b.textContent === "Remove");
    assert(removes.length === 3, `expected a Remove per row, got ${removes.length}`);
    await click(removes[1]);
    assert(emitted.value.map((a) => a.command).join(",") === "a,c",
      `expected a,c got ${JSON.stringify(emitted.value)}`);
  },

  async remove_binding_clears_the_whole_slot() {
    const emitted = await mountPress([cmd("a")]);
    const btn = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent === "Remove Binding");
    await click(btn);
    assert(emitted.cleared, "Remove Binding must clear the binding, not empty the list");
  },

  async adding_appends_an_empty_action() {
    const emitted = await mountPress([cmd("a")]);
    const add = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent.includes("Add another action"));
    await click(add);
    assert(emitted.value.length === 2, "add must append");
    assert(emitted.value[1].action === "", "the appended action starts unconfigured");
  },

  async a_half_built_action_cannot_be_added_to() {
    await mountPress([{ action: "" }]);
    const add = Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent.includes("Add another action"));
    assert(!add, "finish the action you started before adding another");
  },
};

async function main() {
  ({ createElement: h, act, createRoot, PressBindingEditor, ButtonBindingEditor } =
    await loadComponents());
  for (const [name, fn] of Object.entries(tests)) {
    try {
      globalThis.__liveState = {};
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
