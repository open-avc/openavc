/**
 * REST API client for the simulator backend.
 */

import { BASE } from "./paths";
import { authHeaders, rejectCurrentToken } from "./session";

/** Fired when the server refuses this tab's credential. */
export const AUTH_REQUIRED = "openavc:sim-auth-required";

export { BASE, APP_ROOT } from "./paths";

// Every call below goes through here so the credential cannot be forgotten at
// one site. `authHeaders()` is empty until this tab has a token, which is the
// correct state for the brief moment before the handshake completes.
async function api(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = { ...(init.headers as Record<string, string> | undefined), ...authHeaders() };
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    // Dead credential. Drop it and tell the gate, which asks the opener again
    // and falls back to the password form. Without this the UI sits there
    // reading "Disconnected" forever, which looks like a broken simulator.
    rejectCurrentToken();
    window.dispatchEvent(new Event(AUTH_REQUIRED));
  }
  return res;
}

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
  /** v0.5.0 child entities: { child_type: { padded_id: { label, config } } }.
   *  Present only when the device declares children. */
  child_entities?: Record<string, Record<string, { label?: string; config?: Record<string, unknown> }>>;
  /** Modeled child roster (auto-generated simulators): per child type, the
   *  live children and which state props each carries. Values live in the
   *  flat state dict under "<type>.<id>.<prop>" keys — read them there so
   *  per-key WS updates keep the panel current. */
  children?: Record<string, ChildTypeInfo>;
}

export interface ChildTypeInfo {
  label: string;
  entries: { id: string; label: string }[];
  props: string[];
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
  const res = await api(`/api/devices`);
  // Throw rather than hand back undefined. This UI is now served by the main
  // server, so the page can load in the moment BEFORE the simulator process is
  // ready -- the proxy answers 503, and returning `data.devices` from that body
  // put `undefined` into the device list, where rendering it read `.length` and
  // took the whole tab down. The caller already treats a rejection as "nothing
  // yet" and the socket refetches the moment the simulator comes up.
  if (!res.ok) throw new Error(`Simulator not ready (${res.status})`);
  const data = await res.json();
  return data.devices ?? [];
}

export async function fetchDevice(id: string): Promise<DeviceInfo> {
  const res = await api(`/api/devices/${id}`);
  return res.json();
}

export async function setDeviceState(id: string, key: string, value: unknown): Promise<void> {
  const res = await api(`/api/devices/${id}/state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value }),
  });
  if (!res.ok) throw new Error(`Set state failed: ${res.status} ${res.statusText}`);
}

export async function toggleError(id: string, mode: string, active: boolean): Promise<void> {
  const res = await api(`/api/devices/${id}/errors/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ active }),
  });
  if (!res.ok) throw new Error(`Toggle error failed: ${res.status} ${res.statusText}`);
}

export async function fetchLog(id: string, limit = 200): Promise<LogEntry[]> {
  const res = await api(`/api/devices/${id}/log?limit=${limit}`);
  if (!res.ok) throw new Error(`Simulator not ready (${res.status})`);
  const data = await res.json();
  return data.log ?? [];
}

export async function setNetworkPreset(preset: string): Promise<void> {
  await api(`/api/network/preset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ preset }),
  });
}

export async function fetchNetwork(): Promise<{
  global: { latency_ms: number; jitter_pct: number; drop_rate_pct: number; instability: string };
  presets: string[];
}> {
  const res = await api(`/api/network`);
  return res.json();
}

/** Stop the simulator process.
 *
 * Goes through the authenticated wrapper like everything else. It did not,
 * once: a hand-written `fetch` here meant Stop sent no credential, got a 401,
 * and the swallowed error made the button look simply dead.
 */
export async function stopSimulator(): Promise<void> {
  const res = await api(`/api/shutdown`, { method: "POST" });
  // The process may die mid-response; that is success, not failure.
  if (!res.ok && res.status !== 502 && res.status !== 503) {
    throw new Error(`Could not stop the simulator (${res.status})`);
  }
}
