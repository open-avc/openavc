import { BASE, request } from "./base";

// --- Discovery ---

export type DeviceState = "identified" | "possible" | "unknown";

export type SignalTier = "tier1" | "tier2" | "tier3" | "tier4";

export interface DiscoveryEvidence {
  tier: SignalTier;
  source: string;
  data: Record<string, unknown>;
  at: number;
}

export interface IdentificationMatch {
  state: DeviceState;
  driver_id: string | null;
  candidates: string[];
  source: string;
  reason: string;
  evidence: DiscoveryEvidence[];
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
  protocols: string[];
  category: string | null;
  alive: boolean;
  identification: IdentificationMatch | null;
  evidence_log: DiscoveryEvidence[];
  mdns_services: string[];
  ssdp_info: Record<string, unknown> | null;
  snmp_info: Record<string, unknown> | null;
}

// Phase 7 (UI redesign) replaces DiscoveryView.tsx with a state-pill
// design that consumes ``identification`` directly. Until then, this
// transitional alias keeps the legacy view compiling — every legacy
// "matched driver" entry is now derived from ``identification``.
export interface DiscoveryDriverMatch {
  driver_id: string;
  driver_name: string;
  confidence: number;
  match_reasons: string[];
  suggested_config: Record<string, unknown>;
  source: "installed" | "community";
  description: string;
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
  state?: DeviceState;
  category?: string;
  sort?: string;
}): Promise<DiscoveryScanResult> {
  const qs = new URLSearchParams();
  if (params?.state) qs.set("state", params.state);
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
