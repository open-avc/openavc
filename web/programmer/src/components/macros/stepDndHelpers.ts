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
 * keeps its id across reorders while a replaced (edited) step object gets a
 * fresh one.
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
