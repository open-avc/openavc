import { useCallback, useMemo, useRef, useState } from "react";
import * as api from "../../api/restClient";
import { changedElsewhereRevision, parseApiError } from "../../api/errors";
import { showError } from "../../store/toastStore";
import { ChangedElsewhereDialog } from "../shared/ChangedElsewhereDialog";

/** A Code view save refused because another session saved the driver first:
 *  the server's sentence, the revision saved now, the driver, and whether the
 *  refused save was Save & Reload's, which "save mine over it" goes on with. */
export interface SourceConflict {
  message: string;
  revision: string;
  driverId: string;
  reload: boolean;
}

/** What a save the choice was offered for throws: nothing more to report. */
export class ChoiceOffered extends Error {
  constructor() {
    super("This driver was changed by another session since you opened it.");
    this.name = "ChoiceOffered";
  }
}

/** The revision a source read answered, or null for a read that has none
 *  (a script's). */
export function revisionOf(read: object): string | null {
  const revision = (read as { revision?: unknown }).revision;
  return typeof revision === "string" ? revision : null;
}

/** A Code view save that failed, as the view says it: logged and shown,
 *  unless it was refused with the choice, which the dialog already shows. */
export function reportSaveFailure(what: string, e: unknown): void {
  if (e instanceof ChoiceOffered) return;
  console.error(`Failed to save ${what}:`, e);
  showError(`Save failed: ${parseApiError(e)}`);
}

/**
 * The revision the Code view's open Python driver was read at, which each of
 * its saves names (`save`), and the save refused because another session saved
 * over that copy since (`conflict`), which `SourceConflictDialog` offers reload
 * theirs, save mine over it and keep editing for.
 *
 * `readAt` is a ref so a save made straight after "save mine over it" names
 * the new revision without waiting for a render.
 */
export function useSourceRevision() {
  const readAt = useRef<string | null>(null);
  const [conflict, setConflict] = useState<SourceConflict | null>(null);

  /** A driver opened (its read's revision), or the open one closed (null). */
  const opened = useCallback((revision: string | null) => {
    readAt.current = revision;
    setConflict(null);
  }, []);

  /** `api.savePythonDriverSource`, naming the revision the source was read
   *  at and keeping the one a write answers. A save refused as made from an
   *  older copy offers the choice and throws `ChoiceOffered`. */
  const save = useCallback(async (driverId: string, source: string, requireValidSyntax: boolean) => {
    try {
      const res = await api.savePythonDriverSource(driverId, source, requireValidSyntax, readAt.current);
      readAt.current = res.revision ?? readAt.current;
      return res;
    } catch (e) {
      const theirs = changedElsewhereRevision(e);
      if (theirs === null) throw e;
      setConflict({ message: parseApiError(e), revision: theirs, driverId, reload: requireValidSyntax });
      throw new ChoiceOffered();
    }
  }, []);

  /** Save again over the copy saved now: `retry` is the refused save's button. */
  const saveMine = useCallback((retry: () => void) => {
    if (!conflict) return;
    readAt.current = conflict.revision;
    setConflict(null);
    retry();
  }, [conflict]);

  const dismiss = useCallback(() => setConflict(null), []);

  return useMemo(
    () => ({ readAt, opened, save, conflict, saveMine, dismiss }),
    [opened, save, conflict, saveMine, dismiss],
  );
}

/** The choice a Code view save refused as made from an older copy offers:
 *  `onReopen` opens the driver as saved now, `onSave` and `onSaveAndReload`
 *  are the two buttons, the refused one pressed again. */
export function SourceConflictDialog({
  revision,
  onReopen,
  onSave,
  onSaveAndReload,
}: {
  revision: ReturnType<typeof useSourceRevision>;
  onReopen: (driverId: string) => void;
  onSave: () => void;
  onSaveAndReload: () => void;
}) {
  const { conflict } = revision;
  if (!conflict) return null;
  return (
    <ChangedElsewhereDialog
      message={conflict.message}
      onReloadTheirs={() => onReopen(conflict.driverId)}
      onSaveMine={() => revision.saveMine(conflict.reload ? onSaveAndReload : onSave)}
      onKeepEditing={revision.dismiss}
    />
  );
}
