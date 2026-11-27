import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// A control's id is spelled as the platform's rule says, read from the
// generated table, and the same cases hold both sides
// (tests/fixtures/element_id_cases.json; tests/test_ui_element_id_spelling.py
// runs them against openavc/ui/page_structure.py).

import { ELEMENT_ID_RULE } from "../../api/projectValues.gen";
import { isElementId } from "./elementId";
import { validateElementId } from "./projectRewrites";

interface IdCase {
  name: string;
  id: string;
  expected: boolean;
}

const corpus = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../../tests/fixtures/element_id_cases.json"), "utf-8"),
) as { cases: IdCase[] };

describe("the element id rule", () => {
  it.each(corpus.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(isElementId(c.id)).toBe(c.expected);
  });

  it("a rename says the rule's sentence for every case that breaks it", () => {
    for (const c of corpus.cases) {
      const said = validateElementId(c.id, "btn", [], []);
      if (c.expected) expect(said).toBeNull();
      else expect(said).toBe(c.id ? ELEMENT_ID_RULE.sentence : "ID cannot be empty.");
    }
  });
});
