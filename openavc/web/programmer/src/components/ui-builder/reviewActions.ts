/**
 * A `do` action nothing runs.
 *
 * Mirrors "What a `do` action can be called" in openavc/ui/page_review.py.
 */

import type { UIElement } from "../../api/types";
import { DISPATCHED_ACTIONS, MACRO_ONLY_ACTIONS } from "../../api/uiBindingReach.gen";
import { isPlainObject } from "./authoredValues";
import { NESTED_ACTION_FIELDS } from "./bindingActions";
import { resolveMatrixAxis } from "./matrixModel";
import type { ReviewFinding } from "./reviewFinding";

// Mirrors do_action_findings in openavc/ui/page_review.py. The other half of the
// same silence: `bindingFindings` (reviewBindings.ts) answers whether this type's renderer
// READS a show slot, this answers whether the runtime DISPATCHES a do action.
// Both closed sets are generated from the modules that own them, so a new
// action name or a new macro step arrives here without anyone typing it.

type ActionEntry = [string, Record<string, unknown>];

/** One action and everything nested inside it, each with its own path. */
function walkAction(path: string, action: unknown): ActionEntry[] {
  if (!isPlainObject(action)) return [];
  const found: ActionEntry[] = [[path, action]];
  for (const field of NESTED_ACTION_FIELDS) {
    found.push(...walkAction(`${path}.${field}`, action[field]));
  }
  const mapped = action.map;
  if (action.action === "value_map" && isPlainObject(mapped)) {
    // Sorted, because this side hoists integer-like keys to the front of an
    // object whatever the order they were written in, and the Python side
    // walks them as authored. See the comment on the Python twin.
    for (const value of Object.keys(mapped).sort()) {
      found.push(...walkAction(`${path}.map.${value}`, mapped[value]));
    }
  }
  return found;
}

/** Every action dict under `do`, with the path a reader can go and edit. */
function actionEntries(do_: Record<string, unknown>): ActionEntry[] {
  const found: ActionEntry[] = [];
  for (const [interaction, binding] of Object.entries(do_)) {
    const entries = Array.isArray(binding) ? binding : [binding];
    for (const entry of entries) {
      found.push(...walkAction(`do.${interaction}`, entry));
    }
  }
  return found;
}

/** The same walk over a matrix row that overrides the element's do.route. */
function destinationRouteEntries(el: UIElement): ActionEntry[] {
  if (el.type !== "matrix") return [];
  const config = (el as unknown as Record<string, unknown>).matrix_config;
  if (!isPlainObject(config)) return [];
  const found: ActionEntry[] = [];
  for (const entry of resolveMatrixAxis(config, "destinations")) {
    if (!Array.isArray(entry.route)) continue;
    const named = entry.label !== undefined ? entry.label : entry.value;
    for (const action of entry.route) {
      found.push(...walkAction(`do.route for '${named}'`, action));
    }
  }
  return found;
}

/**
 * Every action this element can run, each with the path a reader can go and
 * edit: its own `do` first, then each matrix row's override of `do.route`. A new
 * place an action lives is added here. Mirrors `_element_actions` in
 * page_review.py.
 */
function elementActions(el: UIElement): ActionEntry[] {
  const do_ = ((el.bindings ?? {}) as Record<string, unknown>).do;
  const entries = isPlainObject(do_) ? actionEntries(do_) : [];
  return [...entries, ...destinationRouteEntries(el)];
}

/** The names a binding action can have, as a sentence lists them. */
const BINDING_ACTIONS_SAID = [...DISPATCHED_ACTIONS].sort().join(", ");

/**
 * What the action at `path` is, said after "has", when no branch of the
 * runtime takes it. The middle case is why the sentence is worth reading: a
 * macro step is the natural wrong guess, and how to reach one is the fix.
 * Mirrors `_why_nothing_runs` in page_review.py.
 */
function whyNothingRuns(path: string, action: string): string {
  if (!action) return `a ${path} entry with no action, so nothing runs when it fires.`;
  if (MACRO_ONLY_ACTIONS.includes(action)) {
    return (
      `${path} action '${action}', which is a macro step and not a binding action, ` +
      `so nothing runs when it fires. Put it in a macro and call that one with ` +
      `action 'macro'.`
    );
  }
  return `${path} action '${action}', which nothing dispatches, so nothing runs when it fires.`;
}

/**
 * Actions the runtime has no branch for, so the interaction does nothing.
 *
 * The chain in `ui_events.execute_action` ends with the names it knows
 * (`DISPATCHED_ACTIONS`); anything else reaches no branch. Nothing rejected it on the way in and --
 * until this shipped alongside the runtime's own warning -- nothing logged it
 * either, so a control authored with a retired spelling drew perfectly, took
 * the press, and did nothing at all.
 */
export function doActionFindings(el: UIElement): ReviewFinding[] {
  const findings: ReviewFinding[] = [];
  for (const [path, def] of elementActions(el)) {
    const action = typeof def.action === "string" ? def.action : "";
    if (DISPATCHED_ACTIONS.includes(action)) continue;
    findings.push({
      elementId: el.id,
      kind: "binding_not_dispatched",
      message:
        `${el.id} (${el.type}) has ${whyNothingRuns(path, action)} A binding action is ` +
        `one of: ${BINDING_ACTIONS_SAID}.`,
      key: `binding_not_dispatched|${el.id}|${path}|${action}`,
    });
  }
  return findings;
}
