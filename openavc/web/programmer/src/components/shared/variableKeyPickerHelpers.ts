// Pure helpers for VariableKeyPicker (extracted so the group-header labelling
// and the trigger group's rows can be unit-tested without React).

import { FIRING_CONTEXTS, type FiringContext } from "../../api/firingContext.gen";

/**
 * Human-readable header for a state-key picker group id.
 *
 * The group ids come from VariableKeyPicker's allEntries: "control",
 * "variables", "device:<id>", "system", "plugin:<id>", "ui:<elId>", a bare
 * "ui" for ui.* keys whose element isn't in the project, "trigger", and
 * "fired" for the fields a trigger's guard reads.
 * `name` is the group's display name (device / plugin id / page) where one
 * applies. Anything unrecognised falls back to "Project Variables" — which is
 * correct only for the "variables" group, so every real source (plugin.*,
 * orphan ui.*) must be matched explicitly here or it renders under the wrong
 * category header.
 */
export function groupLabel(group: string, name?: string): string {
  const label = GROUP_LABELS.get(group);
  if (label !== undefined) return label;
  const named = NAMED_GROUPS.find(([prefix]) => group.startsWith(prefix));
  return named ? `${named[1]}: ${name ?? ""}` : "Project Variables";
}

/** The headers of the groups with one fixed name. */
const GROUP_LABELS: ReadonlyMap<string, string> = new Map([
  ["control", "This control"],
  ["system", "System"],
  ["ui", "UI"],
  ["trigger", "What ran this macro"],
  ["fired", "What fired this trigger"],
]);

/** The groups named after their device, plugin or page: the id's prefix and
 *  the header's first word. */
const NAMED_GROUPS: readonly (readonly [string, string])[] = [
  ["device:", "Device"],
  ["plugin:", "Plugin"],
  ["ui:", "UI"],
];

/** One `trigger.<field>` row of the picker's "What ran this macro" group. */
export interface TriggerFieldRow {
  key: string;
  label: string;
  description: string;
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

/** What hands over the fields of an event's own payload, as a row says it. */
const PAYLOAD_SOURCES = Object.values(FIRING_CONTEXTS)
  .filter((context) => context.carries_payload)
  .map((context) => lowerFirst(context.name));

/**
 * Every field a macro run's firing context can hold, once each, in the order
 * the table (value_resolver's FIRING_CONTEXTS, generated) lists them: what the
 * field holds and what hands it over. With `type`, a trigger type, only the
 * fields that type hands over, for that trigger's guard.
 */
export function triggerFieldRows(type?: string): TriggerFieldRow[] {
  if (type !== undefined) {
    const context = (FIRING_CONTEXTS as Record<string, FiringContext>)[type];
    return (context?.fields ?? []).map((field) => ({
      key: `trigger.${field.name}`,
      label: field.name,
      description: `${field.description}.`,
    }));
  }
  const rows = new Map<string, { description: string; from: string[] }>();
  for (const context of Object.values(FIRING_CONTEXTS)) {
    for (const field of context.fields) {
      const row = rows.get(field.name) ?? { description: field.description, from: [] };
      row.from.push(lowerFirst(context.name));
      rows.set(field.name, row);
    }
  }
  return Array.from(rows, ([name, row]) => ({
    key: `trigger.${name}`,
    label: name,
    description: `${row.description}. From ${row.from.join(" or ")}.`,
  }));
}

const TYPED_TRIGGER_FIELD = /^\$?trigger\.([A-Za-z_][A-Za-z0-9_]*)$/;

/**
 * The row for a `trigger.<field>` key typed into the search box, for a field
 * of an event's own payload, which no table can list. Null when the search
 * names no such key. With `type`, a trigger type, for that trigger's guard:
 * null for a type that hands over no event's fields.
 */
export function typedTriggerFieldRow(search: string, type?: string): TriggerFieldRow | null {
  const match = TYPED_TRIGGER_FIELD.exec(search.trim());
  if (!match) return null;
  if (type !== undefined && !(FIRING_CONTEXTS as Record<string, FiringContext>)[type]?.carries_payload) {
    return null;
  }
  return {
    key: `trigger.${match[1]}`,
    label: match[1],
    description: type === undefined
      ? `A field of the event. From ${PAYLOAD_SOURCES.join(" or ")}.`
      : "A field of the event.",
  };
}
