import type {
  ChildEntitiesByTypeResponse,
  ChildEntitiesListResponse,
  ChildEntityDetailResponse,
  ChildEntityRefreshResponse,
  DeviceInfo,
  DeviceSettingValue,
} from "./types";
import { request } from "./base";

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

export async function sendRaw(
  deviceId: string,
  data: string
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}/send-raw`, {
    method: "POST",
    body: JSON.stringify({ data }),
  });
}

export async function invokeDeviceAction(
  deviceId: string,
  actionId: string,
  params: Record<string, unknown> = {}
): Promise<{ success: boolean; result: unknown; action_id: string }> {
  return request(`/devices/${deviceId}/actions/${actionId}`, {
    method: "POST",
    body: JSON.stringify({ params }),
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

export async function pauseDevice(
  deviceId: string
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}/pause`, { method: "POST" });
}

export async function resumeDevice(
  deviceId: string
): Promise<{ status: string; device_id: string }> {
  return request(`/devices/${deviceId}/resume`, { method: "POST" });
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
): Promise<{ status: string; count: number; skipped: string[] }> {
  // `skipped` lists device ids in the table that don't exist in the project —
  // the server keeps the known entries and drops these rather than persisting
  // orphaned connection rows.
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
): Promise<{ status: string; count: number; skipped: string[] }> {
  // `skipped` lists imported ids that match no device in the project (e.g. a
  // site config exported from a different room) — kept entries are applied,
  // these are reported rather than written as orphaned rows.
  return request("/connections/import", {
    method: "POST",
    body: JSON.stringify(table),
  });
}

// --- Child Entities ---

export async function listChildEntities(
  deviceId: string,
): Promise<ChildEntitiesListResponse> {
  return request(`/devices/${deviceId}/children`);
}

export async function listChildEntitiesByType(
  deviceId: string,
  childType: string,
): Promise<ChildEntitiesByTypeResponse> {
  return request(`/devices/${deviceId}/children/${childType}`);
}

export async function getChildEntity(
  deviceId: string,
  childType: string,
  localId: number | string,
): Promise<ChildEntityDetailResponse> {
  return request(
    `/devices/${deviceId}/children/${childType}/${encodeURIComponent(localId)}`,
  );
}

export async function patchChildEntity(
  deviceId: string,
  childType: string,
  localId: number | string,
  patch: { label?: string; config?: Record<string, unknown> },
): Promise<ChildEntityDetailResponse> {
  return request(
    `/devices/${deviceId}/children/${childType}/${encodeURIComponent(localId)}`,
    {
      method: "PATCH",
      body: JSON.stringify(patch),
    },
  );
}

export async function refreshChildEntities(
  deviceId: string,
): Promise<ChildEntityRefreshResponse> {
  return request(`/devices/${deviceId}/children/refresh`, { method: "POST" });
}


// --- Orphaned Device Retry ---

export async function retryOrphanedDevice(
  deviceId: string
): Promise<{ status: string; device_id: string; detail?: string }> {
  return request(`/devices/${deviceId}/retry`, { method: "POST" });
}

// --- Missing Drivers (orphaned devices waiting for a driver install) ---

export interface CommunityMatch {
  id: string;
  name: string;
  manufacturer: string;
  category: string;
  file_url: string;
  min_platform_version: string | null;
}

export interface MissingDriver {
  driver_id: string;
  device_ids: string[];
  community_match: CommunityMatch | null;
}

export async function listMissingDrivers(): Promise<MissingDriver[]> {
  const data = await request<{ missing: MissingDriver[] }>("/devices/missing-drivers");
  return data.missing;
}

export async function installMissingDrivers(
  driverIds: string[]
): Promise<{
  installed: string[];
  failed: { driver_id: string; error: string }[];
  activated_devices: string[];
}> {
  return request("/devices/install-missing", {
    method: "POST",
    body: JSON.stringify({ driver_ids: driverIds }),
  });
}
