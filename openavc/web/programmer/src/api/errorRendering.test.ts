import { describe, it, expect } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Every REST transport throws an `ApiError` whose `.message` is the historical
 * "API <status>: <body>" envelope, so interpolating the thrown value into a
 * toast puts `ApiError: API 400: {"detail":"..."}` in front of a person whose
 * job is aiming a projector. `parseApiError` unwraps it to the sentence the
 * server actually wrote.
 *
 * Fourteen call sites did it the raw way, one of them the Test button, whose
 * entire job is explaining a failure. This is a whole-tree guard rather than a
 * test per site, because the defect is a habit: the raw form is shorter to
 * type and reads fine to whoever writes it, and it stays wrong only for the
 * user.
 */

const SRC = join(__dirname, "..");

// `showError(`... ${e}`)` / `showError(String(e))` and the same for the other
// toasts, for the conventional error identifiers.
//
// `String(e)` counts inside a template too. The first version of this guard
// only knew the bare `${e}` form, so six toasts written as
// `${String(e)}` — four of them siblings of one that unwrapped properly —
// sat in the tree with the guard reporting green over them.
//
// The second version only knew the toasts. A view that keeps its failure in
// state and renders it in a banner does the same thing to the same person, and
// twelve `setError(String(e))` call sites across seven files sat under a green
// guard — including the Cloud Connection page, whose whole job when pairing
// fails is naming which cloud refused and why. `setError` and its
// `setSomethingError` siblings are the same sink, so they are matched too.
const RAW = String.raw`(?:e|err|error)`;
const SINKS = String.raw`(?:show(?:Error|Info|Success)|set[A-Za-z]*Error)`;
const RAW_ERROR_TOAST = new RegExp(
  SINKS + String.raw`\(\s*(?:` +
    String.raw`\`[^\`]*\$\{\s*(?:${RAW}\s*\}|String\(\s*${RAW}\s*\))` +
    String.raw`|String\(\s*${RAW}\s*[\s)])`,
);

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) {
      out.push(...sourceFiles(path));
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(path);
    }
  }
  return out;
}

describe("error rendering", () => {
  it("never puts a raw thrown error in front of a user", () => {
    const offenders: string[] = [];
    for (const path of sourceFiles(SRC)) {
      readFileSync(path, "utf8")
        .split("\n")
        .forEach((line, i) => {
          if (RAW_ERROR_TOAST.test(line)) {
            offenders.push(`${path.slice(SRC.length + 1)}:${i + 1}  ${line.trim()}`);
          }
        });
    }

    expect(
      offenders,
      "these render the raw ApiError envelope; wrap the value in "
        + "parseApiError(e) from api/errors so the user sees the server's sentence",
    ).toEqual([]);
  });

  it("actually looks at the tree it is guarding", () => {
    // A guard that silently scans nothing passes forever. Pin that the walk
    // reaches real files and that the pattern still recognises the shape it
    // was written for.
    const files = sourceFiles(SRC);
    expect(files.length).toBeGreaterThan(100);
    expect(files.some((f) => f.endsWith("ActionListEditor.tsx"))).toBe(true);
    expect(RAW_ERROR_TOAST.test("    showError(`Test failed: ${e}`);")).toBe(true);
    expect(RAW_ERROR_TOAST.test("      showError(String(e));")).toBe(true);
    expect(RAW_ERROR_TOAST.test("      setError(String(e));")).toBe(true);
    expect(RAW_ERROR_TOAST.test("      setUninstallError(String(e));")).toBe(true);
    expect(RAW_ERROR_TOAST.test("    setError(`Failed to queue: ${e}`);")).toBe(true);
    expect(RAW_ERROR_TOAST.test("      setError(parseApiError(e));")).toBe(false);
    expect(RAW_ERROR_TOAST.test("      setError(null);")).toBe(false);
    expect(RAW_ERROR_TOAST.test("    showError(`Install failed: ${String(e)}`);")).toBe(true);
    expect(
      RAW_ERROR_TOAST.test("    showError(String(e instanceof Error ? e.message : e));"),
    ).toBe(true);
    expect(RAW_ERROR_TOAST.test("    showError(`Test failed: ${parseApiError(e)}`);")).toBe(
      false,
    );
    expect(RAW_ERROR_TOAST.test("    showError(`Saved ${errorCount} of them`);")).toBe(false);
    expect(RAW_ERROR_TOAST.test("    showError(parseApiError(e));")).toBe(false);
  });
});
