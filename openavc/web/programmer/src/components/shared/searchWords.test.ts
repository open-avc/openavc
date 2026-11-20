// The search rule's corpus, the one `tests/test_search_words.py` runs against
// the server's copy: what is typed, the words it reads as, and whether those
// words find a thing's fields.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { findsEveryWord, matchesSearch, searchWords } from "./searchWords";

type Case = {
  name: string;
  query: string;
  words: string[];
  fields: (string | null)[];
  finds: boolean;
};

// Read off disk rather than imported: the corpus lives outside this Vite
// project, and vitest runs with openavc/web/programmer as the cwd.
const cases: Case[] = JSON.parse(
  readFileSync(resolve(process.cwd(), "../../../tests/fixtures/search_words_cases.json"), "utf-8"),
).cases;

describe("search rule corpus", () => {
  it("has cases", () => {
    expect(cases.length).toBeGreaterThan(10);
  });

  it.each(cases)("$name: the words", ({ query, words }) => {
    expect(searchWords(query)).toEqual(words);
  });

  it.each(cases)("$name: found or not", ({ query, fields, finds }) => {
    expect(findsEveryWord(searchWords(query), fields)).toBe(finds);
    expect(matchesSearch(fields, query)).toBe(finds);
  });
});
