"use strict";
/*
 * jsdom + React harness for the shared Modal (web/programmer/src/components/shared/Modal.tsx).
 *
 * Bundles the real Modal and the z-index ladder with the esbuild in
 * web/programmer/node_modules, renders them into a jsdom document with
 * react-dom/client, and drives the keyboard and pointer gestures the IDE's
 * dialogs depend on. Everything asserted here used to be twenty separate
 * implementations of, or missing from, each dialog.
 *
 * Invoked as: node modal_harness.cjs <abs path to Modal.tsx>
 * Prints JSON results on stdout for tests/test_modal_behaviour.py.
 */
const path = require("path");
const esbuild = require("esbuild");
const { JSDOM } = require("jsdom");

const modalPath = process.argv[2];
const resolveDir = path.dirname(modalPath);

// --- jsdom, installed as globals before React loads -------------------------
const dom = new JSDOM(
  `<!DOCTYPE html><html><body>
     <button id="opener">open</button>
     <div id="root"></div>
   </body></html>`,
  { url: "http://localhost:8080/programmer", pretendToBeVisual: true },
);
const { window } = dom;
global.window = window;
global.document = window.document;
// Node exposes its own `navigator` as a getter-only global; React only reads it.
Object.defineProperty(global, "navigator", { value: window.navigator, configurable: true });
for (const name of [
  "HTMLElement", "HTMLInputElement", "HTMLTextAreaElement", "Element", "Node",
  "Event", "KeyboardEvent", "MouseEvent", "getComputedStyle",
]) {
  global[name] = window[name];
}
// Run the initial-focus frame synchronously so assertions don't race a timer.
global.requestAnimationFrame = window.requestAnimationFrame = (cb) => { cb(0); return 0; };
global.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
global.IS_REACT_ACT_ENVIRONMENT = true;

// --- bundle the real component ---------------------------------------------
const entry = `
import { createElement, act, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { Modal, openModalCount } from "./Modal";
import { LAYER, modalLayer, MODAL_LAYER_STEP } from "./layers";
export { createElement, act, useState, createRoot, flushSync, Modal, openModalCount, LAYER, modalLayer, MODAL_LAYER_STEP };
`;
const built = esbuild.buildSync({
  stdin: { contents: entry, resolveDir, loader: "tsx" },
  bundle: true,
  format: "cjs",
  platform: "node",
  jsx: "automatic",
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
  write: false,
  logLevel: "silent",
});
const moduleObj = { exports: {} };
new Function("exports", "require", "module", "__filename", "__dirname", built.outputFiles[0].text)(
  moduleObj.exports, require, moduleObj, modalPath, resolveDir,
);
const {
  createElement: h, act, useState, createRoot, flushSync, Modal, openModalCount,
  LAYER, modalLayer, MODAL_LAYER_STEP,
} = moduleObj.exports;

const { document } = window;
const results = {};
function report(name, pass, detail) {
  results[name] = { pass: !!pass, detail: detail === undefined ? null : detail };
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }

function press(key, opts = {}) {
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key, bubbles: true, ...opts }));
}
function click(el) {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
}
function overlayOf(container) { return container.firstElementChild; }
function panelOf(container) { return overlayOf(container).firstElementChild; }

const mounted = [];

async function teardown() {
  while (mounted.length) await mounted.pop()();
}

async function mount(element) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let live = true;
  await act(async () => { root.render(element); });
  const unmount = async () => {
    if (!live) return;
    live = false;
    await act(async () => { root.unmount(); });
    container.remove();
  };
  mounted.push(unmount);
  return {
    container,
    unmount,
    render: async (next) => { await act(async () => { root.render(next); }); },
  };
}

