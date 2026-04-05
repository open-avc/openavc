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
  revision: number | null;  // server revision counter
  conflictDetected: boolean;  // true when 409 received

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
  dismissConflict: () => void;
  forceReload: () => Promise<void>;
  undo: () => void;
  redo: () => void;
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  project: null,
  loading: false,
  saving: false,
  error: null,
  dirty: false,
  revision: null,
  conflictDetected: false,

  undoStack: [],
  redoStack: [],
  lastUndoDescription: "",

  load: async () => {
    // Skip reload if we have local unsaved changes (prevents save/reload race)
    if (get().dirty) return;
    set({ loading: true, error: null });
    try {
      const raw = await api.getProject();
      // Extract runtime revision from response
      const revision = (raw as any)._revision ?? null;
      delete (raw as any)._revision;
      // Double-check dirty hasn't been set while we were fetching
      if (!get().dirty) {
        set({ project: raw, loading: false, dirty: false, revision, conflictDetected: false });
      } else {
        set({ loading: false });
      }
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  save: async (retryCount = 0) => {
    const { project, revision } = get();
    if (!project) return;
    set({ saving: true, error: null });
    try {
      const result = await api.saveProject(project, revision ?? undefined);
      set({ saving: false, dirty: false, revision: result.revision ?? null, conflictDetected: false });
    } catch (e) {
      // Handle 409 Conflict — another session modified the project
      if (String(e).includes("409")) {
        set({ saving: false, conflictDetected: true, error: "Project was modified by another session. Reload to see the latest changes." });
        return;
      }
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

  dismissConflict: () => set({ conflictDetected: false, error: null }),

  forceReload: async () => {
    set({ loading: true, error: null, dirty: false, conflictDetected: false });
    try {
      const raw = await api.getProject();
      const revision = (raw as any)._revision ?? null;
      delete (raw as any)._revision;
      set({ project: raw, loading: false, dirty: false, revision, conflictDetected: false, undoStack: [], redoStack: [] });
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

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
