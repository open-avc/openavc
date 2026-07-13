import { create } from "zustand";
import type { ProjectConfig } from "../api/types";
import * as api from "../api/restClient";
import { runSaveWithRetry } from "./projectStoreSave";

interface UndoEntry {
  description: string;
  snapshot: Partial<ProjectConfig>;
}

const MAX_UNDO = 50;

// Module-level debounce timer for debouncedSave
let _saveTimer: ReturnType<typeof setTimeout> | undefined;

// Serializes saves so a second save() call while one is in flight doesn't
// fire a concurrent PUT with a stale ETag (which would 409). When _saveInFlight
// is set, the current save's tail will fire one more save after it completes.
// _saveChain tracks the in-flight save (and any chained re-save) so callers
// that `await save()` see completion of the actual underlying write.
let _saveInFlight = false;
let _resaveQueued = false;
let _saveChain: Promise<void> = Promise.resolve();

interface ProjectStore {
  project: ProjectConfig | null;
  loading: boolean;
  saving: boolean;
  savePending: boolean;  // true while debouncedSave timer is pending
  error: string | null;
  dirty: boolean;
  revision: number | null;  // kept for WebSocket project.reloaded detection
  etag: string | null;  // ETag for optimistic concurrency
  conflictDetected: boolean;  // true when 409 received

  // Undo/redo
  undoStack: UndoEntry[];
  redoStack: UndoEntry[];
  lastUndoDescription: string;

  load: () => Promise<void>;
  save: (retryCount?: number) => Promise<void>;
  debouncedSave: (delay?: number) => void;
  flushSave: () => void;
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
  savePending: false,
  error: null,
  dirty: false,
  revision: null,
  etag: null,
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
      const etag = (raw as any)._etag ?? null;
      delete (raw as any)._etag;
      const revision = etag ? parseInt(etag.replace(/"/g, ""), 10) || null : null;
      if (!get().dirty) {
        set({ project: raw, loading: false, dirty: false, etag, revision, conflictDetected: false });
      } else {
        set({ loading: false });
      }
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  save: async (retryCount = 0): Promise<void> => {
    // Don't stack concurrent saves — they'd send the same stale ETag and 409.
    // Mark that another save is wanted and return the chain promise so callers
    // that `await save()` (e.g. the import flow) wait for the actual write.
    if (_saveInFlight) {
      _resaveQueued = true;
      await _saveChain;
      return;
    }
    if (!get().project) return;

    const performSave = async (): Promise<void> => {
      if (!get().project) return;
      _saveInFlight = true;
      // Retries are awaited inside runSaveWithRetry, so this promise (and thus
      // _saveChain) resolves only after the final underlying write — see
      // projectStoreSave.ts.
      const outcome = await runSaveWithRetry(
        {
          getProject: () => get().project,
          getEtag: () => get().etag,
          saveProject: api.saveProject,
          isConflict: (e) => e instanceof api.ConflictError,
          conflictMessage: (e) => (e as Error).message,
          setState: (patch) => set(patch),
          sleep: (ms) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
        },
        retryCount,
      );
      if (outcome === "conflict") {
        _saveInFlight = false;
        _resaveQueued = false;  // user must reload to clear the conflict
        return;
      }
      _saveInFlight = false;
      // Chain another save only when there's genuinely more to write: the user
      // kept editing during a SUCCESSFUL save (dirty carries editedDuringSave),
      // or a save was queued while we were busy. A failed save also leaves
      // dirty=true, but re-chaining on that would spin forever, so gate on
      // "saved".
      if (_resaveQueued || (outcome === "saved" && get().dirty)) {
        _resaveQueued = false;
        await get().save();
      }
    };

    _saveChain = performSave();
    return _saveChain;
  },

  debouncedSave: (delay = 500) => {
    clearTimeout(_saveTimer);
    set({ savePending: true });
    _saveTimer = setTimeout(() => {
      _saveTimer = undefined;
      get().save();
    }, delay);
  },

  flushSave: () => {
    if (_saveTimer) {
      clearTimeout(_saveTimer);
      _saveTimer = undefined;
      get().save();
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
      const etag = (raw as any)._etag ?? null;
      delete (raw as any)._etag;
      const revision = etag ? parseInt(etag.replace(/"/g, ""), 10) || null : null;
      set({ project: raw, loading: false, dirty: false, etag, revision, conflictDetected: false, undoStack: [], redoStack: [] });
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
    get().debouncedSave(100);
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
    get().debouncedSave(100);
  },
}));
