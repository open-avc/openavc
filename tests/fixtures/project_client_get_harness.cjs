"use strict";
// Bundles the Programmer SPA's api/projectClient.ts (with api/base.ts) using the
// esbuild already in openavc/web/programmer/node_modules and drives getProject() against
// a fake fetch + Worker. Focus: the >512 KB worker-parse path's onerror
// fallback. If the worker errors AND the body is malformed, the old fallback
// did `resolve(JSON.parse(text))` inline — the throw escaped the handler and
// the promise never settled, hanging the IDE's load. Scenarios assert the
// promise now SETTLES (rejects) on a malformed body, still resolves the
// fallback for a valid body, and resolves the normal worker-success path.
// Prints JSON results to stdout; the Python wrapper skips when Node/esbuild is
// absent.
const path = require("path");

const projectClientPath = process.argv[2];

const esbuild = require("esbuild");

function load(entry) {
  const built = esbuild.buildSync({
    entryPoints: [entry],
    bundle: true,
    format: "cjs",
    platform: "node",
    write: false,
    logLevel: "silent",
    // getProject builds `new URL("../workers/...", import.meta.url)`; give
    // import.meta.url a valid base so the (faked) Worker can construct.
    define: { "import.meta.url": '"file:///harness/projectClient.ts"' },
  });
  const code = built.outputFiles[0].text;
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
  fn(moduleObj.exports, require, moduleObj, entry, path.dirname(entry));
  return moduleObj.exports;
}

// --- Fake browser globals BEFORE loading the bundle (api/base.ts reads
// window.location.pathname at module scope) ---
let nextBody = "";
let nextEtag = null;
global.window = {
  location: {
    href: "http://192.168.4.10:8080/programmer",
    origin: "http://192.168.4.10:8080",
    pathname: "/programmer",
  },
};
global.fetch = async () => ({
  ok: true,
  status: 200,
  headers: { get: (k) => (String(k).toLowerCase() === "etag" ? nextEtag : null) },
  text: async () => nextBody,
});

// Fake Worker: fires onerror or onmessage on the next microtask, after the
// caller has assigned both handlers and called postMessage.
let workerMode = "error"; // "error" | "success"
let workerSuccessData = null;
class FakeWorker {
  constructor(url, opts) {
    this.url = url;
    this.opts = opts;
    this.onmessage = null;
    this.onerror = null;
    this.terminated = false;
  }
  postMessage() {
    queueMicrotask(() => {
      try {
        if (workerMode === "error") {
          if (this.onerror) this.onerror(new Error("worker boom"));
        } else if (this.onmessage) {
          this.onmessage({ data: { ok: true, data: workerSuccessData } });
        }
      } catch {
        // In a browser a throw inside a Worker event handler does NOT crash the
        // page — it surfaces as an uncaught error and the surrounding promise
        // just never settles. Swallow to reproduce that "hang" faithfully, so
        // the pre-fix bug reads as a non-settling promise, not a process crash.
      }
    });
  }
  terminate() {
    this.terminated = true;
  }
}
global.Worker = FakeWorker;

const P = load(projectClientPath);

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}

// A body comfortably over the 512 KB worker threshold.
const PAD = "x".repeat(600_000);
const VALID_BIG = JSON.stringify({ name: "big", pad: PAD });
const MALFORMED_BIG = '{"name":"big","pad":"' + PAD; // no closing quote/brace

// Await getProject() but never block forever: resolve to a settlement tag, or
// "hang" if it hasn't settled within the window (the pre-fix defect).
async function settleOrHang(promise, ms) {
  let tag = "hang";
  const tracked = promise.then(
    (v) => {
      tag = "resolved";
      return v;
    },
    (e) => {
      tag = "rejected";
      throw e;
    },
  );
  tracked.catch(() => {}); // don't trip unhandledRejection while we race
  await Promise.race([
    tracked.then(() => {}, () => {}),
    new Promise((r) => setTimeout(r, ms)),
  ]);
  return tag;
}

async function main() {
  if (typeof P.getProject !== "function") {
    report("malformed_worker_error_settles_not_hang", false, "getProject not exported");
    process.stdout.write(JSON.stringify(results));
    return;
  }

  // 1) Worker errors + malformed body → the fallback parse throws; the promise
  //    must still SETTLE (reject), not hang.
  workerMode = "error";
  nextBody = MALFORMED_BIG;
  nextEtag = null;
  const tag1 = await settleOrHang(P.getProject(), 1500);
  report(
    "malformed_worker_error_settles_not_hang",
    tag1 === "rejected",
    `settled=${tag1}`,
  );

  // 2) Worker errors + VALID body → main-thread fallback parse succeeds and the
  //    promise resolves with the parsed project (fix must not break this).
  workerMode = "error";
  nextBody = VALID_BIG;
  nextEtag = "etag-123";
  let data2 = null;
  let err2 = null;
  try {
    data2 = await P.getProject();
  } catch (e) {
    err2 = e;
  }
  report(
    "valid_worker_error_falls_back_parse",
    !err2 && !!data2 && data2.name === "big" && data2._etag === "etag-123",
    err2 ? String(err2) : data2 && `${data2.name}/${data2._etag}`,
  );

  // 3) Normal worker success → resolves with the worker's parsed data.
  workerMode = "success";
  workerSuccessData = { name: "from-worker" };
  nextBody = VALID_BIG;
  nextEtag = null;
  let data3 = null;
  let err3 = null;
  try {
    data3 = await P.getProject();
  } catch (e) {
    err3 = e;
  }
  report(
    "worker_success_resolves",
    !err3 && !!data3 && data3.name === "from-worker",
    err3 ? String(err3) : data3 && data3.name,
  );

  process.stdout.write(JSON.stringify(results));
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