// --- scenarios --------------------------------------------------------------
const tests = {
  // The backdrop, the panel, and where the ARIA lives. The seventeen
  // hand-rolled overlays put role/aria-modal on the backdrop; one had neither.
  async aria_and_structure() {
    const m = await mount(h(Modal, { onClose: () => {}, label: "Test Dialog" }, "body"));
    const overlay = overlayOf(m.container);
    const panel = panelOf(m.container);
    assert(overlay.style.position === "fixed", "overlay must be fixed");
    assert(["0", "0px"].includes(overlay.style.inset),
      `overlay must cover the viewport, got ${overlay.style.inset}`);
    assert(!overlay.getAttribute("role"), "role belongs on the panel, not the backdrop");
    assert(panel.getAttribute("role") === "dialog", "panel must be role=dialog");
    assert(panel.getAttribute("aria-modal") === "true", "panel must be aria-modal");
    assert(panel.getAttribute("aria-label") === "Test Dialog", "panel must carry the label");
    assert(panel.getAttribute("tabindex") === "-1", "panel must be focusable as a fallback");
    await m.unmount();
  },

  // labelledBy wins over label, so a dialog whose heading already names it
  // doesn't end up announced twice.
  async aria_labelledby() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "ignored", labelledBy: "t", describedBy: "d" },
        h("h3", { id: "t" }, "Delete?")),
    );
    const panel = panelOf(m.container);
    assert(panel.getAttribute("aria-labelledby") === "t", "aria-labelledby must be set");
    assert(!panel.getAttribute("aria-label"), "aria-label must give way to aria-labelledby");
    assert(panel.getAttribute("aria-describedby") === "d", "aria-describedby must be set");
    await m.unmount();
  },

  async escape_closes() {
    let closed = 0;
    const m = await mount(h(Modal, { onClose: () => { closed++; }, label: "X" }, "body"));
    press("Escape");
    assert(closed === 1, `Escape must call onClose, got ${closed}`);
    await m.unmount();
  },

  // Fifteen of the seventeen could not be dismissed from the keyboard at all.
  async escape_opt_out() {
    let closed = 0;
    const m = await mount(
      h(Modal, { onClose: () => { closed++; }, closeOnEscape: false, label: "X" }, "body"),
    );
    press("Escape");
    assert(closed === 0, "closeOnEscape=false must swallow Escape");
    await m.unmount();
  },

  // No onClose at all: the update-in-progress case. Neither gesture dismisses.
  async non_dismissible() {
    const m = await mount(h(Modal, { label: "Updating" }, h("button", null, "Close")));
    press("Escape");
    click(overlayOf(m.container));
    assert(m.container.firstElementChild, "a modal with no onClose must stay on screen");
    await m.unmount();
  },

  // THE nesting rule: one Escape closes the top-most modal only. Every old
  // dialog added its own document listener, so they all fired at once and one
  // Escape collapsed the whole stack.
  async escape_only_top_most() {
    const closed = [];
    const m = await mount(
      h(Modal, { onClose: () => closed.push("outer"), label: "Outer" },
        h(Modal, { onClose: () => closed.push("inner"), label: "Inner" }, "confirm")),
    );
    press("Escape");
    assert(closed.length === 1 && closed[0] === "inner",
      `only the inner modal may answer Escape, got ${JSON.stringify(closed)}`);
    await m.unmount();
  },

  // ...and the outer one takes over once the inner is gone.
  async escape_returns_to_outer() {
    const closed = [];
    let inner = true;
    function Stack() {
      return h(Modal, { onClose: () => closed.push("outer"), label: "Outer" },
        inner ? h(Modal, { onClose: () => closed.push("inner"), label: "Inner" }, "confirm") : null);
    }
    const m = await mount(h(Stack));
    inner = false;
    await m.render(h(Stack));
    press("Escape");
    assert(closed.length === 1 && closed[0] === "outer",
      `outer must answer once the inner closed, got ${JSON.stringify(closed)}`);
    await m.unmount();
  },

  // One Escape is consumed by ONE modal, even when answering it changes who
  // is top-most before the next listener runs. Both modals listen on
  // `document`; the inner registers first (React runs a child's effects before
  // its parent's), so the outer's listener runs second — and a browser flushes
  // a discrete event like keydown synchronously, so the inner is already gone
  // by then and the outer would answer the very same keypress. Live in the
  // browser that showed up as Escape closing the discard confirm and instantly
  // re-raising it, which reads as Escape doing nothing at all.
  async one_escape_is_consumed_once() {
    const closed = [];
    // A stable identity, like every call site that passes its own onClose
    // straight through: the outer's key handler is NOT re-subscribed by the
    // re-render, so it is still registered when the event reaches it.
    const outerClose = () => closed.push("outer");
    function Stack() {
      const [innerOpen, setInnerOpen] = useState(true);
      return h(Modal, { onClose: outerClose, label: "Outer" },
        innerOpen
          ? h(Modal, {
              onClose: () => {
                closed.push("inner");
                // flushSync reproduces the browser's synchronous discrete-event
                // flush: the inner is unmounted before the next listener runs.
                flushSync(() => setInnerOpen(false));
              },
              label: "Inner",
            }, "confirm")
          : null);
    }
    const m = await mount(h(Stack));
    press("Escape");
    await act(async () => {});
    assert(closed.length === 1 && closed[0] === "inner",
      `one Escape must reach one modal, got ${JSON.stringify(closed)}`);
    await m.unmount();
  },

  // A modal must still answer Escape when another listener, registered before
  // it, renders in response to the same keypress. Subscribing with `onClose` in
  // the effect deps meant the listener was removed and re-added during that
  // render — and a listener re-added mid-dispatch is never called for that
  // event, so the keypress vanished. That is what the UI Builder's own Escape
  // handler did to every dialog opened over the canvas.
  async escape_survives_a_render_from_another_listener() {
    const closed = [];
    let bump = () => {};
    function Host() {
      const [n, setN] = useState(0);
      bump = () => flushSync(() => setN((x) => x + 1));
      // A fresh arrow every render, exactly like the call sites.
      return h(Modal, { onClose: () => closed.push("modal@" + n), label: "X" }, "body");
    }
    // Registered BEFORE the modal's, like a view's own shortcut handler.
    const viewListener = () => bump();
    document.addEventListener("keydown", viewListener);
    try {
      const m = await mount(h(Host));
      press("Escape");
      await act(async () => {});
      assert(closed.length === 1,
        `the modal must still answer Escape, got ${JSON.stringify(closed)}`);
      await m.unmount();
    } finally {
      document.removeEventListener("keydown", viewListener);
    }
  },

  async backdrop_click_closes() {
    let closed = 0;
    const m = await mount(h(Modal, { onClose: () => { closed++; }, label: "X" }, "body"));
    click(overlayOf(m.container));
    assert(closed === 1, `a click on the backdrop must close, got ${closed}`);
    await m.unmount();
  },

  // A click that lands on anything inside the panel is not a backdrop click,
  // even without the stopPropagation the old dialogs each hand-wrote.
  async panel_click_does_not_close() {
    let closed = 0;
    const m = await mount(
      h(Modal, { onClose: () => { closed++; }, label: "X" }, h("button", { id: "inside" }, "OK")),
    );
    click(document.getElementById("inside"));
    assert(closed === 0, "a click inside the panel must not close the modal");
    await m.unmount();
  },

  // Dialog opts out because most of its call sites hold a half-typed form.
  async backdrop_opt_out() {
    let closed = 0;
    const m = await mount(
      h(Modal, { onClose: () => { closed++; }, closeOnBackdrop: false, label: "X" }, "body"),
    );
    click(overlayOf(m.container));
    assert(closed === 0, "closeOnBackdrop=false must ignore the backdrop");
    await m.unmount();
  },

  async initial_focus_first_focusable() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X" },
        h("button", { id: "one" }, "One"), h("button", { id: "two" }, "Two")),
    );
    assert(document.activeElement.id === "one",
      `focus must land on the first focusable, got ${document.activeElement.id}`);
    await m.unmount();
  },

  // The safety property worth preserving: a destructive confirm focuses
  // Cancel, so Enter by reflex cannot delete anything.
  async initial_focus_selector() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X", initialFocus: "button[data-cancel]" },
        h("button", { "data-confirm": true, id: "confirm" }, "Delete"),
        h("button", { "data-cancel": true, id: "cancel" }, "Cancel")),
    );
    assert(document.activeElement.id === "cancel",
      `initialFocus selector must win, got ${document.activeElement.id}`);
    await m.unmount();
  },

  async initial_focus_none() {
    document.getElementById("opener").focus();
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X", initialFocus: "none" }, h("button", { id: "one" }, "One")),
    );
    assert(document.activeElement.id === "opener", "initialFocus=none must leave focus alone");
    await m.unmount();
  },

  async select_on_focus() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X", initialFocus: "input", selectOnFocus: true },
        h("input", { id: "field", defaultValue: "existing name" })),
    );
    const field = document.getElementById("field");
    assert(document.activeElement === field, "the field must take focus");
    assert(field.selectionStart === 0 && field.selectionEnd === "existing name".length,
      `the prefilled value must be selected, got ${field.selectionStart}..${field.selectionEnd}`);
    await m.unmount();
  },

  async focus_returns_on_close() {
    const opener = document.getElementById("opener");
    opener.focus();
    const m = await mount(h(Modal, { onClose: () => {}, label: "X" }, h("button", null, "One")));
    assert(document.activeElement !== opener, "focus should have moved into the modal");
    await m.unmount();
    assert(document.activeElement === opener,
      `focus must come back to what opened the modal, got ${document.activeElement.id || document.activeElement.tagName}`);
  },

  // Zero of the seventeen trapped focus: Tab walked straight out into the page
  // behind the backdrop.
  async tab_wraps_forward() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X" },
        h("button", { id: "one" }, "One"), h("button", { id: "two" }, "Two")),
    );
    document.getElementById("two").focus();
    press("Tab");
    assert(document.activeElement.id === "one",
      `Tab past the last control must wrap to the first, got ${document.activeElement.id}`);
    await m.unmount();
  },

  async tab_wraps_backward() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "X" },
        h("button", { id: "one" }, "One"), h("button", { id: "two" }, "Two")),
    );
    document.getElementById("one").focus();
    press("Tab", { shiftKey: true });
    assert(document.activeElement.id === "two",
      `Shift+Tab off the first control must wrap to the last, got ${document.activeElement.id}`);
    await m.unmount();
  },

  // Only the top-most modal traps, so the stack's own dialog keeps the cursor.
  async tab_trap_only_top_most() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "Outer" },
        h("button", { id: "outer-one" }, "One"),
        h("button", { id: "outer-two" }, "Two"),
        h(Modal, { onClose: () => {}, label: "Inner" },
          h("button", { id: "inner-one" }, "One"), h("button", { id: "inner-two" }, "Two"))),
    );
    document.getElementById("inner-two").focus();
    press("Tab");
    assert(document.activeElement.id === "inner-one",
      `the inner modal must own the trap, got ${document.activeElement.id}`);
    await m.unmount();
  },

  // The ladder: base < nested < popover < toast, with the nested modal derived
  // from how many are already open rather than hand-picked per call site.
  async z_index_ladder() {
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "Outer" },
        h(Modal, { onClose: () => {}, label: "Inner" }, "confirm")),
    );
    const backdrops = m.container.querySelectorAll("div[style*='position: fixed']");
    assert(backdrops.length === 2, `expected two backdrops, got ${backdrops.length}`);
    const [outer, inner] = backdrops;
    const outerZ = Number(outer.style.zIndex);
    const innerZ = Number(inner.style.zIndex);
    assert(outerZ === LAYER.modal, `base modal must sit at LAYER.modal, got ${outerZ}`);
    assert(innerZ === LAYER.modal + MODAL_LAYER_STEP,
      `a nested modal must sit one step up, got ${innerZ}`);
    assert(innerZ === LAYER.modalNested, "LAYER.modalNested must name that same step");
    assert(LAYER.popover > modalLayer(9), "a popover must clear the deepest modal");
    assert(LAYER.toast > LAYER.popover, "a toast must clear a popover");
    await m.unmount();
  },

  // The stack has to empty out, or a modal opened later would think it was
  // nested forever and Escape would be answered by a dead component.
  async stack_empties_on_unmount() {
    assert(openModalCount() === 0, `stack must start empty, got ${openModalCount()}`);
    const m = await mount(
      h(Modal, { onClose: () => {}, label: "Outer" },
        h(Modal, { onClose: () => {}, label: "Inner" }, "confirm")),
    );
    assert(openModalCount() === 2, `two modals must register, got ${openModalCount()}`);
    await m.unmount();
    assert(openModalCount() === 0, `stack must empty on unmount, got ${openModalCount()}`);
  },
};

async function main() {
  for (const [name, fn] of Object.entries(tests)) {
    if (process.env.MODAL_HARNESS_TRACE) process.stderr.write("-> " + name + "\n");
    try {
      await fn();
      report(name, true);
    } catch (err) {
      report(name, false, err && err.message ? err.message : String(err));
    }
    // A scenario that threw before its own unmount would otherwise leave a
    // modal registered and skew every scenario after it.
    await teardown();
  }
  process.stdout.write(JSON.stringify(results));
  // jsdom's visual mode keeps a timer alive; nothing is left to do.
  process.exit(0);
}

main().catch((err) => {
  process.stderr.write(String((err && err.stack) || err));
  process.exit(1);
});
