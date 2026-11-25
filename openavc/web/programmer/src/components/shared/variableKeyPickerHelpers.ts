// Pure helpers for VariableKeyPicker (extracted so the group-header labelling
// and the trigger group's rows can be unit-tested without React).

import { FIRING_CONTEXTS } from "../../api/firingContext.gen";

/**
 * Human-readable header for a state-key picker group id.
 *
 * The group ids come from VariableKeyPicker's allEntries: "control",
 * "variables", "device:<id>", "system", "plugin:<id>", "ui:<elId>", a bare
 * "ui" for ui.* keys whose element isn't in the project, and "trigger".
 * `name` is the group's display name (device / plugin id / page) where one
 * applies. Anything unrecognised falls back to "Project Variables" — which is
 * correct only for the "variables" group, so every real source (plugin.*,
 * orphan ui.*) must be matched explicitly here or it renders under the wrong
 * category header.
 */
export function groupLabel(group: string, name?: string): string {
  if (group === "control") return "This control";
  if (group.startsWith("device:")) return `Device: ${name ?? ""}`;
  if (group === "system") return "System";
  if (group.startsWith("plugin:")) return `Plugin: ${name ?? ""}`;
  if (group.startsWith("ui:")) return `UI: ${name ?? ""}`;
  if (group === "ui") return "UI";
  if (group === "trigger") return "What ran this macro";
  return "Project Variables";
}

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
 * field holds and what hands it over.
 */
export function triggerFieldRows(): TriggerFieldRow[] {
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
 * names no such key.
 */
export function typedTriggerFieldRow(search: string): TriggerFieldRow | null {
  const match = TYPED_TRIGGER_FIELD.exec(search.trim());
  if (!match) return null;
  return {
    key: `trigger.${match[1]}`,
    label: match[1],
    description: `A field of the event. From ${PAYLOAD_SOURCES.join(" or ")}.`,
  };
}
