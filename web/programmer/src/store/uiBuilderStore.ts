import { create } from "zustand";
import type { UIElement, Placement } from "../api/types";
import { useProjectStore } from "./projectStore";
import {
  computeRollbackPatch,
  selectionAfterRollback,
  type UndoEntry,
  type UndoScope,
} from "./uiBuilderStore.helpers";

export type { UndoEntry, UndoScope } from "./uiBuilderStore.helpers";

interface UIBuilderStore {
  selectedPageId: string | null;
  /** The layout being authored. null means the page's primary, which is the
   *  only one there is until a page grows a second arrangement. Every geometry
   *  read and write passes this through rather than assuming the primary. */
  activeLayoutId: string | null;
  selectedElementId: string | null;
  selectedElementIds: string[];
  selectedMasterElementId: string | null;
  previewMode: boolean;
  showGrid: boolean;
  zoom: number;
  screenPresetIndex: number;
  customWidth: number;
  customHeight: number;
  /** Copied elements plus where they sat — geometry lives in the page's
   *  layout now, not on the element, so a copy has to carry both halves. */
  clipboard: { elements: UIElement[]; placements: Record<string, Placement> } | null;
  contextMenu: { x: number; y: number; elementId: string; isMaster?: boolean } | null;
  undoStack: UndoEntry[];
  redoStack: UndoEntry[];
  lastMutationTime: number;
  activeDragSource: string | null;

  selectPage: (id: string | null) => void;
  selectLayout: (layoutId: string | null) => void;
  selectElement: (id: string | null) => void;
  selectElements: (ids: string[]) => void;
  toggleSelectElement: (id: string) => void;
  selectMasterElement: (id: string | null) => void;
  setPreviewMode: (v: boolean) => void;
  toggleGrid: () => void;
  setZoom: (zoom: number) => void;
  setScreenPresetIndex: (index: number) => void;
  setCustomSize: (w: number, h: number) => void;
  setClipboard: (
    el: { elements: UIElement[]; placements: Record<string, Placement> } | null,
  ) => void;
  setContextMenu: (
    menu: { x: number; y: number; elementId: string; isMaster?: boolean } | null,
  ) => void;
  pushUndo: (snapshot: UndoScope, description: string) => void;
  undo: () => void;
  redo: () => void;
  clearUndoHistory: () => void;
  touchMutation: () => void;
  setActiveDragSource: (source: string | null) => void;
  toggleLock: (elementId: string) => void;
}

