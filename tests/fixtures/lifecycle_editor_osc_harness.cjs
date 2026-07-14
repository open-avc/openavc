"use strict";
// Bundles the real LifecycleEditor.tsx with the esbuild in
// web/programmer/node_modules and server-renders it for a few on_connect
// shapes. Before the fix, the on_connect editor was string[]-only: an OSC
// `{address, args}` item was shown READ-ONLY (a disabled input holding the
// JSON) with no way to author or edit its arguments, even though the runtime
// (_build_osc_args) accepts them. The editor now renders the shared
// OscArgsEditor for OSC "send once" items, so `{address, args}` and even a
// bare OSC address expose the "+ Add Argument" affordance.
//
// The render wrapper is fed to esbuild via stdin with resolveDir at
// LifecycleEditor's own directory, so `./LifecycleEditor`, `./OscArgsEditor`,
// react, react-dom/server, and lucide-react resolve against the Programmer
// SPA's node_modules. Prints JSON results to stdout; the Python wrapper skips
// when Node/esbuild is absent.
const path = require("path");
const esbuild = require("esbuild");

const lifecyclePath = process.argv[2]; // absolute path to LifecycleEditor.tsx
const resolveDir = path.dirname(lifecyclePath);

const entry = `
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { LifecycleEditor } from "./LifecycleEditor";

export function render(draft) {
  return renderToStaticMarkup(
    createElement(LifecycleEditor, { draft, onUpdate: () => {} })
  );
}
`;

const built = esbuild.buildSync({
  stdin: { contents: entry, resolveDir, loader: "tsx" },
  bundle: true,
  format: "cjs",
  platform: "node",
  jsx: "automatic",
  loader: { ".css": "empty" },
  write: false,
  logLevel: "silent",
});

const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, lifecyclePath, resolveDir);
const { render } = moduleObj.exports;

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}

// The "+ Add Argument" button text is unique to the shared OscArgsEditor, so
// it's the definitive marker that the args editor rendered. The old
// string-only editor never imported it.
const ADD_ARG = "+ Add Argument";
// renderToStaticMarkup escapes `"` to `&quot;`, so a JSON-stringified opaque
// item shows up as an escaped `{&quot;address&quot;:...}` in the disabled input.
const OPAQUE_JSON = "&quot;address&quot;";

function main() {
  if (typeof render !== "function") {
    report("render_exported", false, "render wrapper did not export");
    process.stdout.write(JSON.stringify(results));
    return;
  }

  // OSC {address, args}: the args editor is now shown, and the address is an
  // editable value rather than a read-only JSON blob.
  const oscArgs = render({
    transport: "osc",
    child_entity_types: {},
    on_connect: [{ address: "/ch/01/mix/on", args: [{ type: "i", value: "1" }] }],
  });
  report(
    "osc_args_item_shows_args_editor",
    oscArgs.includes(ADD_ARG) && oscArgs.includes("/ch/01/mix/on") && !oscArgs.includes(OPAQUE_JSON),
    "an OSC {address,args} item must render the args editor and an editable address, not read-only JSON"
  );

  // A bare OSC address also exposes the args editor so args can be added.
  const oscBare = render({
    transport: "osc",
    child_entity_types: {},
    on_connect: ["/xremote"],
  });
  report(
    "osc_bare_string_shows_args_editor",
    oscBare.includes(ADD_ARG) && oscBare.includes("/xremote"),
    "a bare OSC address must expose the '+ Add Argument' affordance"
  );

  // The unified model: one OSC item carries BOTH typed args and a when-gate.
  // The args editor and the gate select render together on the same item.
  const oscArgsWhen = render({
    transport: "osc",
    child_entity_types: {},
    config_schema: { enable_meters: { type: "boolean" } },
    on_connect: [
      { address: "/main/mute", args: [{ type: "i", value: "1" }], when: "enable_meters" },
    ],
  });
  report(
    "osc_args_and_when_coexist",
    oscArgsWhen.includes(ADD_ARG) &&
      oscArgsWhen.includes("Only if enable_meters") &&
      oscArgsWhen.includes("/main/mute"),
    "an OSC {address,args,when} item must render both the args editor and the when-gate select"
  );

  // Non-OSC transports never show the args editor.
  const tcpString = render({
    transport: "tcp",
    child_entity_types: {},
    on_connect: ["GET ALL\\r"],
  });
  report(
    "non_osc_string_no_args_editor",
    !tcpString.includes(ADD_ARG),
    "a non-OSC on_connect string must not render an OSC args editor"
  );

  // A non-OSC object step we can't edit inline stays read-only (opaque JSON) so
  // we don't corrupt it — the pre-existing safety behavior, preserved.
  const tcpObject = render({
    transport: "tcp",
    child_entity_types: {},
    on_connect: [{ address: "/x", args: [] }],
  });
  report(
    "non_osc_object_stays_readonly",
    tcpObject.includes(OPAQUE_JSON) && !tcpObject.includes(ADD_ARG),
    "a non-OSC object step must stay read-only, not gain an args editor"
  );

  process.stdout.write(JSON.stringify(results));
}

main();
