import { create } from "zustand";
import yaml from "js-yaml";
import type { DriverDefinition, DriverInfo, CommunityDriver, InstalledDriver } from "../api/types";
import * as api from "../api/restClient";
import { apiErrorList, parseApiError } from "../api/errors";
import { showError } from "./toastStore";
import type { SaveRefusal } from "../components/driver-builder/saveRefusal";
import {
  reconcileAfterSave,
  makeLatestWins,
  importBlockers,
  cloneDraft,
  DRIVER_YAML_DUMP_OPTIONS,
} from "./driverBuilderStore.helpers";
import {
  addMissingConnectionFields,
  connectionFieldsFor,
  secretFieldsInConfig,
} from "../components/driver-builder/transportPickerHelpers";

const EMPTY_DEFINITION: DriverDefinition = {
  id: "",
  name: "",
  manufacturer: "Generic",
  category: "utility",
  version: "1.0.0",
  author: "",
  description: "",
  transport: "tcp",
  // Real CR, not the escaped text form — matches what YAML decoding gives
  // every installed driver, so the delimiter dropdown recognizes it.
  delimiter: "\r",
  default_config: {},
  config_schema: connectionFieldsFor("tcp"),
  state_variables: {},
  commands: {},
  responses: [],
  polling: {},
  frame_parser: null,
  send_frame: null,
};

// Latest-wins guards for the async list loaders. Overlapping refreshes (e.g. an
// install and an uninstall fired in quick succession) each take a token; only
// the newest-started load applies its result, so the cached lists can't settle
// on a stale snapshot just because an earlier GET happened to resolve last.
const defGuard = makeLatestWins();
const regGuard = makeLatestWins();
const instGuard = makeLatestWins();
const communityGuard = makeLatestWins();

/** Ask before throwing away an unsaved draft. Returns true when it's safe to
 *  proceed (nothing dirty, or the user confirmed the discard). */
function confirmDiscardDraft(dirty: boolean): boolean {
  if (!dirty) return true;
  if (typeof window === "undefined" || typeof window.confirm !== "function") {
    return true;
  }
  return window.confirm("You have unsaved driver changes. Discard them?");
}

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
  /** The last save the server refused with a list of errors, kept with the
   *  draft it refused so the editor can tell whether it still applies. */
  saveRefusal: SaveRefusal | null;
  /** Comment lines in the selected driver's file on disk. The Builder edits a
   *  parsed structure, so a save rewrites the file from that structure and the
   *  comments are gone — this is what the editor warns about before it happens.
   *  Kept out of `draft` on purpose: in the draft it would show up in the live
   *  YAML preview and in an exported .avcdriver. */
  commentLines: number;
  /** Connection fields the editor added to the selected driver when it was
   *  opened (a network driver saved by an earlier Builder has none). Kept out
   *  of `dirty` so opening such a driver to look at it and moving on asks
   *  nothing; the editor offers Save while this is non-empty. */
  addedConnectionFields: string[];

  // All registered drivers (from GET /drivers)
  registeredDrivers: DriverInfo[];

  // Community driver state
  communityDrivers: CommunityDriver[];
  installedDrivers: InstalledDriver[];
  communityLoading: boolean;
  communityError: string | null;
  /** The catalog was asked for at least once this session (it may have failed). */
  communityLoaded: boolean;
  /** Drivers being updated right now, from any screen that offers Update. */
  updatingDriverIds: Set<string>;

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
  /** `refresh` fetches the catalog now instead of the server's cached copy. */
  loadCommunityDrivers: (refresh?: boolean) => Promise<void>;
  loadInstalledDrivers: () => Promise<void>;
  /** Reload every list a driver install, update or removal changes. Called
   *  when the server says a driver changed, whoever changed it. */
  refreshDriverLists: () => Promise<void>;
  installDriver: (driverId: string, fileUrl: string, minPlatformVersion?: string) => Promise<void>;
  uninstallDriver: (driverId: string) => Promise<void>;
  updateDriver: (driverId: string, fileUrl: string, minPlatformVersion?: string) => Promise<string[]>;
}

/** A saved driver as the editor opens it: a network driver gets the
 *  connection fields it lacks, so Add Device can take its address. A built-in
 *  is read-only and is shown as it is. */
function draftForEditing(definition: DriverDefinition): {
  draft: DriverDefinition;
  added: string[];
} {
  const draft = cloneDraft(definition);
  if ((definition as { source?: string }).source === "builtin") return { draft, added: [] };
  const { schema, added } = addMissingConnectionFields(
    draft.config_schema as Record<string, unknown> | undefined,
    draft.transport,
  );
  if (added.length > 0) draft.config_schema = schema as DriverDefinition["config_schema"];
  return { draft, added };
}

