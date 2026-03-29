import { create } from "zustand";
import type { ProjectConfig } from "../api/types";
import * as api from "../api/restClient";

interface ProjectStore {
  project: ProjectConfig | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  dirty: boolean;

  load: () => Promise<void>;
  save: (retryCount?: number) => Promise<void>;
  update: (patch: Partial<ProjectConfig>) => void;
  updateProject: (patch: Partial<ProjectConfig["project"]>) => void;
  setProject: (project: ProjectConfig) => void;
}

export const useProjectStore = create<ProjectStore>((set, get) => ({
  project: null,
  loading: false,
  saving: false,
  error: null,
  dirty: false,

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

  updateProject: (patch) => {
    const { project } = get();
    if (!project) return;
    set({
      project: { ...project, project: { ...project.project, ...patch } },
      dirty: true,
    });
  },

  setProject: (project) => set({ project, dirty: false }),
}));
