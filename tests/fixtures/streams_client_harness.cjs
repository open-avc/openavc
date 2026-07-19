"use strict";
// Bundles the Programmer SPA's api/streamsClient.ts together with api/auth.ts
// (using the esbuild already in web/programmer/node_modules) and exercises the
// stream snapshot fetch: on a claimed instance the snapshot endpoint requires
// auth, and only fetch() carries the Programmer's credential — a native
// <img src> load never does. Scenarios assert the snapshot request rides the
// installed fetch interceptor (Authorization attached), targets a same-origin
// /api path the interceptor matches, converts the JPEG to an object URL, and
// surfaces non-ok responses in the request()-shaped error errorMessage()
// parses. Prints JSON results to stdout; the Python wrapper skips when the
// Node toolchain or esbuild is absent.
const path = require("path");

const streamsPath = process.argv[2];
const authPath = process.argv[3];

const esbuild = require("esbuild");

function load(entry) {
  const built = esbuild.buildSync({
    entryPoints: [entry],
    bundle: true,
    format: "cjs",
    platform: "node",
    write: false,
    logLevel: "silent",
  });
  const code = built.outputFiles[0].text;
  const moduleObj = { exports: {} };
  const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
  fn(moduleObj.exports, require, moduleObj, entry, path.dirname(entry));
  return moduleObj.exports;
}

// --- Fake browser globals BEFORE loading the bundles (api/base.ts computes
// BASE from window.location at module scope) ---
const HREF = "http://192.168.4.10:8080/programmer";
const TOKEN = "tok-URLSAFE_random-VALUE";
const captured = [];
let nextResponse = null;
global.sessionStorage = {
  getItem: (k) => (k === "openavc.programmer.session" ? TOKEN : null),
  setItem() {},
  removeItem() {},
};
global.window = {
  fetch: async (input, init) => {
    captured.push({ input, init });
    return nextResponse;
  },
  location: {
    href: HREF,
    origin: "http://192.168.4.10:8080",
    pathname: "/programmer",
  },
  dispatchEvent() {},
};

const A = load(authPath);
A.installFetchAuth();
// streamsClient calls the bare global fetch; in the browser that IS the
// patched window.fetch, so mirror that wiring here.
global.fetch = global.window.fetch;

const S = load(streamsPath);

const SCENARIOS = [
  "snapshot_carries_credential",
  "snapshot_requests_same_origin_api_path",
  "snapshot_ok_returns_object_url",
  "snapshot_401_throws_api_error",
  "unauthenticated_img_url_export_gone",
];

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}

async function main() {
  URL.createObjectURL = (b) => (b && b.__blob ? "blob:snapshot-1" : "blob:wrong-arg");

  if (typeof S.fetchSnapshot !== "function") {
    for (const name of SCENARIOS) {
      report(name, false, "fetchSnapshot is not exported — snapshot loads via a bare, credential-less URL");
    }
    process.stdout.write(JSON.stringify(results));
    return;
  }

  // The snapshot request must go through the patched fetch with the
  // credential attached, against a same-origin /api path the interceptor
  // actually matches.
  captured.length = 0;
  nextResponse = {
    ok: true,
    status: 200,
    text: async () => "",
    blob: async () => ({ __blob: true }),
  };
  const objUrl = await S.fetchSnapshot("cam 1");
  const call = captured[0];
  const reqUrl = call ? String(call.input) : "";
  const auth =
    call && call.init && call.init.headers
      ? new Headers(call.init.headers).get("Authorization")
      : null;
  report(
    "snapshot_carries_credential",
    auth === `Bearer ${TOKEN}`,
    auth,
  );
  report(
    "snapshot_requests_same_origin_api_path",
    A.isSameOriginApiUrl(reqUrl, HREF) &&
      reqUrl.includes("/api/plugins/video_panel/ext/streams/cam%201/snapshot.jpg"),
    reqUrl,
  );

  // ok → object URL built from the response blob, ready for the <img>.
  report("snapshot_ok_returns_object_url", objUrl === "blob:snapshot-1", objUrl);

  // Non-ok → the request()-shaped "API <status>: <body>" error, so
  // errorMessage() extracts the server's detail for the toast.
  nextResponse = {
    ok: false,
    status: 401,
    text: async () => '{"detail": "Authentication required"}',
    blob: async () => {
      throw new Error("blob read on error response");
    },
  };
  let threw = null;
  try {
    await S.fetchSnapshot("cam1");
  } catch (e) {
    threw = e;
  }
  const parsed = threw ? S.errorMessage(threw) : null;
  report(
    "snapshot_401_throws_api_error",
    threw instanceof Error &&
      /^API 401: /.test(threw.message) &&
      parsed === "Authentication required",
    threw && threw.message,
  );

  // The bare-URL helper must stay gone, so nothing can wire an
  // unauthenticated <img src> to the snapshot endpoint again.
  report(
    "unauthenticated_img_url_export_gone",
    !("snapshotUrl" in S),
    Object.keys(S).join(","),
  );

  process.stdout.write(JSON.stringify(results));
}

main().catch((err) => {
  process.stderr.write(String(err && err.stack ? err.stack : err));
  process.exit(1);
});
