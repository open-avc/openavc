"use strict";
// Loads the real panel-element config field router
// (components/ui-builder/PropertySections/panelElementConfig.ts, bundled on the
// fly with the esbuild in openavc/web/programmer/node_modules) and checks
// panelElementFieldKind: ref types (state_key/device_ref/macro_ref) route to
// their pickers instead of a plain text box (M-159), and `text` is a textarea
// while `string` is a single-line input, matching the plugin CONFIG_SCHEMA form
// (L-094). Prints JSON results; the Python wrapper skips when the Node
// toolchain or esbuild is absent.
const path = require("path");

const utilsPath = process.argv[2];

const esbuild = require("esbuild");
const built = esbuild.buildSync({
  entryPoints: [utilsPath],
  bundle: true,
  format: "cjs",
  platform: "node",
  write: false,
  logLevel: "silent",
});
const code = built.outputFiles[0].text;
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, utilsPath, path.dirname(utilsPath));
const kind = moduleObj.exports.panelElementFieldKind;

const results = {};
const K = (field) => kind(typeof field === "string" ? { type: field } : field);

{
  // M-159: a panel-element config field declaring a ref type used to fall to a
  // bare text box; it must now route to the matching picker.
  results.m159_ref_types_get_pickers = {
    pass:
      K("state_key") === "state_key" &&
      K("macro_ref") === "macro_ref" &&
      K("device_ref") === "device_ref",
    detail: { state_key: K("state_key"), macro_ref: K("macro_ref"), device_ref: K("device_ref") },
  };
}
{
  // L-094: text is a multi-line textarea, string is single-line — parity with
  // the plugin CONFIG_SCHEMA renderer (the old form treated everything that
  // wasn't boolean/select/number as one undifferentiated text input).
  results.l094_text_is_textarea_string_is_input = {
    pass: K("text") === "textarea" && K("string") === "text",
    detail: { text: K("text"), string: K("string") },
  };
}
{
  // The pre-existing widget types still route correctly, and a select with no
  // options falls back to a text box (can't render an empty dropdown).
  results.existing_widgets_unchanged = {
    pass:
      K("boolean") === "boolean" &&
      K({ type: "select", options: ["a", "b"] }) === "select" &&
      K({ type: "select", options_source: "plugin.x.opts" }) === "select" &&
      K({ type: "select" }) === "text" &&
      K("integer") === "number" &&
      K("float") === "number" &&
      K("something_unknown") === "text",
    detail: {
      selectNoOptions: K({ type: "select" }),
      integer: K("integer"),
      unknown: K("something_unknown"),
    },
  };
}

process.stdout.write(JSON.stringify(results));
