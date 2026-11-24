/**
 * Pure helpers behind the plugin extension renderers (PluginExtensions.tsx):
 * glob matching for state patterns and driver ids, metric formatting, the
 * plugin log filter, and which context actions a page draws and what each
 * is sent as.
 */

import type { PluginExtension } from "../../api/restClient";
import { booleanWord } from "../shared/booleanWords";

/** The prefix of every state key a plugin owns (`plugin_namespace` in
 *  core/plugin_extension_rules.py, which holds this copy to it). */
export function pluginNamespace(pluginId: string): string {
  return `plugin.${pluginId}.`;
}

/** What the panel accepts in a plugin element's plugin id and type
 *  (`PANEL_ELEMENT_NAME_RE` in core/plugin_extension_rules.py, which holds
 *  this copy to it); it draws a placeholder for any other name. */
const PANEL_ELEMENT_NAME = /^[A-Za-z0-9_-]+$/;

/** The plugin elements the panel draws, in the order they are served. */
export function drawablePanelElements(elements: PluginExtension[]): PluginExtension[] {
  return elements.filter(
    (e) => PANEL_ELEMENT_NAME.test(e.plugin_id) && PANEL_ELEMENT_NAME.test(e.type ?? ""),
  );
}

// Convert a glob-style pattern to an anchored RegExp.
//
// Replaces every `*` with `.*` (multi-segment, matches across `.`) and
// escapes all other regex specials. Anchoring with ^…$ keeps a pattern
// like `plugin.foo.*` from accidentally matching `plugin.football.*`,
// the bug A70 called out.
export function compileStatePattern(pattern: string): RegExp | null {
  const trimmed = pattern.trim();
  if (!trimmed) return null;
  // Escape every regex special except `*`, then turn `*` into `.*`.
  const escaped = trimmed
    .replace(/[.+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\*/g, ".*");
  try {
    return new RegExp(`^${escaped}$`);
  } catch {
    return null;
  }
}

// Match a driver id against the documented glob syntax (`dante_*`).
// Every `*` is a wildcard wherever it appears; a pattern without `*`
// must match exactly. Same compiler as state patterns so the two glob
// surfaces can't drift apart.
export function matchesDriverGlob(driverId: string, pattern: string): boolean {
  const re = compileStatePattern(pattern);
  return re ? re.test(driverId) : false;
}

// Format a status-card metric value. State values are flat primitives, so a
// plugin can publish a boolean as the string "false" or "0" — JS truthiness
// would render those as "Yes".
export function formatMetric(value: unknown, format: string): string {
  if (value === null || value === undefined) return "—";
  if (format === "boolean") {
    if (typeof value === "string") {
      const lowered = value.trim().toLowerCase();
      return booleanWord(!(lowered === "false" || lowered === "0" || lowered === ""));
    }
    return booleanWord(Boolean(value));
  }
  return String(value);
}

export interface PluginLogEntry {
  timestamp: number;
  level: string;
  source: string;
  message: string;
}

// Filter the log buffer down to one plugin's recent entries.
export function filterPluginLog<T extends PluginLogEntry>(
  entries: T[],
  pluginId: string,
): T[] {
  return entries
    .filter(
      (e) =>
        e.source === "openavc.core.plugin_loader" ||
        e.message.includes(`[Plugin:${pluginId}]`),
    )
    .slice(-50);
}

// Cheap change check for the append-only log ring buffer: same length and
// same last entry means the filtered view is unchanged.
export function sameLogTail(a: PluginLogEntry[], b: PluginLogEntry[]): boolean {
  if (a.length !== b.length) return false;
  if (a.length === 0) return true;
  const la = a[a.length - 1];
  const lb = b[b.length - 1];
  return la.timestamp === lb.timestamp && la.message === lb.message;
}

// ──── Context actions ────

/** The page the IDE draws a context action on, by its `context`: the copy
 *  of plugin_extension_rules.CONTEXT_ACTION_PAGES, held to it by
 *  tests/test_plugin_extension_rules.py. An action with another context is
 *  drawn nowhere. */
export const CONTEXT_ACTION_PAGES: Record<string, "device" | "plugin"> = {
  global: "plugin",
  device: "device",
  plugin: "plugin",
};

/** A page context actions are drawn on: a device's, or a plugin's own. */
export type ContextActionPlace =
  | { page: "device"; deviceId: string; driverId?: string }
  | { page: "plugin"; pluginId: string };

/** The context actions a page draws, in the order they are served. A device
 *  page draws every plugin's device actions its device matches; a plugin's
 *  page draws that plugin's global and plugin actions. */
export function contextActionsFor(
  actions: PluginExtension[],
  place: ContextActionPlace,
): PluginExtension[] {
  return actions.filter((action) => {
    if (CONTEXT_ACTION_PAGES[action.context ?? ""] !== place.page) return false;
    return place.page === "device"
      ? matchesDevice(action, place.driverId)
      : action.plugin_id === place.pluginId;
  });
}

function matchesDevice(action: PluginExtension, driverId: string | undefined): boolean {
  const pattern = action.match?.driver_id;
  if (!pattern || !driverId) return true;
  return matchesDriverGlob(driverId, String(pattern));
}

/** What an action is sent with: a device's id on its page, nothing on a
 *  plugin's page. */
export function contextActionPayload(place: ContextActionPlace): Record<string, unknown> {
  return place.page === "device" ? { device_id: place.deviceId } : {};
}

const EVENT_PREFIX = "action.";

/** The action name an event is sent as: the event with one leading
 *  `action.` taken off, since the server emits `plugin.<id>.action.<name>`.
 *  Null for an action with no event. Anything else is sent as written, and
 *  a name the server refuses is said on screen. */
export function contextActionName(event: string | undefined): string | null {
  const name = event?.startsWith(EVENT_PREFIX) ? event.slice(EVENT_PREFIX.length) : event;
  return name ? name : null;
}

/** The sentence an action asks before it runs, or null when it runs at once. */
export function contextActionConfirm(action: PluginExtension): string | null {
  const sentence = action.confirm;
  return typeof sentence === "string" && sentence.trim() ? sentence : null;
}
