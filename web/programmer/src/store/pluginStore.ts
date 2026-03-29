/**
 * Plugin store — manages plugin list, status, configuration, extensions,
 * and community browse/install.
 *
 * Fetches from /api/plugins endpoints. Plugin state updates flow
 * through the normal connectionStore (state.update WS messages).
 */
import { create } from "zustand";
import type { PluginInfo } from "../api/types";
import type { PluginExtension, CommunityPlugin, InstalledPlugin } from "../api/restClient";
import * as api from "../api/restClient";

interface PluginExtensions {
  views: PluginExtension[];
  device_panels: PluginExtension[];
  status_cards: PluginExtension[];
  context_actions: PluginExtension[];
  panel_elements: PluginExtension[];
}

interface PluginStore {
  // ── Installed Plugins ──
  plugins: PluginInfo[];
  extensions: PluginExtensions;
  loading: boolean;
  error: string | null;
  selectedId: string | null;

  load: () => Promise<void>;
  loadExtensions: () => Promise<void>;
  setSelectedId: (id: string | null) => void;
  enablePlugin: (pluginId: string) => Promise<void>;
  disablePlugin: (pluginId: string) => Promise<void>;
  updateConfig: (pluginId: string, config: Record<string, unknown>) => Promise<void>;
  activatePlugin: (pluginId: string) => Promise<void>;
  updatePluginStatus: (pluginId: string, status: string) => void;

  // ── Community Browse ──
  communityPlugins: CommunityPlugin[];
  installedPlugins: InstalledPlugin[];
  communityLoading: boolean;
  communityError: string | null;
  installingIds: Set<string>;

  loadCommunity: () => Promise<void>;
  loadInstalled: () => Promise<void>;
  installCommunityPlugin: (pluginId: string, fileUrl: string) => Promise<void>;
  uninstallPlugin: (pluginId: string) => Promise<void>;
  updateCommunityPlugin: (pluginId: string, fileUrl: string) => Promise<void>;
}

const EMPTY_EXTENSIONS: PluginExtensions = {
  views: [],
  device_panels: [],
  status_cards: [],
  context_actions: [],
  panel_elements: [],
};

export const usePluginStore = create<PluginStore>((set, get) => ({
  // ── Installed Plugins ──
  plugins: [],
  extensions: EMPTY_EXTENSIONS,
  loading: false,
  error: null,
  selectedId: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const plugins = await api.listPlugins();
      set({ plugins, loading: false });
      get().loadExtensions();
    } catch (e) {
      set({ error: String(e), loading: false });
    }
  },

  loadExtensions: async () => {
    try {
      const extensions = await api.getPluginExtensions();
      set({ extensions });
    } catch {
      // Extensions are optional
    }
  },

  setSelectedId: (id) => set({ selectedId: id }),

  enablePlugin: async (pluginId) => {
    try {
      await api.enablePlugin(pluginId);
      await get().load();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  disablePlugin: async (pluginId) => {
    try {
      await api.disablePlugin(pluginId);
      await get().load();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  updateConfig: async (pluginId, config) => {
    try {
      await api.updatePluginConfig(pluginId, config);
      await get().load();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  activatePlugin: async (pluginId) => {
    try {
      await api.activatePlugin(pluginId);
      await get().load();
    } catch (e) {
      set({ error: String(e) });
    }
  },

  updatePluginStatus: (pluginId, status) => {
    set((s) => ({
      plugins: s.plugins.map((p) =>
        p.plugin_id === pluginId ? { ...p, status } : p
      ),
    }));
  },

  // ── Community Browse ──
  communityPlugins: [],
  installedPlugins: [],
  communityLoading: false,
  communityError: null,
  installingIds: new Set(),

  loadCommunity: async () => {
    set({ communityLoading: true, communityError: null });
    try {
      const [community, installed] = await Promise.all([
        api.browseCommunityPlugins(),
        api.listInstalledPlugins(),
      ]);
      set({
        communityPlugins: community.plugins,
        installedPlugins: installed.plugins,
        communityLoading: false,
        communityError: community.error,
      });
    } catch (e) {
      set({ communityError: String(e), communityLoading: false });
    }
  },

  loadInstalled: async () => {
    try {
      const result = await api.listInstalledPlugins();
      set({ installedPlugins: result.plugins });
    } catch {
      // Non-critical
    }
  },

  installCommunityPlugin: async (pluginId, fileUrl) => {
    set((s) => ({ installingIds: new Set(s.installingIds).add(pluginId) }));
    try {
      await api.installPlugin(pluginId, fileUrl);
      await Promise.all([get().load(), get().loadInstalled()]);
    } finally {
      set((s) => {
        const next = new Set(s.installingIds);
        next.delete(pluginId);
        return { installingIds: next };
      });
    }
  },

  uninstallPlugin: async (pluginId) => {
    try {
      await api.uninstallPlugin(pluginId);
      await Promise.all([get().load(), get().loadInstalled()]);
    } catch (e) {
      set({ error: String(e) });
    }
  },

  updateCommunityPlugin: async (pluginId, fileUrl) => {
    set((s) => ({ installingIds: new Set(s.installingIds).add(pluginId) }));
    try {
      await api.updatePlugin(pluginId, fileUrl);
      await Promise.all([get().load(), get().loadInstalled()]);
    } finally {
      set((s) => {
        const next = new Set(s.installingIds);
        next.delete(pluginId);
        return { installingIds: next };
      });
    }
  },
}));
