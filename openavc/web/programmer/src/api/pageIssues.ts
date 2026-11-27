/**
 * What the pages the Builder holds hold that they may not, asked of the
 * platform: an id two controls share, an arrangement that inherits from
 * nothing or in a loop, a control inside itself, a binding written in a shape
 * the panel cannot read.
 *
 * The rule is `openavc/ui/page_structure.py`'s, asked over
 * `POST /api/ui/page-issues` with the pages as they stand here, unsaved. It is
 * the rule the AI's page tools ask before they write and its review reports,
 * so Validate and the AI say the same about the same page; it is not copied
 * here to run, for the reason `commandIssues.ts` gives.
 */
import { request } from "./base";

/** One issue, placed: `page` is null for a master element; `element` and
 *  `layout` are the control and the arrangement it is about, when it is. */
export interface PageIssue {
  code: string;
  message: string;
  page: string | null;
  element: string | null;
  layout: string | null;
}

/** The part of a project the question is about. */
interface HeldUI {
  pages: unknown[];
  master_elements?: unknown[];
}

/**
 * Ask about these pages and master elements. Never throws and never rejects:
 * a failed request answers `undefined`, "nothing known", and Validate gives
 * its other findings without these.
 */
export async function pageIssues(ui: HeldUI): Promise<PageIssue[] | undefined> {
  try {
    const reply = await request<{ issues: PageIssue[] }>("/ui/page-issues", {
      method: "POST",
      body: JSON.stringify({ ui: { pages: ui.pages, master_elements: ui.master_elements ?? [] } }),
    });
    return Array.isArray(reply?.issues) ? reply.issues : undefined;
  } catch {
    return undefined;
  }
}
