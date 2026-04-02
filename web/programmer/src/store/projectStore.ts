import { create } from "zustand";
import type { ProjectConfig } from "../api/types";
import * as api from "../api/restClient";

interface UndoEntry {
  description: string;
  snapshot: Partial<ProjectConfig>;
}

const MAX_UNDO = 50;

interface ProjectStore {
  project: ProjectConfig | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  dirty: boolean;

  // Undo/redo
  undoStack: UndoEntry[];
  redoStack: UndoEntry[];
  lastUndoDescription: string;

  load: () => Promise<void>;
  save: (retryCount?: number) => Promise<void>;
  update: (patch: Partial<ProjectConfig>) => void;
  updateWithUndo: (patch: Partial<ProjectConfig>, description: string) => void;
  updateProject: (patch: Partial<ProjectConfig["project"]>) => void;
  setProject: (project: ProjectConfig) => void;
  undo: () => void;
  redo: () => void;
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  project: null,
  loading: false,
  saving: false,
  error: null,
  dirty: false,

  undoStack: [],
  redoStack: [],
  lastUndoDescription: "",

  load: async () => {
    // Skip reload if we have local unsaved changes (prevents save/reload race)
    if (get().dirty) return;
    set({ loading: true, error: null });
    try {
      const project = await api.getProject();
      // Double-check dirty hasn't been set while we were fetching
      if (!get().dirty) {
        set({ project, loading: false, dirty: false });
      } else {
        set({ loading: false });
      }
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  save: async (retryCount = 0) => {
    const { project } = get();
    if (!project) return;
    set({ saving: true, error: null });
    try {
      await api.saveProject(project);
      set({ saving: false, dirty: false });
    } catch (e) {
      const maxRetries = 2;
      if (retryCount < maxRetries) {
        const delay = (retryCount + 1) * 1000;
        setTimeout(() => get().save(retryCount + 1), delay);
        set({ error: `Save failed, retrying in ${delay / 1000}s...`, saving: false });
      } else {
        set({ error: String(e), saving: false });
      }
    }
  },

  update: (patch) => {
    const { project } = get();
    if (!project) return;
    set({ project: { ...project, ...patch }, dirty: true });
  },

  updateWithUndo: (patch, description) => {
    const { project, undoStack } = get();
    if (!project) return;

    // Snapshot only the keys being changed
    const snapshot: Partial<ProjectConfig> = {};
    for (const key of Object.keys(patch) as (keyof ProjectConfig)[]) {
      (snapshot as Record<string, unknown>)[key] = project[key];
    }

    set({
      project: { ...project, ...patch },
      dirty: true,
      undoStack: [...undoStack.slice(-(MAX_UNDO - 1)), { description, snapshot }],
      redoStack: [],
      lastUndoDescription: "",
    });
  },

  updateProject: (patch) => {
    const { project } = get();
    if (!project) return;
    set({
      project: { ...project, project: { ...project.project, ...patch } },
      dirty: true,
    });
  },

  setProject: (project) => set({ project, dirty: false }),

  undo: () => {
    const { project, undoStack, redoStack } = get();
    if (!project || undoStack.length === 0) return;

    const entry = undoStack[undoStack.length - 1];

    // Snapshot current state for redo
    const redoSnapshot: Partial<ProjectConfig> = {};
    for (const key of Object.keys(entry.snapshot) as (keyof ProjectConfig)[]) {
      (redoSnapshot as Record<string, unknown>)[key] = project[key];
    }

    set({
      project: { ...project, ...entry.snapshot },
      dirty: true,
      undoStack: undoStack.slice(0, -1),
      redoStack: [...redoStack, { description: entry.description, snapshot: redoSnapshot }],
      lastUndoDescription: `Undo: ${entry.description}`,
    });

    // Auto-save after undo
    setTimeout(() => get().save(), 100);
  },

  redo: () => {
    const { project, undoStack, redoStack } = get();
    if (!project || redoStack.length === 0) return;

    const entry = redoStack[redoStack.length - 1];

    // Snapshot current state for undo
    const undoSnapshot: Partial<ProjectConfig> = {};
    for (const key of Object.keys(entry.snapshot) as (keyof ProjectConfig)[]) {
      (undoSnapshot as Record<string, unknown>)[key] = project[key];
    }

    set({
      project: { ...project, ...entry.snapshot },
      dirty: true,
      undoStack: [...undoStack, { description: entry.description, snapshot: undoSnapshot }],
      redoStack: redoStack.slice(0, -1),
      lastUndoDescription: `Redo: ${entry.description}`,
    });

    // Auto-save after redo
    setTimeout(() => get().save(), 100);
  },
}));
