/**
 * What the pages the Builder holds hold that they may not, asked of the
 * platform: an id two controls share, an arrangement that inherits from
 * nothing or in a loop, a control inside itself, a binding written in a shape
 * the panel cannot read. And each page the project names outside its pages'
 * controls that it does not have: the idle page, a master's pages, a macro's
 * page move.
 *
 * The rules are `openavc/ui/page_structure.py`'s and
 * `openavc/ui/missing_pages.py`'s, asked over
 * `POST /api/ui/page-issues` with the project as it stands here, unsaved. They
 * are the rules the AI's page tools ask before they write and its review
 * reports, so Validate and the AI say the same about the same project; they
 * are not copied here to run, for the reason `commandIssues.ts` gives.
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

/** One place outside a page's controls that names a page that is not there,
 *  placed: `element` is the master, `macro` the macro and `step` the step's
 *  path in it (`steps[2].then_steps[0]`); all three null for the idle page. */
export interface MissingPage {
  code: string;
  message: string;
  element: string | null;
  macro: string | null;
  step: string | null;
}

/** What the platform says of the project. */
export interface HeldAnswer {
  issues: PageIssue[];
  missing_pages: MissingPage[];
}

/** The part of a project the question is about. */
interface HeldProject {
  ui: {
    pages: unknown[];
    master_elements?: unknown[];
    settings?: { idle_page?: unknown };
  };
  macros?: unknown[];
}

/**
 * Ask about this project's pages, master elements, idle page and macros.
 * Never throws and never rejects: a failed request answers `undefined`,
 * "nothing known", and Validate gives its other findings without these.
 */
export async function pageIssues(project: HeldProject): Promise<HeldAnswer | undefined> {
  const { ui } = project;
  try {
    const reply = await request<Partial<HeldAnswer>>("/ui/page-issues", {
      method: "POST",
      body: JSON.stringify({
        ui: {
          pages: ui.pages,
          master_elements: ui.master_elements ?? [],
          settings: { idle_page: ui.settings?.idle_page },
        },
        macros: project.macros ?? [],
      }),
    });
    if (!Array.isArray(reply?.issues)) return undefined;
    const missing = Array.isArray(reply.missing_pages) ? reply.missing_pages : [];
    return { issues: reply.issues, missing_pages: missing };
  } catch {
    return undefined;
  }
}
