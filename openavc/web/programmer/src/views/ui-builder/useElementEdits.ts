/**
 * The edits of a control or a master (elementEdits.ts) as the UI Builder
 * page hands them to the canvas menu, the outline, the Properties panel, the
 * palette and the keyboard.
 *
 * Each edit is bound to what it reads from the page as last drawn and keeps
 * its identity until one of those changes, as the page's own handlers did
 * (the masters are held while the project is the same, where the page's list
 * for a project with none was new on every draw). What reads an edit's
 * identity is a hook's list: the palette's drop and add (`usePaletteDrop`) and
 * the outline's drag (`onReparent`); none reruns an effect on a new one.
 */
import { useCallback, useMemo } from "react";
import type { MasterElement, ProjectConfig, UIElement, UIPage } from "../../api/types";
import type { ElementClipboard } from "../../components/ui-builder/uiBuilderHelpers";
import * as edit from "./elementEdits";
import type { Landing } from "./paletteDrop";
import type { EditRun } from "./useEditRun";

export interface ElementEdits {
  remove: (ids: string[]) => void;
  duplicate: (ids: string[]) => void;
  copy: (ids: string[]) => void;
  paste: () => void;
  toggleHidden: (id: string) => void;
  bringToFront: (id: string) => void;
  sendToBack: (id: string) => void;
  swapOrder: (id: string, neighbourId: string) => void;
  reparent: (id: string, parentId: string | null) => void;
  add: (type: string, landing: Landing) => void;
  editControl: (id: string, patch: Partial<UIElement>) => void;
  editPage: (patch: Partial<UIPage>) => void;
  nudge: (ids: string[], offset: edit.Offset, description: string) => void;
  editMaster: (id: string, patch: Partial<MasterElement>) => void;
  promote: (id: string) => void;
  demote: (masterId: string) => void;
  deleteMaster: (masterId: string) => void;
  rename: (oldId: string, newId: string) => void;
}

export interface ElementEditsInput {
  project: ProjectConfig | null;
  /** The page on screen. */
  page: UIPage | null;
  /** The arrangement being authored. */
  layoutId: string | null;
  run: EditRun;
  clipboard: ElementClipboard | null;
  selected: edit.Selected;
}

export function useElementEdits({ project, page, layoutId, run, clipboard, selected }: ElementEditsInput): ElementEdits {
  const masters = useMemo(() => project?.ui?.master_elements || [], [project]);
  const masterIds = useMemo(() => masters.map((m) => m.id), [masters]);
  const { elementId, masterId } = selected;

  const editPages = useCallback<edit.EditPages>((...a) => edit.editPagesOf({ project, run }, ...a), [project, run]);

  const remove = useCallback((ids: string[]) => edit.removeControls({ page, editPages }, ids), [page, editPages]);
  const duplicate = useCallback(
    (ids: string[]) => edit.duplicateControls({ page, editPages, layoutId, masterIds }, ids),
    [page, editPages, layoutId, masterIds],
  );
  const copy = useCallback((ids: string[]) => edit.copyControls({ page, layoutId }, ids), [page, layoutId]);
  const paste = useCallback(
    () => edit.pasteControls({ page, editPages, layoutId, masterIds }, clipboard),
    [page, editPages, layoutId, masterIds, clipboard],
  );
  const toggleHidden = useCallback((id: string) => edit.toggleHidden({ page, editPages, layoutId }, id), [page, editPages, layoutId]);
  const bringToFront = useCallback((id: string) => edit.moveToEnd({ page, editPages }, id, "front"), [page, editPages]);
  const sendToBack = useCallback((id: string) => edit.moveToEnd({ page, editPages }, id, "back"), [page, editPages]);
  const swapOrder = useCallback((id: string, to: string) => edit.swapOrder({ page, editPages }, id, to), [page, editPages]);
  const reparent = useCallback((id: string, to: string | null) => edit.reparent({ page, editPages }, id, to), [page, editPages]);
  const add = useCallback(
    (type: string, landing: Landing) => edit.addControl({ page, editPages, project, masters }, type, landing),
    [page, editPages, project, masters],
  );

  const editControl = useCallback(
    (id: string, patch: Partial<UIElement>) => edit.editControl({ page, editPages }, id, patch),
    [page, editPages],
  );
  const editPage = useCallback((patch: Partial<UIPage>) => edit.editPage({ page, editPages }, patch), [page, editPages]);
  const nudge = useCallback(
    (ids: string[], offset: edit.Offset, description: string) => edit.nudge({ page, editPages, layoutId }, ids, offset, description),
    [page, editPages, layoutId],
  );
  const editMaster = useCallback(
    (id: string, patch: Partial<MasterElement>) => edit.editMaster({ project, masters, run }, id, patch),
    [project, masters, run],
  );

  const promote = useCallback((id: string) => edit.promote({ project, masters, run, page }, id), [project, masters, run, page]);
  const demote = useCallback((id: string) => edit.demote({ project, masters, run, page }, id), [project, masters, run, page]);
  const deleteMaster = useCallback((id: string) => edit.deleteMaster({ project, masters, run }, id), [project, masters, run]);
  const rename = useCallback(
    (oldId: string, newId: string) => edit.rename({ project, masters, run }, { elementId, masterId }, oldId, newId),
    [project, masters, run, elementId, masterId],
  );

  return {
    remove, duplicate, copy, paste, toggleHidden, bringToFront, sendToBack, swapOrder, reparent, add,
    editControl, editPage, nudge, editMaster, promote, demote, deleteMaster, rename,
  };
}
