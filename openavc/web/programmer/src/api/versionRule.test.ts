// The version rule's corpus, the one `tests/test_version_rule.py` runs against
// the server's copy: every pair of rows is a case, equal within a class and
// ordered by class across them.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { compareVersions, isPrerelease, readVersion } from "./versionRule";

type Corpus = {
  ascending: { versions: string[]; prerelease: boolean }[];
  unreadable: unknown[];
};

// Read off disk rather than imported: the corpus lives outside this Vite
// project, and vitest runs with openavc/web/programmer as the cwd.
const corpus: Corpus = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../../tests/fixtures/version_rule_cases.json"), "utf-8"),
);

const rows = corpus.ascending.flatMap((cls, rank) =>
  cls.versions.map((text) => ({ rank, text, prerelease: cls.prerelease })),
);

describe("version rule corpus", () => {
  it("has rows of every kind", () => {
    expect(rows.some((r) => r.prerelease)).toBe(true);
    expect(rows.some((r) => !r.prerelease)).toBe(true);
    expect(corpus.unreadable.length).toBeGreaterThan(0);
  });

  it.each(rows)("reads $text", ({ text, prerelease }) => {
    const version = readVersion(text);
    expect(version).not.toBeNull();
    expect(isPrerelease(version!)).toBe(prerelease);
  });

  it("orders every pair by class", () => {
    for (const a of rows) {
      for (const b of rows) {
        expect([a.text, b.text, compareVersions(a.text, b.text)]).toEqual([
          a.text, b.text, Math.sign(a.rank - b.rank),
        ]);
      }
    }
  });

  it.each(corpus.unreadable.map((value) => ({ value })))(
    "reads $value as no version",
    ({ value }) => {
      expect(readVersion(value)).toBeNull();
      for (const row of rows) {
        expect(compareVersions(value, row.text)).toBeNull();
        expect(compareVersions(row.text, value)).toBeNull();
      }
    },
  );
});
