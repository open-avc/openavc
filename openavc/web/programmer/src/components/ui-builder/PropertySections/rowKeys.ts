/**
 * Stable React keys for the rows of a list editor in the properties panel.
 */
import { useRef } from "react";

/** Stable React keys for editable rows that have no natural id. With
 *  key={i}, deleting a middle row rebinds every row below it — the input at
 *  that index keeps focus but now edits a different row. Appended rows get
 *  fresh ids; removeAt keeps the survivors' ids with their rows. Lists are
 *  only ever appended to or deleted from (no reorder), so length-sync plus
 *  removeAt covers every mutation. */
export function useRowKeys(length: number) {
  const next = useRef(0);
  const keysRef = useRef<number[]>([]);
  const keys = keysRef.current;
  if (keys.length > length) keys.length = length;
  while (keys.length < length) keys.push(next.current++);
  return {
    keys,
    removeAt: (i: number) => {
      keys.splice(i, 1);
    },
  };
}
