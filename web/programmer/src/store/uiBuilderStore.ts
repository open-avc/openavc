import { create } from "zustand";
import type { UIPage, UIElement, UISettings, MasterElement, PageGroup, MacroConfig, VariableConfig } from "../api/types";
import { useProjectStore } from "./projectStore";

export interface UndoScope {
  // ui.* scopes
  pages?: UIPage[];
  settings?: UISettings;
  master_elements?: MasterElement[];
  page_groups?: PageGroup[];
  // project.* scopes (for cross-project edits like element rename
  // that must rewrite references in macros/variables)
  macros?: MacroConfig[];
  variables?: VariableConfig[];
}

export interface UndoEntry {
  description: string;
  snapshot: UndoScope;
}

interface UIBuilderStore {
  selectedPageId: string | null;
  selectedElementId: string | null;
  selectedElementIds: string[];
  selectedMasterElementId: string | null;
  previewMode: boolean;
  showGrid: boolean;
  zoom: number;
  screenPresetIndex: number;
  customWidth: number;
  customHeight: number;
  clipboard: UIElement[] | null;
  contextMenu: { x: number; y: number; elementId: string; isMaster?: boolean } | null;
  undoStack: UndoEntry[];
  redoStack: UndoEntry[];
  lastMutationTime: number;
  activeDragSource: string | null;
  lockedElementIds: Set<string>;

  selectPage: (id: string | null) => void;
  selectElement: (id: string | null) => void;
  toggleSelectElement: (id: string) => void;
  selectMasterElement: (id: string | null) => void;
  setPreviewMode: (v: boolean) => void;
  toggleGrid: () => void;
  setZoom: (zoom: number) => void;
  setScreenPresetIndex: (index: number) => void;
  setCustomSize: (w: number, h: number) => void;
  setClipboard: (el: UIElement[] | null) => void;
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

// Build (a) the inverse snapshot to push onto the redo/undo stack and
// (b) the project patch to apply, given the snapshot the user is rolling
// back to. UI scopes overlay onto project.ui; project scopes overlay
// directly onto the project. Only scopes present in the original snapshot
// are touched — that's the point of the scoped API.
function computeRollbackPatch(
  snapshot: UndoScope,
  project: import("../api/types").ProjectConfig,
): {
  redoSnapshot: UndoScope;
  projectPatch: Partial<import("../api/types").ProjectConfig>;
} {
  const redoSnapshot: UndoScope = {};
  const uiPatch: Partial<typeof project.ui> = {};
  const projectPatch: Partial<typeof project> = {};
  let touchesUi = false;

  if ("pages" in snapshot) {
    redoSnapshot.pages = project.ui.pages;
    uiPatch.pages = snapshot.pages;
    touchesUi = true;
  }
  if ("settings" in snapshot) {
    redoSnapshot.settings = project.ui.settings;
    uiPatch.settings = snapshot.settings;
    touchesUi = true;
  }
  if ("master_elements" in snapshot) {
    redoSnapshot.master_elements = project.ui.master_elements ?? [];
    uiPatch.master_elements = snapshot.master_elements;
    touchesUi = true;
  }
  if ("page_groups" in snapshot) {
    redoSnapshot.page_groups = project.ui.page_groups ?? [];
    uiPatch.page_groups = snapshot.page_groups;
    touchesUi = true;
  }
  if ("macros" in snapshot) {
    redoSnapshot.macros = project.macros;
    projectPatch.macros = snapshot.macros;
  }
  if ("variables" in snapshot) {
    redoSnapshot.variables = project.variables;
    projectPatch.variables = snapshot.variables;
  }

  if (touchesUi) {
    projectPatch.ui = { ...project.ui, ...uiPatch };
  }

  return { redoSnapshot, projectPatch };
}

function repairSelection(
  scope: UndoScope,
  current: {
    selectedPageId: string | null;
    selectedElementIds: string[];
    selectedMasterElementId: string | null;
  },
  fallback: { pages: UIPage[]; masters: MasterElement[] },
) {
  const pages = scope.pages ?? fallback.pages;
  const masters = scope.master_elements ?? fallback.masters;

  let { selectedPageId, selectedElementIds, selectedMasterElementId } = current;

  if (selectedPageId) {
    const page = pages.find((p) => p.id === selectedPageId);
    if (!page) {
      selectedPageId = pages[0]?.id ?? null;
      selectedElementIds = [];
    } else {
      selectedElementIds = selectedElementIds.filter((eid) =>
        page.elements.some((e) => e.id === eid),
      );
    }
  }

  if (selectedMasterElementId && !masters.some((m) => m.id === selectedMasterElementId)) {
    selectedMasterElementId = null;
  }

  return {
    selectedPageId,
    selectedElementIds,
    selectedElementId: selectedElementIds[0] || null,
    selectedMasterElementId,
  };
}

export const useUIBuilderStore = create<UIBuilderStore>((set, get) => ({
  selectedPageId: null,
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
  lockedElementIds: new Set(),

  selectPage: (id) => set({ selectedPageId: id, selectedElementId: null, selectedElementIds: [], selectedMasterElementId: null }),

  selectElement: (id) => set({
    selectedElementId: id,
    selectedElementIds: id ? [id] : [],
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

    const newSelection = repairSelection(
      entry.snapshot,
      {
        selectedPageId: get().selectedPageId,
        selectedElementIds: get().selectedElementIds,
        selectedMasterElementId: get().selectedMasterElementId,
      },
      { pages: project.ui.pages, masters: project.ui.master_elements ?? [] },
    );

    set({
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, { description: entry.description, snapshot: redoSnapshot }],
      ...newSelection,
    });

    projectStore.update(projectPatch);
  },

  redo: () => {
    const { undoStack, redoStack } = get();
    if (redoStack.length === 0) return;
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;

    const entry = redoStack[redoStack.length - 1];
    const { redoSnapshot: undoSnapshot, projectPatch } = computeRollbackPatch(entry.snapshot, project);

    const newSelection = repairSelection(
      entry.snapshot,
      {
        selectedPageId: get().selectedPageId,
        selectedElementIds: get().selectedElementIds,
        selectedMasterElementId: get().selectedMasterElementId,
      },
      { pages: project.ui.pages, masters: project.ui.master_elements ?? [] },
    );

    set({
      undoStack: [...undoStack, { description: entry.description, snapshot: undoSnapshot }],
      redoStack: redoStack.slice(0, -1),
      ...newSelection,
    });

    projectStore.update(projectPatch);
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

  toggleLock: (elementId) => {
    const next = new Set(get().lockedElementIds);
    if (next.has(elementId)) next.delete(elementId); else next.add(elementId);
    set({ lockedElementIds: next });
  },

}));
