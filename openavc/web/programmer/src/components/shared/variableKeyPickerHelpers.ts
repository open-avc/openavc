// Pure helpers for VariableKeyPicker (extracted so the group-header labelling
// can be unit-tested without React).

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
  if (group === "trigger") return "Trigger event";
  return "Project Variables";
}
