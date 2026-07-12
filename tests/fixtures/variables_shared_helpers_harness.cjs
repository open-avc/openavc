"use strict";
// Bundles variablesShared.helpers.ts (with the esbuild in web/programmer/
// node_modules) and exercises the cross-reference helpers behind the
// Variables / Device States "Used By" panels:
//   - scanBindingForVars / scanBindingForAllKeys read the show/do binding model:
//     show.value / show.look key references, and the action lists under each
//     do.<interaction> (press/release/change/...), so button-action and
//     two-way var/state references are surfaced, not silently dropped.
//   - globMatch fully regex-escapes the pattern, so a script-derived key with
//     metacharacters can't crash (SyntaxError) or hang (ReDoS) the view.
//   - collectWildcardMatches resolves a wildcard against an arbitrary candidate
//     set, including device-only keys macros/UI never referenced.
// Prints JSON results to stdout; the Python wrapper skips when the Node
// toolchain or esbuild is absent.
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
const V = moduleObj.exports;

const collectVars = (bindings) => {
  const found = [];
  V.scanBindingForVars(bindings, (id) => found.push(id));
  return found;
};
const collectAllKeys = (bindings) => {
  const found = [];
  V.scanBindingForAllKeys(bindings, (key) => found.push(key));
  return found;
};

const results = {};

// --- H-126: do.<interaction> action lists are scanned ----------------------

// A button whose do.press is an array with a Set Variable action — the action's
// var reference is surfaced.
results.h126_array_press_var_found = (() => {
  const ids = collectVars({
    do: { press: [{ action: "state.set", key: "var.lights", value: true }] },
  });
  return { pass: ids.includes("lights"), detail: ids };
})();

// Multiple actions across multiple do interactions — every var reference is surfaced.
results.h126_array_multi_action = (() => {
  const ids = collectVars({
    do: {
      press: [
        { action: "device.command", device: "amp", command: "on" },
        { action: "state.set", key: "var.scene", value: "movie" },
      ],
      release: [{ action: "state.set", key: "var.held", value: false }],
    },
  });
  return { pass: ids.includes("scene") && ids.includes("held"), detail: ids };
})();

// value_map nested inside a do.change action still resolves.
results.h126_array_value_map = (() => {
  const ids = collectVars({
    do: {
      change: [
        {
          action: "value_map",
          map: { a: { action: "state.set", key: "var.mode", value: 1 } },
        },
      ],
    },
  });
  return { pass: ids.includes("mode"), detail: ids };
})();

// scanBindingForAllKeys gets device.* keys out of a do action list too.
results.h126_array_allkeys_device = (() => {
  const keys = collectAllKeys({
    do: { press: [{ action: "state.set", key: "device.proj.power", value: "on" }] },
  });
  return { pass: keys.includes("device.proj.power"), detail: keys };
})();

// A do.<interaction> holding a single action object (not wrapped in an array)
// still resolves — asActionList tolerates both shapes.
results.h126_legacy_object_still_works = (() => {
  const ids = collectVars({
    do: { press: { action: "state.set", key: "var.legacy", value: 1 } },
  });
  return { pass: ids.includes("legacy"), detail: ids };
})();

// Two-way variable binding lives at show.value with write_back; the var ref is
// found from the value source.
results.h126_two_way_binding = (() => {
  const ids = collectVars({ show: { value: { key: "var.vol", write_back: true } } });
  return { pass: ids.includes("vol"), detail: ids };
})();

// --- M-176: globMatch escapes regex metacharacters -------------------------

// A key with an unbalanced paren used to throw SyntaxError. Now "*" is the only
// special token, everything else is literal, so it matches safely.
results.m176_metachar_no_crash = (() => {
  try {
    const hit = V.globMatch("var.a*b(c", "var.axxb(c");
    const miss = V.globMatch("var.a*b(c", "var.zzz");
    return { pass: hit === true && miss === false, detail: { hit, miss } };
  } catch (e) {
    return { pass: false, detail: `threw: ${e}` };
  }
})();

// A catastrophic-backtracking pattern resolves immediately instead of hanging,
// because the metacharacters are now escaped to literals.
results.m176_no_redos = (() => {
  const start = Date.now();
  let value;
  try {
    value = V.globMatch("var.(a+)+!z", "var." + "a".repeat(60));
  } catch (e) {
    return { pass: false, detail: `threw: ${e}` };
  }
  const elapsed = Date.now() - start;
  return { pass: value === false && elapsed < 1000, detail: { value, elapsed } };
})();

