import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { isRegularPage, opensOver, pageTypeOf } from "./pageType";

type Case = { page: { id: string; page_type?: unknown }; reads_as: string };

// tests/test_page_rules_mirror.py holds the page rules to the same cases.
const CASES = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../../tests/fixtures/page_type_cases.json"), "utf-8"),
) as Case[];

describe("a page's type", () => {
  it("reads as the page rules read it", () => {
    for (const { page, reads_as } of CASES) {
      expect(pageTypeOf(page), JSON.stringify(page)).toBe(reads_as);
    }
  });

  it("opens over the page on screen only as an overlay or a sidebar", () => {
    for (const { page, reads_as } of CASES) {
      const over = reads_as === "overlay" || reads_as === "sidebar";
      expect(opensOver(page), JSON.stringify(page)).toBe(over);
      expect(isRegularPage(page), JSON.stringify(page)).toBe(!over);
    }
  });

  it("covers every reading", () => {
    expect(new Set(CASES.map((c) => c.reads_as))).toEqual(new Set(["page", "overlay", "sidebar"]));
  });
});
