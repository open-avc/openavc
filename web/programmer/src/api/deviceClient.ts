import type { DeviceInfo, DeviceSettingValue } from "./types";
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
