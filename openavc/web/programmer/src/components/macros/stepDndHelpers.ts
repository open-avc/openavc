// Pure helpers behind the macro step drag-to-reorder (MacroEditor.tsx).
//
// dnd-kit only works when SortableContext items, the ids each child
// registers via useSortable, and the React keys all come from ONE id space.
// These helpers own that space: every step object gets a stable id minted
// once and remembered by object identity, so ids survive reorders (the
// object moves, its id moves with it) and never collide across macros
// sharing the same editor instance.

/**
 * Returns one stable id per step, parallel to `steps`. Ids are minted from
 * `counter` on first sight of an object and cached in `idMap`, so a step
 * keeps its id across reorders. A step object nothing has claimed an id for
 * is new and gets a fresh one -- an edit hands its id over first, see
 * `carryStepId`.
 */
export function getStepIds<T extends object>(
  steps: readonly T[],
  idMap: WeakMap<T, string>,
  counter: { current: number },
): string[] {
  return steps.map((step) => {
    let id = idMap.get(step);
    if (!id) {
      id = `step-${counter.current++}`;
      idMap.set(step, id);
    }
    return id;
  });
}

/**
 * Hands `oldStep`'s id to the object replacing it, so an edited step is the
 * same step as far as the id space is concerned.
 *
 * Editing is immutable here -- a keystroke produces a whole new step object at
 * the same position -- and without this the map has never seen that object, so
 * it mints a fresh id, the React key changes and the entire step row is torn
 * down and rebuilt mid-keystroke. That destroys the focused input: the caret
 * lands on the page body and every character after the first goes nowhere,
 * leaving a truncated-but-valid-looking value (a "1000" typed into a level
 * saved as "1"). A genuinely new step -- added, pasted, duplicated -- has no
 * id to inherit and must not be passed through here.
 */
export function carryStepId<T extends object>(
  idMap: WeakMap<T, string>,
  oldStep: T | undefined,
  newStep: T,
): void {
  if (!oldStep || oldStep === newStep) return;
  const id = idMap.get(oldStep);
  if (id) idMap.set(newStep, id);
}

/**
 * Applies a drag-end to `steps`, where `activeId`/`overId` are entries of
 * `stepIds` (the array getStepIds returned for this exact `steps` array).
 * Returns the reordered copy plus the indices involved, or null when the
 * drop changes nothing or either id is unknown.
 */
export function applyStepReorder<T>(
  steps: readonly T[],
  stepIds: readonly string[],
  activeId: string,
  overId: string,
): { steps: T[]; oldIndex: number; newIndex: number } | null {
  if (activeId === overId) return null;
  const oldIndex = stepIds.indexOf(activeId);
  const newIndex = stepIds.indexOf(overId);
  if (oldIndex === -1 || newIndex === -1) return null;
  const next = [...steps];
  const [moved] = next.splice(oldIndex, 1);
  next.splice(newIndex, 0, moved);
  return { steps: next, oldIndex, newIndex };
}

/**
 * Keeps the expanded step pointing at the same step after a move: it
 * follows the moved step, and shifts by one when the move passes over it.
 */
export function adjustExpandedAfterMove(
  expanded: number | null,
  oldIndex: number,
  newIndex: number,
): number | null {
  if (expanded === null) return null;
  if (expanded === oldIndex) return newIndex;
  if (oldIndex < expanded && newIndex >= expanded) return expanded - 1;
  if (oldIndex > expanded && newIndex <= expanded) return expanded + 1;
  return expanded;
}
