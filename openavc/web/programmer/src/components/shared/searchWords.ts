/** The search rule: how what a person types finds a thing.
 *
 *  What is typed reads as words: lowercased, split at any run of spaces, tabs
 *  or line breaks, nothing empty (`searchWords`). A thing is found when every
 *  word appears, in any case, somewhere inside at least one of its fields,
 *  each word wherever it is: "brindle switch" finds "Brindle Network Switch",
 *  "switch acme" finds it too, and "switch" finds "Switchers"
 *  (`findsEveryWord`; `matchesSearch` does both). Nothing typed finds
 *  everything; a field that is missing or empty finds no word.
 *
 *  Which fields a search reads is its own, and so is the order it lists what
 *  it found in. Asked by Browse Community (`scoreCard`), the Installed list
 *  (`driverMatchesSearch`) and the Add Device driver picker
 *  (`driverPickerShows`). The server's copy is `openavc/utils/search_words.py`,
 *  which the AI's catalog search asks; both run
 *  `tests/fixtures/search_words_cases.json`. */

/** The words of what was typed: lowercased, split at any run of white space,
 *  none empty. Only spaces, or nothing, reads as no words. */
export function searchWords(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

/** Whether every word (as `searchWords` gives them) is inside at least one of
 *  the fields, in any case. No words finds everything. */
export function findsEveryWord(
  words: readonly string[],
  fields: readonly (string | null | undefined)[],
): boolean {
  const texts = fields.filter((f): f is string => !!f).map((f) => f.toLowerCase());
  return words.every((word) => texts.some((text) => text.includes(word)));
}

/** Whether what was typed finds a thing with these fields. */
export function matchesSearch(
  fields: readonly (string | null | undefined)[],
  query: string,
): boolean {
  return findsEveryWord(searchWords(query), fields);
}
