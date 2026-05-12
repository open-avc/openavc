import type {
  DriverInfo,
  DriverDefinition,
  CommunityDriver,
  InstalledDriver,
  PythonDriverInfo,
} from "./types";
import { BASE, request } from "./base";

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

export interface TestCommandResult {
  success: boolean;
  sent: string | null;
  received: string[];
  state_changes: Record<string, unknown>;
  error: string | null;
}

export interface TestCommandRequest {
  host: string;
  /** Numeric port for IP transports; serial port path (e.g. "COM3") for serial. */
  port: number | string;
  transport: string;
  timeout?: number;
  /** Definition mode: full driver definition + which command to invoke. */
  definition?: DriverDefinition;
  command_name?: string;
  params?: Record<string, unknown>;
  config_overrides?: Record<string, unknown>;
  /** Raw mode (legacy fallback) — only used when definition+command_name absent. */
  command_string?: string;
  delimiter?: string;
}

export async function testDriverCommand(
  driverId: string,
  data: TestCommandRequest,
): Promise<TestCommandResult> {
  return request(`/driver-definitions/${driverId}/test-command`, {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export interface TestPanelConflict {
  device_id: string;
  device_name: string;
  driver: string;
  connected: boolean;
  paused: boolean;
}

/**
 * Pre-flight check: does any production device already own this host:port?
 *
 * The driver test panel calls this before opening a competing TCP session.
 * Many AV devices accept only one TCP control connection at a time, so the
 * test would kick the production device offline. Currently TCP-only — UDP
 * and HTTP don't have the single-session problem.
 */
export async function checkConnectionConflict(
  host: string,
  port: number | string,
  transport: string,
): Promise<{ conflicts: TestPanelConflict[] }> {
  const params = new URLSearchParams({
    host,
    port: String(port),
    transport,
  });
  return request(`/driver-test-conflicts?${params.toString()}`);
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

export async function getDriverHelp(
  driverId: string
): Promise<{ driver_id: string; overview: string; setup: string }> {
  return request(`/drivers/${driverId}/help`);
}
