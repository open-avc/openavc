"use strict";
// Loads the real Theme Studio colorUtils.ts (transpiled on the fly with the
// esbuild already in openavc/web/programmer/node_modules) and runs a battery of
// pure-logic checks, printing JSON results to stdout. Mirrors panel_harness.cjs:
// no build step required, and the Python wrapper skips when the toolchain is
// absent rather than failing CI.
const fs = require("fs");
const path = require("path");

const colorUtilsPath = process.argv[2];
const src = fs.readFileSync(colorUtilsPath, "utf8");

const esbuild = require("esbuild");
const { code } = esbuild.transformSync(src, { loader: "ts", format: "cjs" });
const moduleObj = { exports: {} };
const fn = new Function("exports", "require", "module", "__filename", "__dirname", code);
fn(moduleObj.exports, require, moduleObj, colorUtilsPath, path.dirname(colorUtilsPath));
const C = moduleObj.exports;

const approx = (a, b, eps) => a != null && Math.abs(a - b) <= (eps == null ? 0.05 : eps);
const results = {};

// M-072: a valid 3-digit hex parses (the old 6-digit-only parser returned null,
// which made ColorPickerCell treat it as "not a color" and reset it to #000000).
{
  const c = C.parseColor("#abc");
  results.m072_three_digit_hex = {
    pass: !!c && c.r === 170 && c.g === 187 && c.b === 204 && c.a === 1,
    detail: c,
  };
}
// M-072: a 3-digit hex normalizes to the 6-digit hex the native <input type=color>
// accepts, so the picker shows it instead of forcing a destructive fallback.
{
  const hex = C.parseColor("#abc") ? C.rgbToHex6(C.parseColor("#abc")) : null;
  results.m072_three_digit_to_hex6 = { pass: hex === "#aabbcc", detail: hex };
}
// rgba alpha is preserved — drives the picker's opaque-native vs translucent-swatch
// branch so a translucent value is never silently flattened to opaque.
{
  const c = C.parseColor("rgba(0, 0, 0, 0.5)");
  results.rgba_alpha = { pass: !!c && c.a === 0.5 && c.r === 0 && c.g === 0 && c.b === 0, detail: c };
}
// "transparent" → null (the checker swatch path; no false color).
results.transparent_null = {
  pass: C.parseColor("transparent") === null,
  detail: C.parseColor("transparent"),
};
// Unparseable garbage → null, no throw.
results.garbage_null = {
  pass: C.parseColor("not-a-color") === null,
  detail: C.parseColor("not-a-color"),
};

// H-036: pure black/white contrast is exactly 21:1 — including via 3-digit hex,
// which the old parser couldn't read.
results.h036_contrast_extreme = {
  pass: approx(C.contrastRatio("#000", "#fff"), 21, 0.01),
  detail: C.contrastRatio("#000", "#fff"),
};
results.h036_contrast_sixdigit = {
  pass: approx(C.contrastRatio("#000000", "#ffffff"), 21, 0.01),
  detail: C.contrastRatio("#000000", "#ffffff"),
};
// H-036: an unparseable / transparent side yields a null ratio → "na", NOT the
// red "fail" the old code conflated null into.
{
  const ratio = C.contrastRatio("transparent", "#ffffff");
  results.h036_transparent_na = {
    pass: ratio === null && C.wcagLevel(ratio) === "na",
    detail: { ratio, level: C.wcagLevel(ratio) },
  };
}
// wcagLevel thresholds, including the new null → "na".
results.h036_levels = {
  pass:
    C.wcagLevel(null) === "na" &&
    C.wcagLevel(21) === "AAA" &&
    C.wcagLevel(5) === "AA" &&
    C.wcagLevel(3) === "fail",
  detail: [C.wcagLevel(null), C.wcagLevel(21), C.wcagLevel(5), C.wcagLevel(3)],
};
// deriveSurfaceBorder still returns a 6-digit hex for a hex input.
results.derive_surface_border = {
  pass: /^#[0-9a-f]{6}$/i.test(C.deriveSurfaceBorder("#2a2a4a")),
  detail: C.deriveSurfaceBorder("#2a2a4a"),
};
// Effective fallback map mirrors :root in panel-elements.css.
results.css_var_fallbacks = {
  pass: C.CSS_VAR_FALLBACKS.panel_bg === "#1a1a2e" && C.CSS_VAR_FALLBACKS.accent === "#2196F3",
  detail: C.CSS_VAR_FALLBACKS.panel_bg,
};

// L-053: isLightColor classifies a theme's panel background by luminance, so the
// light/dark fallback mode is derived from the actual color, not the theme id.
results.l053_is_light = {
  pass:
    C.isLightColor("#f5f5f5") === true &&   // light-modern panel_bg
    C.isLightColor("#1a1a2e") === false &&  // dark-default panel_bg
    C.isLightColor("transparent") === null && // unparseable → caller falls back
    C.isLightColor("not-a-color") === null,
  detail: [
    C.isLightColor("#f5f5f5"),
    C.isLightColor("#1a1a2e"),
    C.isLightColor("transparent"),
    C.isLightColor("not-a-color"),
  ],
};

process.stdout.write(JSON.stringify(results));
