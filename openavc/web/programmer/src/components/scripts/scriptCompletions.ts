/**
 * What the script editor offers as you type: the scripting API's names, the
 * members of each of its objects, and the ids, keys and events of the open
 * project.
 *
 * The scripting API's names and the events are `api/scriptApi.gen.ts`,
 * rendered from the server's own (`python -m openavc.core.script_api_gen`).
 *
 * A plain function of the text before the cursor and what it reads, so it can
 * be tested without Monaco. `ScriptEditor` turns each entry into a Monaco
 * completion item.
 */

import type { PluginScriptMethod } from "../../api/pluginClient";
import {
  SCRIPT_API,
  SCRIPT_API_MEMBERS,
  SCRIPT_EVENTS,
  type ScriptApiName,
} from "../../api/scriptApi.gen";
import type { ProjectConfig } from "../../api/types";
import { groupByPlugin } from "./pluginScriptApiCache";

export type CompletionKind =
  | "module"
  | "function"
  | "method"
  | "class"
  | "value"
  | "variable"
  | "event"
  | "snippet";

export interface ScriptCompletion {
  label: string;
  kind: CompletionKind;
  insertText: string;
  /** `insertText` holds `${n:...}` placeholders. */
  snippet?: boolean;
  detail?: string;
  /** Markdown. */
  documentation?: string;
}

export interface CompletionSources {
  project: ProjectConfig | null;
  liveState: Record<string, unknown>;
  pluginMethods: PluginScriptMethod[];
}

/** Every completion for the text on the cursor's line, up to the cursor. */
export function scriptCompletions(
  textUntilPosition: string,
  { project, liveState, pluginMethods }: CompletionSources,
): ScriptCompletion[] {
  const suggestions: ScriptCompletion[] = [];
  if (textUntilPosition.match(/from openavc import\s/)) {
    suggestions.push(...SCRIPT_API.map(importCompletion));
  }
  const objectMatch = textUntilPosition.match(/(?:^|[^\w.])(?:openavc\.)?([a-z_]+)\.\s*$/);
  const members = objectMatch ? SCRIPT_API_MEMBERS[objectMatch[1]] : undefined;
  if (members) {
    suggestions.push(...members.map((m) => memberCompletion(objectMatch![1], m)));
  }
  suggestions.push(...pluginCompletions(textUntilPosition, pluginMethods));
  suggestions.push(...projectIdCompletions(textUntilPosition, project));
  suggestions.push(...stateKeyCompletions(textUntilPosition, project, liveState));
  suggestions.push(...eventCompletions(textUntilPosition, project));
  if (textUntilPosition.match(/@\s*$/)) {
    suggestions.push(...SCRIPT_API.filter((n) => n.decorator).map(decoratorCompletion));
  }
  return suggestions;
}

/** A name's help: what it does, then its example. */
function documentation(n: ScriptApiName): string {
  return n.example ? `${n.summary}\n\n\`\`\`python\n${n.example}\n\`\`\`` : n.summary;
}

/** A function's parameters and what it returns; `async` when it is awaited. */
function signatureDetail(n: ScriptApiName): string | undefined {
  if (n.kind === "class") return "class";
  if (n.kind !== "function") return undefined;
  return `${n.awaited ? "async " : ""}${n.signature} -> ${n.returns}`;
}

const IMPORT_KINDS: Record<ScriptApiName["kind"], CompletionKind> = {
  function: "function",
  class: "class",
  object: "module",
};

function importCompletion(n: ScriptApiName): ScriptCompletion {
  return {
    label: n.name,
    kind: IMPORT_KINDS[n.kind],
    insertText: n.name,
    detail: signatureDetail(n),
    documentation: documentation(n),
  };
}

function memberCompletion(object: string, m: ScriptApiName): ScriptCompletion {
  return {
    label: m.name,
    kind: m.kind === "class" ? "class" : "method",
    insertText: m.insertText,
    ...(m.insertText.includes("${") ? { snippet: true } : {}),
    detail: signatureDetail(m) ?? `${object}.${m.name}`,
    documentation: documentation(m),
  };
}

function decoratorCompletion(n: ScriptApiName): ScriptCompletion {
  return {
    label: n.name,
    kind: "snippet",
    insertText: n.decorator,
    snippet: true,
    detail: `decorator ${n.signature}`,
    documentation: documentation(n),
  };
}

