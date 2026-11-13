/**
 * What the IDE says about saving the project, and the UI Builder's Save now.
 *
 * A failed save is shown (the line across the top of the app on every view,
 * components/shared/SaveFailedBanner.tsx, and the Builder toolbar's status
 * word) unless it was a version conflict: that has its own banner above the
 * whole app (App.tsx) with the only two answers that can work, and saying it
 * again here would offer a retry that can only conflict again. A failed
 * project load is the store's `loadError`, never `error`, so it is never said
 * as a failed save.
 *
 * Saving now is what Ctrl+S and the toolbar's Save button do: the save waiting
 * on its timer goes now, and a direct one while changes are unsaved and none is
 * running (flushSave does nothing with no timer). The banner's Retry is a
 * direct save, whatever is waiting. While the Builder is open, the save
 * waiting on its timer also goes out as the browser tab unloads, so the last
 * edit is not lost to the wait (`useFlushBeforeUnload`).
 */
import { useEffect } from "react";
import { useProjectStore } from "./projectStore";

interface SaveStatus {
  error: string | null;
  conflictDetected: boolean;
}

/** Why the last save failed, or null when there is nothing to say here. */
export function saveFailure(s: SaveStatus): string | null {
  return s.conflictDetected ? null : s.error;
}

/** The store is read once, before the flush. */
export function saveNow(): void {
  const store = useProjectStore.getState();
  store.flushSave();
  if (store.dirty && !store.saving) store.save();
}

export function useFlushBeforeUnload(): void {
  useEffect(() => {
    const handler = () => useProjectStore.getState().flushSave();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);
}
