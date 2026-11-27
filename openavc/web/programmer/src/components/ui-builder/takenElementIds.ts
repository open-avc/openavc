/**
 * THE rule for the ids a control may not be given: every page's controls, and
 * the reserved ones (master elements share the ui.<id> namespace, so a copy, or
 * a control promoted to or demoted from a master, cannot be named onto one).
 *
 * Every door that names a control asks it: the palette's drop and
 * click-to-add (`takenIds` in views/ui-builder/paletteDrop.ts), a duplicate, a
 * paste, a page copy, a promote and a demote (uiBuilderHelpers.ts, which
 * re-exports it), and a rename (`validateElementId` in projectRewrites.ts),
 * which takes the control's own id back out.
 */
import type { UIPage } from "../../api/types";

export function takenElementIds(pages: UIPage[], reservedIds: string[]): Set<string> {
  const taken = new Set(pages.flatMap((p) => p.elements.map((e) => e.id)));
  for (const id of reservedIds) taken.add(id);
  return taken;
}
