import type { PluginInfo, SchemaField } from "./types";
import { request } from "./base";

// --- Plugins ---

export async function listPlugins(): Promise<PluginInfo[]> {
  return request<PluginInfo[]>("/plugins");
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

export async function updatePluginConfig(pluginId: string, config: Record<string, unknown>): Promise<{ status: string }> {
  return request(`/plugins/${pluginId}/config`, {
    method: "PUT",
    body: JSON.stringify(config),
  });
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
  return request("/plugins/extensions");
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
  default_size?: { col_span: number; row_span: number };
  config_schema?: Record<string, { type: string; label: string; default?: unknown }>;
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
}

export async function browseCommunityPlugins(): Promise<{ plugins: CommunityPlugin[]; error: string | null }> {
  return request("/plugins/browse");
}

export async function listInstalledPlugins(): Promise<{ plugins: InstalledPlugin[] }> {
  return request("/plugins/installed");
}

export async function installPlugin(pluginId: string, fileUrl: string): Promise<{ status: string }> {
  return request(`/plugins/${pluginId}/install`, {
    method: "POST",
    body: JSON.stringify({ file_url: fileUrl }),
  });
}

export async function updatePlugin(pluginId: string, fileUrl: string): Promise<{ status: string }> {
  return request(`/plugins/${pluginId}/update`, {
    method: "POST",
    body: JSON.stringify({ file_url: fileUrl }),
  });
}

export async function uninstallPlugin(pluginId: string): Promise<{ status: string }> {
  return request(`/plugins/${pluginId}`, { method: "DELETE" });
}

export type { PluginInfo, SchemaField } from "./types";
