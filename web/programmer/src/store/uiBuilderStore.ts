import { create } from "zustand";
import type { UIPage, UIElement } from "../api/types";
import { useProjectStore } from "./projectStore";

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
  clipboard: UIElement | null;
  contextMenu: { x: number; y: number; elementId: string; isMaster?: boolean } | null;
  undoStack: UIPage[][];
  redoStack: UIPage[][];
  lastMutationTime: number;
  activeDragSource: string | null;

  selectPage: (id: string | null) => void;
  selectElement: (id: string | null) => void;
  toggleSelectElement: (id: string) => void;
  selectMasterElement: (id: string | null) => void;
  setPreviewMode: (v: boolean) => void;
  toggleGrid: () => void;
  setZoom: (zoom: number) => void;
  setScreenPresetIndex: (index: number) => void;
  setCustomSize: (w: number, h: number) => void;
  setClipboard: (el: UIElement | null) => void;
  setContextMenu: (
    menu: { x: number; y: number; elementId: string; isMaster?: boolean } | null,
  ) => void;
  pushUndo: (pages: UIPage[]) => void;
  undo: () => void;
  redo: () => void;
  touchMutation: () => void;
  setActiveDragSource: (source: string | null) => void;
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

  pushUndo: (pages) => {
    const { undoStack } = get();
    set({
      undoStack: [...undoStack.slice(-49), pages],
      redoStack: [],
    });
  },

  undo: () => {
    const { undoStack, redoStack, selectedElementIds, selectedPageId } = get();
    if (undoStack.length === 0) return;
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;

    const currentPages = project.ui.pages;
    const previousPages = undoStack[undoStack.length - 1];

    // Clear selection if the element/page no longer exists in the target state
    let newSelectedIds = selectedElementIds;
    let newSelectedPageId = selectedPageId;
    if (selectedPageId) {
      const page = previousPages.find((p) => p.id === selectedPageId);
      if (!page) {
        newSelectedPageId = previousPages[0]?.id ?? null;
        newSelectedIds = [];
      } else {
        newSelectedIds = selectedElementIds.filter((eid) =>
          page.elements.some((e) => e.id === eid),
        );
      }
    }

    set({
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, currentPages],
      selectedElementId: newSelectedIds[0] || null,
      selectedElementIds: newSelectedIds,
      selectedPageId: newSelectedPageId,
    });

    projectStore.update({ ui: { ...project.ui, pages: previousPages } });
  },

  redo: () => {
    const { undoStack, redoStack, selectedElementIds, selectedPageId } = get();
    if (redoStack.length === 0) return;
    const projectStore = useProjectStore.getState();
    const project = projectStore.project;
    if (!project) return;

    const currentPages = project.ui.pages;
    const nextPages = redoStack[redoStack.length - 1];

    let newSelectedIds = selectedElementIds;
    let newSelectedPageId = selectedPageId;
    if (selectedPageId) {
      const page = nextPages.find((p) => p.id === selectedPageId);
      if (!page) {
        newSelectedPageId = nextPages[0]?.id ?? null;
        newSelectedIds = [];
      } else {
        newSelectedIds = selectedElementIds.filter((eid) =>
          page.elements.some((e) => e.id === eid),
        );
      }
    }

    set({
      undoStack: [...undoStack, currentPages],
      redoStack: redoStack.slice(0, -1),
      selectedElementId: newSelectedIds[0] || null,
      selectedElementIds: newSelectedIds,
      selectedPageId: newSelectedPageId,
    });

    projectStore.update({ ui: { ...project.ui, pages: nextPages } });
  },

  touchMutation: () => set({ lastMutationTime: Date.now() }),

  setActiveDragSource: (activeDragSource) => set({ activeDragSource }),
}));
