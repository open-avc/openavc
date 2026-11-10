/**
 * The UI Builder's edits of a control or a master: what each does to the
 * project, as one undo entry (useEditRun.ts), and what is selected after it.
 *
 * The canvas menu, the outline, the Properties panel and the keyboard each
 * offer some of these (`useElementEdits` hands them out), and each surface
 * says which controls it acts on: an edit that can take several (delete,
 * duplicate, copy) takes them as a list, and its words count what it changed.
 * An edit of a control does nothing while there is no page on screen; a
 * master's own edits need none, but promoting and demoting do. Not here:
 * locking a control (the Builder store's own entry) and the canvas's drags and
 * the toolbar's tools, which push their own entries.
 *
 * Page controls and masters share the `ui.<id>` namespace, so a new control
 * (added, duplicated or pasted) avoids every master's id as well as every
 * page's, and a rename is refused onto either.
 */
import type { MasterElement, Placement, ProjectConfig, UIElement, UIPage } from "../../api/types";
import { showError, showInfo, showSuccess } from "../../store/toastStore";
import { useUIBuilderStore, type UndoScope } from "../../store/uiBuilderStore";
import {
  addElementToPage,
  clipboardForSelection,
  createDefaultElement,
  demoteFromMaster,
  duplicateElementsInPage,
  getPlacement,
  moveElementsInPage,
  pasteIntoPage,
  promoteToMaster,
  removeElementFromPage,
  removeMasterElement,
  renameElement,
  reorderElement,
  reparentElement,
  roundPlacement,
  resolveHidden,
  swapElementsInOrder,
  topmostSelection,
  updateElementInPage,
  updateMasterElement,
  validateElementId,
  withHidden,
  type ElementClipboard,
} from "../../components/ui-builder/uiBuilderHelpers";
import { takenIds, type Landing } from "./paletteDrop";
import { projectEdit, type EditRun, type ProjectEdit } from "./useEditRun";

/** An edit of the pages: what they become and what the entry is called. A
 *  run edit (`coalesce`) joins the run in progress, so a held arrow is one
 *  undo entry rather than fifty. */
export type EditPages = (mutate: (pages: UIPage[]) => UIPage[], description: string, coalesce?: boolean) => void;

/** What an edit of the controls reads: the page on screen and where its edit goes. */
export interface OnPage {
  page: UIPage | null;
  editPages: EditPages;
}

/** ... and the arrangement being authored, which decides which box it reads. */
export interface InLayout extends OnPage {
  layoutId: string | null;
}

/** ... and the masters' ids, which a new control may not take. */
export interface Making extends InLayout {
  masterIds: string[];
}

/** What an edit of the masters reads: the project, its masters (an empty list
 *  for none) and the undo run. */
export interface OnProject {
  project: ProjectConfig | null;
  masters: MasterElement[];
  run: EditRun;
}

const store = () => useUIBuilderStore.getState();

/** The words for an edit of `n` controls. */
function counted(verb: string, n: number): string {
  return n === 1 ? `${verb} element` : `${verb} ${n} elements`;
}

export function editPagesOf(
  { project, run }: Pick<OnProject, "project" | "run">,
  ...[mutate, description, coalesce = false]: Parameters<EditPages>
): void {
  if (!project) return;
  const edit = projectEdit(project, { pages: mutate(project.ui.pages) }, description);
  if (coalesce) run.join(edit);
  else run.commit(edit);
}

// --- The controls on the page ---

export function removeControls({ page, editPages }: OnPage, ids: string[]): void {
  if (!page) return;
  editPages(
    (pages) => ids.reduce((result, id) => removeElementFromPage(result, page.id, id), pages),
    counted("Delete", ids.length),
  );
  store().selectElement(null);
}

/** A duplicate copies each container's contents with it, so a selection
 *  holding a container and something inside it makes one copy of that child,
 *  not two. The count in the words is what actually gets copied. */
export function duplicateControls({ page, editPages, layoutId, masterIds }: Making, ids: string[]): void {
  if (!page) return;
  const roots = topmostSelection(page, ids);
  if (roots.length === 0) return;
  editPages(
    (pages) => duplicateElementsInPage(pages, { pageId: page.id, elementIds: ids, layoutId }, masterIds),
    counted("Duplicate", roots.length),
  );
}

