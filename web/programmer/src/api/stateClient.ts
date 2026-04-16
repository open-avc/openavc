import type { StateHistoryEntry, ScriptReference } from "./types";
import { request } from "./base";

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
