"use strict";
// Loads the shared copy-to-clipboard helper (components/shared/clipboard.ts)
// bundled on the fly with the esbuild already in openavc/web/programmer/node_modules
// and drives it under fake navigator/document globals. The IDE's copy
// buttons used to call navigator.clipboard.writeText directly — undefined
// outside a secure context, i.e. on the default plain-HTTP LAN deployment —
// so every copy button threw and did nothing (some still showing
// "Copied!"). The helper must use the Clipboard API when present, fall back
// to the selection/execCommand copy when it isn't (or when it rejects), and
// report success honestly so callers only show their copied feedback when a
// copy happened.
// Mirrors trigger_helpers_harness.cjs. The Python wrapper skips when the
// Node toolchain or esbuild is absent rather than failing the Python-only
// CI gate.
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

const results = {};

// Node ships its own limited `navigator` global; swap in a fake per scenario.
function setNavigator(value) {
  Object.defineProperty(globalThis, "navigator", { value, configurable: true, writable: true });
}

function makeDocument({ execResult = true, execThrows = false, selection = null } = {}) {
  const calls = { appended: [], removed: [], exec: [], selects: 0 };
  const doc = {
    createElement(tag) {
      return {
        tag,
        value: "",
        attrs: {},
        style: {},
        setAttribute(name, v) {
          this.attrs[name] = v;
        },
        select() {
          calls.selects++;
        },
      };
    },
    body: {
      appendChild(el) {
        calls.appended.push(el);
      },
      removeChild(el) {
        calls.removed.push(el);
      },
    },
    getSelection() {
      return selection;
    },
    execCommand(command) {
      calls.exec.push(command);
      if (execThrows) throw new Error("execCommand refused");
      return execResult;
    },
  };
  return { doc, calls };
}

async function scenario(name, run) {
  try {
    results[name] = await run();
  } catch (err) {
    results[name] = { pass: false, detail: `threw: ${err && err.message}` };
  }
}

(async () => {
  // --- Secure context: the Clipboard API is used, no fallback machinery ---
  await scenario("clipboard_api_used_when_available", async () => {
    const written = [];
    setNavigator({ clipboard: { writeText: (t) => (written.push(t), Promise.resolve()) } });
    const { doc, calls } = makeDocument();
    globalThis.document = doc;
    const ok = await H.copyToClipboard("device_12");
    return {
      pass: ok === true && written.length === 1 && written[0] === "device_12" && calls.exec.length === 0,
      detail: { ok, written, exec: calls.exec },
    };
  });

  // --- Plain HTTP: navigator.clipboard is undefined; the copy must still
  // work via the selection fallback (and clean up its textarea) ---
  await scenario("plain_http_copy_still_works", async () => {
    setNavigator({});
    const { doc, calls } = makeDocument();
    globalThis.document = doc;
    const ok = await H.copyToClipboard("proj_main.power");
    const textarea = calls.appended[0];
    return {
      pass:
        ok === true &&
        calls.exec.length === 1 &&
        calls.exec[0] === "copy" &&
        textarea !== undefined &&
        textarea.value === "proj_main.power" &&
        calls.selects === 1 &&
        calls.removed[0] === textarea,
      detail: { ok, calls },
    };
  });

  // --- Clipboard API present but rejecting (unfocused doc / permission):
  // fall back instead of failing or false-succeeding ---
  await scenario("rejected_clipboard_api_falls_back", async () => {
    setNavigator({ clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    const { doc, calls } = makeDocument();
    globalThis.document = doc;
    const ok = await H.copyToClipboard("x");
    return {
      pass: ok === true && calls.exec.length === 1,
      detail: { ok, exec: calls.exec },
    };
  });

  // --- Failure is reported as false, never as success ---
  await scenario("copy_failure_reports_false", async () => {
    setNavigator({});
    const { doc } = makeDocument({ execResult: false });
    globalThis.document = doc;
    const refused = await H.copyToClipboard("a");
    const { doc: doc2 } = makeDocument({ execThrows: true });
    globalThis.document = doc2;
    const threw = await H.copyToClipboard("b");
    return {
      pass: refused === false && threw === false,
      detail: { refused, threw },
    };
  });

  // --- The user's text selection survives the fallback copy ---
  await scenario("selection_restored_after_fallback", async () => {
    const range = { marker: "user-range" };
    const selCalls = { removed: 0, added: [] };
    const selection = {
      rangeCount: 1,
      getRangeAt: () => range,
      removeAllRanges() {
        selCalls.removed++;
      },
      addRange(r) {
        selCalls.added.push(r);
      },
    };
    setNavigator({});
    const { doc } = makeDocument({ selection });
    globalThis.document = doc;
    const ok = await H.copyToClipboard("y");
    return {
      pass: ok === true && selCalls.removed === 1 && selCalls.added[0] === range,
      detail: { ok, selCalls },
    };
  });

  process.stdout.write(JSON.stringify(results));
})();