/** Fills the clipboard, unless none of `ids` is on the page. */
export function copyControls({ page, layoutId }: Omit<InLayout, "editPages">, ids: string[]): void {
  if (!page) return;
  const payload = clipboardForSelection(page, ids, layoutId);
  if (payload.elements.length > 0) store().setClipboard(payload);
}

export function pasteControls({ page, editPages, layoutId, masterIds }: Making, clipboard: ElementClipboard | null): void {
  const held = clipboard?.elements.length ?? 0;
  if (!page || held === 0) return;
  editPages((pages) => pasteIntoPage(pages, { pageId: page.id, layoutId }, clipboard!, masterIds), counted("Paste", held));
}

/** Hiding is per arrangement: it writes to the layout being authored and
 *  leaves every other one showing the control. */
export function toggleHidden({ page, editPages, layoutId }: InLayout, id: string): void {
  if (!page) return;
  const hidden = resolveHidden(page, layoutId).has(id);
  editPages(
    (pages) => pages.map((pg) => (pg.id === page.id ? withHidden(pg, id, !hidden, layoutId) : pg)),
    hidden ? "Show in this layout" : "Hide in this layout",
  );
}

const TO_END = { front: "Bring to front", back: "Send to back" } as const;

/** Bring a control to the front of the page's order, or send it to the back. */
export function moveToEnd({ page, editPages }: OnPage, id: string, end: keyof typeof TO_END): void {
  if (page) editPages((pages) => reorderElement(pages, page.id, id, end), TO_END[end]);
}

export function swapOrder({ page, editPages }: OnPage, id: string, neighbourId: string): void {
  if (page) editPages((pages) => swapElementsInOrder(pages, page.id, id, neighbourId), "Reorder element");
}

/** Moving a control into a container (or back out, `null`) is a geometry
 *  change as much as a structural one: its percentages are of whatever holds
 *  it, so the helper converts through page space and the control stays
 *  exactly where it is drawn. It stays selected. */
export function reparent({ page, editPages }: OnPage, id: string, parentId: string | null): void {
  if (!page) return;
  editPages(
    (pages) => reparentElement(pages, page.id, id, parentId),
    parentId ? "Move into container" : "Move out of container",
  );
  store().selectElement(id);
}

/** A control new from the palette, made where its gesture landed it
 *  (paletteDrop.ts), dropped or clicked; it is selected. */
export function addControl(
  { page, editPages, project, masters }: OnPage & Omit<OnProject, "run">,
  type: string,
  landing: Landing,
): void {
  if (!page || !project) return;
  const element = createDefaultElement(type, takenIds(project.ui.pages, masters));
  if (landing.parentId) element.parent = landing.parentId;
  editPages((pages) => addElementToPage(pages, page.id, element, landing.placement), `Add ${type}`);
  store().selectElement(element.id);
}

// --- Run edits: a burst of these is one undo entry ---

export function editControl({ page, editPages }: OnPage, id: string, patch: Partial<UIElement>): void {
  if (page) editPages((pages) => updateElementInPage(pages, page.id, id, patch), "Edit element", true);
}

export function editPage({ page, editPages }: OnPage, patch: Partial<UIPage>): void {
  if (page) editPages((pages) => pages.map((p) => (p.id === page.id ? { ...p, ...patch } : p)), "Edit page properties", true);
}

/** How far a box moves, in percent of what holds it. */
export interface Offset {
  dx: number;
  dy: number;
}

/** The pages with these controls moved by (dx, dy) in the arrangement being
 *  authored, each box rounded the way a drag rounds it. */
export function nudgedPages(
  pages: UIPage[],
  pageId: string,
  { ids, dx, dy }: Offset & { ids: string[] },
  layoutId: string | null,
): UIPage[] {
  const target = pages.find((p) => p.id === pageId);
  if (!target) return pages;
  const moved: Record<string, Placement> = {};
  for (const id of ids) {
    const box = getPlacement(target, id, layoutId);
    moved[id] = roundPlacement({ ...box, x: box.x + dx, y: box.y + dy });
  }
  return moveElementsInPage(pages, pageId, moved, layoutId);
}

