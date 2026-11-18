import { useCallback, useRef, useState } from "react";
import { changedElsewhereRevision, parseApiError } from "../../api/errors";

/** A Code view save refused because another session saved the driver first:
 *  the server's sentence, the revision saved now, and whether the refused
 *  save was Save & Reload's, which "save mine over it" goes on with. */
export interface SourceConflict {
  message: string;
  revision: string;
  reload: boolean;
}

/**
 * The revision the Code view's open Python driver was read at, which each of
 * its saves names, and the save refused because another session saved over
 * that copy since (`conflict`), which the view offers reload theirs, save mine
 * over it and keep editing for.
 *
 * `readAt` is a ref so a save made straight after "save mine over it" names
 * the new revision without waiting for a render.
 */
export function useSourceRevision() {
  const readAt = useRef<string | null>(null);
  const [conflict, setConflict] = useState<SourceConflict | null>(null);

  /** The revision a read or a save of the open driver answered. */
  const read = useCallback((revision: string | null | undefined) => {
    readAt.current = revision ?? null;
  }, []);

  /** Take `e` when it is the refusal of a save from an older copy (true), so
   *  the view offers the choice instead of an error. */
  const refused = useCallback((e: unknown, reload: boolean): boolean => {
    const theirs = changedElsewhereRevision(e);
    if (theirs === null) return false;
    setConflict({ message: parseApiError(e), revision: theirs, reload });
    return true;
  }, []);

  /** Save again over the copy saved now: `retry` is the refused save's button. */
  const saveMine = useCallback((retry: () => void) => {
    if (!conflict) return;
    readAt.current = conflict.revision;
    setConflict(null);
    retry();
  }, [conflict]);

  const dismiss = useCallback(() => setConflict(null), []);

  return { readAt, read, refused, conflict, saveMine, dismiss };
}