// --- §67: globMatch mirrors the runtime's fnmatch semantics ----------------

// "*" spans dots (like the runtime's fnmatch), so a script subscribing to
// "device.*" covers multi-segment device keys the IDE used to miss.
results.fn_star_spans_dots = (() => {
  const a = V.globMatch("device.*", "device.proj.power");
  const b = V.globMatch("device.*.power", "device.proj.sub.power");
  const c = V.globMatch("device.*", "var.x");
  return { pass: a === true && b === true && c === false, detail: { a, b, c } };
})();

// "?" matches exactly one character.
results.fn_question_single_char = (() => {
  const a = V.globMatch("var.a?c", "var.abc");
  const b = V.globMatch("var.a?c", "var.abbc");
  return { pass: a === true && b === false, detail: { a, b } };
})();

// "[...]" is a character class, including negation.
results.fn_char_class = (() => {
  const a = V.globMatch("var.zone[12]", "var.zone1");
  const b = V.globMatch("var.zone[12]", "var.zone3");
  const neg = V.globMatch("var.zone[!12]", "var.zone3");
  return { pass: a === true && b === false && neg === true, detail: { a, b, neg } };
})();

// An unbalanced "[" is treated literally and never throws.
results.fn_unbalanced_bracket_no_throw = (() => {
  try {
    const a = V.globMatch("var.a[b*", "var.a[bzzz");
    const miss = V.globMatch("var.a[b*", "var.zzz");
    return { pass: a === true && miss === false, detail: { a, miss } };
  } catch (e) {
    return { pass: false, detail: `threw: ${e}` };
  }
})();

// --- L-103: wildcard matches device-only candidate keys --------------------

// A script subscribing to one device's properties ("device.proj.*") must
// annotate that device's state keys even when no macro/UI references them — the
// L-103 fix matches the wildcard against the known device-key set, not just the
// macro/UI-seeded keys.
results.l103_wildcard_matches_device_keys = (() => {
  const hits = V.collectWildcardMatches("device.proj.*", [
    "device.proj.power",
    "device.proj.input",
    "var.lights",
    "device.amp.mute",
  ]);
  return {
    pass:
      hits.includes("device.proj.power") &&
      hits.includes("device.proj.input") &&
      !hits.includes("device.amp.mute") &&
      !hits.includes("var.lights"),
    detail: hits,
  };
})();

results.l103_wildcard_segment_scoped = (() => {
  const hits = V.collectWildcardMatches("device.*.power", [
    "device.proj.power",
    "device.amp.volume",
  ]);
  return { pass: hits.length === 1 && hits[0] === "device.proj.power", detail: hits };
})();

// --- §67 item 2: a var.* wildcard matches the project's variables ----------
results.varmap_wildcard_matches_vars = (() => {
  const hits = V.collectWildcardMatches("var.*", ["var.vol", "var.scene", "device.amp.mute"]);
  return {
    pass:
      hits.includes("var.vol") &&
      hits.includes("var.scene") &&
      !hits.includes("device.amp.mute"),
    detail: hits,
  };
})();

// --- M-277: plugin-action params carry runtime-resolved $var refs ----------
// The macro engine resolves $var in device.command, group.command, AND any
// plugin-registered action's params, but not in the other built-ins. The
// variable rename rewrite + "Used By" scan gate on this predicate, so a var
// referenced only from a plugin-action param is rewritten/counted, not dropped.
results.m277_plugin_action_params_resolve_vars = (() => {
  try {
    const resolves = ["device.command", "group.command", "acme.dim", "widgetplugin.pulse"];
    const skips = ["delay", "state.set", "event.emit", "macro", "conditional", "wait_until", "ui.navigate"];
    const resolvesOk = resolves.every((a) => V.stepParamsResolveVars(a) === true);
    const skipsOk = skips.every((a) => V.stepParamsResolveVars(a) === false);
    const emptyOk = V.stepParamsResolveVars("") === false && V.stepParamsResolveVars(undefined) === false;
    return { pass: resolvesOk && skipsOk && emptyOk, detail: { resolvesOk, skipsOk, emptyOk } };
  } catch (e) {
    return { pass: false, detail: `threw: ${e}` };
  }
})();

process.stdout.write(JSON.stringify(results));