export function nudge({ page, editPages, layoutId }: InLayout, ids: string[], offset: Offset, description: string): void {
  if (page) editPages((pages) => nudgedPages(pages, page.id, { ids, ...offset }, layoutId), description, true);
}

export function editMaster({ project, masters, run }: OnProject, id: string, patch: Partial<MasterElement>): void {
  if (project) run.join(projectEdit(project, { master_elements: updateMasterElement(masters, id, patch) }, "Edit master element"));
}

// --- The masters ---

/** A control made a master leaves the page; nothing is selected after. */
export function promote({ project, masters, run, page }: OnProject & { page: UIPage | null }, id: string): void {
  if (!project || !page) return;
  const result = promoteToMaster(project.ui.pages, masters, page.id, id);
  run.commit(projectEdit(project, { pages: result.pages, master_elements: result.masterElements }, "Promote to master"));
  store().selectElement(null);
}

/** A master moved onto the page on screen; no master is selected after. */
export function demote({ project, masters, run, page }: OnProject & { page: UIPage | null }, masterId: string): void {
  if (!project || !page) return;
  const result = demoteFromMaster(project.ui.pages, masters, masterId, page.id);
  run.commit(projectEdit(project, { pages: result.pages, master_elements: result.masterElements }, "Demote from master"));
  store().selectMasterElement(null);
}

export function deleteMaster({ project, masters, run }: OnProject, masterId: string): void {
  if (!project) return;
  run.commit(projectEdit(project, { master_elements: removeMasterElement(masters, masterId) }, "Delete master element"));
  store().selectMasterElement(null);
}

// --- Renaming a control or a master ---

/** What was selected when the rename was asked for: whichever had the old id
 *  is selected again under the new one. */
export interface Selected {
  elementId: string | null;
  masterId: string | null;
}

/** A rename everywhere the project names the control or master. It is
 *  refused, with the reason, onto an id a page control or a master holds,
 *  and does nothing onto its own id. */
export function rename(on: OnProject, selected: Selected, oldId: string, newId: string): void {
  const { project, masters, run } = on;
  if (!project) return;
  const refused = validateElementId(newId, oldId, project.ui.pages, masters);
  if (refused) {
    showError(refused);
    return;
  }
  if (newId === oldId) return;
  run.commit(renameEdit(project, masters, oldId, newId));
  // Select it under its new id, so the Properties panel does not lose it and
  // refresh into "nothing selected".
  if (selected.elementId === oldId) store().selectElement(newId);
  if (selected.masterId === oldId) store().selectMasterElement(newId);
  showSuccess(`Renamed to ${newId}`);
  if ((project.scripts || []).length > 0) {
    showInfo(`If any scripts reference "ui.${oldId}.*", update them manually.`);
  }
}

/** The rename as an undo entry. It puts back only the parts the rename
 *  changed (the variables stay the same object when no `source_key` named the
 *  control), while its patch writes all four. A part the project does not
 *  have (no macros, say) comes back from the rename as a list, so it is always
 *  put back, as an empty one. */
export function renameEdit(project: ProjectConfig, masters: MasterElement[], oldId: string, newId: string): ProjectEdit {
  const { macros, variables } = project;
  const result = renameElement(project.ui.pages, masters, macros || [], variables || [], project.scripts || [], oldId, newId);
  const parts: [keyof UndoScope, unknown[] | undefined, unknown[]][] = [
    ["pages", project.ui.pages, result.pages],
    ["master_elements", masters, result.master_elements],
    ["macros", macros, result.macros],
    ["variables", variables, result.variables],
  ];
  const before: Record<string, unknown[]> = {};
  for (const [part, was, now] of parts) {
    if (now !== was) before[part] = was || [];
  }
  return {
    before: before as UndoScope,
    description: `Rename element ${oldId} → ${newId}`,
    patch: {
      ui: { ...project.ui, pages: result.pages, master_elements: result.master_elements },
      macros: result.macros,
      variables: result.variables,
    },
  };
}