export const useUIBuilderStore = create<UIBuilderStore>((set, get) => ({
  selectedPageId: null,
  activeLayoutId: null,
  selectedElementId: null,
  selectedElementIds: [],
  selectedMasterElementId: null,
  previewMode: false,
  showGrid: true,
  zoom: 1,
  screenPresetIndex: 0,
  customWidth: 1024,
  customHeight: 600,
  clipboard: null,
  contextMenu: null,
  undoStack: [],
  redoStack: [],
  lastMutationTime: 0,
  activeDragSource: null,

  // Switching pages drops back to the primary layout: a layout id belongs to
  // one page, so carrying it across would point at nothing.
  selectPage: (id) => set({ selectedPageId: id, activeLayoutId: null, selectedElementId: null, selectedElementIds: [], selectedMasterElementId: null }),

  selectLayout: (activeLayoutId) => set({ activeLayoutId }),

  selectElement: (id) => set({
    selectedElementId: id,
    selectedElementIds: id ? [id] : [],
    selectedMasterElementId: null,
    contextMenu: null,
  }),

  // A whole selection at once -- what a marquee produces. The first id is the
  // primary, so it is the one the Properties panel shows and the one match-size
  // measures against.
  selectElements: (ids) => set({
    selectedElementIds: ids,
    selectedElementId: ids[0] ?? null,
    selectedMasterElementId: null,
    contextMenu: null,
  }),

  toggleSelectElement: (id) => {
    const { selectedElementIds } = get();
    let newIds: string[];
    if (selectedElementIds.includes(id)) {
      newIds = selectedElementIds.filter((eid) => eid !== id);
    } else {
      newIds = [...selectedElementIds, id];
    }
    set({
      selectedElementIds: newIds,
      selectedElementId: newIds[0] || null,
      selectedMasterElementId: null,
      contextMenu: null,
    });
  },

  selectMasterElement: (id) => set({ selectedMasterElementId: id, selectedElementId: null, selectedElementIds: [], contextMenu: null }),

  setPreviewMode: (previewMode) =>
    set({ previewMode, selectedElementId: null, selectedElementIds: [], selectedMasterElementId: null, contextMenu: null }),

  toggleGrid: () => set((s) => ({ showGrid: !s.showGrid })),

  setZoom: (zoom) => set({ zoom: Math.max(0.25, Math.min(2, zoom)) }),

  setScreenPresetIndex: (screenPresetIndex) => set({ screenPresetIndex }),

  setCustomSize: (customWidth, customHeight) =>
    set({ customWidth, customHeight }),

  setClipboard: (clipboard) => set({ clipboard }),

  setContextMenu: (contextMenu) => set({ contextMenu }),

  pushUndo: (snapshot, description) => {
    const { undoStack } = get();
    set({
      undoStack: [...undoStack.slice(-49), { description, snapshot }],
      redoStack: [],
    });
  },

  undo: () => {
    const { undoStack, redoStack } = get();
    if (undoStack.length === 0) return;
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;

    const entry = undoStack[undoStack.length - 1];
    const { redoSnapshot, projectPatch } = computeRollbackPatch(entry.snapshot, project);

    const newSelection = selectionAfterRollback(
      entry.snapshot,
      {
        selectedPageId: get().selectedPageId,
        selectedElementIds: get().selectedElementIds,
        selectedMasterElementId: get().selectedMasterElementId,
      },
      project,
    );

    set({
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, { description: entry.description, snapshot: redoSnapshot }],
      ...newSelection,
    });

    projectStore.update(projectPatch);
    // update() only marks the project dirty — arm the shared debounce like
    // the project store's own undo does, or the rolled-back state never
    // persists (flushSave and the unload handler no-op without a timer).
    projectStore.debouncedSave(100);
  },

  redo: () => {
    const { undoStack, redoStack } = get();
    if (redoStack.length === 0) return;
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;

    const entry = redoStack[redoStack.length - 1];
    const { redoSnapshot: undoSnapshot, projectPatch } = computeRollbackPatch(entry.snapshot, project);

    const newSelection = selectionAfterRollback(
      entry.snapshot,
      {
        selectedPageId: get().selectedPageId,
        selectedElementIds: get().selectedElementIds,
        selectedMasterElementId: get().selectedMasterElementId,
      },
      project,
    );

    set({
      undoStack: [...undoStack, { description: entry.description, snapshot: undoSnapshot }],
      redoStack: redoStack.slice(0, -1),
      ...newSelection,
    });

    projectStore.update(projectPatch);
    projectStore.debouncedSave(100);
  },

  clearUndoHistory: () => set({ undoStack: [], redoStack: [] }),

  touchMutation: () => {
    set({ lastMutationTime: Date.now() });
    // Route through the shared project store debounce so flushSave / Ctrl+S
    // / unload handlers can flush UI Builder edits too. 2 s matches the
    // previous local timer.
    useProjectStore.getState().debouncedSave(2000);
  },

  setActiveDragSource: (activeDragSource) => set({ activeDragSource }),

  // Lock lives in the project, not here: a lock that evaporates on reload is
  // worse than none, because you only find out after something has moved.
  toggleLock: (elementId) => {
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;
    const ui = project.ui;

    const master = (ui.master_elements ?? []).find((m) => m.id === elementId);
    if (master) {
      get().pushUndo({ master_elements: ui.master_elements ?? [] }, master.locked ? "Unlock element" : "Lock element");
      projectStore.update({
        ui: {
          ...ui,
          master_elements: (ui.master_elements ?? []).map((m) =>
            m.id === elementId ? { ...m, locked: !m.locked } : m,
          ),
        },
      });
      get().touchMutation();
      return;
    }

    const page = ui.pages.find((p) => p.elements.some((el) => el.id === elementId));
    if (!page) return;
    const element = page.elements.find((el) => el.id === elementId)!;
    get().pushUndo({ pages: ui.pages }, element.locked ? "Unlock element" : "Lock element");
    projectStore.update({
      ui: {
        ...ui,
        pages: ui.pages.map((p) =>
          p.id === page.id
            ? {
                ...p,
                elements: p.elements.map((el) =>
                  el.id === elementId ? { ...el, locked: !el.locked } : el,
                ),
              }
            : p,
        ),
      },
    });
    get().touchMutation();
  },

}));
