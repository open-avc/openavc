import type { PluginInfo, SchemaField } from "./types";
import { request } from "./base";

// --- Plugins ---

export async function listPlugins(): Promise<PluginInfo[]> {
  return (await request<{ plugins: PluginInfo[] }>("/plugins")).plugins;
}

export async function getPlugin(pluginId: string): Promise<PluginInfo> {
  return request<PluginInfo>(`/plugins/${pluginId}`);
}

export async function enablePlugin(pluginId: string): Promise<{ status: string; plugin_id: string; config: Record<string, unknown> }> {
  return request(`/plugins/${pluginId}/enable`, { method: "POST" });
}

export async function disablePlugin(pluginId: string): Promise<{ status: string; plugin_id: string }> {
  return request(`/plugins/${pluginId}/disable`, { method: "POST" });
}

export async function getPluginConfig(pluginId: string): Promise<{ plugin_id: string; config: Record<string, unknown> }> {
  return request(`/plugins/${pluginId}/config`);
}

export async function updatePluginConfig(
  pluginId: string,
  config: Record<string, unknown>,
): Promise<{
  status: string;
  applied?: string;
  warning?: string;
  missing_required?: string[];
}> {
  return request(`/plugins/${pluginId}/config`, {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function removePluginConfig(pluginId: string): Promise<{ status: string; plugin_id: string }> {
  return request(`/plugins/${pluginId}/config`, { method: "DELETE" });
}

export async function getPluginHealth(pluginId: string): Promise<{ status: string; message: string }> {
  return request(`/plugins/${pluginId}/health`);
}

export async function activatePlugin(pluginId: string): Promise<{ activated: boolean; reason?: string }> {
  return request(`/plugins/${pluginId}/activate`, { method: "POST" });
}

export async function getPluginSetupFields(pluginId: string): Promise<{ plugin_id: string; setup_required: boolean; fields: Record<string, SchemaField> }> {
  return request(`/plugins/${pluginId}/setup-fields`);
}

export async function getPluginExtensions(): Promise<{
  views: PluginExtension[];
  device_panels: PluginExtension[];
  status_cards: PluginExtension[];
  context_actions: PluginExtension[];
  panel_elements: PluginExtension[];
}> {
  return (
    await request<{
      extensions: {
        views: PluginExtension[];
        device_panels: PluginExtension[];
        status_cards: PluginExtension[];
        context_actions: PluginExtension[];
        panel_elements: PluginExtension[];
      };
    }>("/plugins/extensions")
  ).extensions;
}

export async function emitContextAction(
  pluginId: string,
  actionId: string,
  payload?: Record<string, unknown>
): Promise<{ status: string }> {
  return request(`/plugins/${pluginId}/context-action/${actionId}`, {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

// --- Plugin Macro Actions ---

export type PluginMacroParamType =
  | "text"
  | "integer"
  | "float"
  | "boolean"
  | "select"
  | "state_key"
  | "device_ref"
  | "macro_ref";

export interface PluginMacroActionParam {
  key: string;
  type: PluginMacroParamType;
  label?: string;
  description?: string;
  required?: boolean;
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  /** Static options for `select` type */
  options?: Array<{ value: string | number | boolean; label: string }>;
  /** State key whose JSON value populates the dropdown for `select` type */
  options_source?: string;
}

export interface PluginMacroAction {
  action_type: string;
  plugin_id: string;
  plugin_name: string;
  label: string;
  description?: string;
  icon?: string;
  params: PluginMacroActionParam[];
}

export async function getPluginMacroActions(): Promise<{ actions: PluginMacroAction[] }> {
  return request("/plugins/macro-actions");
}

// --- Plugin Script API ---

export interface PluginScriptMethod {
  plugin_id: string;
  plugin_name: string;
  method: string;
  sync: boolean;
  doc?: string;
}

export async function getPluginScriptApi(): Promise<{ methods: PluginScriptMethod[] }> {
  return request("/plugins/script-api");
}

export interface PluginExtension {
  id: string;
  label: string;
  icon?: string;
  plugin_id: string;
  plugin_name: string;
  renderer?: string;
  state_pattern?: string;
  schema_key?: string;
  config_scope?: string;
  match?: Record<string, unknown>;
  metrics?: { key: string; label: string; format: string }[];
  context?: string;
  event?: string;
  // panel_elements specific
  type?: string;
  renderer_url?: string;
  /** How big the element is when dropped, as a percentage of the page. */
  default_size?: { w: number; h: number };
  config_schema?: Array<{
    key: string;
    label: string;
    type: string;
    default?: unknown;
    /** Static options for `select` type. */
    options?: string[];
    /**
     * For `select` type: state key whose JSON-encoded value populates the
     * dropdown. Plugins must publish a JSON string like
     * `'[{"value": "a", "label": "A"}, ...]'` because state values are flat
     * primitives. Mirrors the plugin macro action convention.
     */
    options_source?: string;
  }>;
}

// --- Plugin Browse / Install ---

export interface CommunityPlugin {
  id: string;
  name: string;
  file: string;
  format: string;
  category: string;
  manufacturer?: string;
  version: string;
  author: string;
  license: string;
  platforms: string[];
  min_openavc_version?: string;
  capabilities: string[];
  has_native_dependencies?: boolean;
  verified: boolean;
  description: string;
}

export interface InstalledPlugin {
  id: string;
  name: string;
  version: string;
  source: string;
  // "loaded" — plugin class registered, ready to enable.
  // "load_failed" — files installed but importing/registering the
  // plugin class failed (missing PLUGIN_INFO, ImportError, etc.).
  // Older servers omit the field; treat absent as "loaded".
  status?: "loaded" | "load_failed";
  // Captured error message when status === "load_failed". Surface to the
  // user as the diagnostic path for A60.
  error?: string;
}

export async function browseCommunityPlugins(): Promise<{ plugins: CommunityPlugin[]; error: string | null }> {
  return request("/plugins/browse");
}

export async function listInstalledPlugins(): Promise<{ plugins: InstalledPlugin[] }> {
  return request("/plugins/installed");
}

export async function installPlugin(
  pluginId: string,
  fileUrl: string,
): Promise<{ status: "installed" | "load_failed"; plugin_id?: string; error?: string }> {
  return request(`/plugins/${pluginId}/install`, {
    method: "POST",
    body: JSON.stringify({ file_url: fileUrl }),
  });
}

export async function updatePlugin(
  pluginId: string,
  fileUrl: string,
): Promise<{
  // "installed" — new version is live.
  // "load_failed" — files installed but the new class won't register.
  // "update_failed" — reinstall failed (network/min-version/broken code) and
  //   the previous working version was restored (rolled_back: true).
  status: "installed" | "load_failed" | "update_failed";
  plugin_id?: string;
  restarted?: boolean;
  rolled_back?: boolean;
  error?: string;
}> {
  return request(`/plugins/${pluginId}/update`, {
    method: "POST",
    body: JSON.stringify({ file_url: fileUrl }),
  });
}

export async function uninstallPlugin(
  pluginId: string,
  options?: { removeData?: boolean },
): Promise<{ status: string; data_removed?: boolean }> {
  const query = options?.removeData ? "?remove_data=true" : "";
  return request(`/plugins/${pluginId}${query}`, { method: "DELETE" });
}

export interface PluginDataInfo {
  plugin_id: string;
  exists: boolean;
  size_bytes: number;
}

export async function getPluginDataInfo(pluginId: string): Promise<PluginDataInfo> {
  return request(`/plugins/${pluginId}/data-info`);
}

export type { PluginInfo, SchemaField } from "./types";
