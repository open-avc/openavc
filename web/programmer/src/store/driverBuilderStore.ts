import { create } from "zustand";
import yaml from "js-yaml";
import type { DriverDefinition, DriverInfo, CommunityDriver, InstalledDriver } from "../api/types";
import * as api from "../api/restClient";
import { parseApiError } from "../api/errors";

const EMPTY_DEFINITION: DriverDefinition = {
  id: "",
  name: "",
  manufacturer: "Generic",
  category: "utility",
  version: "1.0.0",
  author: "",
  description: "",
  transport: "tcp",
  delimiter: "\\r",
  default_config: {},
  config_schema: {},
  state_variables: {},
  commands: {},
  responses: [],
  polling: {},
  frame_parser: null,
};

interface DriverBuilderState {
  definitions: DriverDefinition[];
  selectedId: string | null;
  /** Selection in the Installed tab — separate from the editor's selectedId
   *  so navigating between tabs preserves both contexts independently. */
  installedDriverId: string | null;
  draft: DriverDefinition;
  dirty: boolean;
  saving: boolean;
  loading: boolean;
  error: string | null;

  // All registered drivers (from GET /drivers)
  registeredDrivers: DriverInfo[];

  // Community driver state
  communityDrivers: CommunityDriver[];
  installedDrivers: InstalledDriver[];
  communityLoading: boolean;
  communityError: string | null;

  loadDefinitions: () => Promise<void>;
  selectDriver: (id: string | null) => void;
  setInstalledDriverId: (id: string | null) => void;
  newDriver: () => void;
  updateDraft: (partial: Partial<DriverDefinition>) => void;
  save: () => Promise<void>;
  deleteDriver: (id: string) => Promise<void>;
  importDriver: (definition: DriverDefinition) => Promise<void>;
  exportDriver: (id: string) => void;
  duplicateDriver: (id: string) => Promise<void>;

  // Driver actions
  loadRegisteredDrivers: () => Promise<void>;
  loadCommunityDrivers: () => Promise<void>;
  loadInstalledDrivers: () => Promise<void>;
  installDriver: (driverId: string, fileUrl: string, minPlatformVersion?: string) => Promise<void>;
  uninstallDriver: (driverId: string) => Promise<void>;
  updateDriver: (driverId: string, fileUrl: string, minPlatformVersion?: string) => Promise<void>;
}

