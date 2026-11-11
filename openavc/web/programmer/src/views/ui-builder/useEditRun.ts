/**
 * How an edit made on the UI Builder page reaches the project and the undo
 * history.
 *
 * An edit is one of two kinds. A structural edit (add, delete, rename, a
 * theme change, a dialog's Save) is an undo entry of its own (`commit`). A
 * run edit (typing in the Properties panel, a held arrow key, dragging a
 * colour) joins the run in progress (`join`): the run's first edit pushes the
 * entry, holding the part of the project it changes from before it, and the
 * run ends 0.8 s after its last edit, when the selection changes, and on an
 * edit of another part (the pages, the masters, the settings), which starts
 * an entry of its own.
 *
 * The run itself is the Builder store's (`joinUndo`), so every other writer
 * ends it without knowing it is there: any entry pushed (a structural edit
 * here, a canvas move, a toolbar edit, the lock), and any undo or redo, from
 * the keyboard or the toolbar's buttons.
 *
 * An edit says only what it changes (`projectEdit`); what it puts back on undo
 * follows from that, through the store's own table of where each part of the
 * project lives and what it reads as when absent.
 */
import { useEffect, useMemo } from "react";
import type { ProjectConfig } from "../../api/types";
import { useProjectStore } from "../../store/projectStore";
import { useUIBuilderStore, type UndoScope } from "../../store/uiBuilderStore";
import { computeRollbackPatch } from "../../store/uiBuilderStore.helpers";

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
}

function apply(patch: Partial<ProjectConfig>) {
  useProjectStore.getState().update(patch);
  useUIBuilderStore.getState().touchMutation();
}

export function useEditRun(): EditRun {
  const selectedElementId = useUIBuilderStore((s) => s.selectedElementId);
  const selectedMasterElementId = useUIBuilderStore((s) => s.selectedMasterElementId);
  const endRun = useUIBuilderStore((s) => s.endEditRun);

  useEffect(endRun, [selectedElementId, selectedMasterElementId, endRun]);

  return useMemo(() => ({
    commit: ({ before, description, patch }) => {
      useUIBuilderStore.getState().pushUndo(before, description);
      apply(patch);
    },
    join: ({ before, description, patch }) => {
      useUIBuilderStore.getState().joinUndo(before, description);
      apply(patch);
    },
  }), []);
}
