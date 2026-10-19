import { request } from "./base";
import type { DeviceAction } from "./types";

export type SetupOutcome = "succeeded" | "failed" | "degraded" | "unverified";
export type SetupStage = "not_requested" | "reported_complete" | "failed" | "unknown";
export interface SetupStatus {
  run_id: string;
  status: "prepared" | "claimed" | "running" | "terminal";
  progress: { code: string; detail: string; percent: number | null; sequence: number } | null;
  result: {
    outcome: SetupOutcome;
    summary: string;
    findings: Array<{ code: string; outcome: SetupOutcome; detail: string;
      fields?: Array<{ label: string; value: string | number | boolean | null }> }>;
    config_save: SetupStage;
    reconnect: SetupStage;
  } | null;
  secret: { state: "none" | "available" | "unavailable" | "reported_saved" | "acknowledged"; generation: number };
}
export interface PreparedSetup { run_id: string; capability: string; status: "prepared" }
export interface SetupSecret { generation: number; values: Record<string, string> }
export interface RetainedSetup extends PreparedSetup {
  device_id: string;
  action: DeviceAction;
  params: Record<string, unknown>;
}

const transferPost = <T>(path: string, body: unknown) => request<T>(path, {
  method: "POST", cache: "no-store", body: JSON.stringify(body),
  headers: { "Content-Type": "application/json", "X-OpenAVC-Setup-Transfer": "1" },
});
export const recoverSetupTransfer = () =>
  transferPost<{ pending: RetainedSetup | null; cleared: boolean }>("/setup-actions/transfer", {});
export const discardSetupTransfer = (run_id: string) =>
  transferPost<{ discarded: string }>("/setup-actions/discard-transfer", { run_id });

const post = <T>(path: string, body: unknown) => request<T>(path, {
  method: "POST", cache: "no-store", body: JSON.stringify(body),
});
const actionPath = (device: string, action: string) =>
  `/devices/${encodeURIComponent(device)}/actions/${encodeURIComponent(action)}`;

export const prepareSetup = (device: string, action: string, params: Record<string, unknown>) =>
  post<PreparedSetup>(`${actionPath(device, action)}/prepare`, { params });
export const startSetup = (device: string, action: string, capability: string, params: Record<string, unknown>) =>
  post<SetupStatus>(`${actionPath(device, action)}/start`, { capability, params });
export const setupStatus = (capability: string) => post<SetupStatus>("/setup-actions/status", { capability });
export const retrieveSetup = (capability: string) => post<SetupSecret>("/setup-actions/retrieve", { capability });
export const acknowledgeSetup = (capability: string, generation: number) =>
  post<{ acknowledged: number }>("/setup-actions/acknowledge", { capability, generation });
export const subscribeSetup = (capability: string, connection_id: string) =>
  post<SetupStatus>("/setup-actions/subscribe", { capability, connection_id });
export const unsubscribeSetup = (capability: string, connection_id: string) =>
  post<{ unsubscribed: boolean }>("/setup-actions/unsubscribe", { capability, connection_id });