export const useDriverBuilderStore = create<DriverBuilderState>((set, get) => ({
  definitions: [],
  selectedId: null,
  installedDriverId: null,
  draft: { ...EMPTY_DEFINITION },
  dirty: false,
  saving: false,
  loading: false,
  error: null,

  registeredDrivers: [],
  communityDrivers: [],
  installedDrivers: [],
  communityLoading: false,
  communityError: null,

  loadDefinitions: async () => {
    set({ loading: true, error: null });
    try {
      const defs = await api.listDriverDefinitions();
      set({ definitions: defs, loading: false });
    } catch (e) {
      set({ error: parseApiError(e), loading: false });
    }
  },

  setInstalledDriverId: (id) => set({ installedDriverId: id }),

  selectDriver: (id) => {
    const { definitions } = get();
    if (id === null) {
      set({ selectedId: null, draft: { ...EMPTY_DEFINITION }, dirty: false });
      return;
    }
    const found = definitions.find((d) => d.id === id);
    if (found) {
      set({ selectedId: id, draft: structuredClone(found), dirty: false, error: null });
    }
  },

  newDriver: () => {
    set({
      selectedId: null,
      draft: { ...EMPTY_DEFINITION },
      dirty: true,
      error: null,
    });
  },

  updateDraft: (partial) => {
    const { draft } = get();
    set({ draft: { ...draft, ...partial }, dirty: true });
  },

  save: async () => {
    const { draft, selectedId } = get();
    if (!draft.id || !draft.name) {
      set({ error: "ID and Name are required" });
      return;
    }
    set({ saving: true, error: null });
    try {
      if (selectedId) {
        await api.updateDriverDefinition(selectedId, draft);
      } else {
        await api.createDriverDefinition(draft);
      }
      set({ saving: false, dirty: false, selectedId: draft.id });
      await get().loadDefinitions();
    } catch (e) {
      set({ saving: false, error: parseApiError(e) });
    }
  },

  deleteDriver: async (id) => {
    try {
      await api.deleteDriverDefinition(id);
      const { selectedId } = get();
      if (selectedId === id) {
        set({ selectedId: null, draft: { ...EMPTY_DEFINITION }, dirty: false });
      }
      await get().loadDefinitions();
    } catch (e) {
      set({ error: parseApiError(e) });
    }
  },

  importDriver: async (definition) => {
    if (!definition.id || !definition.name || !definition.transport) {
      set({ error: "Invalid driver definition: missing id, name, or transport" });
      return;
    }
    set({ saving: true, error: null });
    try {
      await api.createDriverDefinition(definition);
      set({ saving: false, selectedId: definition.id });
      await get().loadDefinitions();
      get().selectDriver(definition.id);
    } catch (e) {
      set({ saving: false, error: parseApiError(e) });
    }
  },

  exportDriver: (id) => {
    const { definitions } = get();
    const def = definitions.find((d) => d.id === id);
    if (!def) return;
    // Export as YAML to match community driver format
    const content = yaml.dump(def, {
      lineWidth: 120,
      noCompatMode: true,
      quotingType: '"',
    });
    const blob = new Blob([content], { type: "application/x-avcdriver" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${def.id}.avcdriver`;
    a.click();
    URL.revokeObjectURL(url);
  },

  duplicateDriver: async (id) => {
    const { definitions } = get();
    const original = definitions.find((d) => d.id === id);
    if (!original) return;

    // Pick a unique id and name for the copy. `<id>_copy`, `<id>_copy2`, …
    const existingIds = new Set(definitions.map((d) => d.id));
    let suffix = "_copy";
    let counter = 1;
    while (existingIds.has(`${original.id}${suffix}`)) {
      counter += 1;
      suffix = `_copy${counter}`;
    }
    const newId = `${original.id}${suffix}`;
    const newName = original.name
      ? `${original.name} (Copy${counter > 1 ? ` ${counter}` : ""})`
      : newId;

    // Deep-clone so we don't mutate the original. Drop the verified flag —
    // verification is server-controlled and the copy hasn't been validated.
    const copy: DriverDefinition = {
      ...structuredClone(original),
      id: newId,
      name: newName,
      verified: undefined,
    };

    set({ saving: true, error: null });
    try {
      await api.createDriverDefinition(copy);
      await get().loadDefinitions();
      get().selectDriver(newId);
      set({ saving: false });
    } catch (e) {
      set({ saving: false, error: parseApiError(e) });
    }
  },

  loadRegisteredDrivers: async () => {
    try {
      const drivers = await api.listDrivers();
      set({ registeredDrivers: drivers });
    } catch (e) {
      console.error("Failed to load registered drivers:", e);
    }
  },

  loadCommunityDrivers: async () => {
    set({ communityLoading: true, communityError: null });
    try {
      const drivers = await api.fetchCommunityDrivers();
      set({ communityDrivers: drivers, communityLoading: false });
    } catch (e) {
      set({ communityError: parseApiError(e), communityLoading: false });
    }
  },

  loadInstalledDrivers: async () => {
    try {
      const drivers = await api.listInstalledDrivers();
      set({ installedDrivers: drivers });
    } catch (e) {
      console.error("Failed to load installed drivers:", e);
    }
  },

  installDriver: async (driverId, fileUrl, minPlatformVersion) => {
    try {
      await api.installCommunityDriver(driverId, fileUrl, minPlatformVersion);
      // Refresh all lists
      await Promise.all([
        get().loadRegisteredDrivers(),
        get().loadInstalledDrivers(),
        get().loadDefinitions(),
      ]);
    } catch (e) {
      throw e;
    }
  },

  uninstallDriver: async (driverId) => {
    try {
      await api.uninstallDriver(driverId);
    } catch (e) {
      throw new Error(parseApiError(e));
    }
    // Refresh all lists
    await Promise.all([
      get().loadRegisteredDrivers(),
      get().loadInstalledDrivers(),
    ]);
  },

  updateDriver: async (driverId, fileUrl, minPlatformVersion) => {
    try {
      await api.updateCommunityDriver(driverId, fileUrl, minPlatformVersion);
    } catch (e) {
      throw new Error(parseApiError(e));
    }
    await Promise.all([
      get().loadRegisteredDrivers(),
      get().loadInstalledDrivers(),
      get().loadDefinitions(),
    ]);
  },
}));
