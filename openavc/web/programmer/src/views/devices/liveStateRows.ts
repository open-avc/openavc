/**
 * Which of a device's live keys its page lists in Live State, and how each
 * value reads there.
 *
 * A driver that manages child entities declares their types, and a key under
 * one (`device.<id>.<type>.<paddedId>.<prop>`) belongs to the Child Entities
 * panel, not this flat list: a fully loaded controller has tens of thousands
 * of them. They are hidden here only (the state store, scripts, macros,
 * triggers and the cloud relay still see every key), and counted, because the
 * count is what the Live State table says lives in the panel above it and how
 * that panel hears its roster changed. The count ignores the filter.
 *
 * A value reads rounded to the precision the driver declares for it
 * (`readingText`), with the full value kept for a tooltip when rounding
 * shortened it. The filter matches a key or the value as shown.
 */
import type { DeclaredReading } from "../../components/shared/MonitorControl";
import { readingText, valueText } from "../../components/shared/booleanWords";

/** What a driver declares about a device-level reading: its label, unit and
 *  type (which Monitor fills in from), and its precision. */
export type StateVariables = Record<string, DeclaredReading & { step?: unknown }>;

export interface LiveStateRow {
  /** The key below `device.<id>.`. */
  key: string;
  /** The value as live state holds it. */
  value: unknown;
  /** The value as the table shows it. */
  shown: string;
  /** The full value, when rounding shortened it. */
  full?: string;
}

/** The child types a driver declares (`child_entity_types`). */
export function childTypeNames(driverInfo: Record<string, unknown> | undefined): Set<string> {
  const cet = driverInfo?.child_entity_types as Record<string, unknown> | undefined;
  return cet ? new Set(Object.keys(cet)) : new Set<string>();
}

/** What decides a device's rows besides live state itself. */
export interface RowRules {
  /** The child types its driver declares (`childTypeNames`). */
  childTypes: Set<string>;
  declared: StateVariables;
  /** The filter box's words, trimmed and lower-cased. */
  filter: string;
}

/** The rows Live State lists for a device, in the order live state holds
 *  them, and how many child keys it left to the Child Entities panel. */
export function deviceStateRows(
  liveState: Record<string, unknown>,
  deviceId: string,
  { childTypes, declared, filter }: RowRules,
): { rows: LiveStateRow[]; childKeyCount: number } {
  const prefix = `device.${deviceId}.`;
  const rows: LiveStateRow[] = [];
  let childKeyCount = 0;
  for (const [fullKey, value] of Object.entries(liveState)) {
    if (!fullKey.startsWith(prefix)) continue;
    const key = fullKey.slice(prefix.length);
    if (isChildKey(key, childTypes)) {
      childKeyCount++;
      continue;
    }
    const shown = readingText(value, declared[key]);
    if (!matchesFilter(key, shown, filter)) continue;
    const full = valueText(value);
    rows.push({ key, value, shown, full: full !== shown ? full : undefined });
  }
  return { rows, childKeyCount };
}

function isChildKey(key: string, childTypes: Set<string>): boolean {
  if (childTypes.size === 0) return false;
  const dot = key.indexOf(".");
  return dot > 0 && childTypes.has(key.slice(0, dot));
}

function matchesFilter(key: string, shown: string, filter: string): boolean {
  if (!filter) return true;
  return key.toLowerCase().includes(filter) || shown.toLowerCase().includes(filter);
}

/** The keys that hold a list a command's or an action's parameter picker
 *  reads (`options_state`): a JSON-encoded {value, label} list, which Live
 *  State summarises by label instead of showing the raw JSON. */
export function optionFeedKeys(
  commands: Record<string, unknown> | undefined,
  driverInfo: Record<string, unknown> | undefined,
): Set<string> {
  const keys = new Set<string>();
  for (const cmd of Object.values(commands ?? {})) addFeedKeys(keys, cmd);
  const actions = driverInfo?.actions;
  if (Array.isArray(actions)) {
    for (const a of actions) addFeedKeys(keys, a);
  }
  return keys;
}

/** The `options_state` keys a command's or an action's parameters read. */
function addFeedKeys(keys: Set<string>, entry: unknown): void {
  const params = (entry as { params?: unknown } | null)?.params;
  if (!params || typeof params !== "object") return;
  for (const def of Object.values(params as Record<string, unknown>)) {
    const os = (def as { options_state?: unknown } | null)?.options_state;
    if (typeof os === "string" && os) keys.add(os);
  }
}