/** `plugins.` lists the running plugins with script methods; `plugins.<id>.` their methods. */
function pluginCompletions(text: string, pluginMethods: PluginScriptMethod[]): ScriptCompletion[] {
  const suggestions: ScriptCompletion[] = [];
  if (text.match(/plugins\.\s*$/)) {
    const grouped = groupByPlugin(pluginMethods);
    for (const [pluginId, info] of grouped) {
      suggestions.push({
        label: pluginId,
        kind: "module",
        insertText: pluginId,
        detail: info.plugin_name,
        documentation: `Plugin: **${info.plugin_name}**\n\nMethods:\n${info.methods
          .map((m) => `- \`${m.method}\`${m.sync ? "" : " (async)"}`)
          .join("\n")}`,
      });
    }
  }

  const pluginMatch = text.match(/plugins\.([a-z][a-zA-Z0-9_]*)\.\s*$/);
  if (pluginMatch) {
    const info = groupByPlugin(pluginMethods).get(pluginMatch[1]);
    for (const m of info?.methods ?? []) {
      suggestions.push({
        label: m.method,
        kind: "method",
        insertText: `${m.method}($0)`,
        snippet: true,
        detail: m.sync ? `${info!.plugin_name} (sync)` : `${info!.plugin_name} (async)`,
        documentation: m.doc
          ? `${m.doc}\n\n${m.sync ? "Call directly." : "Must be `await`ed."}`
          : m.sync
            ? "Call directly."
            : "Must be `await`ed.",
      });
    }
  }
  return suggestions;
}

/** A device id inside `devices.send("`, a macro id inside `macros.execute("`. */
function projectIdCompletions(text: string, project: ProjectConfig | null): ScriptCompletion[] {
  if (!project) return [];
  if (text.match(/devices\.send\(\s*["']\s*$/)) {
    return project.devices.map((d) => ({
      label: d.id,
      kind: "value" as const,
      insertText: d.id,
      detail: d.name,
    }));
  }
  if (text.match(/macros\.(execute|start)\(\s*["']\s*$/)) {
    return project.macros.map((m) => ({
      label: m.id,
      kind: "value" as const,
      insertText: m.id,
      detail: m.name,
    }));
  }
  return [];
}

/** State keys inside `state.get("`, `state.set("` and `@on_state_change("`. */
function stateKeyCompletions(
  text: string,
  project: ProjectConfig | null,
  liveState: Record<string, unknown>,
): ScriptCompletion[] {
  if (!text.match(/(?:state\.(?:get|set)|@on_state_change)\(\s*["']\s*$/)) return [];
  return [
    ...variableKeys(project),
    ...overrideKeys(project),
    ...liveKeys(liveState),
    ...asleepKeys(project, liveState),
  ];
}

function variable(label: string, detail?: string): ScriptCompletion {
  return { label, kind: "variable", insertText: label, ...(detail !== undefined ? { detail } : {}) };
}

function variableKeys(project: ProjectConfig | null): ScriptCompletion[] {
  return (project?.variables ?? []).map((v) => variable(`var.${v.id}`, `${v.label} (${v.type})`));
}

/** The keys a script sets to override how an element looks. */
const OVERRIDE_PROPS = ["label", "visible", "bg_color", "text_color", "opacity"];

function overrideKeys(project: ProjectConfig | null): ScriptCompletion[] {
  return projectElements(project).flatMap((el) =>
    OVERRIDE_PROPS.map((prop) => variable(`ui.${el.id}.${prop}`, `${el.label || el.type}: ${prop}`)),
  );
}

/** Device, system and plugin keys, as the live state has them. */
function liveKeys(liveState: Record<string, unknown>): ScriptCompletion[] {
  return Object.keys(liveState)
    .filter((key) => ["device.", "system.", "plugin."].some((prefix) => key.startsWith(prefix)))
    .map((key) => variable(key));
}

/** `device.<id>.asleep` is in live state only while the device is asleep,
 * so it is offered for every device the project has. */
function asleepKeys(project: ProjectConfig | null, liveState: Record<string, unknown>): ScriptCompletion[] {
  return (project?.devices ?? [])
    .filter((d) => !(`device.${d.id}.asleep` in liveState))
    .map((d) => variable(`device.${d.id}.asleep`, `${d.name}: asleep`));
}

function projectElements(project: ProjectConfig | null) {
  return (project?.ui?.pages ?? []).flatMap((page) => page.elements ?? []);
}

/** Event names inside `@on_event("`. */
function eventCompletions(text: string, project: ProjectConfig | null): ScriptCompletion[] {
  if (!text.match(/@on_event\(\s*["']\s*$/)) return [];
  return [
    ...elementEvents(project),
    ...ownEvents(project),
    ...SCRIPT_EVENTS.map((family) => event(family.pattern, family.description)),
  ];
}

function event(label: string, detail: string): ScriptCompletion {
  return { label, kind: "event", insertText: label, detail };
}

const PRESSED_TYPES = ["button", "page_nav", "camera_preset"];
const CHANGED_TYPES = ["slider", "select", "text_input"];

/** A press for each button on the panels, a change for each slider or select. */
function elementEvents(project: ProjectConfig | null): ScriptCompletion[] {
  return projectElements(project).flatMap((el) => {
    const name = el.label || el.type;
    if (PRESSED_TYPES.includes(el.type)) return [event(`ui.press.${el.id}`, `Button press: ${name}`)];
    if (CHANGED_TYPES.includes(el.type)) return [event(`ui.change.${el.id}`, `Value change: ${name}`)];
    return [];
  });
}

/** Each family that names a device or a macro, for each one in the project. */
function ownEvents(project: ProjectConfig | null): ScriptCompletion[] {
  if (!project) return [];
  const owners: [string, { id: string; name: string }[]][] = [
    ["<device_id>", project.devices],
    ["<macro_id>", project.macros],
  ];
  return SCRIPT_EVENTS.flatMap((family) =>
    owners
      .filter(([placeholder]) => family.name.includes(placeholder))
      .flatMap(([placeholder, items]) =>
        items.map((item) =>
          event(family.name.replace(placeholder, item.id), `${item.name}: ${family.description}`),
        ),
      ),
  );
}
