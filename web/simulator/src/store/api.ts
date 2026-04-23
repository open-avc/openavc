/**
 * REST API client for the simulator backend.
 */

const BASE = "";  // Same origin — served by the simulator server

// ── Declarative Controls Schema ──

export interface PowerControlDef {
  type: "power";
  key: string;
  label?: string;
}

export interface SelectControlDef {
  type: "select";
  key: string;
  options: string[];
  labels?: Record<string, string>;
  label?: string;
}

export interface SliderControlDef {
  type: "slider";
  key: string;
  min: number;
  max: number;
  step?: number;
  label?: string;
  unit?: string;
}

export interface ToggleControlDef {
  type: "toggle";
  key: string;
  label: string;
}

export interface MatrixControlDef {
  type: "matrix";
  inputs: number;
  outputs: number;
  state_pattern: string;
  label?: string;
  input_labels?: string[];
  output_labels?: string[];
}

export interface MeterControlDef {
  type: "meters";
  channels: number;
  key_pattern: string;
  mute_pattern?: string;
  label?: string;
}

export interface PresetControlDef {
  type: "presets";
  key: string;
  count?: number;
  names?: string[];
  label?: string;
}

export interface GroupControlDef {
  type: "group";
  label: string;
  controls: ControlDef[];
}

export interface IndicatorControlDef {
  type: "indicator";
  key: string;
  label: string;
  color_map?: Record<string, string>;
}

export type ControlDef =
  | PowerControlDef
  | SelectControlDef
  | SliderControlDef
  | ToggleControlDef
  | MatrixControlDef
  | MeterControlDef
  | PresetControlDef
  | GroupControlDef
  | IndicatorControlDef;

// ── Device Info ──

export interface DeviceInfo {
  device_id: string;
  device_name: string;
  real_host: string;
  real_port: number;
  driver_id: string;
  name: string;
  category: string;
  transport: string;
  port: number;
  running: boolean;
  state: Record<string, unknown>;
  active_errors: string[];
  available_errors: Record<string, { description: string }>;
  controls?: ControlDef[];
  push_state: boolean;
}

export interface LogEntry {
  timestamp: number;
  device_id: string;
  direction: "in" | "out";
  data: string;
  data_text: string;
  client_id: string;
}

export async function fetchDevices(): Promise<DeviceInfo[]> {
  const res = await fetch(`${BASE}/api/devices`);
  const data = await res.json();
  return data.devices;
}

export async function fetchDevice(id: string): Promise<DeviceInfo> {
  const res = await fetch(`${BASE}/api/devices/${id}`);
  return res.json();
}

export async function setDeviceState(id: string, key: string, value: unknown): Promise<void> {
  await fetch(`${BASE}/api/devices/${id}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
}

export async function toggleError(id: string, mode: string, active: boolean): Promise<void> {
  await fetch(`${BASE}/api/devices/${id}/errors/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
}

export async function fetchLog(id: string, limit = 200): Promise<LogEntry[]> {
  const res = await fetch(`${BASE}/api/devices/${id}/log?limit=${limit}`);
  const data = await res.json();
  return data.log;
}

export async function setNetworkPreset(preset: string): Promise<void> {
  await fetch(`${BASE}/api/network/preset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset }),
  });
}

export async function fetchNetwork(): Promise<{
  global: { latency_ms: number; jitter_pct: number; drop_rate_pct: number; instability: string };
  presets: string[];
}> {
  const res = await fetch(`${BASE}/api/network`);
  return res.json();
}
