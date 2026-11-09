/**
 * How a change to the project's monitors is saved, from every place a reading
 * is tagged: the device page (its Live State and its child readings), State's
 * device readings, and a variable. One undoable step, then the project saved
 * 1.5 s after the last change, so ticking several readings in a row is one
 * write.
 */
import { useCallback } from "react";
import type { MonitorConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";

export type MonitorEdit = (next: MonitorConfig[], description: string) => void;

export function useMonitorEdits(): MonitorEdit {
  const updateWithUndo = useProjectStore((s) => s.updateWithUndo);
  return useCallback(
    (next: MonitorConfig[], description: string) => {
      updateWithUndo({ monitors: next }, description);
      useProjectStore.getState().debouncedSave(1500);
    },
    [updateWithUndo],
  );
}
