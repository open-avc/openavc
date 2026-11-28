/**
 * How a panel page is shown, from the page rules (`openavc/ui/page_rules.py`,
 * generated into `projectValues.gen.ts` as `PAGE_RULES`): a regular page in
 * place of the one on screen, or an overlay or a sidebar over it, in a box of
 * its own. A type the page rules do not know, or none, reads as a regular
 * page, which is how the panel draws it.
 *
 * Every Builder check of a page's type asks these: the page tabs and their
 * home marks, the page pickers, the canvas, the page's properties and the
 * page delete's stand-in. `tests/fixtures/page_type_cases.json` holds them and
 * the Python rules to the same answers.
 *
 * The panel's own moves, `BACK` and `DISMISS`, are the page rules' too: every
 * picker that offers them and every summary that names them reads them here.
 */

import { PAGE_RULES } from "../../api/projectValues.gen";

export type PageType = (typeof PAGE_RULES.page_types)[number];

/** Close the overlay on top, or with none open go back a page; and close the
 *  overlay on top, nothing else. Moves, never page ids. */
export const { back: BACK, dismiss: DISMISS } = PAGE_RULES.navigation_words;

const PAGE_TYPES: readonly unknown[] = PAGE_RULES.page_types;
const OPENS_OVER: readonly PageType[] = PAGE_RULES.opens_over;

/** How `page` is shown: its type when the page rules know it, else a regular page. */
export function pageTypeOf(page: { page_type?: unknown }): PageType {
  const written = page.page_type;
  return PAGE_TYPES.includes(written) ? (written as PageType) : PAGE_RULES.unknown_type_reads_as;
}

/** Whether `page` opens over the page on screen: an overlay or a sidebar. */
export function opensOver(page: { page_type?: unknown }): boolean {
  return OPENS_OVER.includes(pageTypeOf(page));
}

/** Whether `page` is shown in place of the page on screen. */
export function isRegularPage(page: { page_type?: unknown }): boolean {
  return !opensOver(page);
}
