/**
 * How an edit made on the UI Builder page reaches the project and the undo
 * history.
 *
 * An edit is one of two kinds. A structural edit (add, delete, rename, a
 * theme change, a dialog's Save) is an undo entry of its own and ends any
 * run in progress (`commit`). A run edit (typing in the Properties panel, a
 * held arrow key, dragging a colour) joins the run in progress (`join`): the
 * run's first edit pushes the entry, holding the project from before it, and
 * the run ends 0.8 s after its last edit, on a structural edit, on undo or
 * redo (`endRun`), and when the selection changes.
 *
 * One run serves every part of the project: an element edit followed within
 * 0.8 s by a page or master edit is one entry, holding only what the first
 * edit named. The canvas gestures, the toolbar and the store's lock push their
 * own entries without ending a run here.
 *
 * An edit says only what it changes (`projectEdit`); what it puts back on undo
 * follows from that, through the store's own table of where each part of the
 * project lives and what it reads as when absent.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ProjectConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useUIBuilderStore, type UndoScope } from "../../store/uiBuilderStore";
import { computeRollbackPatch } from "../../store/uiBuilderStore.helpers";

/** How long after its last edit a run ends. */
export const RUN_IDLE_MS = 800;

/** What an edit puts back on undo, what it is called, and what it changes. */
export interface ProjectEdit {
  before: UndoScope;
  description: string;
  patch: Partial<ProjectConfig>;
}

/**
 * The edit that gives the parts of the project named in `changes` the values
 * there, and leaves every other part the same object. Its undo entry holds
 * what those parts were, read the way undo reads them (no masters as an empty
 * list, no stylesheet as an empty one): the edit is the same overlay undo
 * makes when it puts a snapshot back.
 */
export function projectEdit(project: ProjectConfig, changes: UndoScope, description: string): ProjectEdit {
  const { redoSnapshot, projectPatch } = computeRollbackPatch(changes, project);
  return { before: redoSnapshot, description, patch: projectPatch };
}

export interface EditRun {
  /** A structural edit: its own undo entry. */
  commit: (edit: ProjectEdit) => void;
  /** A run edit: one entry for the whole run. */
  join: (edit: ProjectEdit) => void;
  /** End the run in progress, so the next edit starts an entry of its own. */
  endRun: () => void;
}

function apply(patch: Partial<ProjectConfig>) {
  useProjectStore.getState().update(patch);
  useUIBuilderStore.getState().touchMutation();
}

export function useEditRun(): EditRun {
  const pushed = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);

  const endRun = useCallback(() => {
    pushed.current = false;
    clearTimeout(timer.current);
  }, []);

  useEffect(endRun, [selectedElementId, selectedMasterElementId, endRun]);

  return useMemo(() => ({
    endRun,
    commit: ({ before, description, patch }) => {
      useUIBuilderStore.getState().pushUndo(before, description);
      endRun();
      apply(patch);
    },
    join: ({ before, description, patch }) => {
      if (!pushed.current) {
        useUIBuilderStore.getState().pushUndo(before, description);
        pushed.current = true;
      }
      clearTimeout(timer.current);
      apply(patch);
      timer.current = setTimeout(() => {
        pushed.current = false;
      }, RUN_IDLE_MS);
    },
  }), [endRun]);
}
