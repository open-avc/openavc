import type { MacroConfig } from "../../api/types";
import type { PlacedResult } from "../../store/logStore";

/**
 * Where a step's result from a run is shown: on which row of a macro's editor,
 * and how the last run names it.
 *
 * A run reports a step by the macro it belongs to and its place in that macro's
 * tree (`PlacedResult`). A step inside a conditional's branch has its own
 * index within the branch, and a macro a Run Macro step calls has its own
 * steps, so the index alone names the wrong row of the macro on screen.
 */

/** The result for this macro's top-level row `row`: one of this macro's own
 *  steps, on the top-level step that holds it (a step inside a conditional's
 *  branch is shown on that conditional). */
export function resultOnRow<T extends PlacedResult>(
  results: T[],
  macroId: string,
  row: number,
): T | undefined {
  return results.find((r) => r.macroId === macroId && r.stepPath[0] === row);
}

/** The result of this macro's top-level step `row` itself, never one from a
 *  step inside its branch: a conditional's answer, a group's results. */
export function resultOfRow<T extends PlacedResult>(
  results: T[],
  macroId: string,
  row: number,
): T | undefined {
  return results.find((r) => r.macroId === macroId && r.stepPath.length === 1 && r.stepPath[0] === row);
}

/** "Step 2" for a step of the run's own macro (the top-level step that holds
 *  it), "Projector On, step 1" for a step of a macro the run called. */
export function resultStepLabel(
  result: PlacedResult,
  runMacroId: string,
  nameOf: (macroId: string) => string,
): string {
  const top = result.stepPath[0];
  const n = typeof top === "number" ? top + 1 : "?";
  return result.macroId === runMacroId ? `Step ${n}` : `${nameOf(result.macroId)}, step ${n}`;
}

/** A macro's name by its id, from the project's macros; the id for one it does not have. */
export function macroNamer(allMacros: MacroConfig[]): (macroId: string) => string {
  return (macroId) => allMacros.find((m) => m.id === macroId)?.name ?? macroId;
}
