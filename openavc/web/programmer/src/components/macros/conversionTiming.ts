/**
 * What Convert to Script leaves out of a trigger's and a macro's timing.
 *
 * A converted script's handler runs its steps each time it is called, so a
 * trigger's Debounce, Cooldown and Overlap set to Queue, and the macro's own
 * Cooldown and Overlap, have no part in it. The preview names each one set
 * (macroHelpers' getConversionWarnings); a setting at its default says nothing.
 */
import type { MacroConfig, TriggerConfig } from "../../api/types";

/** The trigger types a script gets a handler for; a schedule is a comment. */
const HANDLED_TRIGGERS = new Set(["state_change", "event", "startup"]);

/** The names of the settings in `checks` that are set. */
function setNames(checks: [boolean, string][]): string[] {
  return checks.filter(([set]) => set).map(([, name]) => name);
}

/** A trigger's timing a script leaves out. */
function triggerTimingLeftOut(trigger: TriggerConfig): string[] {
  return setNames([
    [(trigger.debounce_seconds ?? 0) > 0, "Debounce"],
    [(trigger.cooldown_seconds ?? 0) > 0, "Cooldown"],
    [trigger.overlap === "queue", "Overlap (Queue)"],
  ]);
}

/** The macro's own timing a script leaves out. */
function macroTimingLeftOut(macro: MacroConfig): string[] {
  return setNames([
    [(macro.cooldown_seconds ?? 0) > 0, "Cooldown"],
    [macro.overlap === "skip" || macro.overlap === "queue", "Overlap"],
  ]);
}

/** "A", "A and B", "A, B and C". */
function andList(names: string[]): string {
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0];
}

/** One trigger's warning, or none when it leaves nothing out. */
function triggerWarning(trigger: TriggerConfig): string[] {
  const left = triggerTimingLeftOut(trigger);
  if (left.length === 0) return [];
  const [are, them] = left.length > 1 ? ["are", "them"] : ["is", "it"];
  return [
    `Trigger "${trigger.id}": its ${andList(left)} ${are} not converted, so the script runs on every change or event the trigger would wait out or turn away. To keep ${them}, keep the trigger on this macro and replace the macro's steps with one Emit Event step for the script to listen for.`,
  ];
}

/** A warning for each enabled trigger that becomes a handler, and for the
 *  macro when one does, naming the timing the script leaves out. */
export function timingWarnings(macro: MacroConfig): string[] {
  const handled = (macro.triggers ?? []).filter((t) => t.enabled && HANDLED_TRIGGERS.has(t.type));
  const warnings = handled.flatMap(triggerWarning);
  const own = macroTimingLeftOut(macro);
  if (handled.length > 0 && own.length > 0) {
    warnings.push(
      `The macro's own ${andList(own)} ${own.length > 1 ? "are" : "is"} not converted, so the script runs its steps where the macro would turn a run away.`,
    );
  }
  return warnings;
}
