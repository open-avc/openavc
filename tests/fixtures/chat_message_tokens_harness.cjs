"use strict";
// Bundles the real ChatMessage.tsx with the esbuild in
// openavc/web/programmer/node_modules and server-renders it to static markup so we can
// prove the assistant footer's token-count guard behaves. The old guard
// `(message.inputTokens || message.outputTokens) && (...)` evaluated to `0`
// when both counts were 0 (0 || 0), and React renders a bare `0` as literal
// text — a stray digit in the footer. `Boolean(...)`-guarding renders nothing.
//
// The render wrapper is fed to esbuild via stdin with resolveDir pointed at
// ChatMessage's own directory, so `./ChatMessage`, react, react-dom/server,
// lucide-react, and react-markdown all resolve against the Programmer SPA's
// node_modules. The `.css` import in MarkdownContent is dropped with the empty
// loader. Prints JSON results to stdout; the Python wrapper skips when
// Node/esbuild is absent.
const path = require("path");
const esbuild = require("esbuild");

const chatMessagePath = process.argv[2]; // absolute path to ChatMessage.tsx
const resolveDir = path.dirname(chatMessagePath);

const entry = `
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { ChatMessage } from "./ChatMessage";

export function render(overrides) {
  const message = Object.assign(
    {
      id: "m1",
      role: "assistant",
      content: "hello world",
      createdAt: "2026-01-01T00:00:00.000Z",
      streaming: false,
    },
    overrides
  );
  return renderToStaticMarkup(createElement(ChatMessage, { message }));
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
fn(moduleObj.exports, require, moduleObj, chatMessagePath, resolveDir);
const { render } = moduleObj.exports;

const results = {};
function report(name, pass, detail) {
  results[name] = { pass, detail: detail === undefined ? null : detail };
}

function main() {
  if (typeof render !== "function") {
    report("render_exported", false, "render wrapper did not export");
    process.stdout.write(JSON.stringify(results));
    return;
  }

  // Both counts present: footer shows them (proves the footer renders at all).
  const both = render({ inputTokens: 42, outputTokens: 7 });
  report(
    "footer_shows_counts",
    both.includes("42 in / 7 out"),
    "expected '42 in / 7 out' in markup"
  );

  // The bug: both counts are 0. `0 || 0` is 0, and React renders a literal 0.
  // With the fix the footer is identical to the no-counts case (nothing shown).
  const zeroZero = render({ inputTokens: 0, outputTokens: 0 });
  const noCounts = render({});
  report(
    "zero_zero_no_stray",
    zeroZero === noCounts,
    "render(0,0) markup must equal render(undefined) markup — a stray '0' means the bug is back"
  );

  // Anchor: with no counts, the token span is absent entirely.
  report(
    "no_counts_no_span",
    !noCounts.includes(" in / "),
    "no-counts render must not contain the token span"
  );

  // The fix must NOT hide a legitimately-present count that happens to be 0:
  // `0 || 56` is truthy, so '0 in / 56 out' still shows.
  const partial = render({ inputTokens: 0, outputTokens: 56 });
  report(
    "partial_zero_shown",
    partial.includes("0 in / 56 out"),
    "a 0/56 count pair must still render '0 in / 56 out'"
  );

  process.stdout.write(JSON.stringify(results));
}

main();
