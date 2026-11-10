/**
 * Which page the UI Builder shows, and what its panels are handed of it.
 *
 * The page on screen is the one selected, else the first (`pageOnScreen`). The
 * page is drawn with the first while nothing is selected yet, and the page drawn
 * is then selected, so the toolbar, the canvas and the stores agree from the
 * next draw.
 * Two things choose a page from outside the Builder: a control another screen
 * pointed at (the navigation store's pending focus, read once as the page
 * opens, the control selected a frame later so its page is drawn first) and, in
 * preview, the server's own page change. The pending focus is read before the
 * first page is chosen, and that choice goes by the selection as the page first
 * drew it, so a control pointed at on another page, before any page was
 * selected, still lands on the first page.
 */
import { useEffect } from "react";
import type { MasterElement, ProjectConfig, UIPage } from "../../api/types";
import * as wsClient from "../../api/wsClient";
import { lockedIdsFor, resolveHidden } from "../../components/ui-builder/uiBuilderHelpers";
import { useNavigationStore } from "../../store/navigationStore";
import { useUIBuilderStore } from "../../store/uiBuilderStore";

export interface ShownPage {
  pages: UIPage[];
  /** The page on screen, or null when the project has none. */
  page: UIPage | null;
  masters: MasterElement[];
  /** Locked controls, the page's and the masters' (a project field, so it
   *  survives a reload). */
  locked: Set<string>;
  /** What the arrangement being authored leaves out, the hides it inherits
   *  included, because that is what the panel would draw. */
  hidden: Set<string>;
}

const store = () => useUIBuilderStore.getState();

/** The page selected, else the first, else none. */
function pageOnScreen(pages: UIPage[], selectedPageId: string | null): UIPage | null {
  return pages.find((p) => p.id === selectedPageId) || pages[0] || null;
}

function shownPage(
  project: ProjectConfig | null,
  selectedPageId: string | null,
  layoutId: string | null,
): ShownPage {
  const pages = project?.ui?.pages ?? [];
  const page = pageOnScreen(pages, selectedPageId);
  const masters = project?.ui?.master_elements || [];
  return {
    pages,
    page,
    masters,
    locked: lockedIdsFor(page ?? undefined, masters),
    hidden: page ? resolveHidden(page, layoutId) : new Set<string>(),
  };
}

/** A control another screen pointed at, on the page it named. */
function goToPointedAt() {
  const focus = useNavigationStore.getState().consumeFocus();
  if (focus?.type !== "element") return;
  const pageId = focus.detail?.startsWith("page:") ? focus.detail.slice(5) : undefined;
  if (pageId) store().selectPage(pageId);
  requestAnimationFrame(() => store().selectElement(focus.id));
}

/** The server's page change, which preview follows. */
function followServerPage() {
  return wsClient.onMessage((msg: Record<string, unknown>) => {
    if (msg.type === "ui.navigate" && msg.page_id) store().selectPage(String(msg.page_id));
  });
}

export function useShownPage(project: ProjectConfig | null): ShownPage {
  const selectedPageId = useUIBuilderStore((s) => s.selectedPageId);
  const activeLayoutId = useUIBuilderStore((s) => s.activeLayoutId);
  const previewMode = useUIBuilderStore((s) => s.previewMode);

  useEffect(goToPointedAt, []);
  useEffect(() => {
    const first = selectedPageId ? null : pageOnScreen(project?.ui?.pages ?? [], null);
    if (first) store().selectPage(first.id);
  }, [selectedPageId, project]);
  useEffect(() => (previewMode ? followServerPage() : undefined), [previewMode]);

  return shownPage(project, selectedPageId, activeLayoutId);
}
