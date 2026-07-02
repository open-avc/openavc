"use strict";
// Loads the UI Builder icon picker helpers (iconPickerHelpers.ts — React-free
// pure logic) bundled on the fly with the esbuild already in
// web/programmer/node_modules, and checks every name the picker can store
// against the symbol ids of the runtime panel sprite (web/panel/icons.svg,
// passed as argv[3]). The panel renders icons by direct sprite lookup
// (`icons.svg#<name>`), so any picker name missing from the sprite is a
// silently broken icon on the panel even though the builder preview (a
// reconstructed lucide-react component) looks fine.
// Mirrors trigger_helpers_harness.cjs. The Python wrapper skips when the Node
// toolchain or esbuild is absent rather than failing the Python-only CI gate.
const fs = require("fs");
const path = require("path");

const helpersPath = process.argv[2];
const spritePath = process.argv[3];

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

// The real runtime sprite's symbol ids — the source of truth the panel
// resolves icon names against.
const spriteText = fs.readFileSync(spritePath, "utf8");
const spriteIds = new Set(
  [...spriteText.matchAll(/<symbol[^>]*\bid="([^"]+)"/g)].map((m) => m[1]),
);

// lucide-react as the builder preview resolves it: the `icons` map first,
// then named exports (aliases) — mirrors getIconComponent in IconPicker.tsx.
const lucide = require("lucide-react");
const iconsMap = lucide.icons || {};
const resolvesInBuilder = (name) => {
  const pascal = H.kebabToPascal(name);
  return Boolean(iconsMap[pascal] ?? lucide[pascal]);
};

const allIcons = H.ALL_ICONS;
const curated = Object.values(H.ICON_CATEGORIES).flat();
const results = {};

// Sanity: the sprite parsed and the picker actually offers a full list.
{
  results.sprite_and_list_nonempty = {
    pass: spriteIds.size > 500 && allIcons.length > 500 && curated.length > 50,
    detail: { sprite: spriteIds.size, all: allIcons.length, curated: curated.length },
  };
}

// --- The H-136 bug: every name the All tab offers must be a real sprite id ---
{
  const missing = allIcons.filter((n) => !spriteIds.has(n));
  results.all_tab_within_sprite = {
    pass: missing.length === 0,
    detail: { missingCount: missing.length, sample: missing.slice(0, 10) },
  };
}

// And the reverse: the All tab should offer everything the sprite can render.
{
  const missing = [...spriteIds].filter((n) => !allIcons.includes(n));
  results.all_tab_covers_sprite = {
    pass: missing.length === 0,
    detail: { missingCount: missing.length, sample: missing.slice(0, 10) },
  };
}

// Digit-containing names are where kebab re-derivation went wrong
// (Building2 -> "building2" while the sprite id is "building-2").
{
  const wantPresent = ["building-2", "clock-2", "axis-3d", "arrow-down-0-1", "grid-3x3"];
  const wantAbsent = ["building2", "clock2", "axis3d", "arrow-down01"];
  const badPresent = wantPresent.filter((n) => !allIcons.includes(n));
  const badAbsent = wantAbsent.filter((n) => allIcons.includes(n));
  results.digit_names_use_sprite_ids = {
    pass: badPresent.length === 0 && badAbsent.length === 0,
    detail: { shouldExistButMissing: badPresent, shouldNotExistButPresent: badAbsent },
  };
}

// --- The M-182 bug: every curated category entry must be a real sprite id ---
{
  const missing = [...new Set(curated.filter((n) => !spriteIds.has(n)))];
  results.curated_within_sprite = {
    pass: missing.length === 0,
    detail: { missing },
  };
}

// Everything the picker offers must also render in the builder preview grid
// (kebab -> PascalCase lookup into lucide-react), or the grid shows holes.
{
  const unresolved = allIcons.filter((n) => !resolvesInBuilder(n));
  results.all_tab_resolves_in_builder = {
    pass: unresolved.length === 0,
    detail: { unresolvedCount: unresolved.length, sample: unresolved.slice(0, 10) },
  };
}
{
  const unresolved = [...new Set(curated.filter((n) => !resolvesInBuilder(n)))];
  results.curated_resolves_in_builder = {
    pass: unresolved.length === 0,
    detail: { unresolved },
  };
}

process.stdout.write(JSON.stringify(results));
