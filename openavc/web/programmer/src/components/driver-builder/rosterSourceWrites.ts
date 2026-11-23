// What the Child Entity Types editor writes when a roster's source changes.
// Pure logic, unit tested without React (rosterSourceWrites.test.ts).
import { followedState, type DriverChildInstances } from "../../api/types";

export type RosterSource = "none" | "count" | "count_from" | "ids_from" | "ids";

/**
 * The instances block a change of roster source writes, the first config
 * field filling `count_from` or `ids_from`. `count_from_state` rides along to
 * `count_from` only: a count, a literal list and an ID-list field fix the
 * roster, so the platform refuses it beside them and no reader follows it.
 * The label and a `reported` presence ride along to every source: they are
 * facts about the roster, not about which field sizes it.
 */
export function instancesForSource(
  next: RosterSource,
  inst: DriverChildInstances | undefined,
  configFields: string[],
): DriverChildInstances | undefined {
  if (next === "none") return undefined;
  const field = configFields[0] ?? "";
  const follows = followedState(inst);
  const written: DriverChildInstances =
    next === "count" ? { count: 2 }
    : next === "ids" ? { ids: [] }
    : next === "ids_from" ? { ids_from: field }
    : { count_from: field, ...(follows ? { count_from_state: follows } : {}) };
  return { ...written, ...kept(inst) };
}

function kept(inst: DriverChildInstances | undefined): DriverChildInstances {
  return {
    ...(inst?.presence === "reported" ? { presence: "reported" as const } : {}),
    ...(inst?.label ? { label: inst.label } : {}),
  };
}
