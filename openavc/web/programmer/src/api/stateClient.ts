import type { StateHistoryEntry, ScriptReference } from "./types";
import { request } from "./base";

// --- State ---

export async function getState(): Promise<Record<string, unknown>> {
  return (await request<{ state: Record<string, unknown> }>("/state")).state;
}

export async function getStateHistory(
  count = 50
): Promise<StateHistoryEntry[]> {
  return (
    await request<{ history: StateHistoryEntry[] }>(`/state/history?count=${count}`)
  ).history;
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
): Promise<{ status: string; macro_id: string }> {
  return request(`/macros/${macroId}/cancel`, { method: "POST" });
}

export async function testTrigger(
  triggerId: string
): Promise<{ status: string }> {
  return request(`/triggers/${triggerId}/test`, { method: "POST" });
}

/** One problem the platform found with a macro, placed where the editor draws it.
 *
 *  `scope` is the list it belongs to and `index` its row in that list, so a
 *  problem inside a conditional's branch marks the conditional -- the row
 *  somebody has to open. `path` is the full location for the sentence itself. */
export interface MacroIssue {
  scope: "step" | "trigger";
  index: number;
  path: string;
  message: string;
}

/**
 * Ask the platform what is incomplete about these macros, without saving them.
 *
 * The rules have exactly one implementation and it is the platform's: the same
 * ones the cloud AI's macro tools apply, so a macro built by hand reads the
 * same as the same macro written by the AI. A copy of them in TypeScript is
 * what this deliberately is not -- it could not see the macro actions a plugin
 * registered at runtime, and would mark every one of those steps wrong.
 *
 * Every macro in one call, because the macro LIST marks its rows too and a
 * project's worth of macros must not be a project's worth of requests. Nothing
 * here blocks a save; a half-built step is what editing looks like.
 */
export async function validateMacros(
  macros: { id: string; steps: unknown[]; triggers?: unknown[] }[],
): Promise<Record<string, { issues: MacroIssue[] }>> {
  const result = await request<{ macros: Record<string, { issues: MacroIssue[] }> }>(
    "/macros/validate",
    { method: "POST", body: JSON.stringify({ macros }) },
  );
  return result.macros;
}

// --- Scripts ---

/** A handler in a script waiting for an event nothing in the project emits.
 *
 *  `line` is the `@on_event` decorator's own line -- the handler below it is
 *  fine and the pattern is what is wrong -- and `event` is that pattern. */
export interface ScriptIssue {
  line: number;
  event: string;
  message: string;
}

/**
 * Ask the platform which handlers in these scripts can never run.
 *
 * The script half of the dead-control failure: `@on_event("custom.start")` in
 * a project where no macro step, no control action and no other script ever
 * emits that name. Nothing raises and nothing is logged, and the handler
 * itself can be perfectly correct, so the search goes to the projector and
 * the cable.
 *
 * `source` is an OVERRIDE, not a requirement: the editor holds one file open
 * and wants a mark on every row of the list beside it, so a script named
 * without its text is checked as it is on disk. The emitting side -- macros,
 * controls, the other scripts -- is the server's to read, which is the whole
 * reason this is not a rule in the browser.
 */
export async function validateScripts(
  scripts: { id: string; source?: string }[],
): Promise<Record<string, { issues: ScriptIssue[] }>> {
  const result = await request<{ scripts: Record<string, { issues: ScriptIssue[] }> }>(
    "/scripts/validate",
    { method: "POST", body: JSON.stringify({ scripts }) },
  );
  return result.scripts;
}


export async function getScriptSource(
  id: string
): Promise<{ script_id: string; file: string; source: string }> {
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

export async function reloadScript(id: string): Promise<{
  status: string;
  handlers?: number;
  error?: string;
  old_script_preserved?: boolean;
  errors?: Record<string, string>;
}> {
  return request(`/scripts/${id}/reload`, { method: "POST" });
}

export async function getScriptErrors(): Promise<Record<string, string>> {
  return (await request<{ errors: Record<string, string> }>("/scripts/errors")).errors;
}

export async function getScriptReferences(): Promise<ScriptReference[]> {
  const data = await request<{ references: ScriptReference[] }>("/scripts/references");
  return data.references;
}

/** One parameter of a script function, as the script itself declares it. */
export interface ScriptFunctionParam {
  name: string;
  required: boolean;
  default?: unknown;
  /** Only present where the script says something: an annotation, or the
   *  type of a non-None default. Absent means the Builder guesses nothing. */
  type?: string;
}

export interface ScriptFunction {
  script: string;
  function: string;
  doc: string;
  /** What a control must pass, picked from the signature rather than typed. */
  params: ScriptFunctionParam[];
  /** The function takes **kwargs, so extra named values are allowed. */
  accepts_extra: boolean;
}

export async function getScriptFunctions(): Promise<ScriptFunction[]> {
  return (await request<{ functions: ScriptFunction[] }>("/scripts/functions")).functions;
}