export const useDriverBuilderStore = create<DriverBuilderState>((set, get) => {
  // Raw selection — clones the definition into the editor draft with no dirty
  // guard. Used by the public selectDriver (which guards first) and by the
  // post-success internal selects in importDriver/duplicateDriver, where the
  // prior draft was already intentionally consumed and re-prompting would be a
  // spurious second confirm.
  const applySelection = (id: string | null) => {
    const { definitions } = get();
    if (id === null) {
      set({
        selectedId: null,
        draft: { ...EMPTY_DEFINITION },
        dirty: false,
        commentLines: 0,
        addedConnectionFields: [],
      });
      return;
    }
    const found = definitions.find((d) => d.id === id);
    if (found) {
      const commentLines =
        (found as unknown as { _comment_lines?: number })._comment_lines ?? 0;
      const { draft, added } = draftForEditing(found);
      set({
        selectedId: id,
        draft,
        dirty: false,
        error: null,
        commentLines,
        addedConnectionFields: added,
      });
    }
  };

  return {
    definitions: [],
    selectedId: null,
    installedDriverId: null,
    draft: { ...EMPTY_DEFINITION },
    dirty: false,
    saving: false,
    loading: false,
    error: null,
    saveRefusal: null,
    commentLines: 0,
    addedConnectionFields: [],

    registeredDrivers: [],
    communityDrivers: [],
    installedDrivers: [],
    communityLoading: false,
    communityError: null,
    communityLoaded: false,
    updatingDriverIds: new Set<string>(),

    loadDefinitions: async () => {
      const token = defGuard.next();
      set({ loading: true, error: null });
      try {
        const defs = await api.listDriverDefinitions();
        if (!defGuard.isCurrent(token)) return;
        set({ definitions: defs, loading: false });
      } catch (e) {
        if (!defGuard.isCurrent(token)) return;
        set({ error: parseApiError(e), loading: false });
      }
    },

    setInstalledDriverId: (id) => set({ installedDriverId: id }),

    selectDriver: (id) => {
      if (!confirmDiscardDraft(get().dirty)) return;
      applySelection(id);
    },

    newDriver: () => {
      if (!confirmDiscardDraft(get().dirty)) return;
      set({
        selectedId: null,
        draft: { ...EMPTY_DEFINITION },
        dirty: true,
        error: null,
        commentLines: 0,
        addedConnectionFields: [],
      });
    },

    updateDraft: (partial) => {
      const { draft } = get();
      set({ draft: { ...draft, ...partial }, dirty: true });
    },

    save: async () => {
      // Snapshot the draft + selection at save start. The editor inputs stay
      // live during the network await, so we compare identity afterwards to
      // tell "kept typing" from "left it alone" and reconcile without losing
      // edits (see reconcileAfterSave).
      const capturedDraft = get().draft;
      const selectionAtStart = get().selectedId;
      if (!capturedDraft.id || !capturedDraft.name) {
        set({ error: "ID and Name are required" });
        return;
      }
      const savedId = capturedDraft.id;
      set({ saving: true, error: null, saveRefusal: null });
      try {
        if (selectionAtStart) {
          await api.updateDriverDefinition(selectionAtStart, capturedDraft);
        } else {
          await api.createDriverDefinition(capturedDraft);
        }
        set({
          ...reconcileAfterSave({
            savedId,
            draftUnchanged: get().draft === capturedDraft,
            selectionUnchanged: get().selectedId === selectionAtStart,
          }),
          addedConnectionFields: [],
        });
        await get().loadDefinitions();
      } catch (e) {
        const text = parseApiError(e);
        const errors = apiErrorList(e);
        set({
          saving: false,
          error: text,
          saveRefusal: errors ? { text, errors, draft: capturedDraft } : null,
        });
      }
    },

    deleteDriver: async (id) => {
      try {
        await api.deleteDriverDefinition(id);
        const { selectedId } = get();
        if (selectedId === id) {
          set({
            selectedId: null,
            draft: { ...EMPTY_DEFINITION },
            dirty: false,
            addedConnectionFields: [],
          });
        }
        await get().loadDefinitions();
      } catch (e) {
        set({ error: parseApiError(e) });
      }
    },

    importDriver: async (definition) => {
      if (!confirmDiscardDraft(get().dirty)) return;
      // Validate before POSTing. A driver with blocking problems loads into the
      // editor as a dirty draft so the user fixes it against the same inline
      // IssueList the form editor shows, instead of getting a terse backend 422.
      const blockers = await importBlockers(definition, get().definitions);
      if (blockers.length > 0) {
        const { draft, added } = draftForEditing(definition);
        set({
          selectedId: null,
          draft,
          addedConnectionFields: added,
          dirty: true,
          saving: false,
          error: `This driver needs fixes before it can be saved: ${blockers.join(" ")}`,
        });
        return;
      }
      set({ saving: true, error: null });
      try {
        await api.createDriverDefinition(definition);
        set({ saving: false });
        await get().loadDefinitions();
        applySelection(definition.id);
      } catch (e) {
        set({ saving: false, error: parseApiError(e) });
      }
    },

    exportDriver: (id) => {
      const { definitions } = get();
      const def = definitions.find((d) => d.id === id);
      if (!def) return;
      // Credentials saved as config defaults land in the exported file as
      // plain text — confirm before writing a shareable file containing them.
      const secrets = secretFieldsInConfig(def.default_config, def.config_schema);
      if (secrets.length > 0) {
        const ok = window.confirm(
          `This driver's default config includes a saved ${secrets.join(" and ")}. ` +
            `The exported file will contain ${secrets.length > 1 ? "them" : "it"} in plain text.\n\nExport anyway?`,
        );
        if (!ok) return;
      }
      // Export as YAML to match community driver format
      const content = yaml.dump(def, DRIVER_YAML_DUMP_OPTIONS);
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
      if (!confirmDiscardDraft(get().dirty)) return;

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
        applySelection(newId);
        set({ saving: false });
      } catch (e) {
        set({ saving: false, error: parseApiError(e) });
      }
    },

    loadRegisteredDrivers: async () => {
      const token = regGuard.next();
      try {
        const drivers = await api.listDrivers();
        if (!regGuard.isCurrent(token)) return;
        set({ registeredDrivers: drivers });
      } catch (e) {
        if (regGuard.isCurrent(token)) {
          console.error("Failed to load registered drivers:", e);
        }
      }
    },

    loadCommunityDrivers: async (refresh) => {
      const token = communityGuard.next();
      set({ communityLoading: true, communityError: null });
      try {
        const drivers = await api.fetchCommunityDrivers(refresh);
        if (!communityGuard.isCurrent(token)) return;
        set({ communityDrivers: drivers, communityLoading: false, communityLoaded: true });
      } catch (e) {
        if (!communityGuard.isCurrent(token)) return;
        set({ communityError: parseApiError(e), communityLoading: false, communityLoaded: true });
      }
    },

    loadInstalledDrivers: async () => {
      const token = instGuard.next();
      try {
        const drivers = await api.listInstalledDrivers();
        if (!instGuard.isCurrent(token)) return;
        set({ installedDrivers: drivers });
      } catch (e) {
        if (instGuard.isCurrent(token)) {
          console.error("Failed to load installed drivers:", e);
        }
      }
    },

    refreshDriverLists: async () => {
      // The loaders are latest-wins guarded, so this overlapping an action's
      // own refresh cannot put a stale list back.
      await Promise.all([
        get().loadRegisteredDrivers(),
        get().loadInstalledDrivers(),
        get().loadDefinitions(),
      ]);
    },

    installDriver: async (driverId, fileUrl, minPlatformVersion) => {
      await api.installCommunityDriver(driverId, fileUrl, minPlatformVersion);
      // Refresh all lists. The loaders are latest-wins guarded, so an
      // overlapping action's refreshes can't clobber these with a stale read.
      await Promise.all([
        get().loadRegisteredDrivers(),
        get().loadInstalledDrivers(),
        get().loadDefinitions(),
      ]);
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
      // One update per driver at a time, whichever screen asked: a second one
      // would rewrite the same files under the first.
      if (get().updatingDriverIds.has(driverId)) {
        throw new Error("This driver is already being updated.");
      }
      const name =
        get().installedDrivers.find((d) => d.id === driverId)?.name ?? driverId;
      set((s) => ({ updatingDriverIds: new Set(s.updatingDriverIds).add(driverId) }));
      let result;
      try {
        result = await api.updateCommunityDriver(driverId, fileUrl, minPlatformVersion);
      } catch (e) {
        throw new Error(parseApiError(e));
      } finally {
        set((s) => {
          const next = new Set(s.updatingDriverIds);
          next.delete(driverId);
          return { updatingDriverIds: next };
        });
      }
      await Promise.all([
        get().loadRegisteredDrivers(),
        get().loadInstalledDrivers(),
        get().loadDefinitions(),
      ]);
      // The update itself landed; these are devices that did not come back on
      // the new code. Not an update error, so it is reported here rather than
      // in the screen's error slot, the same from every screen.
      const stillDown = result.devices_not_reconnected ?? [];
      if (stillDown.length > 0) {
        showError(
          `${name} updated. These devices did not reconnect: ` +
            `${stillDown.join(", ")}. Open Devices to bring them back.`
        );
      }
      return stillDown;
    },
  };
});
