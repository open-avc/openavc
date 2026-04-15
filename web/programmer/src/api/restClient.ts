import type {
  ProjectConfig,
  DeviceInfo,
  DeviceSettingValue,
  DriverInfo,
  DriverDefinition,
  StateHistoryEntry,
  LogEntryResponse,
  CommunityDriver,
  InstalledDriver,
  ScriptReference,
  LibraryProject,
  LibraryProjectDetail,
  PluginInfo,
  PythonDriverInfo,
  SchemaField,
} from "./types";

// Derive API base path so tunneled remote access works.
// /tunnel/{id}/programmer/ → /tunnel/{id}/api
// /programmer/ → /api
function getBasePath(): string {
  const pathParts = window.location.pathname.split("/programmer");
  const prefix = pathParts[0] || "";
  return `${prefix}/api`;
}
const BASE = getBasePath();

/** Tunnel-aware prefix (e.g. "/tunnel/{id}" or ""). */
export function getTunnelPrefix(): string {
  const pathParts = window.location.pathname.split("/programmer");
  return pathParts[0] || "";
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

// --- Project ---

export async function getProject(): Promise<ProjectConfig> {
  // For large projects, parse JSON in a Web Worker to avoid blocking the main thread
  const res = await fetch(`${BASE}/project`, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  const text = await res.text();

  // Only use worker for large payloads (>500KB)
  if (text.length > 512_000 && typeof Worker !== "undefined") {
    return new Promise<ProjectConfig>((resolve, reject) => {
      const worker = new Worker(
        new URL("../workers/projectParser.ts", import.meta.url),
        { type: "module" }
      );
      worker.onmessage = (e) => {
        worker.terminate();
        if (e.data.ok) resolve(e.data.data);
        else reject(new Error(e.data.error));
      };
      worker.onerror = () => {
        worker.terminate();
        resolve(JSON.parse(text));  // Fallback to main-thread parse
      };
      worker.postMessage(text);
    });
  }

  return JSON.parse(text);
}

export async function getSystemStatus(): Promise<Record<string, unknown>> {
  return request("/status");
}

export async function saveProject(
  project: ProjectConfig,
  revision?: number
): Promise<{ status: string; revision?: number }> {
  const body: Record<string, unknown> = { ...project };
  if (revision !== undefined) {
    body._revision = revision;
  }
  return request("/project", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function reloadProject(): Promise<{ status: string }> {
  return request("/project/reload", { method: "POST" });
}

// --- Devices ---

export async function listDevices(): Promise<DeviceInfo[]> {
  return request("/devices");
}

export async function getDevice(id: string): Promise<DeviceInfo> {
  return request(`/devices/${id}`);
}

export async function sendCommand(
  deviceId: string,
  command: string,
  params: Record<string, unknown> = {}
): Promise<{ success: boolean; result: unknown }> {
  return request(`/devices/${deviceId}/command`, {
    method: "POST",
    body: JSON.stringify({ command, params }),
  });
}

export async function updateDevice(
  deviceId: string,
  data: { name?: string; driver?: string; config?: Record<string, unknown> }
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteDevice(
  deviceId: string
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}`, { method: "DELETE" });
}

export async function testDeviceConnection(
  deviceId: string
): Promise<{ success: boolean; error: string | null; latency_ms: number | null }> {
  return request(`/devices/${deviceId}/test`, { method: "POST" });
}

export async function reconnectDevice(
  deviceId: string
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}/reconnect`, { method: "POST" });
}

// --- Device Settings ---

export async function getDeviceSettings(
  deviceId: string
): Promise<{ device_id: string; settings: Record<string, DeviceSettingValue> }> {
  return request(`/devices/${deviceId}/settings`);
}

export async function setDeviceSetting(
  deviceId: string,
  settingKey: string,
  value: unknown
): Promise<{ success: boolean; device_id: string; key: string; value: unknown }> {
  return request(`/devices/${deviceId}/settings/${settingKey}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

export async function storePendingSettings(
  deviceId: string,
  settings: Record<string, unknown>
): Promise<{ status: string; device_id: string; settings: Record<string, unknown> }> {
  return request(`/devices/${deviceId}/settings/pending`, {
    method: "POST",
    body: JSON.stringify({ settings }),
  });
}

// --- Drivers ---

export async function listDrivers(): Promise<DriverInfo[]> {
  return request("/drivers");
}

// --- Driver Definitions ---

export async function listDriverDefinitions(): Promise<DriverDefinition[]> {
  return request("/driver-definitions");
}

export async function getDriverDefinition(
  id: string
): Promise<DriverDefinition> {
  return request(`/driver-definitions/${id}`);
}

export async function createDriverDefinition(
  definition: DriverDefinition
): Promise<{ status: string; id: string }> {
  return request("/driver-definitions", {
    method: "POST",
    body: JSON.stringify(definition),
  });
}

export async function updateDriverDefinition(
  id: string,
  definition: DriverDefinition
): Promise<{ status: string; id: string }> {
  return request(`/driver-definitions/${id}`, {
    method: "PUT",
    body: JSON.stringify(definition),
  });
}

export async function deleteDriverDefinition(
  id: string
): Promise<{ status: string; id: string }> {
  return request(`/driver-definitions/${id}`, { method: "DELETE" });
}

export async function testDriverCommand(
  driverId: string,
  data: {
    host: string;
    port: number;
    transport: string;
    command_string: string;
    delimiter: string;
    timeout?: number;
  }
): Promise<{ success: boolean; response: string | null; error: string | null }> {
  return request(`/driver-definitions/${driverId}/test-command`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// --- State ---

export async function getState(): Promise<Record<string, unknown>> {
  return request("/state");
}

export async function getStateHistory(
  count = 50
): Promise<StateHistoryEntry[]> {
  return request(`/state/history?count=${count}`);
}

export async function setStateValue(
  key: string,
  value: unknown
): Promise<{ key: string; value: unknown }> {
  return request(`/state/${key}`, {
    method: "PUT",
    body: JSON.stringify({ value }),
  });
}

// --- Macros ---

const _macroExecuteTimestamps: Record<string, number> = {};
export async function executeMacro(
  macroId: string
): Promise<{ status: string }> {
  // Rate limit: max 1 execution per macro per 500ms
  const now = Date.now();
  const last = _macroExecuteTimestamps[macroId] || 0;
  if (now - last < 500) {
    return { status: "debounced" };
  }
  _macroExecuteTimestamps[macroId] = now;
  return request(`/macros/${macroId}/execute`, { method: "POST" });
}

export async function cancelMacro(
  macroId: string
): Promise<{ cancelled: boolean; reason?: string }> {
  return request(`/macros/${macroId}/cancel`, { method: "POST" });
}

export async function testTrigger(
  triggerId: string
): Promise<{ status: string }> {
  return request(`/triggers/${triggerId}/test`, { method: "POST" });
}

// --- Scripts ---

export async function getScriptSource(
  id: string
): Promise<{ id: string; file: string; source: string }> {
  return request(`/scripts/${id}/source`);
}

export async function saveScriptSource(
  id: string,
  source: string
): Promise<{ status: string }> {
  return request(`/scripts/${id}/source`, {
    method: "PUT",
    body: JSON.stringify({ source }),
  });
}

export async function createScript(data: {
  id: string;
  file: string;
  description?: string;
  source?: string;
  enabled?: boolean;
}): Promise<{ status: string; id: string }> {
  return request("/scripts", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteScript(
  id: string
): Promise<{ status: string }> {
  return request(`/scripts/${id}`, { method: "DELETE" });
}

export async function reloadScripts(): Promise<{
  status: string;
  handlers: number;
  errors?: Record<string, string>;
}> {
  return request("/scripts/reload", { method: "POST" });
}

export async function getScriptErrors(): Promise<Record<string, string>> {
  return request("/scripts/errors");
}

export async function getScriptReferences(): Promise<ScriptReference[]> {
  const data = await request<{ references: ScriptReference[] }>("/scripts/references");
  return data.references;
}

export interface ScriptFunction {
  script: string;
  function: string;
  doc: string;
}

export async function getScriptFunctions(): Promise<ScriptFunction[]> {
  return request<ScriptFunction[]>("/scripts/functions");
}

// --- Python Drivers ---

export async function getPythonDrivers(): Promise<{ drivers: PythonDriverInfo[] }> {
  return request("/python-drivers");
}

export async function getPythonDriverSource(
  id: string
): Promise<{ id: string; filename: string; source: string }> {
  return request(`/python-drivers/${id}/source`);
}

export async function savePythonDriverSource(
  id: string,
  source: string
): Promise<{ status: string }> {
  return request(`/python-drivers/${id}/source`, {
    method: "PUT",
    body: JSON.stringify({ source }),
  });
}

export async function createPythonDriver(data: {
  id: string;
  source: string;
}): Promise<{ status: string; id: string }> {
  return request("/python-drivers", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deletePythonDriver(
  id: string
): Promise<{ status: string }> {
  return request(`/python-drivers/${id}`, { method: "DELETE" });
}

export async function reloadPythonDriver(
  id: string
): Promise<{
  status: string;
  driver_id?: string;
  devices_reconnected?: string[];
  error?: string;
  line?: number;
  old_driver_preserved?: boolean;
}> {
  return request(`/python-drivers/${id}/reload`, { method: "POST" });
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

// --- Backups ---

export interface BackupInfo {
  filename: string;
  reason: string;
  timestamp: string;
  project_name: string;
  size: number;
  format: "zip" | "legacy";
}

export async function listBackups(): Promise<BackupInfo[]> {
  return request<BackupInfo[]>("/backups");
}

export async function createBackup(
  reason?: string
): Promise<{ status: string; filename: string }> {
  return request("/backups/create", {
    method: "POST",
    body: JSON.stringify({ reason: reason || "Manual backup" }),
  });
}

export async function restoreBackup(
  filename: string
): Promise<{ status: string; filename: string }> {
  return request(`/backups/${encodeURIComponent(filename)}/restore`, { method: "POST" });
}

// --- Project Library ---

export async function listLibrary(): Promise<LibraryProject[]> {
  return request("/library");
}

export async function getLibraryProject(id: string): Promise<LibraryProjectDetail> {
  return request(`/library/${id}`);
}

export async function saveToLibrary(data: {
  id: string;
  name: string;
  description?: string;
}): Promise<{ status: string; id: string }> {
  return request("/library", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function deleteLibraryProject(
  id: string
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}`, { method: "DELETE" });
}

export async function updateLibraryProject(
  id: string,
  data: { name?: string; description?: string }
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

export async function duplicateLibraryProject(
  id: string,
  newId: string,
  newName: string
): Promise<{ status: string; id: string }> {
  return request(`/library/${id}/duplicate`, {
    method: "POST",
    body: JSON.stringify({ new_id: newId, new_name: newName }),
  });
}

export async function exportLibraryProject(id: string): Promise<void> {
  const res = await fetch(`${BASE}/library/${id}/export`);
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  const disposition = res.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : `${id}.avc`;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export async function importToLibrary(file: File, id?: string): Promise<{
  status: string; id: string;
  installed_drivers?: string[];
  missing_drivers?: { driver_id: string; driver_name: string; affected_devices: string[] }[];
  warnings?: string[];
}> {
  const formData = new FormData();
  formData.append("file", file);
  if (id) formData.append("id", id);
  const res = await fetch(`${BASE}/library/import`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
  return res.json();
}

export async function openFromLibrary(
  libraryId: string,
  projectName: string,
  projectId?: string
): Promise<{ status: string; project_name: string }> {
  return request("/project/open-from-library", {
    method: "POST",
    body: JSON.stringify({
      library_id: libraryId,
      project_name: projectName,
      project_id: projectId,
    }),
  });
}

export async function createBlankProject(
  projectName: string,
  projectId?: string
): Promise<{ status: string; project_name: string }> {
  return request("/project/create-blank", {
    method: "POST",
    body: JSON.stringify({
      project_name: projectName,
      project_id: projectId,
    }),
  });
}

// --- Community Drivers ---

export async function fetchCommunityDrivers(): Promise<CommunityDriver[]> {
  const data = await request<{ drivers: CommunityDriver[] }>("/drivers/community");
  return data.drivers;
}

export async function installCommunityDriver(
  driverId: string,
  fileUrl: string,
  minPlatformVersion?: string
): Promise<void> {
  await request("/drivers/install", {
    method: "POST",
    body: JSON.stringify({
      driver_id: driverId,
      file_url: fileUrl,
      min_platform_version: minPlatformVersion || null,
    }),
  });
}

export async function listInstalledDrivers(): Promise<InstalledDriver[]> {
  const data = await request<{ drivers: InstalledDriver[] }>("/drivers/installed");
  return data.drivers;
}

export async function uploadDriver(file: File): Promise<void> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${BASE}/drivers/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${res.status}: ${body}`);
  }
}

export async function uninstallDriver(
  driverId: string
): Promise<{ status: string; driver_id: string }> {
  return request(`/drivers/installed/${driverId}`, { method: "DELETE" });
}

export async function updateCommunityDriver(
  driverId: string,
  fileUrl: string,
  minPlatformVersion?: string
): Promise<{ status: string }> {
  return request(`/drivers/installed/${driverId}/update`, {
    method: "POST",
    body: JSON.stringify({
      file_url: fileUrl,
      min_platform_version: minPlatformVersion || null,
    }),
  });
}

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

// --- Discovery ---

export interface DiscoveryDriverMatch {
  driver_id: string;
  driver_name: string;
  confidence: number;
  match_reasons: string[];
  suggested_config: Record<string, unknown>;
  source: "installed" | "community";
  description: string;
}

export interface DiscoveredDevice {
  ip: string;
  mac: string | null;
  hostname: string | null;
  manufacturer: string | null;
  model: string | null;
  device_name: string | null;
  firmware: string | null;
  serial_number: string | null;
  open_ports: number[];
  banners: Record<number, string>;
  sources: string[];
  protocols: string[];
  confidence: number;
  category: string | null;
  alive: boolean;
  matched_drivers: DiscoveryDriverMatch[];
  mdns_services: string[];
  ssdp_info: Record<string, unknown> | null;
  snmp_info: Record<string, unknown> | null;
}

export interface DiscoveryScanResult {
  scan_id: string;
  status: string;
  devices: DiscoveredDevice[];
  total_hosts_scanned: number;
  total_alive: number;
  total_devices: number;
  scan_duration_seconds: number;
  port_labels?: Record<string, string>;
}

export interface DiscoveryScanStatus {
  scan_id: string;
  status: string;
  phase: string;
  phase_number: number;
  total_phases: number;
  message: string;
  progress: number;
  devices_found: number;
  started_at: number;
  duration: number;
  subnets: string[];
  total_hosts_scanned: number;
}

export type ScanDepth = "quick" | "standard" | "thorough";

export interface DiscoveryConfig {
  snmp_enabled: boolean;
  snmp_community: string;
  gentle_mode: boolean;
  scan_depth: ScanDepth;
  max_subnet_size: number;
}

export async function discoveryStartScan(options?: {
  subnets?: string[];
  extra_subnets?: string[];
  snmp_enabled?: boolean;
  snmp_community?: string;
  gentle_mode?: boolean;
  scan_depth?: ScanDepth;
  max_subnet_size?: number;
  timeout?: number;
}): Promise<{ scan_id: string; status: string; subnets: string[] }> {
  return request("/discovery/scan", {
    method: "POST",
    body: JSON.stringify(options ?? {}),
  });
}

export async function discoveryGetStatus(): Promise<DiscoveryScanStatus> {
  return request("/discovery/status");
}

export async function discoveryGetResults(params?: {
  min_confidence?: number;
  category?: string;
  sort?: string;
}): Promise<DiscoveryScanResult> {
  const qs = new URLSearchParams();
  if (params?.min_confidence) qs.set("min_confidence", String(params.min_confidence));
  if (params?.category) qs.set("category", params.category);
  if (params?.sort) qs.set("sort", params.sort);
  const q = qs.toString();
  return request(`/discovery/results${q ? `?${q}` : ""}`);
}

export async function discoveryStopScan(): Promise<{ status: string }> {
  return request("/discovery/stop", { method: "POST" });
}

export async function discoveryClearResults(): Promise<{ status: string }> {
  return request("/discovery/clear", { method: "POST" });
}

export async function discoveryGetSubnets(): Promise<{ subnets: string[] }> {
  return request("/discovery/subnets");
}

export async function discoveryGetConfig(): Promise<DiscoveryConfig> {
  return request("/discovery/config");
}

export async function discoveryUpdateConfig(config: DiscoveryConfig): Promise<{ status: string }> {
  return request("/discovery/config", {
    method: "PUT",
    body: JSON.stringify(config),
  });
}

export async function discoveryAddDevice(options: {
  ip: string;
  driver_id: string;
  name?: string;
  config?: Record<string, unknown>;
  group?: string;
}): Promise<{ status: string; device_id: string; name: string }> {
  return request("/discovery/add-device", {
    method: "POST",
    body: JSON.stringify(options),
  });
}

export async function discoveryExport(): Promise<string> {
  const res = await fetch(`${BASE}/discovery/export`, {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) throw new Error(`Export failed: ${res.status}`);
  return res.text();
}

export async function discoveryInstallAndMatch(options: {
  ip: string;
  driver_id: string;
  file_url: string;
}): Promise<{ status: string; device: DiscoveredDevice | null; device_id?: string; name?: string; error?: string }> {
  return request("/discovery/install-and-match", {
    method: "POST",
    body: JSON.stringify(options),
  });
}

export async function getDriverHelp(
  driverId: string
): Promise<{ driver_id: string; overview: string; setup: string }> {
  return request(`/drivers/${driverId}/help`);
}

// --- Connection Table (Site Config) ---

export async function getConnections(): Promise<Record<string, Record<string, unknown>>> {
  return request("/connections");
}

export async function updateConnection(
  deviceId: string,
  overrides: Record<string, unknown>
): Promise<{ status: string; device_id: string }> {
  return request(`/connections/${deviceId}`, {
    method: "PUT",
    body: JSON.stringify(overrides),
  });
}

export async function updateConnectionsBulk(
  table: Record<string, Record<string, unknown>>
): Promise<{ status: string; count: number }> {
  return request("/connections", {
    method: "PUT",
    body: JSON.stringify(table),
  });
}

export async function deleteConnection(
  deviceId: string
): Promise<{ status: string; device_id: string }> {
  return request(`/connections/${deviceId}`, { method: "DELETE" });
}

export async function exportConnections(): Promise<Record<string, Record<string, unknown>>> {
  return request("/connections/export");
}

export async function importConnections(
  table: Record<string, Record<string, unknown>>
): Promise<{ status: string; count: number }> {
  return request("/connections/import", {
    method: "POST",
    body: JSON.stringify(table),
  });
}

// --- Orphaned Device Retry ---

export async function retryOrphanedDevice(
  deviceId: string
): Promise<{ status: string; device_id: string; detail?: string }> {
  return request(`/devices/${deviceId}/retry`, { method: "POST" });
}

// --- Driver Validation ---

export async function validateDrivers(): Promise<{
  available: string[];
  missing: { driver_id: string; affected_devices: string[] }[];
}> {
  return request("/project/validate-drivers");
}

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

export async function validatePlugins(): Promise<{
  available: { plugin_id: string; plugin_name: string; version: string; status: string }[];
  missing: { plugin_id: string; affected_config: boolean }[];
  platform_warnings: { plugin_id: string; current_platform: string; supported_platforms: string[]; message: string }[];
}> {
  return request("/project/validate-plugins");
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
  auth: { programmer_password: string; api_key: string; panel_lock_code: string };
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
