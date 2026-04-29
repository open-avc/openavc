import type { LogEntryResponse } from "./types";
import { BASE, request } from "./base";

// --- Cloud Connection ---

export interface CloudStatus {
  enabled: boolean;
  connected: boolean;
  system_id: string;
  endpoint: string;
  session_id?: string;
  last_heartbeat?: string;
  uptime?: number;
}

export interface CloudPairResult {
  success: boolean;
  system_id: string;
  endpoint: string;
}

export async function getCloudStatus(): Promise<CloudStatus> {
  return request<CloudStatus>("/cloud/status");
}

export async function cloudPair(
  token: string,
  cloudApiUrl: string = "https://cloud.openavc.com"
): Promise<CloudPairResult> {
  return request<CloudPairResult>("/cloud/pair", {
    method: "POST",
    body: JSON.stringify({ token, cloud_api_url: cloudApiUrl }),
  });
}

export async function cloudUnpair(): Promise<{ success: boolean }> {
  return request<{ success: boolean }>("/cloud/unpair", { method: "POST" });
}

// --- Logs ---

export async function getRecentLogs(
  count = 100,
  category = ""
): Promise<LogEntryResponse[]> {
  const params = new URLSearchParams({ count: String(count) });
  if (category) params.set("category", category);
  return request(`/logs/recent?${params}`);
}

// --- System Updates ---

export interface UpdateStatus {
  current_version: string;
  deployment_type: string;
  can_self_update: boolean;
  update_available: string;
  update_channel: string;
  update_status: string;
  update_progress: number;
  update_error: string;
  rollback_available: boolean;
  rollback_version: string;
}

export interface UpdateCheckResult {
  update_available: boolean;
  current_version: string;
  available_version?: string;
  channel: string;
  prerelease?: boolean;
  changelog?: string;
  published_at?: string;
  can_self_update?: boolean;
  deployment_type?: string;
  instructions?: string;
  error?: string;
}

export interface UpdateHistoryEntry {
  from_version: string;
  to_version: string;
  status: string;
  error?: string;
  timestamp: string;
}

export async function checkForUpdates(): Promise<UpdateCheckResult> {
  return request("/system/updates/check");
}

export async function applyUpdate(): Promise<{ success: boolean; message?: string; error?: string }> {
  return request("/system/updates/apply", { method: "POST" });
}

export async function rollbackUpdate(): Promise<{ success: boolean; message?: string; error?: string }> {
  return request("/system/updates/rollback", { method: "POST" });
}

export async function getUpdateStatus(): Promise<UpdateStatus> {
  return request("/system/updates/status");
}

export async function getUpdateHistory(): Promise<UpdateHistoryEntry[]> {
  return request("/system/updates/history");
}

// --- Assets ---

export interface AssetInfo {
  name: string;
  size: number;
  type: string;
}

export async function listAssets(): Promise<{ assets: AssetInfo[]; total_size: number }> {
  return request("/projects/default/assets");
}

export async function uploadAsset(file: File): Promise<{ name: string; reference: string; size: number }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${BASE}/projects/default/assets`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Upload failed: ${body}`);
  }
  return res.json();
}

export async function deleteAsset(filename: string): Promise<{ status: string }> {
  return request(`/projects/default/assets/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
}

export function getAssetUrl(filename: string): string {
  return `${BASE}/projects/default/assets/${encodeURIComponent(filename)}`;
}

// --- Themes ---

export interface ThemeSummary {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  preview_colors: string[];
  /** Full theme variables — used by the Theme Studio picker to render rich
   *  per-card previews (font + button colors + surface) without per-card fetches. */
  variables: Record<string, unknown>;
  source: string;
}

export interface ThemeDefinition {
  id: string;
  name: string;
  version: string;
  author: string;
  description: string;
  preview_colors: string[];
  variables: Record<string, unknown>;
  element_defaults: Record<string, Record<string, unknown>>;
  page_defaults?: Record<string, unknown>;
  _source?: string;
}

export async function listThemes(): Promise<ThemeSummary[]> {
  return request<ThemeSummary[]>("/themes");
}

export async function getTheme(themeId: string): Promise<ThemeDefinition> {
  return request<ThemeDefinition>(`/themes/${themeId}`);
}

export async function createTheme(data: ThemeDefinition): Promise<{ status: string; id: string }> {
  return request("/themes", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateTheme(themeId: string, data: ThemeDefinition): Promise<{ status: string }> {
  return request(`/themes/${themeId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteTheme(themeId: string): Promise<{ status: string }> {
  return request(`/themes/${themeId}`, { method: "DELETE" });
}

export async function importTheme(file: File): Promise<{ status: string; id: string; name: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${BASE}/themes/import`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Import failed: ${body}`);
  }
  return res.json();
}

// --- System Config ---

export interface SystemConfig {
  network: { http_port: number; bind_address: string; control_interface: string };
  auth: { programmer_username: string; programmer_password: string; api_key: string; panel_lock_code: string };
  isc: { enabled: boolean; discovery_enabled: boolean; auth_key: string };
  logging: { level: string; file_enabled: boolean; max_size_mb: number; max_files: number };
  updates: { check_enabled: boolean; channel: string; auto_check_interval_hours: number; auto_backup_before_update: boolean; notify_only: boolean };
  cloud: { enabled: boolean; endpoint: string; system_key: string; system_id: string };
  kiosk: { enabled: boolean; target_url: string; cursor_visible: boolean };
}

export async function getSystemVersion(): Promise<{
  version: string;
  channel: string;
  platform: string;
  kiosk_available: boolean;
}> {
  return request("/system/version");
}

export async function getSystemConfig(): Promise<SystemConfig> {
  return request("/system/config");
}

export async function updateSystemConfig(
  data: Partial<SystemConfig>
): Promise<{ success: boolean; updated_sections: string[] }> {
  return request("/system/config", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function rebootSystem(): Promise<{ status: string }> {
  return request("/system/reboot", { method: "POST" });
}

// --- Network Adapters ---

export interface NetworkAdapter {
  name: string;
  ip: string;
  subnet: string;
  mac: string;
}

export async function getNetworkAdapters(): Promise<{ adapters: NetworkAdapter[] }> {
  return request("/network/adapters");
}
